"""
WeChat Video Channel (视频号) Scraper — 最终版
Channel : sphflCd4Tm6wLvR

Cookie 获取：
  Charles → channels.weixin.qq.com → 任意请求 → Headers 标签 → 复制 Cookie 行
  存入 cookies.txt（格式：sessionInfo=xxx 或 uin=xxx; skey=xxx; ...）
"""

import csv
import json
import logging
import re
import sys
import time
from pathlib import Path
from urllib.parse import unquote

import requests

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

BASE = "https://channels.weixin.qq.com"

# 候选接口路径（按优先级尝试）
CANDIDATE_APIS = [
    f"{BASE}/cgi-bin/mmfindertrip/finder/profile/getpage",
    f"{BASE}/cgi-bin/mmchannelfindertrip/finder/profile/getpage",
    f"{BASE}/web/api/finder/profile/getpage",
    f"{BASE}/web/api/profile/getpage",
]

# 与 Charles 抓包完全一致的请求头
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/132.0.0.0 Safari/537.36 "
        "NetType/WIFI MicroMessenger/7.0.20.1781(0x6700143B) "
        "WindowsWechat(0x63090a13) UnifiedPCWindowsWechat(0xf254186b) "
        "XWEB/19481 Flue"
    ),
    "Referer":         f"{BASE}/web/pages/profile?username={FINDER_USERNAME}&entrance_id=1002&wx_header=0",
    "Origin":          BASE,
    "Content-Type":    "application/json",
    "Accept":          "application/json, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Fetch-Site":  "same-origin",
    "Sec-Fetch-Mode":  "cors",
    "Sec-Fetch-Dest":  "empty",
    "Connection":      "keep-alive",
}

# ── Cookie ─────────────────────────────────────────────────────────────────────

def load_cookies() -> dict[str, str]:
    p = Path("cookies.txt")
    if not p.exists():
        logger.error(
            "找不到 cookies.txt！\n"
            "Charles → channels.weixin.qq.com → 任意请求 → Headers 标签\n"
            "复制 Cookie 行的值，新建 cookies.txt 粘贴保存。"
        )
        sys.exit(1)
    raw = p.read_text(encoding="utf-8").strip()
    cookies: dict[str, str] = {}
    for part in raw.split(";"):
        part = part.strip()
        if "=" in part:
            k, _, v = part.partition("=")
            cookies[k.strip()] = unquote(v.strip())
    if not cookies:
        logger.error("cookies.txt 内容无法解析，请确认格式正确。")
        sys.exit(1)
    logger.info("Cookie 已加载：%s", list(cookies.keys()))
    return cookies

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

# ── 探测有效接口 ───────────────────────────────────────────────────────────────

def detect_api(session: requests.Session) -> str | None:
    payload = {"finderUsername": FINDER_USERNAME, "count": 1, "lastBuffer": ""}
    for url in CANDIDATE_APIS:
        try:
            r = session.post(url, json=payload, timeout=10)
            body = r.json()
            ret  = body.get("base_resp", {}).get("ret", body.get("errCode", -99))
            # ret=0 成功，ret=-3/200013 是 cookie 失效（接口本身是通的）
            if ret in (0, -3, 200013) or body.get("data", {}).get("object") is not None:
                logger.info("找到有效接口：%s  (ret=%s)", url, ret)
                return url
            logger.debug("接口 %s 返回 ret=%s，跳过", url, ret)
        except Exception as e:
            logger.debug("接口 %s 请求失败：%s", url, e)
    return None

# ── HTML 页面兜底解析 ──────────────────────────────────────────────────────────

def scrape_from_html(session: requests.Session) -> list[dict]:
    """
    当 cgi-bin 接口全部失效时，尝试从 profile 页面的 HTML 源码里
    提取服务端预渲染的 JSON 数据（常见于 SSR 页面）。
    """
    logger.info("尝试从 HTML 页面源码提取数据…")
    url = f"{BASE}/web/pages/profile?username={FINDER_USERNAME}&entrance_id=1002&wx_header=0"
    try:
        r = session.get(url, timeout=15)
    except Exception as e:
        logger.error("GET profile 页面失败：%s", e)
        return []

    html = r.text
    videos: list[dict] = []

    # 尝试提取 window.__INITIAL_STATE__ 或类似的嵌入 JSON
    patterns = [
        r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})(?:;|</script>)",
        r"window\.__STORE__\s*=\s*(\{.*?\})(?:;|</script>)",
        r'"object"\s*:\s*(\[.*?\])',
    ]
    for pat in patterns:
        m = re.search(pat, html, re.DOTALL)
        if not m:
            continue
        try:
            data = json.loads(m.group(1))
            objs = (data if isinstance(data, list)
                    else data.get("data", {}).get("object", [])
                    or data.get("object", []))
            for obj in objs:
                if "objectId" in obj:
                    videos.append(_parse_video(obj, len(videos) + 1))
        except Exception:
            continue
        if videos:
            break

    if videos:
        logger.info("从 HTML 提取到 %d 条数据", len(videos))
    else:
        logger.warning(
            "HTML 页面中未找到视频数据。\n"
            "  → 请在 Charles 里找到实际的视频列表接口路径，告知我更新脚本。\n"
            "  → 步骤：Charles 清空 → 微信PC打开该视频号并滚动 → 截图 channels.weixin.qq.com 下的请求列表"
        )
    return videos

# ── 翻页采集 ───────────────────────────────────────────────────────────────────

def fetch_page(session: requests.Session, api_url: str, last_buffer: str):
    payload = {"finderUsername": FINDER_USERNAME, "count": PAGE_SIZE, "lastBuffer": last_buffer}
    r = session.post(api_url, json=payload, timeout=20)
    r.raise_for_status()
    body = r.json()
    ret  = body.get("base_resp", {}).get("ret", -99)
    if ret in (-3, 200013):
        raise RuntimeError("Cookie 已失效，请重新从 Charles 复制 Cookie 到 cookies.txt。")
    if ret != 0:
        raise RuntimeError(f"API ret={ret}: {body}")
    data     = body.get("data", {})
    objects  = data.get("object", [])
    next_buf = data.get("continuationBuffer", "")
    has_more = bool(data.get("hasMore", next_buf))
    return objects, next_buf, has_more

# ── 主流程 ─────────────────────────────────────────────────────────────────────

def scrape() -> list[dict]:
    cookies = load_cookies()
    session = requests.Session()
    session.cookies.update(cookies)
    session.headers.update(HEADERS)

    # 1. 探测可用接口
    api_url = detect_api(session)

    # 2. 若所有 cgi-bin 接口不通，尝试 HTML 兜底
    if api_url is None:
        logger.warning("所有候选接口均不可用，尝试 HTML 解析兜底…")
        return scrape_from_html(session)

    # 3. 正常翻页采集
    videos: list[dict] = []
    last_buffer = ""
    logger.info("开始翻页采集，接口：%s", api_url)

    while len(videos) < TARGET_COUNT:
        try:
            objects, last_buffer, has_more = fetch_page(session, api_url, last_buffer)
        except RuntimeError as e:
            logger.error("%s", e)
            break
        except Exception as e:
            logger.error("请求异常：%s", e)
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

        time.sleep(REQUEST_DELAY)

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
    logger.info("CSV  → %s", OUTPUT_CSV)

if __name__ == "__main__":
    results = scrape()
    if results:
        save(results)
        logger.info("完成！共采集 %d 条。", len(results))
    else:
        logger.warning("未采集到任何数据，请查看上方提示。")
