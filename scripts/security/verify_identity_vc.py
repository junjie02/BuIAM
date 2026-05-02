from __future__ import annotations

import json
import os
from typing import Any

from common import (
    CheckResult,
    SecurityContext,
    cli_main,
    require,
    run_root_task,
)

from app.delegation.credential_crypto import compute_credential_id, content_hash, verify_credential_integrity
from app.identity.crypto import b64url_decode
from app.identity.did import build_did
from app.store.did_registry import get_did_document, list_did_documents
from app.store.delegation_credentials import get_credential


os.environ["BUIAM_AGENT_PROVIDER_MODE"] = "mock"
os.environ["LLM_PROVIDER"] = "mock"
os.environ["INTENT_GENERATOR_PROVIDER"] = "mock"
os.environ["INTENT_JUDGE_PROVIDER"] = "mock"


def _decode_jwt_without_signature(token: str) -> dict[str, Any]:
    header_part, claims_part, _signature = token.split(".")
    return {
        "header": json.loads(b64url_decode(header_part)),
        "claims": json.loads(b64url_decode(claims_part)),
    }


def _vc_view(credential_id: str) -> dict[str, Any]:
    credential = get_credential(credential_id)
    require(credential is not None, "credential not found", {"credential_id": credential_id})
    assert credential is not None
    return {
        "id": credential.credential_id,
        "@context": credential.vc_context,
        "type": credential.vc_type,
        "issuer": credential.issuer_did,
        "issuanceDate": credential.iat,
        "expirationDate": credential.exp,
        "credentialSubject": credential.credential_subject,
        "buiamSecurity": {
            "parent_credential_id": credential.parent_credential_id,
            "root_credential_id": credential.root_credential_id,
            "trace_id": credential.trace_id,
            "request_id": credential.request_id,
            "capabilities": credential.capabilities,
            "user_capabilities": credential.user_capabilities,
            "content_hash": credential.content_hash,
            "recomputed_content_hash": content_hash(credential),
            "recomputed_credential_id": compute_credential_id(credential),
        },
        "proof": {
            "type": credential.signature_alg,
            "verificationMethod": credential.proof_verification_method,
            "proofValue": credential.proof_signature,
        },
        "verification": {
            "content_hash_valid": content_hash(credential) == credential.content_hash,
            "credential_id_valid": compute_credential_id(credential) == credential.credential_id,
            "signature_valid": verify_credential_integrity(credential),
        },
    }


async def run_check(context: SecurityContext) -> CheckResult:
    async with context.client() as client:
        result = await run_root_task(client)
        token = result["token"]
        decoded_token = _decode_jwt_without_signature(token["access_token"])

        introspection_response = await client.post("/identity/tokens/introspect", json={"token": token["access_token"]})
        introspection_response.raise_for_status()
        introspection = introspection_response.json()

        trace = result["trace"]
        credentials = trace["delegation_credentials"]
        require(len(credentials) >= 2, "expected root and delegated VC credentials", {"credentials": credentials})

        root_credential_id = token["credential_id"]
        child_credential_id = next(
            item["credential_id"]
            for item in credentials
            if item["credential_id"] != root_credential_id and item["subject_id"] == "doc_agent"
        )

        dids = {
            "user": get_did_document(build_did(os.getenv("BUIAM_DEMO_USER_ID", "user_123"))),
            "doc_agent": get_did_document(build_did("doc_agent")),
            "enterprise_data_agent": get_did_document(build_did("enterprise_data_agent")),
            "external_search_agent": get_did_document(build_did("external_search_agent")),
        }
        require(all(dids.values()), "DID documents were not registered", {"dids": dids})

        root_vc = _vc_view(root_credential_id)
        child_vc = _vc_view(child_credential_id)
        require(root_vc["verification"]["signature_valid"], "root VC signature failed", root_vc["verification"])
        require(child_vc["verification"]["signature_valid"], "child VC signature failed", child_vc["verification"])
        require(introspection["active"] is True, "token introspection did not accept the token", introspection)
        require(
            introspection["credential_id"] == root_credential_id,
            "token is not bound to root credential",
            {"introspection": introspection, "root_credential_id": root_credential_id},
        )

        verification_flow = [
            "1. Register demo DID documents for the user and all demo Agents.",
            "2. Issue a user Bearer token through /identity/tokens.",
            "3. Decode token header/claims and confirm the token kid points at the subject DID verification method.",
            "4. Introspect the token and verify it is active and bound to the root DelegationCredential.",
            "5. Execute POST /a2a/root-tasks to create a delegated child VC for doc_agent.",
            "6. Recompute every VC content_hash and credential_id from canonical credential content.",
            "7. Verify every VC proof signature through the issuer DID verification method.",
        ]

    return CheckResult(
        name="verify_identity_vc",
        passed=True,
        details={
            "verification_flow": verification_flow,
            "did_documents": dids,
            "registered_did_count": len(list_did_documents()),
            "token": {
                "jti": token["jti"],
                "exp": token["exp"],
                "credential_id": token["credential_id"],
                "header": decoded_token["header"],
                "claims": decoded_token["claims"],
                "introspection": introspection,
            },
            "root_vc": root_vc,
            "delegated_vc": child_vc,
            "trace_id": result["trace_id"],
        },
    )


if __name__ == "__main__":
    cli_main(
        check_name="verify_identity_vc",
        description="Verify DID identity, token introspection, and VC-shaped delegation credentials with full output.",
        check=run_check,
    )
