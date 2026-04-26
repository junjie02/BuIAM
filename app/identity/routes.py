from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException

from app.identity.jwt_service import issue_token
from app.protocol import TokenIssueRequest, TokenRevokeRequest
from app.runtime.tasks import cancel_traces
from app.store.registry import get_agent
from app.store.tokens import revoke_token_and_credentials


router = APIRouter(prefix="/identity", tags=["identity"])


@router.post("/tokens")
def create_token(request: TokenIssueRequest) -> dict:
    if request.actor_type == "agent" and get_agent(request.agent_id) is None:
        raise HTTPException(status_code=404, detail={"error_code": "AGENT_NOT_REGISTERED"})
    return issue_token(
        agent_id=request.agent_id,
        delegated_user=request.delegated_user,
        capabilities=request.capabilities,
        user_capabilities=request.user_capabilities or None,
        actor_type=request.actor_type,
        ttl_seconds=request.ttl_seconds,
    )


@router.post("/tokens/{jti}/revoke")
def revoke(jti: str, request: TokenRevokeRequest | None = Body(default=None)) -> dict:
    reason = request.reason if request is not None else "manual_revoke"
    revoked, trace_ids = revoke_token_and_credentials(jti, reason=reason)
    if not revoked:
        raise HTTPException(status_code=404, detail={"error_code": "AUTH_TOKEN_INVALID"})
    cancelled_tasks = cancel_traces(trace_ids, "token_revoked")
    return {
        "jti": jti,
        "revoked": True,
        "trace_ids": trace_ids,
        "cancelled_tasks": cancelled_tasks,
    }
