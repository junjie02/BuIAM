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
    agent_type: str
    description: str
    owner_org: str
    allowed_resource_domains: frozenset[str]
    status: str
    endpoint: str
    static_capabilities: frozenset[str]
    created_at: str
    updated_at: str
    last_seen_at: str | None


def upsert_agent(
    *,
    agent_id: str,
    name: str,
    agent_type: str,
    description: str,
    owner_org: str,
    allowed_resource_domains: list[str],
    status: str,
    endpoint: str,
    static_capabilities: list[str],
    db_path: Path = DB_PATH,
) -> RegisteredAgent:
    init_schema(db_path)
    capabilities = sorted(set(static_capabilities))
    domains = sorted({domain for domain in allowed_resource_domains if domain})
    now = sqlite3.Date.today().isoformat()
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO agents (
                agent_id,
                name,
                agent_type,
                description,
                owner_org,
                allowed_resource_domains,
                status,
                endpoint,
                static_capabilities,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                name = excluded.name,
                agent_type = excluded.agent_type,
                description = excluded.description,
                owner_org = excluded.owner_org,
                allowed_resource_domains = excluded.allowed_resource_domains,
                status = excluded.status,
                endpoint = excluded.endpoint,
                static_capabilities = excluded.static_capabilities,
                updated_at = excluded.updated_at
            """,
            (
                agent_id,
                name,
                agent_type,
                description,
                owner_org,
                json.dumps(domains, ensure_ascii=False),
                status,
                endpoint,
                json.dumps(capabilities, ensure_ascii=False),
                now,
                now,
            ),
        )
    return RegisteredAgent(
        agent_id=agent_id,
        name=name,
        agent_type=agent_type,
        description=description,
        owner_org=owner_org,
        allowed_resource_domains=frozenset(domains),
        status=status,
        endpoint=endpoint,
        static_capabilities=frozenset(capabilities),
        created_at=now,
        updated_at=now,
        last_seen_at=None,
    )


def get_agent_by_name(name: str, db_path: Path = DB_PATH) -> RegisteredAgent | None:
    return _get_agent("name", name, db_path)


def get_agent(agent_id: str, db_path: Path = DB_PATH) -> RegisteredAgent | None:
    return _get_agent("agent_id", agent_id, db_path)


def list_agents(db_path: Path = DB_PATH) -> list[RegisteredAgent]:
    init_schema(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute("SELECT * FROM agents ORDER BY agent_id ASC").fetchall()
    return [agent_from_row(row) for row in rows]


def _get_agent(column: str, value: str, db_path: Path) -> RegisteredAgent | None:
    if column not in {"agent_id", "name"}:
        raise ValueError(f"unsupported agent lookup column: {column}")
    init_schema(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(f"SELECT * FROM agents WHERE {column} = ?", (value,)).fetchone()
    return agent_from_row(row) if row is not None else None


def agent_from_row(row: sqlite3.Row) -> RegisteredAgent:
    return RegisteredAgent(
        agent_id=row["agent_id"],
        name=row["name"],
        agent_type=row["agent_type"],
        description=row["description"],
        owner_org=row["owner_org"],
        allowed_resource_domains=frozenset(_decode_domains(row["allowed_resource_domains"])),
        status=row["status"],
        endpoint=row["endpoint"],
        static_capabilities=frozenset(json.loads(row["static_capabilities"])),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_seen_at=row["last_seen_at"],
    )


def _decode_domains(raw: str) -> list[str]:
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        decoded = raw.split(",")
    return [str(domain) for domain in decoded if str(domain)]
