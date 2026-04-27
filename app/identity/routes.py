from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

from app.identity.jwt_service import TokenError, issue_token, verify_token
from app.identity.keys import load_public_key
from app.protocol import TokenIssueRequest, TokenRevokeRequest
from app.registry.bootstrap import USER_ID
from app.runtime.tasks import cancel_traces
from app.store.registry import get_agent
from app.store.tokens import revoke_token_and_credentials


class TokenIntrospectRequest(BaseModel):
    token: str


router = APIRouter(prefix="/identity", tags=["identity"])


@router.post("/tokens")
def create_token(request: TokenIssueRequest) -> dict:
    if request.actor_type == "agent":
        agent = get_agent(request.agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail={"error_code": "AGENT_NOT_REGISTERED"})
        if agent.status != "active":
            raise HTTPException(status_code=403, detail={"error_code": "AGENT_INACTIVE"})

    delegated_user = request.delegated_user or USER_ID
    return issue_token(
        agent_id=request.agent_id,
        delegated_user=delegated_user,
        capabilities=request.capabilities,
        user_capabilities=request.user_capabilities or request.capabilities,
        actor_type=request.actor_type,
        ttl_seconds=request.ttl_seconds,
    )


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
