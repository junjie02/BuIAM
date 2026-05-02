from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

from app.identity.did import build_did
from app.identity.did_proof import verify_did_proof
from app.identity.jwt_service import TokenError, issue_token, verify_token
from app.identity.keys import load_public_key
from app.protocol import TokenIssueRequest, TokenRevokeRequest
from app.registry.bootstrap import USER_ID
from app.runtime.tasks import cancel_traces
from app.store.did_registry import get_did_document, upsert_did_document
from app.store.registry import get_agent
from app.store.tokens import revoke_token_and_credentials

logger = logging.getLogger("buiam.identity.routes")


class TokenIntrospectRequest(BaseModel):
    token: str


class DidRegisterRequest(BaseModel):
    did_document: dict
    proof: dict


router = APIRouter(prefix="/identity", tags=["identity"])

_DID_RE = re.compile(r"^did:buiam:[a-zA-Z0-9._\-]+$")


@router.post("/tokens")
def create_token(request: TokenIssueRequest) -> dict:
    if request.actor_type == "agent":
        agent = get_agent(request.agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail={"error_code": "AGENT_NOT_REGISTERED"})
        if agent.status != "active":
            raise HTTPException(status_code=403, detail={"error_code": "AGENT_INACTIVE"})

    # DID Document must be registered before token issuance (identity whitelist check)
    did = build_did(request.agent_id)
    if get_did_document(did) is None:
        raise HTTPException(status_code=400, detail={"error_code": "AGENT_DID_NOT_REGISTERED"})

    delegated_user = request.delegated_user or USER_ID
    return issue_token(
        agent_id=request.agent_id,
        delegated_user=delegated_user,
        capabilities=request.capabilities,
        user_capabilities=request.user_capabilities or request.capabilities,
        actor_type=request.actor_type,
        ttl_seconds=request.ttl_seconds,
    )


@router.post("/did-register")
def register_did(request: DidRegisterRequest) -> dict:
    did_document = request.did_document
    proof = request.proof

    # 1. Validate DID format
    did = did_document.get("id", "")
    if not did or not isinstance(did, str):
        raise HTTPException(status_code=400, detail={"error_code": "DID_MISSING_ID"})
    if not _DID_RE.match(did):
        raise HTTPException(status_code=400, detail={"error_code": "DID_INVALID_FORMAT"})

    # 2. Validate verificationMethod
    vms = did_document.get("verificationMethod", [])
    if not vms or not isinstance(vms, list):
        raise HTTPException(status_code=400, detail={"error_code": "DID_MISSING_VERIFICATION_METHOD"})
    vm = vms[0]
    if not isinstance(vm, dict):
        raise HTTPException(status_code=400, detail={"error_code": "DID_INVALID_VERIFICATION_METHOD"})
    vm_id = vm.get("id", "")
    if not vm_id or not isinstance(vm_id, str):
        raise HTTPException(status_code=400, detail={"error_code": "DID_MISSING_VM_ID"})
    jwk = vm.get("publicKeyJwk")
    if not jwk or not isinstance(jwk, dict):
        raise HTTPException(status_code=400, detail={"error_code": "DID_MISSING_JWK"})

    # 3. Validate JWK completeness
    kty = jwk.get("kty", "")
    if kty == "ML-DSA":
        if not jwk.get("pk") or not jwk.get("alg"):
            raise HTTPException(status_code=400, detail={"error_code": "DID_INCOMPLETE_MLDSA_JWK"})
    elif kty == "RSA":
        if not jwk.get("n") or not jwk.get("e"):
            raise HTTPException(status_code=400, detail={"error_code": "DID_INCOMPLETE_RSA_JWK"})
    else:
        raise HTTPException(status_code=400, detail={"error_code": "DID_UNSUPPORTED_KEY_TYPE"})

    # 4. Verify proof signature (self-proof, using key from the document itself)
    if not verify_did_proof(did_document, proof):
        raise HTTPException(status_code=400, detail={"error_code": "DID_PROOF_INVALID"})

    # 5. Check for duplicate
    subject_id = did_document.get("metadata", {}).get("subject_id", "")
    if get_did_document(did) is not None:
        raise HTTPException(status_code=409, detail={"error_code": "DID_ALREADY_REGISTERED"})

    # 6. Store
    upsert_did_document(did=did, subject_id=subject_id, document=did_document)
    logger.info("DID registered: %s (subject: %s)", did, subject_id)
    return {"did": did, "subject_id": subject_id, "status": "registered"}


@router.post("/tokens/introspect")
def introspect_token(request: TokenIntrospectRequest) -> dict:
    try:
        auth_context = verify_token(request.token)
    except TokenError as error:
        return {"active": False, "error_code": error.error_code, "message": error.message}
    return {
        "active": True,
        "agent_id": auth_context.agent_id,
        "actor_type": auth_context.actor_type,
        "delegated_user": auth_context.delegated_user,
        "capabilities": auth_context.capabilities,
        "user_capabilities": auth_context.user_capabilities,
        "exp": auth_context.exp,
        "jti": auth_context.jti,
        "credential_id": auth_context.credential_id,
        "root_credential_id": auth_context.root_credential_id,
    }


@router.get("/public-key/{key_id}")
def get_public_key(key_id: str) -> dict:
    public_key = load_public_key(key_id)
    return {"kid": key_id, "kty": public_key["kty"], "n": public_key["n"], "e": public_key["e"]}


@router.post("/tokens/{jti}/revoke")
def revoke(jti: str, request: TokenRevokeRequest | None = Body(default=None)) -> dict:
    reason = request.reason if request is not None else "manual_revoke"
    revoked, trace_ids = revoke_token_and_credentials(jti, reason=reason)
    if not revoked:
        raise HTTPException(status_code=404, detail={"error_code": "AUTH_TOKEN_INVALID"})
    return {
        "jti": jti,
        "revoked": True,
        "trace_ids": trace_ids,
        "cancelled_tasks": cancel_traces(trace_ids, "token_revoked"),
    }
