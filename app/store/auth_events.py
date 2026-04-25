from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from app.protocol import AuthEvent
from app.store.schema import DB_PATH, init_schema


def bool_to_db(value: bool | None) -> int | None:
    if value is None:
        return None
    return 1 if value else 0


def db_to_bool(value: int | None) -> bool | None:
    if value is None:
        return None
    return bool(value)


def record_auth_event(
    *,
    trace_id: str,
    request_id: str,
    caller_agent_id: str | None = None,
    claimed_agent_id: str | None = None,
    token_jti: str | None = None,
    token_sub: str | None = None,
    token_agent_id: str | None = None,
    delegated_user: str | None = None,
    token_fingerprint: str | None = None,
    token_issued_at: int | None = None,
    token_expires_at: int | None = None,
    verified_at: int,
    is_expired: bool | None = None,
    is_revoked: bool | None = None,
    is_jti_registered: bool | None = None,
    signature_valid: bool | None = None,
    issuer_valid: bool | None = None,
    audience_valid: bool | None = None,
    identity_decision: str,
    error_code: str | None = None,
    reason: str,
    db_path: Path = DB_PATH,
) -> None:
    init_schema(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO auth_events (
                trace_id,
                request_id,
                caller_agent_id,
                claimed_agent_id,
                token_jti,
                token_sub,
                token_agent_id,
                delegated_user,
                token_fingerprint,
                token_issued_at,
                token_expires_at,
                verified_at,
                is_expired,
                is_revoked,
                is_jti_registered,
                signature_valid,
                issuer_valid,
                audience_valid,
                identity_decision,
                error_code,
                reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trace_id,
                request_id,
                caller_agent_id,
                claimed_agent_id,
                token_jti,
                token_sub,
                token_agent_id,
                delegated_user,
                token_fingerprint,
                token_issued_at,
                token_expires_at,
                verified_at,
                bool_to_db(is_expired),
                bool_to_db(is_revoked),
                bool_to_db(is_jti_registered),
                bool_to_db(signature_valid),
                bool_to_db(issuer_valid),
                bool_to_db(audience_valid),
                identity_decision,
                error_code,
                reason,
            ),
        )


def list_auth_events(
    *,
    trace_id: str | None = None,
    request_id: str | None = None,
    jti: str | None = None,
    agent_id: str | None = None,
    decision: str | None = None,
    db_path: Path = DB_PATH,
) -> list[AuthEvent]:
    init_schema(db_path)
    query = "SELECT * FROM auth_events"
    clauses: list[str] = []
    params: list[Any] = []
    if trace_id is not None:
        clauses.append("trace_id = ?")
        params.append(trace_id)
    if request_id is not None:
        clauses.append("request_id = ?")
        params.append(request_id)
    if jti is not None:
        clauses.append("token_jti = ?")
        params.append(jti)
    if agent_id is not None:
        clauses.append("token_agent_id = ?")
        params.append(agent_id)
    if decision is not None:
        clauses.append("identity_decision = ?")
        params.append(decision)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY id ASC"

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(query, tuple(params)).fetchall()

    return [auth_event_from_row(row) for row in rows]


def auth_event_from_row(row: sqlite3.Row) -> AuthEvent:
    return AuthEvent(
        id=row["id"],
        trace_id=row["trace_id"],
        request_id=row["request_id"],
        caller_agent_id=row["caller_agent_id"],
        claimed_agent_id=row["claimed_agent_id"],
        token_jti=row["token_jti"],
        token_sub=row["token_sub"],
        token_agent_id=row["token_agent_id"],
        delegated_user=row["delegated_user"],
        token_fingerprint=row["token_fingerprint"],
        token_issued_at=row["token_issued_at"],
        token_expires_at=row["token_expires_at"],
        verified_at=row["verified_at"],
        is_expired=db_to_bool(row["is_expired"]),
        is_revoked=db_to_bool(row["is_revoked"]),
        is_jti_registered=db_to_bool(row["is_jti_registered"]),
        signature_valid=db_to_bool(row["signature_valid"]),
        issuer_valid=db_to_bool(row["issuer_valid"]),
        audience_valid=db_to_bool(row["audience_valid"]),
        identity_decision=row["identity_decision"],
        error_code=row["error_code"],
        reason=row["reason"],
        created_at=row["created_at"],
    )
