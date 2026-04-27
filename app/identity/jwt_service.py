from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from uuid import uuid4

from app.delegation.credential_crypto import (
    auth_context_from_credential,
    build_delegation_credential,
    verify_credential_integrity,
)
from app.identity.crypto import b64url_decode, b64url_encode, rsa_sign, rsa_verify
from app.protocol import AuthContext
from app.store.delegation_credentials import get_credential, upsert_credential
from app.store.tokens import get_token, mark_jti_seen, store_token


ISSUER = "buiam.local"
AUDIENCE = "buiam.agents"


class TokenError(Exception):
    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


@dataclass(frozen=True)
class TokenVerificationResult:
    auth_context: AuthContext | None
    error_code: str | None
    message: str
    token_fingerprint: str | None
    token_jti: str | None
    token_sub: str | None
    token_agent_id: str | None
    actor_type: str | None
    delegated_user: str | None
    token_issued_at: int | None
    token_expires_at: int | None
    verified_at: int
    is_expired: bool | None
    is_revoked: bool | None
    is_jti_registered: bool | None
    signature_valid: bool | None
    issuer_valid: bool | None
    audience_valid: bool | None

    @property
    def allowed(self) -> bool:
        return self.auth_context is not None and self.error_code is None


def _b64url(data: bytes) -> str:
    return b64url_encode(data)


def _b64url_decode(data: str) -> bytes:
    return b64url_decode(data)


def _json_b64(payload: dict) -> str:
    return _b64url(json.dumps(payload, separators=(",", ":")).encode())


def token_fingerprint(token: str | None) -> str | None:
    if not token:
        return None
    return hashlib.sha256(token.encode()).hexdigest()


def issue_token(
    *,
    agent_id: str,
    role: str,
    delegated_user: str | None = None,
    task_id: str | None = None,
    scope: list[str] | None = None,
    aud: str | None = None,
    source_agent: str | None = None,
    target_agent: str | None = None,
    source_ip: str | None = None,
    client_instance_id: str | None = None,
    delegation_depth: int = 0,
    ttl_seconds: int = 300,  # 默认5分钟短时有效期
    max_allowed_ttl: int = 86400,  # 最大有效期1天，防止超长令牌
) -> dict:
    now = int(time.time())
    exp = now + ttl_seconds
    jti = f"tok_{uuid4()}"
    header = {"alg": "BUIAM-RS256", "typ": "JWT", "kid": SYSTEM_KEY_ID}
    
    # 限制最大有效期，防止超长令牌安全风险
    if ttl_seconds > max_allowed_ttl:
        ttl_seconds = max_allowed_ttl
    exp = now + ttl_seconds
    
    claims = {
        "jti": jti,
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": agent_id,
        "agent_id": agent_id,
        "role": role,
        "delegated_user": delegated_user,
        "task_id": task_id,
        "scope": scope or [],
        "aud": aud or AUDIENCE,
        "source_agent": source_agent,
        "target_agent": target_agent,
        "source_ip": source_ip,
        "client_instance_id": client_instance_id,
        "delegation_depth": delegation_depth,
        "iat": now,
        "exp": exp,
    }
    signing_input = f"{_json_b64(header)}.{_json_b64(claims)}"
    token = f"{signing_input}.{rsa_sign(signing_input, agent_id)}"
    root_credential = build_delegation_credential(
        issuer_id=agent_id,
        subject_id=agent_id,
        delegated_user=delegated_user,
        capabilities=capabilities,
        user_capabilities=stored_user_capabilities,
        exp=exp,
        parent=None,
        trace_id=None,
        request_id=jti,
        iat=now,
    )
    upsert_credential(root_credential)
    store_token(
        jti=jti,
        sub=agent_id,
        agent_id=agent_id,
        actor_type="agent",
        delegated_user=delegated_user or "",
        capabilities=scope or [],
        exp=exp,
        credential_id=root_credential.credential_id,
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "jti": jti,
        "exp": exp,
        "credential_id": root_credential.credential_id,
    }


def verify_token(token: str) -> AuthContext:
    result = inspect_token(token)
    if result.auth_context is None:
        raise TokenError(result.error_code or "AUTH_TOKEN_INVALID", result.message)
    return result.auth_context


def inspect_token(token: str) -> TokenVerificationResult:
    verified_at = int(time.time())
    fingerprint = token_fingerprint(token)
    claims: dict = {}
    try:
        header_part, claims_part, signature = token.split(".")
        header = json.loads(_b64url_decode(header_part))
        claims = json.loads(_b64url_decode(claims_part))
        kid = str(header.get("kid", ""))
        if header.get("alg") != "BUIAM-RS256" or kid != SYSTEM_KEY_ID:
            raise TokenError("AUTH_TOKEN_INVALID", "invalid token header")
        
        # 验证签名
        signing_input = f"{header_part}.{claims_part}"
        if not _rsa_verify(signing_input, signature, load_system_public_key()):
            raise TokenError("AUTH_TOKEN_INVALID", "token signature verification failed")
        
        # 验证签发方和受众
        if claims.get("iss") != ISSUER:
            raise TokenError("AUTH_TOKEN_INVALID", "token issuer mismatch")
        
        # 验证过期时间
        now = int(time.time())
        if claims.get("exp", 0) < now:
            raise TokenError("AUTH_TOKEN_EXPIRED", "token has expired")
        
        # 检查是否被吊销
        token_record = get_token(claims.get("jti", ""))
        if not token_record or token_record.revoked:
            raise TokenError("AUTH_TOKEN_REVOKED", "token has been revoked")
        
        # 返回完整身份上下文
        return AuthContext(
            agent_id=claims.get("agent_id", ""),
            role=claims.get("role", ""),
            delegated_user=claims.get("delegated_user"),
            task_id=claims.get("task_id"),
            scope=claims.get("scope", []),
            source_agent=claims.get("source_agent"),
            target_agent=claims.get("target_agent"),
            source_ip=claims.get("source_ip"),
            client_instance_id=claims.get("client_instance_id"),
            delegation_depth=claims.get("delegation_depth", 0),
            exp=claims.get("exp", 0),
            jti=claims.get("jti", "")
        )
    except TokenError:
        raise


def inspect_token(token: str) -> TokenVerificationResult:
    verified_at = int(time.time())
    fingerprint = token_fingerprint(token)
    header: dict = {}
    claims: dict = {}
    try:
        header_part, claims_part, signature = token.split(".")
        header = json.loads(_b64url_decode(header_part))
        claims = json.loads(_b64url_decode(claims_part))
        agent_id = str(header.get("kid", ""))
        if header.get("alg") != "BUIAM-RS256" or not agent_id:
            return failed_token_result(
                token_fingerprint=fingerprint,
                verified_at=verified_at,
                claims=claims,
                error_code="AUTH_TOKEN_INVALID",
                message="invalid token header",
            )
        signing_input = f"{header_part}.{claims_part}"
        if not rsa_verify(signing_input, signature, agent_id):
            return failed_token_result(
                token_fingerprint=fingerprint,
                verified_at=verified_at,
                claims=claims,
                error_code="AUTH_TOKEN_SIGNATURE_INVALID",
                message="token signature verification failed",
                signature_valid=False,
            )
        issuer_valid = claims.get("iss") == ISSUER
        if not issuer_valid:
            return failed_token_result(
                token_fingerprint=fingerprint,
                verified_at=verified_at,
                claims=claims,
                error_code="AUTH_TOKEN_ISSUER_MISMATCH",
                message="token issuer mismatch",
                signature_valid=True,
                issuer_valid=False,
            )
        audience_valid = claims.get("aud") == AUDIENCE
        if not audience_valid:
            return failed_token_result(
                token_fingerprint=fingerprint,
                verified_at=verified_at,
                claims=claims,
                error_code="AUTH_TOKEN_AUDIENCE_MISMATCH",
                message="token audience mismatch",
                signature_valid=True,
                issuer_valid=True,
                audience_valid=False,
            )
        if int(claims.get("exp", 0)) < verified_at:
            return failed_token_result(
                token_fingerprint=fingerprint,
                verified_at=verified_at,
                claims=claims,
                error_code="AUTH_TOKEN_EXPIRED",
                message="token has expired",
                signature_valid=True,
                issuer_valid=True,
                audience_valid=True,
                is_expired=True,
            )
    except Exception as error:
        return failed_token_result(
            token_fingerprint=fingerprint,
            verified_at=verified_at,
            claims=claims,
            error_code="AUTH_TOKEN_MALFORMED",
            message="token verification failed",
        )

    stored = get_token(str(claims["jti"]))
    if stored is None:
        return failed_token_result(
            token_fingerprint=fingerprint,
            verified_at=verified_at,
            claims=claims,
            error_code="AUTH_TOKEN_JTI_NOT_REGISTERED",
            message="token jti is not registered",
            signature_valid=True,
            issuer_valid=True,
            audience_valid=True,
            is_expired=False,
            is_jti_registered=False,
        )
    if stored.revoked:
        return failed_token_result(
            token_fingerprint=fingerprint,
            verified_at=verified_at,
            claims=claims,
            error_code="AUTH_TOKEN_REVOKED",
            message="token has been revoked",
            signature_valid=True,
            issuer_valid=True,
            audience_valid=True,
            is_expired=False,
            is_jti_registered=True,
            is_revoked=True,
        )

    mark_jti_seen(stored.jti)
    root_credential = get_credential(stored.credential_id) if stored.credential_id else None
    if stored.credential_id and root_credential is None:
        return failed_token_result(
            token_fingerprint=fingerprint,
            verified_at=verified_at,
            claims=claims,
            error_code="AUTH_CREDENTIAL_INVALID",
            message="token credential is not registered",
            signature_valid=True,
            issuer_valid=True,
            audience_valid=True,
            is_expired=False,
            is_jti_registered=True,
        )
    if root_credential is not None:
        if not verify_credential_integrity(root_credential):
            return failed_token_result(
                token_fingerprint=fingerprint,
                verified_at=verified_at,
                claims=claims,
                error_code="AUTH_CREDENTIAL_INVALID",
                message="token credential integrity verification failed",
                signature_valid=True,
                issuer_valid=True,
                audience_valid=True,
                is_expired=False,
                is_jti_registered=True,
            )
        if root_credential.revoked:
            return failed_token_result(
                token_fingerprint=fingerprint,
                verified_at=verified_at,
                claims=claims,
                error_code="AUTH_CREDENTIAL_REVOKED",
                message="token credential has been revoked",
                signature_valid=True,
                issuer_valid=True,
                audience_valid=True,
                is_expired=False,
                is_jti_registered=True,
                is_revoked=True,
            )
        auth_context = auth_context_from_credential(
            root_credential,
            jti=stored.jti,
            actor_type=stored.actor_type,
        )
    else:
        auth_context = AuthContext(
            jti=stored.jti,
            sub=stored.sub,
            exp=stored.exp,
            delegated_user=stored.delegated_user,
            agent_id=stored.agent_id,
            actor_type=stored.actor_type,
            capabilities=stored.capabilities,
            user_capabilities=stored.user_capabilities,
        )
    return TokenVerificationResult(
        auth_context=auth_context,
        error_code=None,
        message="token verified",
        token_fingerprint=fingerprint,
        token_jti=stored.jti,
        token_sub=stored.sub,
        token_agent_id=stored.agent_id,
        actor_type=stored.actor_type,
        delegated_user=stored.delegated_user,
        token_issued_at=int(claims.get("iat")) if claims.get("iat") is not None else None,
        token_expires_at=stored.exp,
        verified_at=verified_at,
        is_expired=False,
        is_revoked=False,
        is_jti_registered=True,
        signature_valid=True,
        issuer_valid=True,
        audience_valid=True,
    )


def failed_token_result(
    *,
    token_fingerprint: str | None,
    verified_at: int,
    claims: dict,
    error_code: str,
    message: str,
    signature_valid: bool | None = None,
    issuer_valid: bool | None = None,
    audience_valid: bool | None = None,
    is_expired: bool | None = None,
    is_revoked: bool | None = None,
    is_jti_registered: bool | None = None,
) -> TokenVerificationResult:
    return TokenVerificationResult(
        auth_context=None,
        error_code=error_code,
        message=message,
        token_fingerprint=token_fingerprint,
        token_jti=str(claims.get("jti")) if claims.get("jti") is not None else None,
        token_sub=str(claims.get("sub")) if claims.get("sub") is not None else None,
        token_agent_id=str(claims.get("agent_id")) if claims.get("agent_id") is not None else None,
        actor_type=str(claims.get("actor_type")) if claims.get("actor_type") is not None else None,
        delegated_user=str(claims.get("delegated_user")) if claims.get("delegated_user") is not None else None,
        token_issued_at=int(claims.get("iat")) if claims.get("iat") is not None else None,
        token_expires_at=int(claims.get("exp")) if claims.get("exp") is not None else None,
        verified_at=verified_at,
        is_expired=is_expired,
        is_revoked=is_revoked,
        is_jti_registered=is_jti_registered,
        signature_valid=signature_valid,
        issuer_valid=issuer_valid,
        audience_valid=audience_valid,
    )
