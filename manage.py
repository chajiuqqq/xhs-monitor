"""
小红书监控任务管理器

用法:
  python manage.py list                          # 查看所有监控任务
  python manage.py add "AI Agent 招聘"           # 添加关键词
  python manage.py add "AI Agent 招聘" "AI 求职" # 批量添加
  python manage.py remove "AI Agent 招聘"        # 删除关键词
  python manage.py run                           # 立即执行所有任务并推送
  python manage.py run --keyword "AI Agent"      # 只跑该关键词
  python manage.py run --task agent_jd_30w       # 只跑该任务组
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

from config import DB_PATH, LLM_ENABLED, MAX_RESULTS_DEFAULT, OUTPUT_DIR, PROJECT_ROOT

# 监控任务存储文件（JSON，简单持久化）
TASKS_FILE = PROJECT_ROOT / "tasks.json"

# Python 解释器路径（跨平台）
if sys.platform == "win32":
    PYTHON = r"C:\Users\chaji\.workbuddy\binaries\python\envs\xhs_monitor\Scripts\python.exe"
else:
    _VENV_PYTHON = str(PROJECT_ROOT / "venv" / "bin" / "python")
    PYTHON = _VENV_PYTHON if Path(_VENV_PYTHON).exists() else sys.executable


def _build_subprocess_env() -> dict:
    """构建 subprocess 环境变量。

    关键点：subprocess 会继承当前进程的环境，但如果当前进程 PATH 不全
    （如 systemd --user / nohup 启动时只含 /usr/bin:/bin），子进程也找不到
    lark-cli。这里强制注入常用路径，覆盖各场景。
    """
    import os
    env = os.environ.copy()
    if sys.platform != "win32":
        # Linux: 补全 lark-cli / node / chromium / playwright 的查找路径
        extra_paths = [
            str(Path.home() / ".npm-global" / "bin"),  # npm 全局安装（lark-cli）
            str(Path.home() / ".local" / "bin"),        # pip --user 安装
            str(Path.home() / "xhs_monitor" / "venv" / "bin"),  # 项目 venv（如果有）
            "/usr/local/bin",
            "/snap/bin",                                # snap chromium
        ]
        # 保留原 PATH 前缀，再加上补充路径
        env["PATH"] = ":".join(extra_paths) + ":" + env.get("PATH", "")

        # LD_LIBRARY_PATH：paddlepaddle-gpu (cu11.8) dlopen 需要 cuDNN/cuBLAS/nvrtc/cudart
        # 这些库以 pip wheel 安装，必须在进程启动前由 ld.so 读取（python 内 os.environ 改动无效）
        site = str(Path.home() / ".local" / "lib" / "python3.12" / "site-packages")
        cuda_lib_dirs = [
            str(Path.home() / ".local" / "cudnn8" / "lib"),  # cudnn 8.6（含无版本号软链）
            f"{site}/nvidia/cublas/lib",
            f"{site}/nvidia/cuda_nvrtc/lib",
            f"{site}/nvidia/cuda_runtime/lib",
            f"{site}/nvidia/cudnn/lib",
        ]
        existing_ld = env.get("LD_LIBRARY_PATH", "")
        existing_parts = existing_ld.split(":") if existing_ld else []
        new_parts = [p for p in cuda_lib_dirs if p not in existing_parts]
        if new_parts:
            env["LD_LIBRARY_PATH"] = ":".join(new_parts + existing_parts)

    env["PYTHONUTF8"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    return env


# ──────────────────────────────────────────────────────
# 任务持久化
# ──────────────────────────────────────────────────────

def load_tasks() -> dict:
    """读取任务配置文件。"""
    if TASKS_FILE.exists():
        data = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
        # 兼容旧 schema：没有 tasks 字段时补默认空列表
        if isinstance(data, dict) and "tasks" not in data:
            data["tasks"] = []
        return data
    return {
        "keywords": [],
        "tasks": [],
        "max_per_keyword": MAX_RESULTS_DEFAULT,
        "push_user_id": "",
    }


def save_tasks(tasks: dict) -> None:
    """写入任务配置文件。"""
    TASKS_FILE.write_text(
        json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ──────────────────────────────────────────────────────
# 命令实现
# ──────────────────────────────────────────────────────

def cmd_list(args):
    """列出所有监控关键词和任务组。"""
    tasks = load_tasks()
    keywords = tasks.get("keywords", [])
    task_list = tasks.get("tasks", [])

    print("=" * 50)
    print("  小红书帖子监控 - 任务列表")
    print("=" * 50)

    # 关键词
    print("\n[关键词] (老路径，无 LLM 过滤)")
    if not keywords:
        print("  (无)")
    else:
        for i, kw in enumerate(keywords, 1):
            print(f"  {i}. {kw}")

    # 任务组
    print("\n[任务组] (新路径，LLM 拆解 + 智能过滤)")
    if not task_list:
        print("  (无，用 'manage.py task add <名称> | <描述>' 创建)")
    else:
        for i, t in enumerate(task_list, 1):
            name = t.get("name", "?")
            kws = t.get("keywords", [])
            flt = t.get("filter", "")
            flt_mark = "🎯" if flt else "·"
            print(f"  {i}. {flt_mark} [{name}] {len(kws)} 关键词")
            if flt:
                print(f"      过滤: {flt[:60]}{'...' if len(flt) > 60 else ''}")

    print(f"\n  每关键词最大抓取数: {tasks.get('max_per_keyword', MAX_RESULTS_DEFAULT)}")
    print(f"  LLM 状态: {'✅ 已启用' if LLM_ENABLED else '❌ 未配置 (set XHS_LLM_API_KEY)'}")
    push_target = tasks.get("push_user_id") or tasks.get("push_chat_id") or "默认私聊"
    print(f"  推送目标: {push_target}")

    # 显示数据库统计
    if DB_PATH.exists():
        conn = sqlite3.connect(DB_PATH)
        total = conn.execute("SELECT COUNT(*) FROM seen_notes").fetchone()[0]
        new_today = conn.execute(
            "SELECT COUNT(*) FROM seen_notes WHERE first_seen >= date('now')"
        ).fetchone()[0]
        llm_filtered = conn.execute(
            "SELECT COUNT(*) FROM seen_notes WHERE status='filtered_by_llm'"
        ).fetchone()[0]
        last_run = conn.execute(
            "SELECT MAX(first_seen) FROM seen_notes"
        ).fetchone()[0]
        conn.close()
        print(f"\n  数据库: {total} 篇 | 今日新增: {new_today} | LLM 过滤累计: {llm_filtered} | 最后运行: {last_run}")
    else:
        print("\n  数据库: 未初始化（首次运行后自动创建）")

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
    import os

    # env var 兜底：XHS_NO_OCR=1 / XHS_NO_SUMMARY=1（bot 触发场景用）
    if not args.no_ocr and os.getenv("XHS_NO_OCR") == "1":
        args.no_ocr = True
    if not args.no_summary and os.getenv("XHS_NO_SUMMARY") == "1":
        args.no_summary = True

    tasks = load_tasks()
    keywords = tasks.get("keywords", [])
    task_list = tasks.get("tasks", [])

    # 单选模式：--keyword 只跑该关键词；--task 只跑该任务组
    if args.keyword and args.task_name:
        print("[错误] --keyword 和 --task 不能同时使用")
        sys.exit(1)

    if args.keyword:
        keywords = [args.keyword]
        task_list = []  # --keyword 单关键词模式不走 tasks 路径
    elif args.task_name:
        task_list = [t for t in task_list if t.get("name") == args.task_name]
        if not task_list:
            print(f"[错误] 任务组 '{args.task_name}' 不存在")
            print("用 'manage.py task list' 查看所有任务组")
            sys.exit(1)
        keywords = []  # --task 模式不走 keywords 路径

    if not keywords and not task_list:
        print("[错误] 没有监控关键词或任务组，请先用 'manage.py add' 或 'manage.py task add' 添加")
        sys.exit(1)

    max_results = args.max or tasks.get("max_per_keyword", MAX_RESULTS_DEFAULT)

    print(f"[执行] 关键词: {keywords}")
    print(f"[执行] 任务组: {[t.get('name', '?') for t in task_list]}")
    print(f"[执行] 每词最大: {max_results}")
    print()

    # 调用 pipeline
    pipeline_script = str(PROJECT_ROOT / "pipeline.py")
    kw_json = json.dumps(keywords, ensure_ascii=False)

    pipeline_cmd = [PYTHON, pipeline_script, kw_json, "--max", str(max_results)]
    if args.no_llm:
        pipeline_cmd.append("--no-llm")
    if args.no_ocr:
        pipeline_cmd.append("--no-ocr")
    if args.no_summary:
        pipeline_cmd.append("--no-summary")
    if task_list:
        # 把任务组写入临时 JSON 文件传给 pipeline（避免命令行长度问题）
        tmp = PROJECT_ROOT / "output" / "_pipeline_tasks.json"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(
            json.dumps({"tasks": task_list}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        pipeline_cmd += ["--tasks-json", str(tmp)]

    result = subprocess.run(
        pipeline_cmd,
        capture_output=False,  # 直接输出到控制台
        env=_build_subprocess_env(),
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
        env=_build_subprocess_env(),
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
    total_llm = conn.execute(
        "SELECT COUNT(*) FROM seen_notes WHERE status='filtered_by_llm'"
    ).fetchone()[0]
    last_run = conn.execute("SELECT MAX(first_seen) FROM seen_notes").fetchone()[0]

    print(f"\n  总记录数: {total}")
    print(f"  有效新帖: {total_new}")
    print(f"  内容重复: {total_dup}")
    print(f"  LLM 过滤: {total_llm}")
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
# 任务组命令（tasks 子命令族）
# ──────────────────────────────────────────────────────

def cmd_task_list(args):
    """列出所有任务组。"""
    tasks = load_tasks()
    task_list = tasks.get("tasks", [])

    print("=" * 50)
    print("  任务组列表（LLM 拆解 + 智能过滤）")
    print("=" * 50)
    if not task_list:
        print("  (无，用 'manage.py task add <名称> | <描述>' 创建)")
    else:
        for i, t in enumerate(task_list, 1):
            name = t.get("name", "?")
            desc = t.get("description", "")
            kws = t.get("keywords", [])
            flt = t.get("filter", "")
            print(f"\n  {i}. [{name}]")
            if desc:
                print(f"     描述: {desc}")
            print(f"     关键词 ({len(kws)}): {', '.join(kws)}")
            if flt:
                print(f"     过滤: {flt}")
            else:
                print(f"     过滤: (无，仅按关键词去重)")
    print("\n" + "=" * 50)


def cmd_task_show(args):
    """查看单个任务组详情。"""
    name = args.name
    tasks = load_tasks()
    task = next((t for t in tasks.get("tasks", []) if t.get("name") == name), None)
    if not task:
        print(f"[错误] 任务组 '{name}' 不存在\n用 'manage.py task list' 查看")
        return
    print(json.dumps(task, ensure_ascii=False, indent=2))


def cmd_task_add(args):
    """添加任务组（手动输入 keywords 和 filter，不走 LLM）。"""
    if "|" not in args.spec:
        print("❌ 用法: manage.py task add <名称> | <自然语言描述> [--keywords k1,k2] [--filter '条件']")
        return

    name, _, description = args.spec.partition("|")
    name = name.strip()
    description = description.strip()
    if not name or not description:
        print("❌ 名称和描述都不能为空")
        return

    # 解析 keywords 和 filter
    keywords: list[str] = []
    flt = ""
    if args.keywords:
        keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
    if args.filter:
        flt = args.filter.strip()

    if not keywords:
        print("❌ 必须提供至少 1 个关键词（--keywords k1,k2,...）")
        return

    tasks = load_tasks()
    task_list = tasks.setdefault("tasks", [])

    existed_idx = next((i for i, t in enumerate(task_list) if t.get("name") == name), None)
    new_task = {
        "name": name,
        "description": description,
        "keywords": keywords,
        "filter": flt,
    }
    if existed_idx is not None:
        task_list[existed_idx] = new_task
        print(f"[覆盖] 任务组 '{name}' 已更新")
    else:
        task_list.append(new_task)
        print(f"[添加] 任务组 '{name}' 已创建")
    save_tasks(tasks)
    print(f"  关键词 ({len(keywords)}): {', '.join(keywords)}")
    if flt:
        print(f"  过滤条件: {flt}")


def cmd_task_remove(args):
    """删除任务组。"""
    tasks = load_tasks()
    task_list = tasks.get("tasks", [])
    name = args.name
    before = len(task_list)
    task_list[:] = [t for t in task_list if t.get("name") != name]
    removed = before - len(task_list)
    if removed == 0:
        print(f"[错误] 任务组 '{name}' 不存在")
        return
    save_tasks(tasks)
    print(f"[删除] 任务组 '{name}' 已移除（剩余 {len(task_list)} 个）")


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
    sub.add_parser("list", help="查看所有监控关键词、任务组和状态")

    # add
    p_add = sub.add_parser("add", help="添加监控关键词（无 LLM）")
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
    p_run.add_argument("--keyword", help="只运行指定关键词（默认全部，跳过 tasks 路径）")
    p_run.add_argument("--task", dest="task_name", help="只运行指定任务组（按 name）")
    p_run.add_argument("--max", type=int, help="覆盖最大抓取数")
    p_run.add_argument("--dry-run", action="store_true", help="仅预览推送内容不发送")
    p_run.add_argument("--force-push", action="store_true", help="即使无新帖也推送")
    p_run.add_argument("--no-llm", action="store_true", help="强制跳过 LLM 过滤")
    p_run.add_argument("--no-ocr", action="store_true", help="跳过图片 OCR")
    p_run.add_argument("--no-summary", action="store_true", help="跳过 LLM 摘要")

    # status
    sub.add_parser("status", help="查看运行统计")

    # history
    p_hist = sub.add_parser("history", help="查看历史记录")
    p_hist.add_argument("--limit", type=int, default=20, help="显示条数（默认20）")
    p_hist.add_argument("--keyword", help="过滤关键词")

    # task 子命令族
    p_task = sub.add_parser("task", help="任务组管理（LLM 拆解）")
    task_sub = p_task.add_subparsers(dest="task_cmd", required=True)

    p_task_list = task_sub.add_parser("list", help="列出所有任务组")
    p_task_list.set_defaults(func=cmd_task_list)

    p_task_show = task_sub.add_parser("show", help="查看单个任务组详情")
    p_task_show.add_argument("name", help="任务组名称")
    p_task_show.set_defaults(func=cmd_task_show)

    p_task_add = task_sub.add_parser("add", help="手动添加任务组（不走 LLM）")
    p_task_add.add_argument("spec", help="<名称> | <自然语言描述>")
    p_task_add.add_argument("--keywords", help="逗号分隔的关键词列表")
    p_task_add.add_argument("--filter", dest="filter", help="LLM 过滤条件")
    p_task_add.set_defaults(func=cmd_task_add)

    p_task_rm = task_sub.add_parser("remove", help="删除任务组")
    p_task_rm.add_argument("name", help="任务组名称")
    p_task_rm.set_defaults(func=cmd_task_remove)

    args = parser.parse_args()

    # task 子命令族有独立的 dispatch
    if args.cmd == "task":
        args.func(args)
        return

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
