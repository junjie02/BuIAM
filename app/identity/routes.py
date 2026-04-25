from __future__ import annotations
from typing import List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.identity.jwt_service import issue_token, verify_token, TokenError
from app.identity.keys import load_system_public_key
from app.store.registry import get_agent
from app.store.tokens import revoke_token

class TokenIssueRequest(BaseModel):
    agent_id: str
    role: str
    delegated_user: Optional[str] = None
    task_id: Optional[str] = None
    scope: Optional[List[str]] = None
    aud: Optional[str] = None
    source_agent: Optional[str] = None
    target_agent: Optional[str] = None
    delegation_depth: int = 0
    ttl_seconds: int = 300

class TokenIntrospectRequest(BaseModel):
    token: str


router = APIRouter(prefix="/identity", tags=["identity"])


@router.post("/tokens")
def create_token(request: TokenIssueRequest) -> dict:
<<<<<<< HEAD
    agent = get_agent(request.agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail={"error_code": "AGENT_NOT_REGISTERED", "message": "Agent未注册"})
    if agent.status != "active":
        raise HTTPException(status_code=403, detail={"error_code": "AGENT_INACTIVE", "message": "Agent已被禁用"})
    
=======
    if request.actor_type == "agent" and get_agent(request.agent_id) is None:
        raise HTTPException(status_code=404, detail={"error_code": "AGENT_NOT_REGISTERED"})
>>>>>>> 1ceabae7d5b79f5a379e7b9938e6ea923b641840
    return issue_token(
        agent_id=request.agent_id,
        role=request.role,
        delegated_user=request.delegated_user,
<<<<<<< HEAD
        task_id=request.task_id,
        scope=request.scope,
        aud=request.aud,
        source_agent=request.source_agent,
        target_agent=request.target_agent,
        delegation_depth=request.delegation_depth,
=======
        capabilities=request.capabilities,
        actor_type=request.actor_type,
>>>>>>> 1ceabae7d5b79f5a379e7b9938e6ea923b641840
        ttl_seconds=request.ttl_seconds,
    )


@router.post("/tokens/introspect")
def introspect_token(request: TokenIntrospectRequest) -> dict:
    try:
        auth_context = verify_token(request.token)
        return {
            "active": True,
            "agent_id": auth_context.agent_id,
            "role": auth_context.role,
            "delegated_user": auth_context.delegated_user,
            "task_id": auth_context.task_id,
            "scope": auth_context.scope,
            "source_agent": auth_context.source_agent,
            "target_agent": auth_context.target_agent,
            "delegation_depth": auth_context.delegation_depth,
            "exp": auth_context.exp,
            "jti": auth_context.jti
        }
    except TokenError as e:
        return {
            "active": False,
            "error_code": e.error_code,
            "message": e.message
        }


@router.get("/public-key")
def get_public_key() -> dict:
    pub_key = load_system_public_key()
    return {
        "kty": pub_key["kty"],
        "n": pub_key["n"],
        "e": pub_key["e"]
    }


@router.post("/tokens/{jti}/revoke")
def revoke(jti: str) -> dict:
    revoked = revoke_token(jti)
    if not revoked:
        raise HTTPException(status_code=404, detail={"error_code": "AUTH_TOKEN_INVALID", "message": "令牌不存在"})
    return {"jti": jti, "revoked": True}
