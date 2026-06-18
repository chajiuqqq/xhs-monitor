"""
帖子内容解析模块

从 searcher.py 通过 Playwright 提取的 SSR 结构化数据中整理内容。
不再使用正则解析 HTML，而是直接读取 window.__INITIAL_STATE__ 中的
noteDetailMap 字段，精确可靠。
"""

import re


def _clean(s: str) -> str:
    """清理转义字符。"""
    if not s:
        return ""
    s = s.replace("\\u002F", "/").replace("\\/", "/")
    s = s.replace("\\n", "\n").replace('\\"', '"')
    return s.strip()


def extract_tags(desc: str) -> list[str]:
    """从正文中提取话题标签。"""
    if not desc:
        return []
    tags = re.findall(r"#([^#\n]+?)#", desc)
    return [t.strip() for t in tags if t.strip()]


def parse_note_from_detail(note_id: str, detail: dict) -> dict:
    """
    从 Playwright 提取的 SSR 结构化数据中整理帖子内容。

    Args:
        note_id: 笔记ID
        detail: _fetch_detail() 返回的结构化字典，包含
                title, desc, type, user, imageList, video, interactInfo

    Returns:
        {
            "note_id", "title", "desc", "tags",
            "image_urls", "video_url", "type"
        }
    """
    title = _clean(detail.get("title", ""))
    desc = _clean(detail.get("desc", ""))
    tags = extract_tags(desc)

    # 图片 URL
    image_urls = [_clean(u) for u in detail.get("imageList", []) if u]

    # 视频 URL（从 stream.h264[0].masterUrl 提取）
    video_url = None
    video_data = detail.get("video")
    if video_data and video_data.get("media"):
        stream = video_data["media"].get("stream", {})
        for codec in ("h264", "h265", "h266", "av1"):
            streams = stream.get(codec, [])
            if streams and streams[0].get("masterUrl"):
                video_url = _clean(streams[0]["masterUrl"])
                break

    # 小红书 type: "normal"=图文帖, "video"=视频帖
    raw_type = detail.get("type", "normal")
    note_type = "video" if video_url or raw_type == "video" else "image"

    return {
        "note_id": note_id,
        "title": title,
        "desc": desc,
        "tags": tags,
        "image_urls": image_urls,
        "video_url": video_url,
        "type": note_type,
    }
