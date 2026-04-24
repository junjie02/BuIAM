from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from app.protocol import AuditLog, DelegationDecision, DelegationEnvelope, DelegationHop


DB_PATH = Path("data/audit.db")


def init_db(db_path: Path = DB_PATH) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trace_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                caller_agent_id TEXT NOT NULL,
                target_agent_id TEXT NOT NULL,
                requested_capabilities TEXT NOT NULL,
                effective_capabilities TEXT NOT NULL,
                decision TEXT NOT NULL,
                reason TEXT NOT NULL,
                delegation_chain TEXT NOT NULL,
                decision_detail TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(audit_logs)").fetchall()
        }
        if "decision_detail" not in columns:
            connection.execute(
                "ALTER TABLE audit_logs ADD COLUMN decision_detail TEXT NOT NULL DEFAULT '{}'"
            )


def record_decision(
    envelope: DelegationEnvelope,
    decision: DelegationDecision,
    db_path: Path = DB_PATH,
) -> None:
    init_db(db_path)
    decision_chain = [
        *envelope.delegation_chain,
        DelegationHop(
            from_actor=envelope.caller_agent_id,
            to_agent_id=envelope.target_agent_id,
            task_type=envelope.task_type,
            delegated_capabilities=decision.effective_capabilities
            if decision.decision == "allow"
            else [],
            missing_capabilities=decision.missing_capabilities,
            decision=decision.decision,
        ),
    ]
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO audit_logs (
                trace_id,
                request_id,
                caller_agent_id,
                target_agent_id,
                requested_capabilities,
                effective_capabilities,
                decision,
                reason,
                delegation_chain,
                decision_detail
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                envelope.trace_id,
                envelope.request_id,
                envelope.caller_agent_id,
                envelope.target_agent_id,
                json.dumps(envelope.requested_capabilities, ensure_ascii=False),
                json.dumps(decision.effective_capabilities, ensure_ascii=False),
                decision.decision,
                decision.reason,
                json.dumps(
                    [hop.model_dump() for hop in decision_chain],
                    ensure_ascii=False,
                ),
                decision.to_detail().model_dump_json(),
            ),
        )


def list_logs(db_path: Path = DB_PATH, trace_id: str | None = None) -> list[AuditLog]:
    init_db(db_path)
    query = "SELECT * FROM audit_logs"
    params: tuple[Any, ...] = ()
    if trace_id is not None:
        query += " WHERE trace_id = ?"
        params = (trace_id,)
    query += " ORDER BY id ASC"

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(query, params).fetchall()

    return [
        AuditLog(
            id=row["id"],
            trace_id=row["trace_id"],
            request_id=row["request_id"],
            caller_agent_id=row["caller_agent_id"],
            target_agent_id=row["target_agent_id"],
            requested_capabilities=json.loads(row["requested_capabilities"]),
            effective_capabilities=json.loads(row["effective_capabilities"]),
            decision=row["decision"],
            reason=row["reason"],
            delegation_chain=json.loads(row["delegation_chain"]),
            decision_detail=json.loads(row["decision_detail"] or "{}"),
            created_at=row["created_at"],
        )
        for row in rows
    ]
