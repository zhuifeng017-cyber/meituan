"""
WeChat Video Channel (视频号) Scraper
Channel : sphflCd4Tm6wLvR
Fields  : title, description, views, likes, comments, shares, favorites

安装：
  pip install playwright
  playwright install chromium

运行：
  python scraper_playwright.py

首次运行会弹出浏览器并等待扫码；之后登录态自动保存，无需再次扫码。
"""

import asyncio
import csv
import json
import logging
from pathlib import Path

from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── 配置 ───────────────────────────────────────────────────────────────────────

FINDER_USERNAME = "sphflCd4Tm6wLvR"
TARGET_COUNT    = 200
PAGE_SIZE       = 10
REQUEST_DELAY   = 1.5

# 持久化 Chrome 配置目录：登录态保存在这里，第二次运行直接跳过扫码
PROFILE_DIR = str(Path(__file__).parent / "chrome_profile")

OUTPUT_JSON = "videos.json"
OUTPUT_CSV  = "videos.csv"
CSV_FIELDS  = [
    "index", "video_id", "title", "description",
    "views", "likes", "comments", "shares", "favorites",
    "create_time", "video_url",
]

# ── 反检测 JS（在每个页面加载前注入）────────────────────────────────────────────

_STEALTH_JS = """
() => {
    // 隐藏 webdriver 标记
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});

    // 伪造 chrome 对象
    window.chrome = {
        runtime: {},
        loadTimes: function(){},
        csi: function(){},
        app: {}
    };

    // 伪造插件列表
    Object.defineProperty(navigator, 'plugins', {
        get: () => [
            {name: 'Chrome PDF Plugin',   filename: 'internal-pdf-viewer',              description: 'Portable Document Format'},
            {name: 'Chrome PDF Viewer',   filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: ''},
            {name: 'Native Client',       filename: 'internal-nacl-plugin',             description: ''},
        ]
    });

    // 修正语言
    Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en-US', 'en']});

    // permissions.query 修复
    const _origQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (params) =>
        params.name === 'notifications'
            ? Promise.resolve({state: Notification.permission})
            : _origQuery(params);
}
"""

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

# ── 采集 ────────────────────────────────────────────────────────────────────────

async def _fetch_page(page, last_buffer: str) -> tuple[list, str, bool]:
    payload = json.dumps({
        "finderUsername": FINDER_USERNAME,
        "count":          PAGE_SIZE,
        "lastBuffer":     last_buffer,
    })
    result = await page.evaluate(
        """async (payload) => {
            const resp = await fetch(
                'https://channels.weixin.qq.com/cgi-bin/mmfindertrip/finder/profile/getpage',
                {method:'POST', headers:{'Content-Type':'application/json'},
                 body:payload, credentials:'include'}
            );
            return resp.json();
        }""",
        payload,
    )
    ret = result.get("base_resp", {}).get("ret", -1)
    if ret != 0:
        raise RuntimeError(f"API ret={ret}: {result.get('base_resp',{}).get('err_msg','')}")
    data     = result.get("data", {})
    objects  = data.get("object", [])
    next_buf = data.get("continuationBuffer", "")
    has_more = bool(data.get("hasMore", next_buf))
    return objects, next_buf, has_more


async def scrape() -> list[dict]:
    Path(PROFILE_DIR).mkdir(exist_ok=True)
    videos: list[dict] = []

    async with async_playwright() as p:
        # launch_persistent_context：使用持久化配置，登录态保存在 PROFILE_DIR
        context = await p.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-features=NativeMessaging",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-infobars",
            ],
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            viewport={"width": 1280, "height": 800},
            ignore_https_errors=True,
        )

        # 每个页面加载前都注入反检测脚本
        await context.add_init_script(_STEALTH_JS)

        page = context.pages[0] if context.pages else await context.new_page()

        # ── 1. 打开视频号首页 ──────────────────────────────────────────────────
        logger.info("正在打开 channels.weixin.qq.com …")
        try:
            await page.goto(
                "https://channels.weixin.qq.com/web/pages/home",
                wait_until="domcontentloaded",
                timeout=30_000,
            )
        except Exception as e:
            logger.warning("页面加载超时（继续等待）: %s", e)

        await asyncio.sleep(4)  # 等待 JS 渲染

        # 检查是否被跳转到微信版本更新页
        if "support.weixin.qq.com" in page.url:
            logger.error(
                "跳到了微信更新页，说明微信PC客户端仍在后台运行。\n"
                "请右键任务栏微信图标 → 退出微信，然后重新运行脚本。"
            )
            await context.close()
            return []

        # ── 2. 检查登录态 ──────────────────────────────────────────────────────
        cookies   = await context.cookies()
        auth_keys = [c["name"] for c in cookies if c["name"] in ("uin", "wxuin", "skey", "webex_data")]

        if not auth_keys:
            logger.info("未检测到登录态，等待扫码登录…")
            print("\n" + "═" * 60)
            print("  浏览器已打开，请用 手机微信 扫描页面上的二维码。")
            print("  扫码完成、页面跳转到主页后，回到这里按 Enter 继续…")
            print("═" * 60)
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, input, "")

            cookies   = await context.cookies()
            auth_keys = [c["name"] for c in cookies if c["name"] in ("uin", "wxuin", "skey", "webex_data")]

        if not auth_keys:
            logger.error(
                "仍未找到登录 Cookie（找到的键：%s）。\n"
                "可能原因：\n"
                "  1. 扫码后页面还未跳转，请等页面跳转到主页再按 Enter\n"
                "  2. 微信PC客户端未退出，导致版本检测失败\n"
                "请关闭本脚本，退出微信PC，重新运行。",
                [c["name"] for c in cookies],
            )
            await context.close()
            return []

        logger.info("登录成功（Cookie: %s）。下次运行将自动跳过扫码。", auth_keys)

        # ── 3. 跳转到目标视频号 ────────────────────────────────────────────────
        channel_url = (
            f"https://channels.weixin.qq.com/web/pages/profile"
            f"?username={FINDER_USERNAME}&entrance_id=1002"
        )
        await page.goto(channel_url, wait_until="domcontentloaded", timeout=20_000)
        await asyncio.sleep(2)

        # ── 4. 翻页采集 ────────────────────────────────────────────────────────
        logger.info("开始采集（目标 %d 条）…", TARGET_COUNT)
        last_buffer = ""

        while len(videos) < TARGET_COUNT:
            try:
                objects, last_buffer, has_more = await _fetch_page(page, last_buffer)
            except Exception as e:
                logger.error("采集出错：%s", e)
                break

            if not objects:
                logger.info("已无更多数据。")
                break

            for obj in objects:
                if len(videos) >= TARGET_COUNT:
                    break
                videos.append(_parse_video(obj, len(videos) + 1))

            logger.info("已采集 %d / %d 条", len(videos), TARGET_COUNT)

            if not has_more or not last_buffer:
                logger.info("已到最后一页。")
                break

            await asyncio.sleep(REQUEST_DELAY)

        await context.close()

    return videos

# ── 保存 ────────────────────────────────────────────────────────────────────────

def save(videos: list[dict]) -> None:
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(videos, f, ensure_ascii=False, indent=2)
    logger.info("JSON → %s", OUTPUT_JSON)

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(videos)
    logger.info("CSV  → %s（可直接用 Excel 打开）", OUTPUT_CSV)


if __name__ == "__main__":
    results = asyncio.run(scrape())
    if results:
        save(results)
        logger.info("完成，共采集 %d 条视频数据。", len(results))
    else:
        logger.warning("未采集到任何数据。")
