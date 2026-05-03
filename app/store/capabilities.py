"""Master capability list — the authoritative set of known capability names."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.store.schema import DB_PATH, init_schema


def list_capabilities(*, db_path: Path = DB_PATH) -> list[dict]:
    """Return all capabilities with their descriptions."""
    init_schema(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT name, description, created_at FROM capabilities ORDER BY name"
        ).fetchall()
    return [{"name": r["name"], "description": r["description"], "created_at": r["created_at"]} for r in rows]


def capability_names(*, db_path: Path = DB_PATH) -> set[str]:
    """Return just the set of known capability names (for validation)."""
    init_schema(db_path)
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute("SELECT name FROM capabilities").fetchall()
    return {r[0] for r in rows}


def add_capability(name: str, description: str = "", *, db_path: Path = DB_PATH) -> bool:
    """Add a new capability to the master list. Returns True if inserted."""
    init_schema(db_path)
    with sqlite3.connect(db_path) as connection:
        try:
            connection.execute(
                "INSERT INTO capabilities (name, description) VALUES (?, ?)",
                (name, description),
            )
            return True
        except sqlite3.IntegrityError:
            return False


def remove_capability(name: str, *, db_path: Path = DB_PATH) -> bool:
    """Remove a capability from the master list. Returns True if deleted."""
    init_schema(db_path)
    with sqlite3.connect(db_path) as connection:
        cursor = connection.execute("DELETE FROM capabilities WHERE name = ?", (name,))
    return cursor.rowcount > 0
