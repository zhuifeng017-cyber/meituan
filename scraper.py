"""
WeChat Video Channel (视频号) Scraper
Channel: sphflCd4Tm6wLvR
Fields : title, description, views, likes, comments, shares, favorites

─── 获取 Cookie 的方法 ────────────────────────────────────────────────────────
方法 A（推荐）：Cookie 字符串文件
  1. Charles 左侧点 channels.weixin.qq.com → 展开任意请求
  2. 右侧 Headers 标签 → 找到 Cookie 行 → 右键复制整行值
  3. 新建 cookies.txt，把复制的内容粘贴进去保存

方法 B：Charles 导出 HAR
  File → Export Session → 选 HTTP Archive (.har) → 保存为 channels.har

方法 C：直接写在脚本里（COOKIES_RAW 变量）

─── 运行 ─────────────────────────────────────────────────────────────────────
  pip install requests
  python scraper.py
"""

import csv
import json
import logging
import os
import time
from http.cookiejar import CookieJar
from urllib.parse import unquote

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── 配置区 ─────────────────────────────────────────────────────────────────────

FINDER_USERNAME = "sphflCd4Tm6wLvR"
TARGET_COUNT    = 200
PAGE_SIZE       = 10     # 每次请求条数（接口上限 10）
REQUEST_DELAY   = 1.5    # 请求间隔秒数

# 方法 C：直接粘贴完整 Cookie 字符串（从 Charles 复制）
# 例如: COOKIES_RAW = "uin=o123456; skey=@abc; wxuin=123456; ..."
COOKIES_RAW: str = ""

# ── 常量 ──────────────────────────────────────────────────────────────────────

API_URL = "https://channels.weixin.qq.com/cgi-bin/mmfindertrip/finder/profile/getpage"

HEADERS = {
    # 与 Charles 抓包一致的 UA（Windows WeChat 客户端内置浏览器）
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/129.0.6668.101 Safari/537.36 "
        "Language/zh ColorScheme/Light wxwork/5.0.7 (MicroMessenger/6.2) "
        "WindowsWechat"
    ),
    "Referer":        "https://channels.weixin.qq.com/",
    "Origin":         "https://channels.weixin.qq.com",
    "Content-Type":   "application/json",
    "Accept":         "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "sec-ch-ua":       '"Chromium";v="129", "Not=A?Brand";v="8"',
    "sec-ch-ua-mobile":   "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Site":  "same-origin",
    "Sec-Fetch-Mode":  "cors",
    "Sec-Fetch-Dest":  "empty",
}

OUTPUT_JSON = "videos.json"
OUTPUT_CSV  = "videos.csv"
CSV_FIELDS  = [
    "index", "video_id", "title", "description",
    "views", "likes", "comments", "shares", "favorites",
    "create_time", "video_url",
]


# ── Cookie 加载 ────────────────────────────────────────────────────────────────

def _parse_cookie_string(raw: str) -> dict[str, str]:
    """把 'k1=v1; k2=v2' 解析为字典。"""
    cookies: dict[str, str] = {}
    for part in raw.split(";"):
        part = part.strip()
        if "=" in part:
            k, _, v = part.partition("=")
            cookies[k.strip()] = unquote(v.strip())
    return cookies


def _load_from_txt(path: str) -> dict[str, str]:
    with open(path, encoding="utf-8") as f:
        return _parse_cookie_string(f.read().strip())


def _load_from_har(path: str) -> dict[str, str]:
    """从 Charles 导出的 HAR 文件中提取 channels.weixin.qq.com 的 Cookie。"""
    with open(path, encoding="utf-8") as f:
        har = json.load(f)
    entries = har.get("log", {}).get("entries", [])
    for entry in entries:
        url = entry.get("request", {}).get("url", "")
        if "channels.weixin.qq.com" not in url:
            continue
        headers = entry.get("request", {}).get("headers", [])
        for h in headers:
            if h.get("name", "").lower() == "cookie":
                cookies = _parse_cookie_string(h["value"])
                if cookies:
                    logger.info("从 HAR 文件提取到 %d 个 Cookie", len(cookies))
                    return cookies
    return {}


def load_cookies() -> dict[str, str]:
    # 优先级: cookies.txt > channels.har > COOKIES_RAW
    if os.path.exists("cookies.txt"):
        cookies = _load_from_txt("cookies.txt")
        logger.info("从 cookies.txt 加载了 %d 个 Cookie", len(cookies))
        return cookies

    if os.path.exists("channels.har"):
        cookies = _load_from_har("channels.har")
        if cookies:
            return cookies
        logger.warning("channels.har 中未找到 channels.weixin.qq.com 的 Cookie")

    if COOKIES_RAW.strip():
        cookies = _parse_cookie_string(COOKIES_RAW)
        logger.info("从 COOKIES_RAW 加载了 %d 个 Cookie", len(cookies))
        return cookies

    return {}


# ── API 调用 ───────────────────────────────────────────────────────────────────

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


def fetch_page(session: requests.Session, last_buffer: str) -> tuple[list[dict], str, bool]:
    payload = {
        "finderUsername": FINDER_USERNAME,
        "count":          PAGE_SIZE,
        "lastBuffer":     last_buffer,
    }
    resp = session.post(API_URL, json=payload, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    body = resp.json()

    ret_code = body.get("base_resp", {}).get("ret", -1)
    if ret_code != 0:
        err = body.get("base_resp", {}).get("err_msg", "")
        raise RuntimeError(f"API ret={ret_code}: {err}")

    data     = body.get("data", {})
    objects  = data.get("object", [])
    next_buf = data.get("continuationBuffer", "")
    has_more = bool(data.get("hasMore", next_buf))
    return objects, next_buf, has_more


# ── 主流程 ─────────────────────────────────────────────────────────────────────

def scrape() -> list[dict]:
    cookies = load_cookies()
    if not cookies:
        logger.error(
            "\n未找到 Cookie，请选择以下任一方式提供：\n"
            "  A. 在 Charles 里找 channels.weixin.qq.com 的请求 → 复制 Cookie 值 → 存入 cookies.txt\n"
            "  B. Charles → File → Export Session → 保存为 channels.har\n"
            "  C. 直接把 Cookie 字符串粘贴到脚本中 COOKIES_RAW 变量\n"
        )
        raise SystemExit(1)

    session = requests.Session()
    session.cookies.update(cookies)

    videos: list[dict] = []
    last_buffer = ""

    logger.info("开始采集视频号 %s（目标 %d 条）", FINDER_USERNAME, TARGET_COUNT)

    while len(videos) < TARGET_COUNT:
        try:
            objects, last_buffer, has_more = fetch_page(session, last_buffer)
        except requests.HTTPError as e:
            logger.error("HTTP 错误: %s", e)
            break
        except RuntimeError as e:
            logger.error("接口错误: %s", e)
            break

        if not objects:
            logger.info("接口返回空列表，采集结束。")
            break

        for obj in objects:
            if len(videos) >= TARGET_COUNT:
                break
            videos.append(_parse_video(obj, len(videos) + 1))

        logger.info("已采集 %d / %d 条", len(videos), TARGET_COUNT)

        if not has_more:
            logger.info("已到达最后一页。")
            break

        time.sleep(REQUEST_DELAY)

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
    results = scrape()
    if results:
        save(results)
        logger.info("完成，共采集 %d 条视频数据。", len(results))
    else:
        logger.warning("未采集到任何数据。")
