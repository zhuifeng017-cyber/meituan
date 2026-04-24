"""
Charles HAR 解析器 — 从抓包结果提取视频号数据
Channel : sphflCd4Tm6wLvR

步骤：
  1. 微信PC 打开视频号，慢慢向下滚动直到加载 200 条左右
  2. Charles → File → Export Session → HTTP Archive (.har) → 存为 channels.har
  3. python har_parser.py

无需任何 Cookie / 登录，直接解析 Charles 捕获的响应体。
"""

import csv
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

HAR_FILE    = "channels.har"
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


def _try_parse_body(text: str) -> list[dict]:
    """尝试从一条 HTTP 响应体里解析出视频对象列表。"""
    try:
        data = json.loads(text)
    except Exception:
        return []

    # 兼容两种结构：
    # 1. channels.weixin.qq.com cgi-bin API:  data.object[]
    # 2. 其他结构：直接在顶层 object[]
    candidates = [
        data.get("data", {}).get("object", []),
        data.get("object", []),
    ]
    for objs in candidates:
        if isinstance(objs, list) and objs:
            # 粗略验证：有 objectId 字段才算视频对象
            if any("objectId" in o for o in objs):
                return objs
    return []


# ── 主逻辑 ─────────────────────────────────────────────────────────────────────

def parse_har(har_path: str) -> list[dict]:
    p = Path(har_path)
    if not p.exists():
        logger.error(
            "找不到 %s\n"
            "请在 Charles 中：File → Export Session → HTTP Archive (.har)\n"
            "保存文件名为 channels.har，和本脚本放同一目录。",
            har_path,
        )
        sys.exit(1)

    logger.info("正在读取 %s …", har_path)
    with open(p, encoding="utf-8", errors="replace") as f:
        har = json.load(f)

    entries = har.get("log", {}).get("entries", [])
    logger.info("HAR 共包含 %d 条请求记录", len(entries))

    seen_ids: set[str] = set()
    videos: list[dict] = []

    for entry in entries:
        url     = entry.get("request", {}).get("url", "")
        content = entry.get("response", {}).get("content", {})
        text    = content.get("text", "")

        if not text:
            continue

        # 只关心 channels.weixin.qq.com 的 JSON 响应
        if "weixin.qq.com" not in url and "channels" not in url:
            continue

        objs = _try_parse_body(text)
        for obj in objs:
            vid = obj.get("objectId", "")
            if vid and vid not in seen_ids:
                seen_ids.add(vid)
                videos.append(_parse_video(obj, len(videos) + 1))

    return videos


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
    har_path = sys.argv[1] if len(sys.argv) > 1 else HAR_FILE
    videos   = parse_har(har_path)

    if not videos:
        logger.warning(
            "未解析到任何视频数据。\n"
            "  请确认：\n"
            "  1. 在微信PC里打开了目标视频号并向下滚动加载了视频\n"
            "  2. Charles 的 SSL Proxying 已对 *.weixin.qq.com 启用\n"
            "  3. 导出的是 HTTP Archive (.har) 格式而不是 .chls\n"
            "  4. HAR 文件放在与本脚本同一目录"
        )
    else:
        save(videos)
        logger.info("完成，共解析到 %d 条视频数据。", len(videos))
