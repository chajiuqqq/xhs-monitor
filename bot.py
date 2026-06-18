"""
飞书 Bot - 小红书监控任务管理

通过飞书私聊 bot 发送命令管理监控任务：
  /list              - 查看所有监控关键词
  /add 关键词1 关键词2  - 添加监控关键词（空格分隔，可批量）
  /delete 1          - 按序号删除
  /delete 关键词      - 按关键词删除
  /status            - 查看监控运行状态
  /run               - 立即执行监控（完成后自动推送）
  /help              - 查看帮助

启动方式:
  python bot.py
"""

import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
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
from manage import load_tasks, save_tasks

# ── 配置 ──────────────────────────────────────────────────
# Python 解释器：Windows 用 venv，Linux 用 venv 或系统 python3
if sys.platform == "win32":
    PYTHON = r"C:\Users\chaji\.workbuddy\binaries\python\envs\xhs_monitor\Scripts\python.exe"
else:
    _VENV_PYTHON = str(PROJECT_ROOT / "venv" / "bin" / "python")
    PYTHON = _VENV_PYTHON if Path(_VENV_PYTHON).exists() else sys.executable

# Windows 下 lark-cli 需要 Git Bash；Linux 直接执行
_IS_WIN = sys.platform == "win32"
_BASH_EXE = r"C:\Program Files\Git\bin\bash.exe" if _IS_WIN else None

# 授权用户（只有此人能操控 bot）
AUTHORIZED_USER = "ou_8ce2c84aa949a2b28cae7d7de3b0a0c6"

# lark-cli 事件监听命令
EVENT_CMD = "lark-cli event consume im.message.receive_v1 --as bot"


# ── 飞书消息发送 ──────────────────────────────────────────

def send_message(text: str, user_id: str = "", chat_id: str = "") -> bool:
    """通过 lark-cli 发送飞书消息。

    Windows: lark-cli 是 sh 脚本，需通过 Git Bash 执行
    Linux:   直接执行 lark-cli
    长文本写入临时文件，用 $(cat) 传递避免参数截断。
    """
    if chat_id and chat_id.startswith("oc_"):
        target = f"--chat-id {chat_id}"
    else:
        target = f"--user-id {user_id or AUTHORIZED_USER}"

    # 写临时文件
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp_file = OUTPUT_DIR / "_bot_reply.txt"
    tmp_file.write_text(text, encoding="utf-8")

    bash_cmd = (
        f'lark-cli im +messages-send {target} '
        f'--text "$(cat output/_bot_reply.txt)" --as bot'
    )

    try:
        if _IS_WIN:
            result = subprocess.run(
                [_BASH_EXE, "-c", bash_cmd],
                capture_output=True,
                text=True,
                encoding="utf-8",
                cwd=str(PROJECT_ROOT),
                timeout=30,
            )
        else:
            result = subprocess.run(
                bash_cmd,
                shell=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                cwd=str(PROJECT_ROOT),
                timeout=30,
            )
        if result.returncode != 0:
            print(f"[发送失败] {result.stderr[:200]}", file=sys.stderr)
            return False
        return True
    except Exception as e:
        print(f"[发送异常] {e}", file=sys.stderr)
        return False


# ── 命令处理 ──────────────────────────────────────────────

def handle_command(content: str, sender_id: str, chat_id: str, chat_type: str) -> str | None:
    """解析并执行命令，返回回复文本。返回 None 表示忽略该消息。"""

    # 权限检查
    if sender_id != AUTHORIZED_USER:
        return "⚠️ 抱歉，你没有权限使用此机器人。"

    # 去掉 @bot 前缀（群聊场景）
    content = re.sub(r"^@\S+\s*", "", content).strip()
    if not content:
        return None

    if not content.startswith("/"):
        return None  # 非命令消息，忽略

    parts = content.split(maxsplit=1)
    cmd = parts[0].lower()
    args_str = parts[1].strip() if len(parts) > 1 else ""

    dispatch = {
        "/list": _cmd_list,
        "/ls": _cmd_list,
        "/add": _cmd_add,
        "/delete": _cmd_delete,
        "/del": _cmd_delete,
        "/remove": _cmd_delete,
        "/status": _cmd_status,
        "/run": _cmd_run,
        "/help": _cmd_help,
        "/start": _cmd_help,
    }

    handler = dispatch.get(cmd)
    if not handler:
        return f"❓ 未知命令: {cmd}\n\n输入 /help 查看可用命令"

    try:
        return handler(args_str, sender_id, chat_id)
    except Exception as e:
        return f"❌ 执行出错: {e}"


def _cmd_list(args, sender_id, chat_id):
    """列出所有监控关键词。"""
    tasks = load_tasks()
    keywords = tasks.get("keywords", [])

    lines = ["📋 监控任务列表", ""]
    if not keywords:
        lines.append("（空）用 /add <关键词> 添加")
    else:
        for i, kw in enumerate(keywords, 1):
            lines.append(f"  {i}. {kw}")

    lines.append(f"\n每词最大抓取: {tasks.get('max_per_keyword', MAX_RESULTS_DEFAULT)}")

    # 数据库统计
    if DB_PATH.exists():
        conn = sqlite3.connect(DB_PATH)
        total = conn.execute("SELECT COUNT(*) FROM seen_notes").fetchone()[0]
        new_today = conn.execute(
            "SELECT COUNT(*) FROM seen_notes WHERE first_seen >= date('now') AND status='new'"
        ).fetchone()[0]
        last_run = conn.execute("SELECT MAX(first_seen) FROM seen_notes").fetchone()[0]
        conn.close()
        lines.append(f"数据库: {total} 篇 | 今日新增: {new_today} | 最后运行: {last_run or '无'}")

    return "\n".join(lines)


def _cmd_add(args, sender_id, chat_id):
    """添加监控关键词。"""
    if not args:
        return "❌ 用法: /add <关键词1> [关键词2] [关键词3]\n例如: /add AI Agent招聘 RAG工程师"

    tasks = load_tasks()
    keywords = tasks.setdefault("keywords", [])
    new_kws = args.split()

    added = []
    skipped = []
    for kw in new_kws:
        kw = kw.strip()
        if not kw:
            continue
        if kw in keywords:
            skipped.append(kw)
        else:
            keywords.append(kw)
            added.append(kw)

    save_tasks(tasks)

    msg = ""
    if added:
        msg += f"✅ 已添加: {', '.join(added)}\n"
    if skipped:
        msg += f"⏭️ 已存在: {', '.join(skipped)}\n"
    msg += f"当前共 {len(keywords)} 个关键词"
    return msg


def _cmd_delete(args, sender_id, chat_id):
    """删除监控关键词（按序号或关键词名）。"""
    if not args:
        return "❌ 用法: /delete <序号或关键词>\n例如: /delete 1 或 /delete AI Agent招聘"

    tasks = load_tasks()
    keywords = tasks.get("keywords", [])
    arg = args.strip()

    if arg.isdigit():
        # 按序号删除
        idx = int(arg) - 1
        if 0 <= idx < len(keywords):
            removed = keywords.pop(idx)
            save_tasks(tasks)
            return f"✅ 已删除: {removed}\n当前共 {len(keywords)} 个关键词"
        else:
            return f"❌ 序号 {arg} 无效，范围 1-{len(keywords)}"
    else:
        # 按关键词删除
        if arg in keywords:
            keywords.remove(arg)
            save_tasks(tasks)
            return f"✅ 已删除: {arg}\n当前共 {len(keywords)} 个关键词"
        else:
            return f"❌ 关键词 '{arg}' 不存在\n\n用 /list 查看所有关键词"


def _cmd_status(args, sender_id, chat_id):
    """查看监控运行状态。"""
    if not DB_PATH.exists():
        return "📊 尚未运行过监控，数据库为空\n\n用 /run 立即执行一次监控"

    conn = sqlite3.connect(DB_PATH)
    total = conn.execute("SELECT COUNT(*) FROM seen_notes").fetchone()[0]
    total_new = conn.execute("SELECT COUNT(*) FROM seen_notes WHERE status='new'").fetchone()[0]
    total_dup = conn.execute("SELECT COUNT(*) FROM seen_notes WHERE status='dup_content'").fetchone()[0]
    last_run = conn.execute("SELECT MAX(first_seen) FROM seen_notes").fetchone()[0]

    lines = ["📊 监控运行状态", ""]
    lines.append(f"总记录: {total} 篇")
    lines.append(f"有效新帖: {total_new} 篇")
    lines.append(f"内容重复: {total_dup} 篇")
    lines.append(f"最后运行: {last_run or '未知'}")

    # 按关键词统计
    rows = conn.execute(
        "SELECT keyword, COUNT(*) as cnt, MAX(first_seen) as last "
        "FROM seen_notes GROUP BY keyword ORDER BY last DESC"
    ).fetchall()
    if rows:
        lines.append("\n按关键词:")
        for kw, cnt, last in rows:
            lines.append(f"  [{kw}] {cnt} 篇 | {last[:16] if last else '无'}")

    # 今日新增
    today = conn.execute(
        "SELECT title, author, likes FROM seen_notes "
        "WHERE first_seen >= date('now') AND status='new' "
        "ORDER BY first_seen DESC LIMIT 5"
    ).fetchall()
    if today:
        lines.append("\n今日新增:")
        for title, author, likes in today:
            lines.append(f"  · {title[:30]} | {author} | 赞{likes}")

    conn.close()
    return "\n".join(lines)


def _cmd_run(args, sender_id, chat_id):
    """异步执行监控 pipeline。"""
    tasks = load_tasks()
    keywords = tasks.get("keywords", [])
    if not keywords:
        return "❌ 没有监控关键词\n\n用 /add <关键词> 添加后再执行"

    # 异步启动 pipeline（不阻塞事件循环）
    subprocess.Popen(
        [PYTHON, "manage.py", "run"],
        env={**os.environ, "PYTHONUTF8": "1"},
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    return (
        f"🔄 监控已启动\n"
        f"关键词: {', '.join(keywords)}\n"
        f"完成后会自动推送结果到飞书，请稍候..."
    )


def _cmd_help(args, sender_id, chat_id):
    """帮助信息。"""
    return (
        "🤖 小红书监控 Bot\n\n"
        "可用命令:\n"
        "  /list              查看所有监控关键词\n"
        "  /add 关键词1 关键词2  添加监控关键词\n"
        "  /delete 1          按序号删除关键词\n"
        "  /delete 关键词      按名称删除关键词\n"
        "  /status            查看监控运行状态\n"
        "  /run               立即执行监控\n"
        "  /help              查看此帮助\n\n"
        "示例:\n"
        "  /add AI Agent招聘 RAG工程师 大模型应用\n"
        "  /delete 2\n"
        "  /run"
    )


# ── 事件循环 ──────────────────────────────────────────────

def _read_stderr(proc):
    """后台线程：读 lark-cli stderr，打印诊断信息。"""
    ready = False
    for line in proc.stderr:
        line = line.strip()
        if not line:
            continue
        if "ready" in line.lower():
            ready = True
            print(f"[bot] ✅ 事件监听已就绪，等待飞书消息...")
        elif line.startswith("{"):
            # JSON 错误信息
            try:
                err = json.loads(line)
                if not err.get("ok", True):
                    print(f"[bot] ❌ lark-cli 错误: {err.get('error', {}).get('message', line)}")
            except json.JSONDecodeError:
                print(f"[lark-stderr] {line}")
        else:
            print(f"[lark-stderr] {line}")


def event_loop():
    """启动事件监听，处理飞书消息。"""
    print("[bot] 启动 lark-cli 事件监听...")

    if _IS_WIN:
        # Windows: lark-cli 是 sh 脚本，通过 Git Bash 执行
        proc = subprocess.Popen(
            [_BASH_EXE, "-c", EVENT_CMD],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,  # 保持 stdin 开启，防止 unbounded 模式 EOF 退出
            text=True,
            encoding="utf-8",
            bufsize=1,  # 行缓冲
            cwd=str(PROJECT_ROOT),
        )
    else:
        # Linux: 直接执行
        proc = subprocess.Popen(
            EVENT_CMD,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            cwd=str(PROJECT_ROOT),
        )

    # 后台线程读 stderr
    stderr_thread = threading.Thread(target=_read_stderr, args=(proc,), daemon=True)
    stderr_thread.start()

    # 主线程读 stdout（NDJSON 事件流）
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue

        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            print(f"[bot] 无法解析事件: {line[:100]}")
            continue

        # 提取事件字段
        msg_type = event.get("message_type", "")
        content = event.get("content", "")
        sender_id = event.get("sender_id", "")
        chat_id = event.get("chat_id", "")
        chat_type = event.get("chat_type", "p2p")
        msg_id = event.get("message_id", "")

        # 只处理文本消息
        if msg_type != "text":
            continue

        print(f"[bot] 收到消息 from {sender_id[:12]}...: {content[:50]}")

        # 处理命令
        reply = handle_command(content, sender_id, chat_id, chat_type)
        if reply:
            # 回复到消息来源（P2P 用 user_id，群聊用 chat_id）
            target_user = sender_id if chat_type == "p2p" else ""
            target_chat = chat_id if chat_type == "group" else ""
            ok = send_message(reply, user_id=target_user, chat_id=target_chat)
            if ok:
                print(f"[bot] ✅ 已回复 ({len(reply)} 字)")
            else:
                print(f"[bot] ❌ 回复失败")

    # stdout 关闭 = lark-cli 退出
    rc = proc.wait()
    print(f"[bot] lark-cli 进程退出，返回码 {rc}")
    return rc


def main():
    """主循环：事件监听崩溃后自动重启。"""
    print("=" * 50)
    print("  小红书监控飞书 Bot")
    print(f"  授权用户: {AUTHORIZED_USER[:12]}...")
    print("  输入消息命令: /list /add /delete /status /run /help")
    print("=" * 50)

    while True:
        try:
            rc = event_loop()
            if rc == 0:
                print("[bot] 正常退出")
                break
            else:
                print(f"[bot] 异常退出(返回码 {rc})，5秒后重启...")
                time.sleep(5)
        except KeyboardInterrupt:
            print("\n[bot] 用户中断，退出")
            break
        except Exception as e:
            print(f"[bot] 事件循环异常: {e}，5秒后重启...")
            time.sleep(5)


if __name__ == "__main__":
    main()
