from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from app.store.schema import DB_PATH, init_schema


def upsert_did_document(*, did: str, subject_id: str, document: dict[str, Any], db_path: Path = DB_PATH) -> None:
    init_schema(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO did_documents (did, subject_id, document_json)
            VALUES (?, ?, ?)
            ON CONFLICT(did) DO UPDATE SET
                subject_id = excluded.subject_id,
                document_json = excluded.document_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            (did, subject_id, json.dumps(document, ensure_ascii=False)),
        )


def get_did_document(did: str, db_path: Path = DB_PATH) -> dict[str, Any] | None:
    init_schema(db_path)
    with sqlite3.connect(db_path) as connection:
        row = connection.execute("SELECT document_json FROM did_documents WHERE did = ?", (did,)).fetchone()
    if row is None:
        return None
    return json.loads(str(row[0]))


def list_did_documents(db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    init_schema(db_path)
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute("SELECT document_json FROM did_documents ORDER BY did ASC").fetchall()
    return [json.loads(str(row[0])) for row in rows]
