"""
飞书推送模块 - 将监控结果推送到飞书

用法:
  python push_feishu.py                          # 推送 latest_result.json 给自己
  python push_feishu.py --chat-id oc_xxx         # 推送到指定群
  python push_feishu.py --dry-run                # 仅预览消息内容不发送
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# Windows UTF-8
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from config import OUTPUT_DIR

# 默认推送目标：用户自己的 openId（P2P 私聊）
DEFAULT_USER_ID = "ou_8ce2c84aa949a2b28cae7d7de3b0a0c6"

# Windows 下 lark-cli 是 sh 脚本，需要 Git Bash；Linux 下直接执行
_IS_WIN = sys.platform == "win32"
_BASH_EXE = r"C:\Program Files\Git\bin\bash.exe" if _IS_WIN else None


def build_markdown(result: dict) -> str:
    """将监控结果整理为飞书 markdown 消息。"""
    notes = result.get("new_notes", [])
    stats = result.get("stats", {})
    ts = result.get("timestamp", "")

    if not notes:
        return f"小红书监控简报\n\n本次无新增帖子。\n\n执行时间: {ts}"

    lines = []
    lines.append(f"## 小红书监控简报")
    lines.append("")
    lines.append(
        f"搜索 {stats.get('total_searched', 0)} 篇 | "
        f"新增 {stats.get('total_new', 0)} 篇 | "
        f"重复 {stats.get('total_dup_content', 0)} 篇"
    )
    lines.append("")

    for i, note in enumerate(notes, 1):
        title = note.get("title", "无标题")
        author = note.get("author", "未知")
        likes = note.get("likes", 0)
        url = note.get("url", "")
        desc = note.get("desc", "")
        img_count = note.get("image_count", 0)
        tags = note.get("tags", [])
        kw = note.get("keyword", "")

        # 截断正文（飞书消息不宜过长）
        desc_short = desc[:200] + "..." if len(desc) > 200 else desc
        # 去掉正文中的换行（飞书 markdown 显示）
        desc_short = desc_short.replace("\n", " ")

        tag_str = " ".join(f"#{t}" for t in tags[:5]) if tags else ""

        lines.append(f"### {i}. {title}")
        lines.append(f"作者: {author} | 点赞: {likes} | 图片: {img_count}张 | 关键词: {kw}")
        if desc_short:
            lines.append(f"摘要: {desc_short}")
        if tag_str:
            lines.append(f"标签: {tag_str}")
        lines.append(f"[查看原帖]({url})")
        lines.append("")

    lines.append(f"---")
    lines.append(f"执行时间: {ts}")
    return "\n".join(lines)


def send_to_feishu(
    markdown: str,
    user_id: str = "",
    chat_id: str = "",
    as_bot: bool = True,
    dry_run: bool = False,
) -> dict:
    """调用 lark-cli 发送飞书消息。

    Windows: 用 Git Bash 执行（lark-cli 是 sh 脚本），markdown 通过临时文件 + $(cat) 传递
    Linux:   直接执行 lark-cli，markdown 通过临时文件 + $(cat) 传递
    """
    target = f"--chat-id {chat_id}" if chat_id else f"--user-id {user_id or DEFAULT_USER_ID}"
    identity = "bot" if as_bot else "user"

    # 先把 markdown 写到临时文件（避免 shell 参数长度/转义问题）
    tmp_file = OUTPUT_DIR / "_feishu_msg.md"
    tmp_file.write_text(markdown, encoding="utf-8")
    # 用相对路径（cwd 设为项目根目录）
    tmp_rel = "output/_feishu_msg.md"

    bash_cmd = f'lark-cli im +messages-send {target} --markdown "$(cat {tmp_rel})" --as {identity}'
    if dry_run:
        bash_cmd += " --dry-run"

    print(f"[推送] 目标: {'群 ' + chat_id if chat_id else '私聊 ' + (user_id or DEFAULT_USER_ID)}")
    print(f"[推送] 身份: {identity}")

    if _IS_WIN:
        # Windows: 用 Git Bash 执行
        result = subprocess.run(
            [_BASH_EXE, "-c", bash_cmd],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(Path(__file__).parent),
        )
    else:
        # Linux: 直接用 shell 执行
        result = subprocess.run(
            bash_cmd,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(Path(__file__).parent),
        )
    if result.returncode != 0:
        print(f"[错误] lark-cli 返回码: {result.returncode}")
        print(result.stderr)
        return {"ok": False, "error": result.stderr}

    try:
        resp = json.loads(result.stdout)
        return resp
    except json.JSONDecodeError:
        return {"ok": True, "raw": result.stdout}


def main():
    parser = argparse.ArgumentParser(description="推送小红书监控结果到飞书")
    parser.add_argument("--chat-id", default="", help="群聊 ID (oc_xxx)，不填则私聊自己")
    parser.add_argument("--user-id", default="", help="用户 openId (ou_xxx)，默认发给自己")
    parser.add_argument("--as-user", action="store_true", help="用 user 身份发送（默认 bot）")
    parser.add_argument("--dry-run", action="store_true", help="仅预览不发送")
    parser.add_argument(
        "--input",
        default=str(OUTPUT_DIR / "latest_result.json"),
        help="输入 JSON 文件路径",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"[错误] 结果文件不存在: {input_path}")
        sys.exit(1)

    result = json.loads(input_path.read_text(encoding="utf-8"))
    markdown = build_markdown(result)

    if args.dry_run:
        print("=== 消息预览 ===")
        print(markdown)
        print("=== 预览结束（未发送）===")
        return

    resp = send_to_feishu(
        markdown,
        user_id=args.user_id,
        chat_id=args.chat_id,
        as_bot=not args.as_user,
        dry_run=False,
    )
    print(f"[结果] {json.dumps(resp, ensure_ascii=False, indent=2)}")

    if resp.get("ok", resp.get("message_id")):
        print("[完成] 推送成功!")
    else:
        print("[失败] 推送失败，请检查错误信息")


if __name__ == "__main__":
    main()
