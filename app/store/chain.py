from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app.protocol import DelegationHop
from app.store.schema import DB_PATH, init_schema


def append_chain_hop(
    *,
    trace_id: str,
    request_id: str,
    hop: DelegationHop,
    db_path: Path = DB_PATH,
) -> None:
    init_schema(db_path)
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT COALESCE(MAX(step_index), -1) + 1 FROM delegation_chain WHERE trace_id = ?",
            (trace_id,),
        ).fetchone()
        step_index = int(row[0])
        connection.execute(
            """
            INSERT INTO delegation_chain (
                trace_id,
                request_id,
                step_index,
                from_actor,
                to_agent_id,
                task_type,
                delegated_capabilities,
                missing_capabilities,
                decision
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trace_id,
                request_id,
                step_index,
                hop.from_actor,
                hop.to_agent_id,
                hop.task_type,
                json.dumps(hop.delegated_capabilities, ensure_ascii=False),
                json.dumps(hop.missing_capabilities, ensure_ascii=False),
                hop.decision,
            ),
    )


def chain_exists(trace_id: str, db_path: Path = DB_PATH) -> bool:
    init_schema(db_path)
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT 1 FROM delegation_chain WHERE trace_id = ? LIMIT 1",
            (trace_id,),
        ).fetchone()
    return row is not None


def append_chain_hops_if_empty(
    *,
    trace_id: str,
    request_id: str,
    hops: list[DelegationHop],
    db_path: Path = DB_PATH,
) -> None:
    if chain_exists(trace_id, db_path):
        return
    for hop in hops:
        append_chain_hop(
            trace_id=trace_id,
            request_id=request_id,
            hop=hop,
            db_path=db_path,
        )


def list_chain(trace_id: str, db_path: Path = DB_PATH) -> list[dict]:
    init_schema(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT * FROM delegation_chain WHERE trace_id = ? ORDER BY step_index ASC",
            (trace_id,),
        ).fetchall()
    return [
        {
            "from_actor": row["from_actor"],
            "to_agent_id": row["to_agent_id"],
            "task_type": row["task_type"],
            "delegated_capabilities": json.loads(row["delegated_capabilities"]),
            "missing_capabilities": json.loads(row["missing_capabilities"]),
            "decision": row["decision"],
        }
        for row in rows
    ]
