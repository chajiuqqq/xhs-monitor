"""
主流程编排 - 搜索 -> 去重 -> 解析 -> (LLM 过滤) -> 输出

用法:
  python pipeline.py '["AI Agent 招聘"]'                       # 老路径：仅 keywords
  python pipeline.py '[]' --tasks-json 'tasks.json'           # 新路径：含 LLM 任务组
  python pipeline.py '["kw1"]' --no-llm                       # 强制跳过 LLM 过滤

输出:
  output/latest_result.json  (供 push_feishu 推送)
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

import llm
from config import LLM_ENABLED, MAX_RESULTS_DEFAULT, OUTPUT_DIR
from content_parser import parse_note_from_detail
from dedup import (
    filter_new_notes,
    init_db,
    is_content_duplicate,
    mark_seen,
)
from searcher import search_and_parse


# ── 单帖处理（关键词级 / 任务级共用） ────────────────────

def _build_push_note(note: dict, keyword_label: str) -> dict:
    """从解析后的 note 字典构造推送结构。"""
    return {
        "note_id": note["note_id"],
        "keyword": keyword_label,
        "title": note.get("title", ""),
        "desc": note.get("desc", ""),
        "tags": note.get("tags", []),
        "author": note.get("author", ""),
        "likes": note.get("likes", 0),
        "url": note.get("url", ""),
        "type": note.get("type", "image"),
        "image_count": len(note.get("image_urls", [])),
        "image_urls": note.get("image_urls", []),
        "llm_match_reason": note.get("llm_match_reason", ""),
    }


def _process_notes(
    notes: list[dict],
    keyword_label: str,
    *,
    use_llm_filter: bool,
    llm_criteria: str,
    use_llm: bool = True,
) -> tuple[list[dict], dict]:
    """
    处理一批新帖子：解析 + 内容去重 + (LLM 过滤) + 标记 seen。

    Args:
        notes: filter_new_notes() 的输出
        keyword_label: 写入 DB 的 keyword 字段（keywords 路径用 kw 本身，tasks 路径用 task.name）
        use_llm_filter: 是否启用 LLM 二次过滤
        llm_criteria: 过滤条件（仅 use_llm_filter=True 时有效）
        use_llm: 全局开关（pipeline --no-llm 时为 False）

    Returns:
        (推送用 notes 列表, 统计 dict)
    """
    push_notes: list[dict] = []
    stat = {"searched": len(notes), "new": 0, "dup_content": 0, "filtered_by_llm": 0}

    # 1) 解析 + 内容指纹去重（状态直接落库）
    parsed: list[dict] = []
    for note in notes:
        try:
            detail = parse_note_from_detail(note["note_id"], note.get("detail", {}))
            note.update(detail)

            if is_content_duplicate(note.get("desc", "")):
                stat["dup_content"] += 1
                mark_seen(
                    note_id=note["note_id"],
                    keyword=keyword_label,
                    title=note.get("title", ""),
                    author=note.get("author", ""),
                    likes=note.get("likes", 0),
                    content=note.get("desc", ""),
                    status="dup_content",
                )
                continue

            # 占位标记：等 LLM 决策后再改 status
            mark_seen(
                note_id=note["note_id"],
                keyword=keyword_label,
                title=note.get("title", ""),
                author=note.get("author", ""),
                likes=note.get("likes", 0),
                content=note.get("desc", ""),
                status="new_llm_pending" if use_llm_filter else "new",
            )
            parsed.append(note)
        except Exception as e:
            print(f"  [错误] 解析失败 {note['note_id']}: {e}")
            mark_seen(note_id=note["note_id"], keyword=keyword_label, status="parse_error")

    # 2) LLM 过滤
    if use_llm_filter and parsed:
        matched = llm.filter_posts(parsed, llm_criteria, use_llm=use_llm)
        matched_ids = {p["note_id"] for p in matched}
        for note in parsed:
            nid = note["note_id"]
            if nid in matched_ids:
                # 升级状态
                mark_seen(
                    note_id=nid,
                    keyword=keyword_label,
                    title=note.get("title", ""),
                    author=note.get("author", ""),
                    likes=note.get("likes", 0),
                    content=note.get("desc", ""),
                    status="new",
                )
                push_notes.append(_build_push_note(note, keyword_label))
                stat["new"] += 1
            else:
                # LLM 判定不匹配
                mark_seen(
                    note_id=nid,
                    keyword=keyword_label,
                    title=note.get("title", ""),
                    author=note.get("author", ""),
                    likes=note.get("likes", 0),
                    content=note.get("desc", ""),
                    status="filtered_by_llm",
                )
                stat["filtered_by_llm"] += 1
    else:
        # 不走 LLM：所有 parsed 全部推送
        for note in parsed:
            push_notes.append(_build_push_note(note, keyword_label))
            stat["new"] += 1

    return push_notes, stat


# ── 顶层 pipeline 入口 ───────────────────────────────────

def run_pipeline(
    keywords: list[str] | None = None,
    tasks: list[dict] | None = None,
    max_results: int = MAX_RESULTS_DEFAULT,
    use_llm: bool = True,
) -> dict:
    """
    执行完整监控 pipeline（关键词路径 + 任务组路径并行）。

    Args:
        keywords: 旧版关键词列表
        tasks: 新版任务组列表，每项 {name, description, keywords, filter}
        max_results: 每个搜索词最大抓取数
        use_llm: 是否启用 LLM 过滤（pipeline --no-llm 时为 False）

    Returns:
        {
            "new_notes": [...],
            "stats": {"total_searched", "total_new", "total_dup_content",
                      "total_filtered_by_llm", "by_keyword", "by_task"},
            "timestamp": "...",
        }
    """
    keywords = keywords or []
    tasks = tasks or []

    init_db()

    all_new_notes: list[dict] = []
    stats = {
        "total_searched": 0,
        "total_new": 0,
        "total_dup_content": 0,
        "total_filtered_by_llm": 0,
        "by_keyword": {},
        "by_task": {},
    }

    # ── 路径 A：旧版 keywords（无 LLM 过滤） ──
    if keywords:
        print(f"\n[路径A] 关键词模式: {keywords}")
        search_results = search_and_parse(keywords, max_results)
        for keyword, notes in search_results.items():
            print(f"  [搜索] {keyword} 命中 {len(notes)} 条")
            new_notes = filter_new_notes(notes, keyword)
            push_notes, kw_stat = _process_notes(
                new_notes,
                keyword,
                use_llm_filter=False,
                llm_criteria="",
                use_llm=use_llm,
            )
            all_new_notes.extend(push_notes)
            stats["total_searched"] += kw_stat["searched"]
            stats["total_new"] += kw_stat["new"]
            stats["total_dup_content"] += kw_stat["dup_content"]
            stats["by_keyword"][keyword] = kw_stat

    # ── 路径 B：新版 tasks（带 LLM 过滤） ──
    if tasks:
        if not LLM_ENABLED or not use_llm:
            print(f"\n[路径B] 任务组模式: {len(tasks)} 个任务 (LLM 关闭，仅按关键词去重)")

        for task in tasks:
            task_name = task.get("name", "").strip()
            task_kws = task.get("keywords", []) or []
            task_filter = (task.get("filter") or "").strip()
            if not task_name or not task_kws:
                print(f"  [跳过] 任务缺少 name 或 keywords: {task}")
                continue

            use_filter = bool(task_filter) and LLM_ENABLED and use_llm
            print(
                f"\n[路径B] 任务 [{task_name}] {len(task_kws)} 关键词 | "
                f"LLM 过滤: {'是' if use_filter else '否'}"
            )
            search_results = search_and_parse(task_kws, max_results)
            # 把同一 task 下的多个 kw 搜索结果合并去重
            merged: dict[str, dict] = {}  # note_id -> note
            for kw, notes in search_results.items():
                for n in notes:
                    nid = n.get("note_id")
                    if nid and nid not in merged:
                        merged[nid] = n
            print(f"  [搜索] 合并去重后 {len(merged)} 条")

            new_notes = filter_new_notes(list(merged.values()), task_name)
            push_notes, task_stat = _process_notes(
                new_notes,
                task_name,
                use_llm_filter=use_filter,
                llm_criteria=task_filter,
                use_llm=use_llm,
            )
            all_new_notes.extend(push_notes)
            stats["total_searched"] += task_stat["searched"]
            stats["total_new"] += task_stat["new"]
            stats["total_dup_content"] += task_stat["dup_content"]
            stats["total_filtered_by_llm"] += task_stat["filtered_by_llm"]
            stats["by_task"][task_name] = task_stat

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
        nargs="?",
        default="[]",
        help='关键词 JSON 数组（老路径），如 \'["AI Agent 招聘"]\'；可传 []',
    )
    parser.add_argument(
        "--max",
        type=int,
        default=MAX_RESULTS_DEFAULT,
        help=f"每个关键词最大抓取数（默认 {MAX_RESULTS_DEFAULT}）",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="强制跳过 LLM 过滤（仅走关键词命中 + 内容去重）",
    )
    parser.add_argument(
        "--tasks-json",
        type=str,
        default="",
        help="任务组 JSON 文件路径（与 keywords 并行执行）",
    )
    args = parser.parse_args()

    keywords = json.loads(args.keywords) if args.keywords else []
    tasks = []
    if args.tasks_json:
        tasks_path = Path(args.tasks_json)
        if tasks_path.exists():
            tasks_data = json.loads(tasks_path.read_text(encoding="utf-8"))
            tasks = tasks_data.get("tasks", []) if isinstance(tasks_data, dict) else tasks_data
        else:
            print(f"[警告] 任务组文件不存在: {tasks_path}")

    print(f"[启动] 关键词: {keywords}")
    print(f"[启动] 任务组: {[t.get('name', '?') for t in tasks]}")
    print(f"[配置] 每词最大: {args.max}")
    print(f"[配置] LLM 过滤: {'关闭' if args.no_llm else '开启' if LLM_ENABLED else '未配置'}")

    result = run_pipeline(
        keywords=keywords,
        tasks=tasks,
        max_results=args.max,
        use_llm=not args.no_llm,
    )

    print(f"\n{'='*50}")
    print(f"[完成] 搜索: {result['stats']['total_searched']} 篇")
    print(f"[完成] 新增: {result['stats']['total_new']} 篇")
    print(f"[完成] 内容重复: {result['stats']['total_dup_content']} 篇")
    if result['stats']['total_filtered_by_llm']:
        print(f"[完成] LLM 过滤: {result['stats']['total_filtered_by_llm']} 篇")
    print(f"[输出] {OUTPUT_DIR / 'latest_result.json'}")


if __name__ == "__main__":
    main()
