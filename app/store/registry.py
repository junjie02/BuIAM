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
    agent_id: str,
    name: str,
    agent_type: str,
    description: str,
    owner_org: str,
    allowed_resource_domains: str,
    status: str,
    endpoint: str,
    static_capabilities: str,
    db_path: Path = DB_PATH,
) -> RegisteredAgent:
    init_schema(db_path)
    capabilities = sorted(json.loads(static_capabilities))
    domains = sorted(allowed_resource_domains.split(","))
    current_time = sqlite3.Date.today().isoformat()
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO agents (
                agent_id, name, agent_type, description, owner_org, 
                allowed_resource_domains, status, endpoint, static_capabilities,
                created_at, updated_at
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
                updated_at = ?
            """,
            (
                agent_id, name, agent_type, description, owner_org,
                allowed_resource_domains, status, endpoint, json.dumps(capabilities, ensure_ascii=False),
                current_time, current_time, current_time
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
        created_at=current_time,
        updated_at=current_time,
        last_seen_at=None
    )


def get_agent_by_name(name: str, db_path: Path = DB_PATH) -> RegisteredAgent | None:
    init_schema(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM agents WHERE name = ?", (name,)
        ).fetchone()
    if row is None:
        return None
    return RegisteredAgent(
        agent_id=row["agent_id"],
        name=row["name"],
        agent_type=row["agent_type"],
        description=row["description"],
        owner_org=row["owner_org"],
        allowed_resource_domains=frozenset(row["allowed_resource_domains"].split(",")),
        status=row["status"],
        endpoint=row["endpoint"],
        static_capabilities=frozenset(json.loads(row["static_capabilities"])),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_seen_at=row["last_seen_at"]
    )


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
        agent_type=row["agent_type"],
        description=row["description"],
        owner_org=row["owner_org"],
        allowed_resource_domains=frozenset(row["allowed_resource_domains"].split(",")),
        status=row["status"],
        endpoint=row["endpoint"],
        static_capabilities=frozenset(json.loads(row["static_capabilities"])),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_seen_at=row["last_seen_at"]
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
