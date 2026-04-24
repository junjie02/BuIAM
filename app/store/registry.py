from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from app.store.schema import DB_PATH, init_schema


@dataclass(frozen=True)
class RegisteredAgent:
    agent_id: str
    name: str
    endpoint: str
    static_capabilities: frozenset[str]


def upsert_agent(
    agent_id: str,
    name: str,
    endpoint: str,
    static_capabilities: list[str] | set[str] | frozenset[str],
    db_path: Path = DB_PATH,
) -> RegisteredAgent:
    init_schema(db_path)
    capabilities = sorted(static_capabilities)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO agents (agent_id, name, endpoint, static_capabilities)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                name = excluded.name,
                endpoint = excluded.endpoint,
                static_capabilities = excluded.static_capabilities
            """,
            (agent_id, name, endpoint, json.dumps(capabilities, ensure_ascii=False)),
        )
    return RegisteredAgent(agent_id, name, endpoint, frozenset(capabilities))


def get_agent(agent_id: str, db_path: Path = DB_PATH) -> RegisteredAgent | None:
    init_schema(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM agents WHERE agent_id = ?", (agent_id,)
        ).fetchone()
    if row is None:
        return None
    return RegisteredAgent(
        agent_id=row["agent_id"],
        name=row["name"],
        endpoint=row["endpoint"],
        static_capabilities=frozenset(json.loads(row["static_capabilities"])),
    )


def list_agents(db_path: Path = DB_PATH) -> list[RegisteredAgent]:
    init_schema(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute("SELECT * FROM agents ORDER BY agent_id ASC").fetchall()
    return [
        RegisteredAgent(
            agent_id=row["agent_id"],
            name=row["name"],
            endpoint=row["endpoint"],
            static_capabilities=frozenset(json.loads(row["static_capabilities"])),
        )
        for row in rows
    ]
