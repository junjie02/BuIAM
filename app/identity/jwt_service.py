from __future__ import annotations

import base64
import hashlib
import json
import time
from uuid import uuid4

from app.identity.keys import load_private_key, load_public_key, load_system_private_key, load_system_public_key, SYSTEM_KEY_ID
from app.protocol import AuthContext
from app.store.tokens import get_token, mark_jti_seen, store_token


ISSUER = "buiam.local"
AUDIENCE = "buiam.agents"


class TokenError(Exception):
    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _json_b64(payload: dict) -> str:
    return _b64url(json.dumps(payload, separators=(",", ":")).encode())


def _rsa_sign(signing_input: str, private_key: dict) -> str:
    digest = hashlib.sha256(signing_input.encode()).digest()
    digest_int = int.from_bytes(digest, "big")
    signature_int = pow(digest_int, int(private_key["d"]), int(private_key["n"]))
    length = (int(private_key["n"]).bit_length() + 7) // 8
    return _b64url(signature_int.to_bytes(length, "big"))


def _rsa_verify(signing_input: str, signature: str, public_key: dict) -> bool:
    digest_int = int.from_bytes(hashlib.sha256(signing_input.encode()).digest(), "big")
    signature_int = int.from_bytes(_b64url_decode(signature), "big")
    verified = pow(signature_int, int(public_key["e"]), int(public_key["n"]))
    return verified == digest_int


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
    delegation_depth: int = 0,
    ttl_seconds: int = 300,  # 默认5分钟短时有效期
) -> dict:
    now = int(time.time())
    exp = now + ttl_seconds
    jti = f"tok_{uuid4()}"
    header = {"alg": "BUIAM-RS256", "typ": "JWT", "kid": SYSTEM_KEY_ID}
    
    claims = {
        "jti": jti,
        "iss": ISSUER,
        "sub": agent_id,
        "agent_id": agent_id,
        "role": role,
        "delegated_user": delegated_user,
        "task_id": task_id,
        "scope": scope or [],
        "aud": aud or AUDIENCE,
        "source_agent": source_agent,
        "target_agent": target_agent,
        "delegation_depth": delegation_depth,
        "iat": now,
        "exp": exp,
    }
    
    signing_input = f"{_json_b64(header)}.{_json_b64(claims)}"
    token = f"{signing_input}.{_rsa_sign(signing_input, load_system_private_key())}"
    
    store_token(
        jti=jti,
        sub=agent_id,
        agent_id=agent_id,
        delegated_user=delegated_user or "",
        capabilities=scope or [],
        exp=exp,
    )
    
    return {
        "access_token": token,
        "token_type": "bearer",
        "jti": jti,
        "exp": exp,
        "expires_in": ttl_seconds
    }


def verify_token(token: str) -> AuthContext:
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
            delegation_depth=claims.get("delegation_depth", 0),
            exp=claims.get("exp", 0),
            jti=claims.get("jti", "")
        )
        if int(claims["exp"]) < int(time.time()):
            raise TokenError("AUTH_TOKEN_EXPIRED", "token has expired")
    except TokenError:
        raise
    except Exception as error:
        raise TokenError("AUTH_TOKEN_INVALID", "token verification failed") from error

    stored = get_token(str(claims["jti"]))
    if stored is None:
        raise TokenError("AUTH_TOKEN_INVALID", "token jti is not registered")
    if stored.revoked:
        raise TokenError("AUTH_TOKEN_REVOKED", "token has been revoked")
    mark_jti_seen(stored.jti)
    return AuthContext(
        jti=stored.jti,
        sub=stored.sub,
        exp=stored.exp,
        delegated_user=stored.delegated_user,
        agent_id=stored.agent_id,
        capabilities=stored.capabilities,
    )
