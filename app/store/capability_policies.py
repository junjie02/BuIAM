from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app.store.schema import DB_PATH, init_schema


def get_policy(subject_type: str, agent_type: str, *, db_path: Path = DB_PATH) -> dict | None:
    """Get the capability policy for (subject_type, agent_type).

    Falls back to agent_type='*' if no exact match.
    """
    init_schema(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM capability_policies WHERE subject_type = ? AND agent_type = ?",
            (subject_type, agent_type),
        ).fetchone()
        if row is None and agent_type != "*":
            row = connection.execute(
                "SELECT * FROM capability_policies WHERE subject_type = ? AND agent_type = '*'",
                (subject_type,),
            ).fetchone()
    if row is None:
        return None
    return {
        "subject_type": row["subject_type"],
        "agent_type": row["agent_type"],
        "allowed_capabilities": json.loads(row["allowed_capabilities"]),
    }


def list_policies(*, db_path: Path = DB_PATH) -> list[dict]:
    """List all capability policies."""
    init_schema(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT * FROM capability_policies ORDER BY subject_type, agent_type"
        ).fetchall()
    return [
        {
            "subject_type": r["subject_type"],
            "agent_type": r["agent_type"],
            "allowed_capabilities": json.loads(r["allowed_capabilities"]),
        }
        for r in rows
    ]


def upsert_policy(subject_type: str, agent_type: str, allowed_capabilities: list[str], *, db_path: Path = DB_PATH) -> None:
    """Create or update a capability policy."""
    init_schema(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO capability_policies (subject_type, agent_type, allowed_capabilities, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(subject_type, agent_type) DO UPDATE SET
                allowed_capabilities = excluded.allowed_capabilities,
                updated_at = CURRENT_TIMESTAMP
            """,
            (subject_type, agent_type, json.dumps(sorted(allowed_capabilities), ensure_ascii=False)),
        )


def delete_policy(subject_type: str, agent_type: str, *, db_path: Path = DB_PATH) -> bool:
    """Delete a capability policy. Returns True if a row was deleted."""
    init_schema(db_path)
    with sqlite3.connect(db_path) as connection:
        cursor = connection.execute(
            "DELETE FROM capability_policies WHERE subject_type = ? AND agent_type = ?",
            (subject_type, agent_type),
        )
    return cursor.rowcount > 0
