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
    actor_type: str
    delegated_user: str
    capabilities: list[str]
    user_capabilities: list[str]
    exp: int
    revoked: bool
    credential_id: str | None = None


def store_token(
    *,
    jti: str,
    sub: str,
    agent_id: str,
    actor_type: str,
    delegated_user: str,
    capabilities: list[str],
    user_capabilities: list[str] | None = None,
    exp: int,
    credential_id: str | None = None,
    db_path: Path = DB_PATH,
) -> None:
    init_schema(db_path)
    stored_user_capabilities = capabilities if user_capabilities is None else user_capabilities
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO tokens
            (jti, sub, agent_id, actor_type, delegated_user, capabilities, user_capabilities, exp, revoked, credential_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, COALESCE((SELECT revoked FROM tokens WHERE jti = ?), 0), ?)
            """,
            (
                jti,
                sub,
                agent_id,
                actor_type,
                delegated_user,
                json.dumps(capabilities),
                json.dumps(stored_user_capabilities),
                exp,
                jti,
                credential_id,
            ),
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
        actor_type=row["actor_type"],
        delegated_user=row["delegated_user"],
        capabilities=json.loads(row["capabilities"]),
        user_capabilities=json.loads(row["user_capabilities"]),
        exp=int(row["exp"]),
        revoked=bool(row["revoked"]),
        credential_id=row["credential_id"],
    )


def revoke_token(
    jti: str,
    db_path: Path = DB_PATH,
    *,
    reason: str = "manual_revoke",
) -> bool:
    revoked, _ = revoke_token_and_credentials(jti, db_path=db_path, reason=reason)
    return revoked


def revoke_token_and_credentials(
    jti: str,
    db_path: Path = DB_PATH,
    *,
    reason: str = "manual_revoke",
) -> tuple[bool, list[str]]:
    init_schema(db_path)
    stored = get_token(jti, db_path)
    with sqlite3.connect(db_path) as connection:
        cursor = connection.execute("UPDATE tokens SET revoked = 1 WHERE jti = ?", (jti,))
    trace_ids: list[str] = []
    if stored is not None and stored.credential_id:
        from app.store.delegation_credentials import revoke_credential_tree

        _, trace_ids = revoke_credential_tree(stored.credential_id, reason=reason, db_path=db_path)
    return cursor.rowcount > 0, trace_ids


def mark_jti_seen(jti: str, db_path: Path = DB_PATH) -> None:
    init_schema(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("INSERT OR IGNORE INTO jti_seen (jti) VALUES (?)", (jti,))
