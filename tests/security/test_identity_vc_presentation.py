"""DID + Token + VC end-to-end verification with recomputed hashes.

Covers what ``scripts/security/verify_identity_vc.py`` did, now as pytest.
Use ``pytest -s --json`` to print the full VC presentation.
"""

from __future__ import annotations

import json
import os

import httpx
import pytest

from app.delegation.credential_crypto import compute_credential_id, content_hash, verify_credential_integrity
from app.identity.crypto import b64url_decode
from app.identity.did import build_did
from app.store.did_registry import get_did_document, list_did_documents
from app.store.delegation_credentials import get_credential

from tests.security_helpers import (
    ALL_CAPABILITIES,
    GATEWAY_URL,
    USER_ID,
    find_trace_credential,
    run,
    run_root_task,
)


def _decode_jwt(token: str) -> dict:
    header_part, claims_part, _ = token.split(".")
    return {
        "header": json.loads(b64url_decode(header_part)),
        "claims": json.loads(b64url_decode(claims_part)),
    }


def _vc_presentation(credential_id: str) -> dict:
    cred = get_credential(credential_id)
    assert cred is not None, f"credential {credential_id} not found"
    return {
        "id": cred.credential_id,
        "@context": cred.vc_context,
        "type": cred.vc_type,
        "issuer": cred.issuer_did,
        "issuanceDate": cred.iat,
        "expirationDate": cred.exp,
        "credentialSubject": cred.credential_subject,
        "buiamSecurity": {
            "parent_credential_id": cred.parent_credential_id,
            "root_credential_id": cred.root_credential_id,
            "trace_id": cred.trace_id,
            "capabilities": cred.capabilities,
            "content_hash": cred.content_hash,
            "recomputed_content_hash": content_hash(cred),
            "recomputed_credential_id": compute_credential_id(cred),
        },
        "proof": {
            "type": cred.signature_alg,
            "verificationMethod": cred.proof_verification_method,
            "proofValue": cred.proof_signature,
        },
        "verification": {
            "content_hash_valid": content_hash(cred) == cred.content_hash,
            "credential_id_valid": compute_credential_id(cred) == cred.credential_id,
            "signature_valid": verify_credential_integrity(cred),
        },
    }


def test_identity_vc_full_presentation(servers, request) -> None:
    """End-to-end: DID docs, JWT decode, introspection, VC signatures, recomputed hashes."""
    result = run(run_root_task("doc_agent", "generate_report", ALL_CAPABILITIES))
    jwt_token = result["token"]  # raw JWT string
    jti = result["token_jti"]
    credential_id = result["credential_id"]
    trace = result["trace"]

    # 1. Decode JWT
    decoded = _decode_jwt(jwt_token)
    kid = decoded["header"].get("kid", "")
    assert kid, "JWT header missing kid"
    assert "#" in kid, f"kid should be DID-style: {kid}"
    assert decoded["claims"]["sub"] == USER_ID

    # 2. Token introspection
    r = httpx.post(
        f"{GATEWAY_URL}/identity/tokens/introspect",
        json={"token": jwt_token},
        timeout=10,
    )
    assert r.status_code == 200
    intro = r.json()
    assert intro["active"] is True
    assert intro["credential_id"] == credential_id

    # 3. DID documents registered for all demo entities
    for subj in ["user_123", "doc_agent", "enterprise_data_agent", "external_search_agent"]:
        doc = get_did_document(build_did(subj))
        assert doc is not None, f"DID document missing for {subj}"
    assert len(list_did_documents()) >= 4

    # 4. At least root + child VC
    credentials = trace["delegation_credentials"]
    assert len(credentials) >= 2, f"expected >=2 VCs, got {len(credentials)}"

    # 5. Root VC signature + hash verification
    root_vc = _vc_presentation(credential_id)
    assert root_vc["verification"]["signature_valid"], "root VC signature invalid"
    assert root_vc["verification"]["content_hash_valid"], "root VC content hash mismatch"
    assert root_vc["verification"]["credential_id_valid"], "root VC credential_id mismatch"

    # 6. Child VC (doc_agent) signature + hash verification
    doc_cred = find_trace_credential(trace, subject_id="doc_agent")
    child_vc = _vc_presentation(doc_cred["credential_id"])
    assert child_vc["verification"]["signature_valid"], "child VC signature invalid"
    assert child_vc["verification"]["content_hash_valid"], "child VC content hash mismatch"
    assert child_vc["verification"]["credential_id_valid"], "child VC credential_id mismatch"

    # 7. Full JSON output when requested (pytest -s --json)
    if request.config.getoption("--json", default=False):
        print(json.dumps({
            "did_documents": [list_did_documents()],
            "token": {"jti": jti, "exp": decoded["claims"]["exp"],
                       "credential_id": credential_id,
                       "header": decoded["header"], "claims": decoded["claims"],
                       "introspection": intro},
            "root_vc": root_vc,
            "delegated_vc": child_vc,
            "trace_id": result["trace_id"],
            "verification_flow": [
                "1. Register demo DID documents for all entities.",
                "2. Issue user Bearer token via /identity/tokens.",
                "3. Decode JWT header/claims; verify kid points at subject DID.",
                "4. Introspect token; verify active + bound to root credential.",
                "5. Execute POST /a2a/root-tasks → child VC for doc_agent.",
                "6. Recompute content_hash and credential_id from canonical content.",
                "7. Verify every VC proof signature via issuer DID.",
            ],
            "all_passed": True,
        }, ensure_ascii=False, indent=2))
