from __future__ import annotations
import time
import json
import sqlite3
from pathlib import Path
from typing import Any

from app.protocol import AuditLog, DelegationDecision, DelegationEnvelope, DelegationHop
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

    should_append_current_hop = not (
        decision.decision == "allow"
        and envelope.delegation_chain
        and envelope.delegation_chain[-1].decision == "root"
        and envelope.caller_agent_id == envelope.delegation_chain[-1].from_actor
    )

    full_chain = list(envelope.delegation_chain)

    if should_append_current_hop:
        current_hop = delegation_decision_hop(envelope, decision)

        append_chain_hop(
            trace_id=envelope.trace_id,
            request_id=envelope.request_id,
            hop=current_hop,
            db_path=db_path,
        )

        full_chain.append(current_hop)

    chain_json = json.dumps(
        [hop.model_dump() for hop in full_chain],
        ensure_ascii=False,
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
                chain_json,
                decision_detail_json(envelope, decision),
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


def cleanup_expired_audit_logs(retention_days: int = 30, db_path: Path = DB_PATH) -> int:
    """清理超过保留期的审计日志，默认保留30天"""
    init_db(db_path)
    cutoff_time = time.time() - retention_days * 86400
    cutoff_date = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(cutoff_time))
    
    with sqlite3.connect(db_path) as connection:
        cursor = connection.execute(
            "DELETE FROM audit_logs WHERE created_at < ?",
            (cutoff_date,)
        )
        # 同时清理过期的授权事件和委托链记录
        cursor.execute(
            "DELETE FROM auth_events WHERE created_at < ?",
            (cutoff_date,)
        )
        cursor.execute(
            "DELETE FROM delegation_chain WHERE created_at < ?",
            (cutoff_date,)
        )
    return cursor.rowcount

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


def delegation_decision_hop(
    envelope: DelegationEnvelope,
    decision: DelegationDecision,
) -> DelegationHop:
    return DelegationHop(
        from_actor=envelope.caller_agent_id,
        to_agent_id=envelope.target_agent_id,
        task_type=envelope.task_type,
        delegated_capabilities=decision.effective_capabilities
        if decision.decision == "allow"
        else [],
        missing_capabilities=decision.missing_capabilities,
        decision=decision.decision,
    )


def decision_detail_json(envelope: DelegationEnvelope, decision: DelegationDecision) -> str:
    detail = decision.to_detail().model_dump()
    detail["auth_event_recorded"] = envelope.auth_context is not None
    detail["token_jti"] = envelope.auth_context.jti if envelope.auth_context is not None else None
    detail["token_agent_id"] = (
        envelope.auth_context.agent_id if envelope.auth_context is not None else None
    )
    detail["credential_id"] = (
        envelope.auth_context.credential_id if envelope.auth_context is not None else None
    )
    detail["parent_credential_id"] = (
        envelope.auth_context.parent_credential_id if envelope.auth_context is not None else None
    )
    detail["root_credential_id"] = (
        envelope.auth_context.root_credential_id if envelope.auth_context is not None else None
    )
    return json.dumps(detail, ensure_ascii=False)
