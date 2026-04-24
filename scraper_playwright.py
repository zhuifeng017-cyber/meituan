"""
WeChat Video Channel (视频号) Scraper — Playwright 版
Channel : sphflCd4Tm6wLvR
Fields  : title, description, views, likes, comments, shares, favorites

原理：
  1. 自动打开浏览器，跳转到微信视频号网页版
  2. 等你扫码登录（浏览器窗口会显示二维码）
  3. 登录成功后自动抓取接口数据，无需手动复制任何 Cookie

安装：
  pip install playwright
  playwright install chromium

运行：
  python scraper_playwright.py
"""

import asyncio
import csv
import json
import logging
import time

from playwright.async_api import async_playwright, Page, Response

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── 配置 ───────────────────────────────────────────────────────────────────────

FINDER_USERNAME  = "sphflCd4Tm6wLvR"
TARGET_COUNT     = 200
PAGE_SIZE        = 10
REQUEST_DELAY    = 1.5   # 翻页间隔（秒）
LOGIN_TIMEOUT    = 120   # 等待扫码的最长秒数

OUTPUT_JSON = "videos.json"
OUTPUT_CSV  = "videos.csv"
CSV_FIELDS  = [
    "index", "video_id", "title", "description",
    "views", "likes", "comments", "shares", "favorites",
    "create_time", "video_url",
]

API_PATH = "/cgi-bin/mmfindertrip/finder/profile/getpage"
PROFILE_URL = (
    "https://channels.weixin.qq.com/web/pages/home"
)

# ── 数据解析 ───────────────────────────────────────────────────────────────────

def _parse_video(obj: dict, index: int) -> dict:
    desc       = obj.get("objectDesc", {})
    media_list = desc.get("media", [{}])
    media      = media_list[0] if media_list else {}
    return {
        "index":       index,
        "video_id":    obj.get("objectId", ""),
        "title":       media.get("title") or desc.get("title", ""),
        "description": desc.get("description", ""),
        "views":       obj.get("readCount", obj.get("viewCount", 0)),
        "likes":       obj.get("likeCount", 0),
        "comments":    obj.get("commentCount", 0),
        "shares":      obj.get("forwardCount", 0),
        "favorites":   obj.get("favCount", 0),
        "create_time": obj.get("createTime", 0),
        "video_url":   media.get("url", ""),
    }


# ── 主逻辑 ─────────────────────────────────────────────────────────────────────

async def scrape() -> list[dict]:
    videos: list[dict] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,   # 显示浏览器窗口，方便扫码
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/129.0.0.0 Safari/537.36"
            ),
            locale="zh-CN",
        )
        page = await context.new_page()

        # ── 1. 打开视频号网页版，等待扫码登录 ──────────────────────────────────
        logger.info("正在打开浏览器，请在弹出窗口中扫码登录微信视频号…")
        await page.goto("https://channels.weixin.qq.com/web/pages/home", wait_until="domcontentloaded")

        logger.info("等待登录（最多 %d 秒）…", LOGIN_TIMEOUT)
        try:
            # 登录成功后页面会出现 .finder-home 或跳转离开登录页
            await page.wait_for_function(
                "() => !document.querySelector('.login-page, .qrcode-page, [class*=\"login\"]') "
                "|| document.cookie.includes('uin')",
                timeout=LOGIN_TIMEOUT * 1000,
            )
        except Exception:
            pass  # 超时就继续，让后续请求失败时再报错

        logger.info("登录检测完成，开始采集视频数据…")

        # ── 2. 跳转到目标视频号主页 ────────────────────────────────────────────
        channel_url = (
            f"https://channels.weixin.qq.com/web/pages/profile"
            f"?username={FINDER_USERNAME}&entrance_id=1002"
        )
        await page.goto(channel_url, wait_until="domcontentloaded")
        await asyncio.sleep(2)

        # ── 3. 提取当前 Cookie 供后续 fetch 使用 ──────────────────────────────
        cookies = await context.cookies()
        cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in cookies)

        if not any(c["name"] in ("uin", "wxuin", "skey") for c in cookies):
            logger.error("未检测到登录态 Cookie，请确认已在浏览器窗口完成扫码登录。")
            await browser.close()
            return []

        logger.info("已获取登录 Cookie，开始翻页采集（目标 %d 条）…", TARGET_COUNT)

        # ── 4. 通过 page.evaluate 调用接口（带 Cookie、同域）──────────────────
        last_buffer = ""
        while len(videos) < TARGET_COUNT:
            payload = json.dumps({
                "finderUsername": FINDER_USERNAME,
                "count":          PAGE_SIZE,
                "lastBuffer":     last_buffer,
            })

            result = await page.evaluate(
                """async (payload) => {
                    const resp = await fetch(
                        'https://channels.weixin.qq.com/cgi-bin/mmfindertrip/finder/profile/getpage',
                        {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: payload,
                            credentials: 'include',
                        }
                    );
                    return resp.json();
                }""",
                payload,
            )

            ret_code = result.get("base_resp", {}).get("ret", -1)
            if ret_code != 0:
                err = result.get("base_resp", {}).get("err_msg", "")
                logger.error("接口错误 ret=%d: %s", ret_code, err)
                break

            data     = result.get("data", {})
            objects  = data.get("object", [])
            next_buf = data.get("continuationBuffer", "")
            has_more = bool(data.get("hasMore", next_buf))

            if not objects:
                logger.info("接口返回空列表，采集结束。")
                break

            for obj in objects:
                if len(videos) >= TARGET_COUNT:
                    break
                videos.append(_parse_video(obj, len(videos) + 1))

            logger.info("已采集 %d / %d 条", len(videos), TARGET_COUNT)

            if not has_more or not next_buf:
                logger.info("已到达最后一页。")
                break

            last_buffer = next_buf
            await asyncio.sleep(REQUEST_DELAY)

        await browser.close()

    return videos


def save(videos: list[dict]) -> None:
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(videos, f, ensure_ascii=False, indent=2)
    logger.info("JSON 已保存 → %s", OUTPUT_JSON)

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(videos)
    logger.info("CSV  已保存 → %s（可直接用 Excel 打开）", OUTPUT_CSV)


if __name__ == "__main__":
    results = asyncio.run(scrape())
    if results:
        save(results)
        logger.info("完成，共采集 %d 条视频数据。", len(results))
    else:
        logger.warning("未采集到任何数据。")
