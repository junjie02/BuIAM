from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from app.protocol import AuditLog, DelegationDecision, DelegationEnvelope
from app.store.chain import append_chain_hop, append_chain_hops_if_empty
from app.store.schema import DB_PATH, init_schema


def init_db(db_path: Path = DB_PATH) -> None:
    init_schema(db_path)


def record_decision(
    envelope: DelegationEnvelope,
    decision: DelegationDecision,
    db_path: Path = DB_PATH,
) -> None:
    init_db(db_path)
    append_chain_hops_if_empty(
        trace_id=envelope.trace_id,
        request_id=envelope.request_id,
        hops=envelope.delegation_chain,
        db_path=db_path,
    )
    append_chain_hop(
        trace_id=envelope.trace_id,
        request_id=envelope.request_id,
        hop=delegation_decision_hop(envelope, decision),
        db_path=db_path,
    )
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
                "[]",
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
            decision_detail=json.loads(row["decision_detail"] or "{}"),
            created_at=row["created_at"],
        )
        for row in rows
    ]


def delegation_decision_hop(envelope: DelegationEnvelope, decision: DelegationDecision):
    from app.protocol import DelegationHop

    return DelegationHop(
        from_actor=envelope.caller_agent_id,
        to_agent_id=envelope.target_agent_id,
        task_type=envelope.task_type,
        delegated_capabilities=decision.effective_capabilities if decision.decision == "allow" else [],
        missing_capabilities=decision.missing_capabilities,
        decision=decision.decision,
    )
