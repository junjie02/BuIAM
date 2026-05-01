from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from app.protocol import DelegationCredential
from app.store.schema import DB_PATH, init_schema


def upsert_credential(credential: DelegationCredential, db_path: Path = DB_PATH) -> None:
    init_schema(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO delegation_credentials (
                credential_id,
                parent_credential_id,
                root_credential_id,
                issuer_id,
                subject_id,
                issuer_did,
                subject_did,
                delegated_user,
                capabilities,
                user_capabilities,
                iat,
                exp,
                trace_id,
                request_id,
                vc_context,
                vc_type,
                credential_subject,
                proof_verification_method,
                proof_signature,
                content_hash,
                signature,
                signature_alg,
                revoked,
                revoked_at,
                revoke_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                credential.credential_id,
                credential.parent_credential_id,
                credential.root_credential_id,
                credential.issuer_id,
                credential.subject_id,
                credential.issuer_did,
                credential.subject_did,
                credential.delegated_user,
                json.dumps(credential.capabilities, ensure_ascii=False),
                json.dumps(credential.user_capabilities, ensure_ascii=False),
                credential.iat,
                credential.exp,
                credential.trace_id,
                credential.request_id,
                json.dumps(credential.vc_context, ensure_ascii=False),
                json.dumps(credential.vc_type, ensure_ascii=False),
                json.dumps(credential.credential_subject, ensure_ascii=False),
                credential.proof_verification_method,
                credential.proof_signature,
                credential.content_hash,
                credential.signature,
                credential.signature_alg,
                1 if credential.revoked else 0,
                credential.revoked_at,
                credential.revoke_reason,
            ),
        )


def get_credential(credential_id: str, db_path: Path = DB_PATH) -> DelegationCredential | None:
    init_schema(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM delegation_credentials WHERE credential_id = ?", (credential_id,)).fetchone()
    return credential_from_row(row) if row is not None else None


def list_credentials(*, trace_id: str | None = None, root_credential_id: str | None = None, db_path: Path = DB_PATH) -> list[DelegationCredential]:
    init_schema(db_path)
    query = "SELECT * FROM delegation_credentials"
    clauses: list[str] = []
    params: list[str] = []
    if trace_id is not None:
        clauses.append("trace_id = ?")
        params.append(trace_id)
    if root_credential_id is not None:
        clauses.append("(root_credential_id = ? OR credential_id = ?)")
        params.extend([root_credential_id, root_credential_id])
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY created_at ASC, rowid ASC"
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(query, tuple(params)).fetchall()
    return [credential_from_row(row) for row in rows]


def revoke_credential_tree(credential_id: str, *, reason: str = "manual_revoke", db_path: Path = DB_PATH) -> tuple[int, list[str]]:
    init_schema(db_path)
    target = get_credential(credential_id, db_path)
    if target is None:
        return 0, []
    credentials = list_credentials(root_credential_id=target.root_credential_id, db_path=db_path)
    by_parent: dict[str | None, list[DelegationCredential]] = {}
    for credential in credentials:
        by_parent.setdefault(credential.parent_credential_id, []).append(credential)

    revoked_ids: set[str] = set()
    stack = [credential_id]
    while stack:
        current_id = stack.pop()
        if current_id in revoked_ids:
            continue
        revoked_ids.add(current_id)
        for child in by_parent.get(current_id, []):
            stack.append(child.credential_id)

    revoked_at = int(time.time())
    with sqlite3.connect(db_path) as connection:
        connection.executemany(
            """
            UPDATE delegation_credentials
            SET revoked = 1, revoked_at = ?, revoke_reason = ?
            WHERE credential_id = ?
            """,
            [(revoked_at, reason, current_id) for current_id in revoked_ids],
        )

    trace_ids = sorted({credential.trace_id for credential in credentials if credential.credential_id in revoked_ids and credential.trace_id})
    return len(revoked_ids), trace_ids


def credential_from_row(row: sqlite3.Row) -> DelegationCredential:
    vc_context_raw = row["vc_context"] if "vc_context" in row.keys() else "[]"
    vc_type_raw = row["vc_type"] if "vc_type" in row.keys() else "[]"
    credential_subject_raw = row["credential_subject"] if "credential_subject" in row.keys() else "{}"
    proof_vm = row["proof_verification_method"] if "proof_verification_method" in row.keys() else None
    proof_sig = row["proof_signature"] if "proof_signature" in row.keys() else None
    issuer_did = row["issuer_did"] if "issuer_did" in row.keys() else None
    subject_did = row["subject_did"] if "subject_did" in row.keys() else None

    return DelegationCredential(
        credential_id=row["credential_id"],
        parent_credential_id=row["parent_credential_id"],
        root_credential_id=row["root_credential_id"],
        issuer_id=row["issuer_id"],
        subject_id=row["subject_id"],
        issuer_did=issuer_did,
        subject_did=subject_did,
        delegated_user=row["delegated_user"],
        capabilities=json.loads(row["capabilities"]),
        user_capabilities=json.loads(row["user_capabilities"]),
        iat=int(row["iat"]),
        exp=int(row["exp"]),
        trace_id=row["trace_id"],
        request_id=row["request_id"],
        vc_context=json.loads(vc_context_raw or "[]"),
        vc_type=json.loads(vc_type_raw or "[]"),
        credential_subject=json.loads(credential_subject_raw or "{}"),
        proof_verification_method=proof_vm,
        proof_signature=proof_sig,
        content_hash=row["content_hash"],
        signature=row["signature"],
        signature_alg=row["signature_alg"],
        revoked=bool(row["revoked"]),
        revoked_at=row["revoked_at"],
        revoke_reason=row["revoke_reason"],
    )
