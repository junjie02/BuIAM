from __future__ import annotations
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.identity.jwt_service import TokenError, issue_token, verify_token
from app.identity.keys import load_system_public_key
from app.store.registry import get_agent
from app.store.tokens import revoke_token, batch_revoke_tokens_by_agent, cleanup_expired_tokens

class TokenIssueRequest(BaseModel):
    agent_id: str
    role: str
    delegated_user: Optional[str] = None
    task_id: Optional[str] = None
    scope: Optional[List[str]] = None
    aud: Optional[str] = None
    source_agent: Optional[str] = None
    target_agent: Optional[str] = None
    client_instance_id: Optional[str] = None
    delegation_depth: int = 0
    ttl_seconds: int = 300

class TokenIntrospectRequest(BaseModel):
    token: str


router = APIRouter(prefix="/identity", tags=["identity"])


@router.post("/tokens")
def create_token(request: TokenIssueRequest, fastapi_request: Request) -> dict:
    agent = get_agent(request.agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail={"error_code": "AGENT_NOT_REGISTERED", "message": "Agent未注册"})
    if agent.status != "active":
        raise HTTPException(status_code=403, detail={"error_code": "AGENT_INACTIVE", "message": "Agent已被禁用"})
    
    # 获取客户端IP地址
    client_ip = fastapi_request.client.host if fastapi_request.client else None
    
    return issue_token(
        agent_id=request.agent_id,
        delegated_user=request.delegated_user,
        task_id=request.task_id,
        scope=request.scope,
        aud=request.aud,
        source_agent=request.source_agent,
        target_agent=request.target_agent,
        source_ip=client_ip,
        client_instance_id=request.client_instance_id,
        delegation_depth=request.delegation_depth,
        ttl_seconds=request.ttl_seconds,
    )


@router.post("/tokens/introspect")
def introspect_token(request: TokenIntrospectRequest) -> dict:
    try:
        auth_context = verify_token(request.token)
        return {
            "active": True,
            "agent_id": auth_context.agent_id,
            "actor_type": auth_context.actor_type,
            "delegated_user": auth_context.delegated_user,
            "capabilities": auth_context.capabilities,
            "exp": auth_context.exp,
            "jti": auth_context.jti,
        }
    except TokenError as error:
        return {"active": False, "error_code": error.error_code, "message": error.message}


@router.get("/public-key")
def get_public_key() -> dict:
    pub_key = load_system_public_key()
    return {"kty": pub_key["kty"], "n": pub_key["n"], "e": pub_key["e"]}


@router.post("/tokens/{jti}/revoke")
def revoke(jti: str, request: TokenRevokeRequest | None = Body(default=None)) -> dict:
    reason = request.reason if request is not None else "manual_revoke"
    revoked, trace_ids = revoke_token_and_credentials(jti, reason=reason)
    if not revoked:
        raise HTTPException(status_code=404, detail={"error_code": "AUTH_TOKEN_INVALID", "message": "令牌不存在"})
    return {"jti": jti, "revoked": True}


@router.post("/agents/{agent_id}/revoke-all-tokens")
def revoke_all_agent_tokens(agent_id: str) -> dict:
    """吊销指定Agent的所有有效令牌"""
    agent = get_agent(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail={"error_code": "AGENT_NOT_REGISTERED", "message": "Agent未注册"})
    
    revoked_count = batch_revoke_tokens_by_agent(agent_id)
    return {
        "agent_id": agent_id,
        "revoked_tokens_count": revoked_count,
        "message": f"已成功吊销{revoked_count}个有效令牌"
    }


@router.post("/tokens/cleanup-expired")
def cleanup_expired() -> dict:
    """手动触发清理过期令牌和黑名单数据"""
    cleaned_count = cleanup_expired_tokens()
    return {
        "cleaned_count": cleaned_count,
        "message": f"已成功清理{cleaned_count}条过期数据"
    }
