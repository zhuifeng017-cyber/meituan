"""
WeChat Video Channel (视频号) Scraper — 最终版
Channel : sphflCd4Tm6wLvR
Fields  : title, description, views, likes, comments, shares, favorites

── 获取 Cookie（一次性操作）──────────────────────────────────────────────────────
1. 打开 Charles，打开微信PC，进入任意视频号主页
2. Charles 左侧点 channels.weixin.qq.com → 找任意一条请求
3. 右侧 Headers 标签 → 找到 Cookie 行，复制整行的值
   (值格式：sessionInfo=NvyL_xxxx...)
4. 新建 cookies.txt，粘贴进去保存（和本脚本放同一目录）
5. python scraper.py

Cookie 通常有效数天，失效后重复 1-4 步更新 cookies.txt 即可。
───────────────────────────────────────────────────────────────────────────────
"""

import csv
import json
import logging
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

API_URL = "https://channels.weixin.qq.com/cgi-bin/mmfindertrip/finder/profile/getpage"

# 完全匹配 Charles 抓到的 WeChat PC 内置浏览器请求头
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/132.0.0.0 Safari/537.36 "
        "NetType/WIFI MicroMessenger/7.0.20.1781(0x6700143B) "
        "WindowsWechat(0x63090a13) UnifiedPCWindowsWechat(0xf254186b) "
        "XWEB/19481 Flue"
    ),
    "Referer": (
        f"https://channels.weixin.qq.com/web/pages/profile"
        f"?username={FINDER_USERNAME}&entrance_id=1002&wx_header=0"
    ),
    "Origin":          "https://channels.weixin.qq.com",
    "Content-Type":    "application/json",
    "Accept":          "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Fetch-Site":  "same-origin",
    "Sec-Fetch-Mode":  "cors",
    "Sec-Fetch-Dest":  "empty",
    "Connection":      "keep-alive",
}

# ── Cookie 加载 ────────────────────────────────────────────────────────────────

def load_cookies() -> dict[str, str]:
    cookie_file = Path("cookies.txt")
    if not cookie_file.exists():
        logger.error(
            "\n找不到 cookies.txt！\n"
            "步骤：\n"
            "  1. Charles 左侧 → channels.weixin.qq.com → 任意请求\n"
            "  2. 右侧 Headers 标签 → 找 Cookie 行\n"
            "  3. 复制 Cookie 的值（如：sessionInfo=NvyL_xxxx...）\n"
            "  4. 新建 cookies.txt，粘贴，保存\n"
            "  5. 重新运行 python scraper.py\n"
        )
        sys.exit(1)

    raw = cookie_file.read_text(encoding="utf-8").strip()
    if not raw:
        logger.error("cookies.txt 是空的，请按上述步骤填入 Cookie。")
        sys.exit(1)

    # 解析 "k1=v1; k2=v2" 格式
    cookies: dict[str, str] = {}
    for part in raw.split(";"):
        part = part.strip()
        if "=" in part:
            k, _, v = part.partition("=")
            cookies[k.strip()] = unquote(v.strip())

    if not cookies:
        logger.error("cookies.txt 内容无法解析，请确认格式正确（sessionInfo=xxx...）。")
        sys.exit(1)

    logger.info("Cookie 加载成功：%s", list(cookies.keys()))
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

# ── API 请求 ───────────────────────────────────────────────────────────────────

def fetch_page(session: requests.Session, last_buffer: str) -> tuple[list, str, bool]:
    payload = {
        "finderUsername": FINDER_USERNAME,
        "count":          PAGE_SIZE,
        "lastBuffer":     last_buffer,
    }
    resp = session.post(API_URL, json=payload, timeout=20)
    resp.raise_for_status()
    body = resp.json()

    ret = body.get("base_resp", {}).get("ret", -1)
    err = body.get("base_resp", {}).get("err_msg", "")

    if ret == 200013 or ret == -3:
        raise RuntimeError(
            f"Cookie 已失效（ret={ret}）。\n"
            "  请重新从 Charles 复制 Cookie 到 cookies.txt，再运行脚本。"
        )
    if ret != 0:
        raise RuntimeError(f"API 错误 ret={ret}: {err}\n  响应：{body}")

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

    videos: list[dict] = []
    last_buffer = ""

    logger.info("开始采集 %s（目标 %d 条）…", FINDER_USERNAME, TARGET_COUNT)

    while len(videos) < TARGET_COUNT:
        try:
            objects, last_buffer, has_more = fetch_page(session, last_buffer)
        except RuntimeError as e:
            logger.error("%s", e)
            break
        except requests.HTTPError as e:
            logger.error("HTTP 错误：%s", e)
            break
        except Exception as e:
            logger.error("未知错误：%s", e)
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
    logger.info("CSV  → %s（可直接用 Excel 打开）", OUTPUT_CSV)

# ── 入口 ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    results = scrape()
    if results:
        save(results)
        logger.info("完成！共采集 %d 条视频数据。", len(results))
    else:
        logger.warning("未采集到任何数据，请检查上方错误信息。")
