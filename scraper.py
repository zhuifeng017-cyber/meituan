"""
WeChat Video Channel (视频号) Scraper — requests 版
Channel : sphflCd4Tm6wLvR
Fields  : title, description, views, likes, comments, shares, favorites

── 获取 Cookie（只需一次）──────────────────────────────────────────────────────
1. 用 Chrome 打开 https://channels.weixin.qq.com/web/pages/home
2. 手机微信扫码登录，进入主页
3. 按 F12 → Console 标签 → 粘贴下面这行代码回车：
       copy(document.cookie)
4. 新建 cookies.txt，Ctrl+V 粘贴，保存
5. 运行本脚本：python scraper.py

Cookie 有效期约 7 天，过期后重复第 1-4 步即可。
───────────────────────────────────────────────────────────────────────────────
"""

import csv
import json
import logging
import os
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
REQUEST_DELAY   = 1.5     # 翻页间隔（秒），不要调太小

OUTPUT_JSON = "videos.json"
OUTPUT_CSV  = "videos.csv"
CSV_FIELDS  = [
    "index", "video_id", "title", "description",
    "views", "likes", "comments", "shares", "favorites",
    "create_time", "video_url",
]

API_URL = "https://channels.weixin.qq.com/cgi-bin/mmfindertrip/finder/profile/getpage"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer":         "https://channels.weixin.qq.com/",
    "Origin":          "https://channels.weixin.qq.com",
    "Content-Type":    "application/json",
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# ── Cookie 加载 ────────────────────────────────────────────────────────────────

def _parse_cookie_str(raw: str) -> dict[str, str]:
    """把 'k1=v1; k2=v2' 解析为字典。"""
    result: dict[str, str] = {}
    for part in raw.split(";"):
        part = part.strip()
        if "=" in part:
            k, _, v = part.partition("=")
            result[k.strip()] = unquote(v.strip())
    return result


def load_cookies() -> dict[str, str]:
    cookie_file = Path("cookies.txt")
    if not cookie_file.exists():
        logger.error(
            "\n找不到 cookies.txt，请按以下步骤操作：\n"
            "  1. 用 Chrome 打开 https://channels.weixin.qq.com/web/pages/home\n"
            "  2. 手机微信扫码登录\n"
            "  3. F12 → Console → 输入 copy(document.cookie) 回车\n"
            "  4. 新建 cookies.txt，粘贴内容，保存\n"
            "  5. 重新运行 python scraper.py\n"
        )
        raise SystemExit(1)

    raw = cookie_file.read_text(encoding="utf-8").strip()
    if not raw:
        logger.error("cookies.txt 是空的，请重新按上述步骤获取 Cookie。")
        raise SystemExit(1)

    cookies = _parse_cookie_str(raw)
    auth = [k for k in cookies if k in ("uin", "wxuin", "skey", "webex_data", "mmid")]
    if not auth:
        logger.warning(
            "cookies.txt 中未找到微信登录态 Cookie（uin/skey 等）。\n"
            "请确认已在 channels.weixin.qq.com 登录后再复制 Cookie。\n"
            "当前读取到的键：%s", list(cookies.keys())
        )
    else:
        logger.info("Cookie 加载成功，登录态键：%s", auth)

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

# ── 采集 ────────────────────────────────────────────────────────────────────────

def fetch_page(session: requests.Session, last_buffer: str) -> tuple[list, str, bool]:
    payload = {
        "finderUsername": FINDER_USERNAME,
        "count":          PAGE_SIZE,
        "lastBuffer":     last_buffer,
    }
    resp = session.post(API_URL, json=payload, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    body = resp.json()

    ret = body.get("base_resp", {}).get("ret", -1)
    if ret != 0:
        err = body.get("base_resp", {}).get("err_msg", "")
        # ret=200013 表示登录态失效
        if ret == 200013:
            raise RuntimeError(
                f"Cookie 已过期（ret={ret}）。\n"
                "  请重新在 Chrome 登录 channels.weixin.qq.com，\n"
                "  再执行 copy(document.cookie) 更新 cookies.txt。"
            )
        raise RuntimeError(f"API 错误 ret={ret}: {err}  响应：{body}")

    data     = body.get("data", {})
    objects  = data.get("object", [])
    next_buf = data.get("continuationBuffer", "")
    has_more = bool(data.get("hasMore", next_buf))
    return objects, next_buf, has_more


def scrape() -> list[dict]:
    cookies = load_cookies()
    session = requests.Session()
    session.cookies.update(cookies)

    videos: list[dict] = []
    last_buffer = ""

    logger.info("开始采集视频号 %s（目标 %d 条）…", FINDER_USERNAME, TARGET_COUNT)

    while len(videos) < TARGET_COUNT:
        try:
            objects, last_buffer, has_more = fetch_page(session, last_buffer)
        except RuntimeError as e:
            logger.error("%s", e)
            break
        except requests.HTTPError as e:
            logger.error("HTTP 错误：%s", e)
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


if __name__ == "__main__":
    results = scrape()
    if results:
        save(results)
        logger.info("完成，共采集 %d 条。", len(results))
    else:
        logger.warning("未采集到任何数据。")
