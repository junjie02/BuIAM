from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.identity.jwt_service import issue_token
from app.protocol import TokenIssueRequest
from app.store.registry import get_agent
from app.store.tokens import revoke_token


router = APIRouter(prefix="/identity", tags=["identity"])


@router.post("/tokens")
def create_token(request: TokenIssueRequest) -> dict:
    if get_agent(request.agent_id) is None:
        raise HTTPException(status_code=404, detail={"error_code": "AGENT_NOT_REGISTERED"})
    return issue_token(
        agent_id=request.agent_id,
        delegated_user=request.delegated_user,
        capabilities=request.capabilities,
        ttl_seconds=request.ttl_seconds,
    )


@router.post("/tokens/{jti}/revoke")
def revoke(jti: str) -> dict:
    revoked = revoke_token(jti)
    if not revoked:
        raise HTTPException(status_code=404, detail={"error_code": "AUTH_TOKEN_INVALID"})
    return {"jti": jti, "revoked": True}
