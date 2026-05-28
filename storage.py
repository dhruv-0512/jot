"""
storage.py — SQLite wrapper for jot clipboard history manager.

Text history:  ~/.jot/history.db
Image storage: /tmp/jot/  (or %TEMP%\\jot on Windows)
"""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────

DB_DIR  = Path.home() / ".jot"
DB_PATH = DB_DIR / "history.db"

# /tmp/jot on Unix; %TEMP%\jot on Windows
IMG_DIR: Path = (
    Path("/tmp/jot")
    if sys.platform != "win32"
    else Path(tempfile.gettempdir()) / "jot"
)

# ── Caps ───────────────────────────────────────────────────────────────────────

MAX_TEXT_ENTRIES  = 500
MAX_IMAGE_ENTRIES = 50


# ── Internal helpers ───────────────────────────────────────────────────────────


def _get_connection() -> sqlite3.Connection:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS history (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            content      TEXT    NOT NULL,
            content_type TEXT    NOT NULL DEFAULT 'text',
            sha256       TEXT    NOT NULL UNIQUE,
            copied_at    TEXT    NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_sha256    ON history (sha256);
        CREATE INDEX IF NOT EXISTS idx_type_time ON history (content_type, copied_at DESC);
        """
    )
    conn.commit()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── Hashing ────────────────────────────────────────────────────────────────────


def sha256_of_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def sha256_of_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ── Text entries ───────────────────────────────────────────────────────────────


def add_text_entry(content: str) -> bool:
    """
    Add a text entry.
    Returns True if new, False if it already existed (timestamp bumped).
    """
    digest = sha256_of_text(content)
    now    = _now_iso()

    with _get_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM history WHERE sha256 = ?", (digest,)
        ).fetchone()

        if existing:
            conn.execute(
                "UPDATE history SET copied_at = ? WHERE sha256 = ?", (now, digest)
            )
            conn.commit()
            return False

        conn.execute(
            "INSERT INTO history (content, content_type, sha256, copied_at) VALUES (?, 'text', ?, ?)",
            (content, digest, now),
        )
        # Trim oldest text entries
        conn.execute(
            """
            DELETE FROM history
            WHERE content_type = 'text'
              AND id IN (
                  SELECT id FROM history
                  WHERE content_type = 'text'
                  ORDER BY copied_at DESC
                  LIMIT -1 OFFSET ?
              )
            """,
            (MAX_TEXT_ENTRIES,),
        )
        conn.commit()
    return True


# ── Image entries ──────────────────────────────────────────────────────────────


def add_image_entry(png_bytes: bytes) -> tuple[bool, str]:
    """
    Save image bytes as a PNG and record it in the DB.

    Returns (is_new: bool, file_path: str).
    If the same image was seen before, bumps its timestamp and returns the
    existing path (is_new=False).
    """
    digest = sha256_of_bytes(png_bytes)
    now    = _now_iso()

    with _get_connection() as conn:
        existing = conn.execute(
            "SELECT id, content FROM history WHERE sha256 = ? AND content_type = 'image'",
            (digest,),
        ).fetchone()

        if existing:
            conn.execute(
                "UPDATE history SET copied_at = ? WHERE sha256 = ?", (now, digest)
            )
            conn.commit()
            return False, existing["content"]

        # Write PNG file
        IMG_DIR.mkdir(parents=True, exist_ok=True)
        ts_tag   = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        filename = f"{ts_tag}_{digest[:8]}.png"
        file_path = str(IMG_DIR / filename)
        Path(file_path).write_bytes(png_bytes)

        conn.execute(
            "INSERT INTO history (content, content_type, sha256, copied_at) VALUES (?, 'image', ?, ?)",
            (file_path, digest, now),
        )

        # Trim oldest image entries (also delete files from disk)
        old_images = conn.execute(
            """
            SELECT id, content FROM history
            WHERE content_type = 'image'
            ORDER BY copied_at DESC
            LIMIT -1 OFFSET ?
            """,
            (MAX_IMAGE_ENTRIES,),
        ).fetchall()

        for row in old_images:
            _delete_image_file(row["content"])
            conn.execute("DELETE FROM history WHERE id = ?", (row["id"],))

        conn.commit()

    return True, file_path


def _delete_image_file(path: str) -> None:
    """Silently remove an image file if it still exists."""
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


# ── Queries ────────────────────────────────────────────────────────────────────


def get_recent(limit: int = 20) -> list[sqlite3.Row]:
    """Most-recently copied entries (all types), newest first."""
    with _get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, content, content_type, sha256, copied_at
            FROM history
            ORDER BY copied_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return rows


def search_entries(query: str, limit: int = 50) -> list[sqlite3.Row]:
    """Text-only search (LIKE, case-insensitive), newest first."""
    pattern = f"%{query}%"
    with _get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, content, content_type, sha256, copied_at
            FROM history
            WHERE content LIKE ? COLLATE NOCASE
            ORDER BY copied_at DESC
            LIMIT ?
            """,
            (pattern, limit),
        ).fetchall()
    return rows


def get_by_rank(rank: int) -> sqlite3.Row | None:
    """1-based rank in the recency-ordered unified list."""
    if rank < 1:
        return None
    with _get_connection() as conn:
        row = conn.execute(
            """
            SELECT id, content, content_type, sha256, copied_at
            FROM history
            ORDER BY copied_at DESC
            LIMIT 1 OFFSET ?
            """,
            (rank - 1,),
        ).fetchone()
    return row


def clear_all() -> tuple[int, int]:
    """
    Delete every entry and all image files from IMG_DIR.
    Returns (text_deleted, image_deleted).
    """
    with _get_connection() as conn:
        image_rows = conn.execute(
            "SELECT content FROM history WHERE content_type = 'image'"
        ).fetchall()

        for row in image_rows:
            _delete_image_file(row["content"])

        text_count  = conn.execute(
            "SELECT COUNT(*) FROM history WHERE content_type = 'text'"
        ).fetchone()[0]
        image_count = len(image_rows)

        conn.execute("DELETE FROM history")
        conn.commit()

    # Also nuke the image directory
    if IMG_DIR.exists():
        try:
            shutil.rmtree(IMG_DIR)
        except OSError:
            pass

    return text_count, image_count


def total_count() -> int:
    with _get_connection() as conn:
        return conn.execute("SELECT COUNT(*) FROM history").fetchone()[0]


def count_by_type() -> tuple[int, int]:
    """Returns (text_count, image_count)."""
    with _get_connection() as conn:
        t = conn.execute(
            "SELECT COUNT(*) FROM history WHERE content_type = 'text'"
        ).fetchone()[0]
        i = conn.execute(
            "SELECT COUNT(*) FROM history WHERE content_type = 'image'"
        ).fetchone()[0]
    return t, i
