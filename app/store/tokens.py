from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from app.store.schema import DB_PATH, init_schema


@dataclass(frozen=True)
class StoredToken:
    jti: str
    sub: str
    agent_id: str
    delegated_user: str
    capabilities: list[str]
    exp: int
    revoked: bool


def store_token(
    *,
    jti: str,
    sub: str,
    agent_id: str,
    delegated_user: str,
    capabilities: list[str],
    exp: int,
    db_path: Path = DB_PATH,
) -> None:
    init_schema(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO tokens
            (jti, sub, agent_id, delegated_user, capabilities, exp, revoked)
            VALUES (?, ?, ?, ?, ?, ?, COALESCE((SELECT revoked FROM tokens WHERE jti = ?), 0))
            """,
            (jti, sub, agent_id, delegated_user, json.dumps(capabilities), exp, jti),
        )


def get_token(jti: str, db_path: Path = DB_PATH) -> StoredToken | None:
    init_schema(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM tokens WHERE jti = ?", (jti,)).fetchone()
    if row is None:
        return None
    return StoredToken(
        jti=row["jti"],
        sub=row["sub"],
        agent_id=row["agent_id"],
        delegated_user=row["delegated_user"],
        capabilities=json.loads(row["capabilities"]),
        exp=int(row["exp"]),
        revoked=bool(row["revoked"]),
    )


def revoke_token(jti: str, db_path: Path = DB_PATH) -> bool:
    init_schema(db_path)
    with sqlite3.connect(db_path) as connection:
        cursor = connection.execute("UPDATE tokens SET revoked = 1 WHERE jti = ?", (jti,))
    return cursor.rowcount > 0


def mark_jti_seen(jti: str, db_path: Path = DB_PATH) -> None:
    init_schema(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("INSERT OR IGNORE INTO jti_seen (jti) VALUES (?)", (jti,))
