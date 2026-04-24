"""
WeChat Video Channel (视频号) Scraper — Playwright 版
Channel : sphflCd4Tm6wLvR
Fields  : title, description, views, likes, comments, shares, favorites

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

from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── 配置 ───────────────────────────────────────────────────────────────────────

FINDER_USERNAME = "sphflCd4Tm6wLvR"
TARGET_COUNT    = 200
PAGE_SIZE       = 10
REQUEST_DELAY   = 1.5

OUTPUT_JSON = "videos.json"
OUTPUT_CSV  = "videos.csv"
CSV_FIELDS  = [
    "index", "video_id", "title", "description",
    "views", "likes", "comments", "shares", "favorites",
    "create_time", "video_url",
]

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
            headless=False,
            args=[
                # 禁用 Native Messaging，阻止微信客户端注入版本信息
                "--disable-features=NativeMessaging",
                "--disable-extensions",
                "--no-first-run",
                "--no-default-browser-check",
            ],
        )
        context = await browser.new_context(
            # 使用标准 Chrome UA，不带 WindowsWechat 标记
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="zh-CN",
        )
        page = await context.new_page()

        # ── 1. 打开视频号网页版 ────────────────────────────────────────────────
        logger.info("正在打开浏览器…")
        await page.goto(
            "https://channels.weixin.qq.com/web/pages/home",
            wait_until="domcontentloaded",
            timeout=30_000,
        )
        await asyncio.sleep(2)

        # ── 检查是否被跳转到版本更新页 ───────────────────────────────────────
        if "support.weixin.qq.com" in page.url or "update" in page.url:
            logger.error(
                "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "  检测到：微信客户端版本过低（当前 4.1.9）\n"
                "  网页版需要更高版本的微信客户端支持。\n\n"
                "  解决方法（任选其一）：\n"
                "  A. 更新微信桌面客户端到最新版本后重试\n"
                "  B. 在 Charles 里抓 channels.weixin.qq.com\n"
                "     的 cgi-bin POST 请求，把 Cookie 复制到\n"
                "     cookies.txt，再运行 scraper.py\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            )
            await browser.close()
            return []

        # ── 2. 等待用户扫码登录（手动确认）────────────────────────────────────
        print("\n" + "═" * 55)
        print("  浏览器已打开 channels.weixin.qq.com")
        print("  请用手机微信扫描页面上的二维码完成登录。")
        print("  登录成功后（页面跳转到主页），")
        print("  回到这个终端窗口，按 Enter 继续…")
        print("═" * 55)

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, input, "")

        # ── 3. 验证登录态 ──────────────────────────────────────────────────────
        cookies = await context.cookies()
        cookie_map = {c["name"]: c["value"] for c in cookies}
        auth_keys = [k for k in cookie_map if k in ("uin", "wxuin", "skey", "webex_data", "mmid")]

        if not auth_keys:
            logger.error("未检测到登录 Cookie，请确认已完成扫码登录，然后重试。")
            logger.info("当前 Cookie 键名：%s", list(cookie_map.keys()))
            await browser.close()
            return []

        logger.info("登录成功，检测到 Cookie：%s", auth_keys)

        # ── 4. 跳转到目标视频号主页 ────────────────────────────────────────────
        channel_url = (
            f"https://channels.weixin.qq.com/web/pages/profile"
            f"?username={FINDER_USERNAME}&entrance_id=1002"
        )
        await page.goto(channel_url, wait_until="domcontentloaded", timeout=20_000)
        await asyncio.sleep(2)

        # ── 5. 翻页采集（通过页面内 fetch 调用接口，自动携带 Cookie）──────────
        logger.info("开始采集（目标 %d 条）…", TARGET_COUNT)
        last_buffer = ""

        while len(videos) < TARGET_COUNT:
            payload = json.dumps({
                "finderUsername": FINDER_USERNAME,
                "count":          PAGE_SIZE,
                "lastBuffer":     last_buffer,
            })

            try:
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
            except Exception as e:
                logger.error("fetch 调用失败：%s", e)
                break

            ret_code = result.get("base_resp", {}).get("ret", -1)
            if ret_code != 0:
                err = result.get("base_resp", {}).get("err_msg", "")
                logger.error("接口错误 ret=%d: %s  (返回内容: %s)", ret_code, err, result)
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
