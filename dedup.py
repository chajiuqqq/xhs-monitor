"""
增量去重模块 - SQLite 持久化

职责：
  1. noteId 主键去重（同一帖子不重复抓取）
  2. content_hash 内容指纹去重（防止搬运帖/同内容不同 noteId 重复推送）
"""

import hashlib
import sqlite3
from datetime import datetime

from config import DB_PATH, DEDUP_CONTENT_HASH


def init_db() -> None:
    """初始化数据库表结构（幂等，可重复调用）。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS seen_notes (
            note_id        TEXT PRIMARY KEY,
            keyword        TEXT,
            title          TEXT,
            author         TEXT,
            likes          INTEGER DEFAULT 0,
            first_seen     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_checked   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            content_hash   TEXT,
            status         TEXT DEFAULT 'new'
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_content_hash ON seen_notes(content_hash)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_keyword ON seen_notes(keyword)"
    )
    conn.commit()
    conn.close()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def is_seen(note_id: str) -> bool:
    """noteId 是否已在库中。"""
    conn = _connect()
    row = conn.execute("SELECT 1 FROM seen_notes WHERE note_id = ?", (note_id,)).fetchone()
    conn.close()
    return row is not None


def is_content_duplicate(content: str) -> bool:
    """内容指纹是否已存在（防搬运帖）。"""
    if not DEDUP_CONTENT_HASH or not content:
        return False
    content_hash = hashlib.md5(content.encode("utf-8")).hexdigest()
    conn = _connect()
    row = conn.execute(
        "SELECT 1 FROM seen_notes WHERE content_hash = ?", (content_hash,)
    ).fetchone()
    conn.close()
    return row is not None


def mark_seen(
    note_id: str,
    keyword: str,
    title: str = "",
    author: str = "",
    likes: int = 0,
    content: str = "",
    status: str = "new",
) -> None:
    """记录一条已抓取帖子（存在则更新）。"""
    content_hash = (
        hashlib.md5(content.encode("utf-8")).hexdigest()
        if DEDUP_CONTENT_HASH and content
        else None
    )
    now = datetime.now().isoformat()
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        """
        INSERT INTO seen_notes
            (note_id, keyword, title, author, likes, first_seen, last_checked, content_hash, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(note_id) DO UPDATE SET
            last_checked = excluded.last_checked,
            title        = excluded.title,
            likes        = excluded.likes,
            content_hash = excluded.content_hash,
            status       = excluded.status
        """,
        (note_id, keyword, title, author, likes, now, now, content_hash, status),
    )
    conn.commit()
    conn.close()


def filter_new_notes(notes: list[dict], keyword: str) -> list[dict]:
    """从搜索结果中过滤出未抓取过的新帖子。"""
    new_notes = []
    for note in notes:
        if not is_seen(note["note_id"]):
            new_notes.append(note)
    return new_notes


def get_stats() -> dict:
    """返回数据库统计信息。"""
    conn = _connect()
    total = conn.execute("SELECT COUNT(*) FROM seen_notes").fetchone()[0]
    by_status = {
        row["status"]: row["cnt"]
        for row in conn.execute(
            "SELECT status, COUNT(*) as cnt FROM seen_notes GROUP BY status"
        )
    }
    conn.close()
    return {"total": total, "by_status": by_status}
