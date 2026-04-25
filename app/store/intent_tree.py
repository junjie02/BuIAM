from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app.intent.crypto import content_hash
from app.protocol import IntentCommitment, IntentNode
from app.store.schema import DB_PATH, init_schema


def upsert_intent_node(
    *,
    node: IntentNode,
    trace_id: str,
    request_id: str,
    root_node_id: str,
    judge_decision: str | None,
    judge_reason: str | None,
    db_path: Path = DB_PATH,
) -> None:
    init_schema(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO intent_tree (
                node_id,
                parent_node_id,
                root_node_id,
                trace_id,
                request_id,
                actor_id,
                actor_type,
                target_agent_id,
                task_type,
                intent,
                description,
                data_refs,
                constraints,
                signature,
                signature_alg,
                content_hash,
                judge_decision,
                judge_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                node.node_id,
                node.parent_node_id,
                root_node_id,
                trace_id,
                request_id,
                node.actor_id,
                node.actor_type,
                node.target_agent_id,
                node.task_type,
                node.intent_commitment.intent,
                node.intent_commitment.description,
                json.dumps(node.intent_commitment.data_refs, ensure_ascii=False),
                json.dumps(node.intent_commitment.constraints, ensure_ascii=False),
                node.signature,
                node.signature_alg,
                content_hash(node),
                judge_decision,
                judge_reason,
            ),
        )


def get_intent_node(node_id: str, db_path: Path = DB_PATH) -> dict | None:
    init_schema(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM intent_tree WHERE node_id = ?", (node_id,)).fetchone()
    return intent_row_to_dict(row) if row is not None else None


def list_intent_tree(trace_id: str, db_path: Path = DB_PATH) -> list[dict]:
    init_schema(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT * FROM intent_tree WHERE trace_id = ? ORDER BY created_at ASC, rowid ASC",
            (trace_id,),
        ).fetchall()
    return [intent_row_to_dict(row) for row in rows]


def intent_row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "node_id": row["node_id"],
        "parent_node_id": row["parent_node_id"],
        "root_node_id": row["root_node_id"],
        "trace_id": row["trace_id"],
        "request_id": row["request_id"],
        "actor_id": row["actor_id"],
        "actor_type": row["actor_type"],
        "target_agent_id": row["target_agent_id"],
        "task_type": row["task_type"],
        "intent_commitment": {
            "intent": row["intent"],
            "description": row["description"],
            "data_refs": json.loads(row["data_refs"]),
            "constraints": json.loads(row["constraints"]),
        },
        "signature": row["signature"],
        "signature_alg": row["signature_alg"],
        "content_hash": row["content_hash"],
        "judge_decision": row["judge_decision"],
        "judge_reason": row["judge_reason"],
        "created_at": row["created_at"],
    }


def row_to_intent_node(row: dict) -> IntentNode:
    return IntentNode(
        node_id=row["node_id"],
        parent_node_id=row["parent_node_id"],
        actor_id=row["actor_id"],
        actor_type=row["actor_type"],
        target_agent_id=row["target_agent_id"],
        task_type=row["task_type"],
        intent_commitment=IntentCommitment.model_validate(row["intent_commitment"]),
        signature=row["signature"],
        signature_alg=row["signature_alg"],
    )
