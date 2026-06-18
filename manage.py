"""
小红书监控任务管理器

用法:
  python manage.py list                          # 查看所有监控任务
  python manage.py add "AI Agent 招聘"           # 添加关键词
  python manage.py add "AI Agent 招聘" "AI 求职" # 批量添加
  python manage.py remove "AI Agent 招聘"        # 删除关键词
  python manage.py run                           # 立即执行所有任务并推送
  python manage.py run --keyword "AI Agent"      # 只运行指定关键词
  python manage.py status                        # 查看最近运行统计
  python manage.py history [--limit 20]          # 查看已抓取帖子列表
"""

import argparse
import json
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Windows UTF-8
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).parent))

from config import DB_PATH, MAX_RESULTS_DEFAULT, OUTPUT_DIR, PROJECT_ROOT

# 监控任务存储文件（JSON，简单持久化）
TASKS_FILE = PROJECT_ROOT / "tasks.json"

# Python 解释器路径（跨平台）
if sys.platform == "win32":
    PYTHON = r"C:\Users\chaji\.workbuddy\binaries\python\envs\xhs_monitor\Scripts\python.exe"
else:
    _VENV_PYTHON = str(PROJECT_ROOT / "venv" / "bin" / "python")
    PYTHON = _VENV_PYTHON if Path(_VENV_PYTHON).exists() else sys.executable


# ──────────────────────────────────────────────────────
# 任务持久化
# ──────────────────────────────────────────────────────

def load_tasks() -> dict:
    """读取任务配置文件。"""
    if TASKS_FILE.exists():
        return json.loads(TASKS_FILE.read_text(encoding="utf-8"))
    return {"keywords": [], "max_per_keyword": MAX_RESULTS_DEFAULT, "push_user_id": ""}


def save_tasks(tasks: dict) -> None:
    """写入任务配置文件。"""
    TASKS_FILE.write_text(
        json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ──────────────────────────────────────────────────────
# 命令实现
# ──────────────────────────────────────────────────────

def cmd_list(args):
    """列出所有监控关键词。"""
    tasks = load_tasks()
    keywords = tasks.get("keywords", [])

    print("=" * 50)
    print("  小红书帖子监控 - 任务列表")
    print("=" * 50)

    if not keywords:
        print("  (无监控任务，用 'manage.py add <关键词>' 添加)")
    else:
        for i, kw in enumerate(keywords, 1):
            print(f"  {i}. {kw}")

    print()
    print(f"  每关键词最大抓取数: {tasks.get('max_per_keyword', MAX_RESULTS_DEFAULT)}")
    push_target = tasks.get("push_user_id") or "默认私聊"
    print(f"  推送目标: {push_target}")

    # 显示数据库统计
    if DB_PATH.exists():
        conn = sqlite3.connect(DB_PATH)
        total = conn.execute("SELECT COUNT(*) FROM seen_notes").fetchone()[0]
        new_today = conn.execute(
            "SELECT COUNT(*) FROM seen_notes WHERE first_seen >= date('now')"
        ).fetchone()[0]
        last_run = conn.execute(
            "SELECT MAX(first_seen) FROM seen_notes"
        ).fetchone()[0]
        conn.close()
        print()
        print(f"  数据库: {total} 篇已记录 | 今日新增: {new_today} | 最后运行: {last_run}")
    else:
        print()
        print("  数据库: 未初始化（首次运行后自动创建）")

    print("=" * 50)


def cmd_add(args):
    """添加监控关键词。"""
    tasks = load_tasks()
    keywords = tasks.setdefault("keywords", [])
    added = []
    skipped = []
    for kw in args.keywords:
        kw = kw.strip()
        if not kw:
            continue
        if kw in keywords:
            skipped.append(kw)
        else:
            keywords.append(kw)
            added.append(kw)
    save_tasks(tasks)
    if added:
        print(f"[添加] 已加入监控: {', '.join(added)}")
    if skipped:
        print(f"[跳过] 已存在: {', '.join(skipped)}")
    print(f"[当前] 共 {len(keywords)} 个关键词")


def cmd_remove(args):
    """删除监控关键词。"""
    tasks = load_tasks()
    keywords = tasks.setdefault("keywords", [])
    removed = []
    not_found = []
    for kw in args.keywords:
        kw = kw.strip()
        if kw in keywords:
            keywords.remove(kw)
            removed.append(kw)
        else:
            not_found.append(kw)
    save_tasks(tasks)
    if removed:
        print(f"[删除] 已移除: {', '.join(removed)}")
    if not_found:
        print(f"[提示] 不存在: {', '.join(not_found)}")
    print(f"[当前] 剩余 {len(keywords)} 个关键词")


def cmd_config(args):
    """修改配置（最大抓取数、推送目标）。"""
    tasks = load_tasks()
    changed = []
    if args.max is not None:
        tasks["max_per_keyword"] = args.max
        changed.append(f"max_per_keyword={args.max}")
    if args.push_user is not None:
        tasks["push_user_id"] = args.push_user
        changed.append(f"push_user_id={args.push_user}")
    if args.push_chat is not None:
        tasks["push_chat_id"] = args.push_chat
        changed.append(f"push_chat_id={args.push_chat}")
    save_tasks(tasks)
    if changed:
        print(f"[配置] 已更新: {', '.join(changed)}")
    else:
        print("[配置] 用法: manage.py config --max 15 --push-user ou_xxx")


def cmd_run(args):
    """立即执行监控 pipeline 并推送结果。"""
    tasks = load_tasks()
    keywords = tasks.get("keywords", [])

    # 如果指定了 --keyword 参数，只运行该关键词
    if args.keyword:
        keywords = [args.keyword]

    if not keywords:
        print("[错误] 没有监控关键词，请先用 'manage.py add <关键词>' 添加")
        sys.exit(1)

    max_results = args.max or tasks.get("max_per_keyword", MAX_RESULTS_DEFAULT)

    print(f"[执行] 关键词: {keywords}")
    print(f"[执行] 每词最大: {max_results}")
    print()

    # 调用 pipeline
    pipeline_script = str(PROJECT_ROOT / "pipeline.py")
    kw_json = json.dumps(keywords, ensure_ascii=False)
    result = subprocess.run(
        [PYTHON, pipeline_script, kw_json, "--max", str(max_results)],
        capture_output=False,  # 直接输出到控制台
        env={**__import__("os").environ, "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"},
        cwd=str(PROJECT_ROOT),
        timeout=600,  # 10 分钟超时
    )

    if result.returncode != 0:
        print("[错误] pipeline 执行失败")
        return

    # 读取结果
    output_file = OUTPUT_DIR / "latest_result.json"
    if not output_file.exists():
        print("[错误] 输出文件不存在")
        return

    result_data = json.loads(output_file.read_text(encoding="utf-8"))
    new_count = result_data.get("stats", {}).get("total_new", 0)

    if new_count == 0 and not args.force_push:
        print("[推送] 无新增帖子，跳过推送（用 --force-push 强制推送）")
        return

    # 调用推送脚本
    push_script = str(PROJECT_ROOT / "push_feishu.py")
    push_cmd = [PYTHON, push_script]

    user_id = tasks.get("push_user_id", "")
    chat_id = tasks.get("push_chat_id", "")
    if chat_id:
        push_cmd += ["--chat-id", chat_id]
    elif user_id:
        push_cmd += ["--user-id", user_id]

    if args.dry_run:
        push_cmd += ["--dry-run"]

    print()
    print("[推送] 发送飞书通知...")
    subprocess.run(
        push_cmd,
        env={**__import__("os").environ, "PYTHONUTF8": "1"},
        cwd=str(PROJECT_ROOT),
    )


def cmd_status(args):
    """查看监控状态统计。"""
    if not DB_PATH.exists():
        print("[状态] 数据库不存在，尚未运行过监控")
        return

    conn = sqlite3.connect(DB_PATH)

    print("=" * 50)
    print("  小红书监控 - 运行状态")
    print("=" * 50)

    # 总体统计
    total = conn.execute("SELECT COUNT(*) FROM seen_notes").fetchone()[0]
    total_new = conn.execute(
        "SELECT COUNT(*) FROM seen_notes WHERE status='new'"
    ).fetchone()[0]
    total_dup = conn.execute(
        "SELECT COUNT(*) FROM seen_notes WHERE status='dup_content'"
    ).fetchone()[0]
    last_run = conn.execute("SELECT MAX(first_seen) FROM seen_notes").fetchone()[0]

    print(f"\n  总记录数: {total}")
    print(f"  有效新帖: {total_new}")
    print(f"  内容重复: {total_dup}")
    print(f"  最后运行: {last_run or '未知'}")

    # 按关键词统计
    print("\n  按关键词统计:")
    rows = conn.execute(
        "SELECT keyword, COUNT(*) as cnt, MAX(first_seen) as last "
        "FROM seen_notes GROUP BY keyword ORDER BY last DESC"
    ).fetchall()
    for kw, cnt, last in rows:
        print(f"    [{kw}] {cnt} 篇 | 最后: {last}")

    # 今日新增
    today_notes = conn.execute(
        "SELECT note_id, title, author, likes FROM seen_notes "
        "WHERE first_seen >= date('now') AND status='new' "
        "ORDER BY first_seen DESC LIMIT 5"
    ).fetchall()
    if today_notes:
        print(f"\n  今日新增（最多5条）:")
        for note_id, title, author, likes in today_notes:
            print(f"    - {title[:30]} | {author} | 赞{likes}")

    conn.close()
    print("=" * 50)


def cmd_history(args):
    """查看已抓取帖子历史。"""
    if not DB_PATH.exists():
        print("[历史] 数据库不存在")
        return

    conn = sqlite3.connect(DB_PATH)
    limit = args.limit or 20
    kw_filter = f"AND keyword='{args.keyword}'" if args.keyword else ""

    rows = conn.execute(
        f"SELECT keyword, note_id, title, author, likes, first_seen "
        f"FROM seen_notes WHERE status='new' {kw_filter} "
        f"ORDER BY first_seen DESC LIMIT {limit}"
    ).fetchall()
    conn.close()

    print(f"  最近 {len(rows)} 条帖子记录:")
    print("-" * 60)
    for kw, note_id, title, author, likes, ts in rows:
        xhs_url = f"https://www.xiaohongshu.com/explore/{note_id}"
        print(f"  [{kw}] {title[:30]}")
        print(f"         作者: {author} | 赞: {likes} | 时间: {ts}")
        print(f"         {xhs_url}")
        print()


# ──────────────────────────────────────────────────────
# CLI 入口
# ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="小红书帖子监控任务管理器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # list
    sub.add_parser("list", help="查看所有监控关键词和状态")

    # add
    p_add = sub.add_parser("add", help="添加监控关键词")
    p_add.add_argument("keywords", nargs="+", help="关键词（可多个）")

    # remove
    p_rm = sub.add_parser("remove", help="删除监控关键词")
    p_rm.add_argument("keywords", nargs="+", help="要删除的关键词")

    # config
    p_cfg = sub.add_parser("config", help="修改配置")
    p_cfg.add_argument("--max", type=int, help="每关键词最大抓取数")
    p_cfg.add_argument("--push-user", dest="push_user", help="推送用户 openId (ou_xxx)")
    p_cfg.add_argument("--push-chat", dest="push_chat", help="推送群聊 ID (oc_xxx)")

    # run
    p_run = sub.add_parser("run", help="立即执行监控并推送")
    p_run.add_argument("--keyword", help="只运行指定关键词（默认全部）")
    p_run.add_argument("--max", type=int, help="覆盖最大抓取数")
    p_run.add_argument("--dry-run", action="store_true", help="仅预览推送内容不发送")
    p_run.add_argument("--force-push", action="store_true", help="即使无新帖也推送")

    # status
    sub.add_parser("status", help="查看运行统计")

    # history
    p_hist = sub.add_parser("history", help="查看历史记录")
    p_hist.add_argument("--limit", type=int, default=20, help="显示条数（默认20）")
    p_hist.add_argument("--keyword", help="过滤关键词")

    args = parser.parse_args()

    dispatch = {
        "list": cmd_list,
        "add": cmd_add,
        "remove": cmd_remove,
        "config": cmd_config,
        "run": cmd_run,
        "status": cmd_status,
        "history": cmd_history,
    }
    dispatch[args.cmd](args)


if __name__ == "__main__":
    main()
