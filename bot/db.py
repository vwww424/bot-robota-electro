"""SQLite-сховище. Дані кожного користувача ізольовані по його Telegram user_id."""

import sqlite3
from pathlib import Path

from .phases import DEFAULT_PHASES

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    current_phase INTEGER
);
CREATE TABLE IF NOT EXISTS objects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    address TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS objects_user ON objects(user_id);
CREATE TABLE IF NOT EXISTS phases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    object_id INTEGER NOT NULL REFERENCES objects(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    kind TEXT,
    position INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'todo'
);
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phase_id INTEGER NOT NULL REFERENCES phases(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phase_id INTEGER NOT NULL REFERENCES phases(id) ON DELETE CASCADE,
    text TEXT,
    voice_file_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phase_id INTEGER NOT NULL REFERENCES phases(id) ON DELETE CASCADE,
    file_id TEXT NOT NULL,
    caption TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

# Join used to make sure a phase belongs to the requesting user.
_PHASE_OWNED = """
SELECT p.*, o.name AS object_name, o.user_id
FROM phases p JOIN objects o ON o.id = p.object_id
WHERE p.id = ? AND o.user_id = ?
"""


class DB:
    def __init__(self, path: str | Path):
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def _q(self, sql: str, *args) -> list[sqlite3.Row]:
        return self.conn.execute(sql, args).fetchall()

    def _one(self, sql: str, *args) -> sqlite3.Row | None:
        return self.conn.execute(sql, args).fetchone()

    def _do(self, sql: str, *args) -> int:
        cur = self.conn.execute(sql, args)
        self.conn.commit()
        return cur.lastrowid

    # --- users ---
    def set_current_phase(self, user_id: int, phase_id: int | None) -> None:
        self._do(
            "INSERT INTO users(user_id, current_phase) VALUES(?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET current_phase = excluded.current_phase",
            user_id, phase_id,
        )

    def get_current_phase(self, user_id: int) -> sqlite3.Row | None:
        row = self._one("SELECT current_phase FROM users WHERE user_id = ?", user_id)
        if not row or row["current_phase"] is None:
            return None
        return self.get_phase(user_id, row["current_phase"])

    # --- objects ---
    def add_object(self, user_id: int, name: str, address: str | None = None) -> int:
        obj_id = self._do(
            "INSERT INTO objects(user_id, name, address) VALUES(?, ?, ?)", user_id, name, address
        )
        for pos, (kind, phase_name) in enumerate(DEFAULT_PHASES):
            self.conn.execute(
                "INSERT INTO phases(object_id, name, kind, position) VALUES(?, ?, ?, ?)",
                (obj_id, phase_name, kind, pos),
            )
        self.conn.commit()
        return obj_id

    def list_objects(self, user_id: int) -> list[sqlite3.Row]:
        # current_phase_name = first unfinished phase, NULL when everything is done.
        return self._q(
            """
            SELECT o.*,
              (SELECT p.name FROM phases p WHERE p.object_id = o.id AND p.status != 'done'
               ORDER BY p.position LIMIT 1) AS current_phase_name,
              (SELECT COUNT(*) FROM phases p WHERE p.object_id = o.id) AS phase_count
            FROM objects o WHERE o.user_id = ? ORDER BY o.id DESC
            """,
            user_id,
        )

    def get_object(self, user_id: int, obj_id: int) -> sqlite3.Row | None:
        return self._one("SELECT * FROM objects WHERE id = ? AND user_id = ?", obj_id, user_id)

    def delete_object(self, user_id: int, obj_id: int) -> None:
        self._do("DELETE FROM objects WHERE id = ? AND user_id = ?", obj_id, user_id)

    # --- phases ---
    def list_phases(self, obj_id: int) -> list[sqlite3.Row]:
        return self._q(
            """
            SELECT p.*,
              (SELECT COUNT(*) FROM items i WHERE i.phase_id = p.id) AS items_total,
              (SELECT COUNT(*) FROM items i WHERE i.phase_id = p.id AND i.done) AS items_done
            FROM phases p WHERE p.object_id = ? ORDER BY p.position
            """,
            obj_id,
        )

    def get_phase(self, user_id: int, phase_id: int) -> sqlite3.Row | None:
        return self._one(_PHASE_OWNED, phase_id, user_id)

    def add_phase(self, obj_id: int, name: str) -> int:
        row = self._one("SELECT COALESCE(MAX(position), -1) + 1 AS pos FROM phases WHERE object_id = ?", obj_id)
        return self._do(
            "INSERT INTO phases(object_id, name, kind, position) VALUES(?, ?, NULL, ?)",
            obj_id, name, row["pos"],
        )

    def set_phase_status(self, phase_id: int, status: str) -> None:
        self._do("UPDATE phases SET status = ? WHERE id = ?", status, phase_id)

    def mark_started(self, phase_id: int) -> None:
        self._do("UPDATE phases SET status = 'doing' WHERE id = ? AND status = 'todo'", phase_id)

    def delete_phase(self, phase_id: int) -> None:
        self._do("DELETE FROM phases WHERE id = ?", phase_id)

    # --- items ---
    def add_items(self, phase_id: int, lines: list[str]) -> int:
        lines = [line.strip(" \t-•") for line in lines]
        lines = [line for line in lines if line]
        self.conn.executemany(
            "INSERT INTO items(phase_id, text) VALUES(?, ?)", [(phase_id, line) for line in lines]
        )
        self.conn.commit()
        if lines:
            self.mark_started(phase_id)
        return len(lines)

    def list_items(self, phase_id: int) -> list[sqlite3.Row]:
        return self._q("SELECT * FROM items WHERE phase_id = ? ORDER BY id", phase_id)

    def get_item(self, user_id: int, item_id: int) -> sqlite3.Row | None:
        return self._one(
            """
            SELECT i.* FROM items i
            JOIN phases p ON p.id = i.phase_id JOIN objects o ON o.id = p.object_id
            WHERE i.id = ? AND o.user_id = ?
            """,
            item_id, user_id,
        )

    def toggle_item(self, item_id: int) -> None:
        self._do("UPDATE items SET done = 1 - done WHERE id = ?", item_id)

    def delete_item(self, item_id: int) -> None:
        self._do("DELETE FROM items WHERE id = ?", item_id)

    # --- notes & photos ---
    def add_note(self, phase_id: int, text: str | None = None, voice_file_id: str | None = None) -> int:
        note_id = self._do(
            "INSERT INTO notes(phase_id, text, voice_file_id) VALUES(?, ?, ?)", phase_id, text, voice_file_id
        )
        self.mark_started(phase_id)
        return note_id

    def list_notes(self, phase_id: int) -> list[sqlite3.Row]:
        return self._q("SELECT * FROM notes WHERE phase_id = ? ORDER BY id", phase_id)

    def add_photo(self, phase_id: int, file_id: str, caption: str | None = None) -> int:
        photo_id = self._do(
            "INSERT INTO photos(phase_id, file_id, caption) VALUES(?, ?, ?)", phase_id, file_id, caption
        )
        self.mark_started(phase_id)
        return photo_id

    def list_photos(self, phase_id: int) -> list[sqlite3.Row]:
        return self._q("SELECT * FROM photos WHERE phase_id = ? ORDER BY id", phase_id)

    def count(self, table: str, phase_id: int) -> int:
        assert table in ("notes", "photos", "items")
        return self._one(f"SELECT COUNT(*) AS c FROM {table} WHERE phase_id = ?", phase_id)["c"]
