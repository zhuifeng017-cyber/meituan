"""
WeChat Video Channel (视频号) Scraper
Target: sphflCd4Tm6wLvR
Fields: title, description, views, likes, comments, shares, favorites

Usage:
    1. Open WeChat channels in browser: https://channels.weixin.qq.com
    2. Log in and copy your cookies from DevTools → Application → Cookies
    3. Fill in COOKIES dict below
    4. Run: python scraper.py
"""

import csv
import json
import time
import logging
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────────

FINDER_USERNAME = "sphflCd4Tm6wLvR"
TARGET_COUNT = 200
PAGE_SIZE = 10          # items per request (API max is 10)
REQUEST_DELAY = 1.5     # seconds between requests to avoid rate limiting

# Paste your cookies from Charles / browser DevTools here.
# Required keys (at minimum): uin, skey, wxuin, pass_ticket, webex_data or similar.
COOKIES: dict[str, str] = {
    # Example — replace with your actual values:
    # "uin": "o1234567890",
    # "skey": "@xxxxxxxx",
    # "wxuin": "1234567890",
    # "pass_ticket": "xxxx...",
    # "webex_data": "xxxx...",
}

# ── Constants ──────────────────────────────────────────────────────────────────

API_URL = "https://channels.weixin.qq.com/cgi-bin/mmfindertrip/finder/profile/getpage"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://channels.weixin.qq.com/",
    "Origin": "https://channels.weixin.qq.com",
    "Content-Type": "application/json",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

OUTPUT_JSON = "videos.json"
OUTPUT_CSV = "videos.csv"
CSV_FIELDS = ["index", "video_id", "title", "description", "views", "likes",
              "comments", "shares", "favorites", "create_time", "video_url"]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _parse_video(obj: dict, index: int) -> dict:
    desc = obj.get("objectDesc", {})
    media_list = desc.get("media", [{}])
    media = media_list[0] if media_list else {}

    return {
        "index": index,
        "video_id": obj.get("objectId", ""),
        "title": media.get("title") or desc.get("title", ""),
        "description": desc.get("description", ""),
        "views": obj.get("readCount", obj.get("viewCount", 0)),
        "likes": obj.get("likeCount", 0),
        "comments": obj.get("commentCount", 0),
        "shares": obj.get("forwardCount", 0),
        "favorites": obj.get("favCount", 0),
        "create_time": obj.get("createTime", 0),
        "video_url": media.get("url", ""),
    }


def fetch_page(session: requests.Session, last_buffer: str) -> tuple[list[dict], str, bool]:
    """Return (items, next_buffer, has_more)."""
    payload = {
        "finderUsername": FINDER_USERNAME,
        "count": PAGE_SIZE,
        "lastBuffer": last_buffer,
    }
    resp = session.post(API_URL, json=payload, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    body = resp.json()

    ret_code = body.get("base_resp", {}).get("ret", -1)
    if ret_code != 0:
        raise RuntimeError(f"API error ret={ret_code}: {body.get('base_resp', {}).get('err_msg', '')}")

    data = body.get("data", {})
    objects = data.get("object", [])
    next_buf = data.get("continuationBuffer", "")
    has_more = bool(data.get("hasMore", next_buf))

    return objects, next_buf, has_more


# ── Main ───────────────────────────────────────────────────────────────────────

def scrape() -> list[dict]:
    if not COOKIES:
        logger.error(
            "COOKIES is empty. "
            "Please fill in your WeChat session cookies in scraper.py and re-run."
        )
        raise SystemExit(1)

    session = requests.Session()
    session.cookies.update(COOKIES)

    videos: list[dict] = []
    last_buffer = ""

    logger.info("Starting scrape of channel %s (target: %d videos)", FINDER_USERNAME, TARGET_COUNT)

    while len(videos) < TARGET_COUNT:
        try:
            objects, last_buffer, has_more = fetch_page(session, last_buffer)
        except requests.HTTPError as e:
            logger.error("HTTP error: %s", e)
            break
        except RuntimeError as e:
            logger.error("API error: %s", e)
            break

        if not objects:
            logger.info("No more videos returned.")
            break

        for obj in objects:
            if len(videos) >= TARGET_COUNT:
                break
            videos.append(_parse_video(obj, len(videos) + 1))

        logger.info("Fetched %d / %d videos", len(videos), TARGET_COUNT)

        if not has_more:
            logger.info("Reached end of channel (no more pages).")
            break

        time.sleep(REQUEST_DELAY)

    return videos


def save(videos: list[dict]) -> None:
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(videos, f, ensure_ascii=False, indent=2)
    logger.info("Saved JSON → %s", OUTPUT_JSON)

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(videos)
    logger.info("Saved CSV  → %s", OUTPUT_CSV)


if __name__ == "__main__":
    results = scrape()
    if results:
        save(results)
        logger.info("Done. Total videos collected: %d", len(results))
    else:
        logger.warning("No data collected.")
