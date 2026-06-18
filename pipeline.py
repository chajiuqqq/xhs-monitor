"""
主流程编排 - 搜索 -> 去重 -> 解析 -> 输出

用法:
  python pipeline.py '["AI Agent 招聘", "AI Agent 工程师"]'
  python pipeline.py '["关键词"]' --max 15

输出:
  output/latest_result.json  (供 WorkBuddy automation 读取并推送)
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

# Windows 控制台 UTF-8（避免 emoji 输出报错）
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# 确保同目录模块可导入
sys.path.insert(0, str(Path(__file__).parent))

from config import MAX_RESULTS_DEFAULT, OUTPUT_DIR
from content_parser import parse_note_from_detail
from dedup import (
    filter_new_notes,
    init_db,
    is_content_duplicate,
    mark_seen,
)
from searcher import search_and_parse


def run_pipeline(
    keywords: list[str],
    max_results: int = MAX_RESULTS_DEFAULT,
) -> dict:
    """
    执行完整监控 pipeline。

    Returns:
        {
            "new_notes": [...],     # 新增命中帖子（含详情）
            "stats": {...},         # 统计信息
            "timestamp": "..."      # 执行时间
        }
    """
    init_db()

    all_new_notes: list[dict] = []
    stats = {
        "total_searched": 0,
        "total_new": 0,
        "total_dup_content": 0,
        "by_keyword": {},
    }

    # ① + ② 搜索 + ④ 详情解析（同一浏览器实例一体化完成）
    search_results = search_and_parse(keywords, max_results)

    for keyword, notes in search_results.items():
        kw_stat = {"searched": len(notes), "new": 0, "dup_content": 0}
        stats["total_searched"] += len(notes)

        # ③ 去重过滤
        new_notes = filter_new_notes(notes, keyword)

        # ④ 从 SSR 结构化数据中整理内容
        for note in new_notes:
            try:
                detail = parse_note_from_detail(note["note_id"], note.get("detail", {}))
                note.update(detail)

                # 内容指纹去重
                if is_content_duplicate(note.get("desc", "")):
                    kw_stat["dup_content"] += 1
                    stats["total_dup_content"] += 1
                    mark_seen(
                        note_id=note["note_id"],
                        keyword=keyword,
                        title=note.get("title", ""),
                        author=note.get("author", ""),
                        likes=note.get("likes", 0),
                        content=note.get("desc", ""),
                        status="dup_content",
                    )
                    continue

                mark_seen(
                    note_id=note["note_id"],
                    keyword=keyword,
                    title=note.get("title", ""),
                    author=note.get("author", ""),
                    likes=note.get("likes", 0),
                    content=note.get("desc", ""),
                    status="new",
                )

                # 构建推送用的精简结构
                all_new_notes.append({
                    "note_id": note["note_id"],
                    "keyword": keyword,
                    "title": note.get("title", ""),
                    "desc": note.get("desc", ""),
                    "tags": note.get("tags", []),
                    "author": note.get("author", ""),
                    "likes": note.get("likes", 0),
                    "url": note.get("url", ""),
                    "type": note.get("type", "image"),
                    "image_count": len(note.get("image_urls", [])),
                    "image_urls": note.get("image_urls", []),
                })
                kw_stat["new"] += 1

            except Exception as e:
                print(f"  [错误] 解析失败 {note['note_id']}: {e}")
                mark_seen(note_id=note["note_id"], keyword=keyword, status="parse_error")

        stats["by_keyword"][keyword] = kw_stat
        stats["total_new"] += kw_stat["new"]

    result = {
        "new_notes": all_new_notes,
        "stats": stats,
        "timestamp": datetime.now().isoformat(),
    }

    # 写入输出文件
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / "latest_result.json"
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return result


def main():
    parser = argparse.ArgumentParser(description="小红书帖子监控 pipeline")
    parser.add_argument(
        "keywords",
        type=str,
        help='关键词 JSON 数组，如 \'["AI Agent 招聘"]\'',
    )
    parser.add_argument(
        "--max",
        type=int,
        default=MAX_RESULTS_DEFAULT,
        help=f"每个关键词最大抓取数（默认 {MAX_RESULTS_DEFAULT}）",
    )
    args = parser.parse_args()

    keywords = json.loads(args.keywords)
    print(f"[启动] 监控关键词: {keywords}")
    print(f"[配置] 每词最大抓取: {args.max}")

    result = run_pipeline(keywords, args.max)

    print(f"\n{'='*50}")
    print(f"[完成] 搜索: {result['stats']['total_searched']} 篇")
    print(f"[完成] 新增: {result['stats']['total_new']} 篇")
    print(f"[完成] 内容重复: {result['stats']['total_dup_content']} 篇")
    print(f"[输出] {OUTPUT_DIR / 'latest_result.json'}")


if __name__ == "__main__":
    main()
