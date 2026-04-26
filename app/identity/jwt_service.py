from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import dataclass
from uuid import uuid4

from app.identity.keys import SYSTEM_KEY_ID, load_system_private_key, load_system_public_key
from app.protocol import AuthContext
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
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _json_b64(payload: dict) -> str:
    return _b64url(json.dumps(payload, separators=(",", ":")).encode())


def token_fingerprint(token: str | None) -> str | None:
    if not token:
        return None
    return hashlib.sha256(token.encode()).hexdigest()


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
    delegated_user: str,
    capabilities: list[str],
    actor_type: str = "agent",
    ttl_seconds: int = 3600,
) -> dict:
    now = int(time.time())
    exp = now + ttl_seconds
    jti = f"tok_{uuid4()}"
    header = {"alg": "BUIAM-RS256", "typ": "JWT", "kid": SYSTEM_KEY_ID}
    claims = {
        "jti": jti,
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": agent_id,
        "agent_id": agent_id,
        "actor_type": actor_type,
        "delegated_user": delegated_user,
        "capabilities": capabilities,
        "iat": now,
        "exp": exp,
    }
    signing_input = f"{_json_b64(header)}.{_json_b64(claims)}"
    token = f"{signing_input}.{_rsa_sign(signing_input, load_system_private_key())}"
    store_token(
        jti=jti,
        sub=agent_id,
        agent_id=agent_id,
        actor_type=actor_type,
        delegated_user=delegated_user,
        capabilities=capabilities,
        exp=exp,
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "jti": jti,
        "exp": exp,
        "expires_in": ttl_seconds,
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
        if header.get("alg") != "BUIAM-RS256" or header.get("kid") != SYSTEM_KEY_ID:
            return failed_token_result(
                token_fingerprint=fingerprint,
                verified_at=verified_at,
                claims=claims,
                error_code="AUTH_TOKEN_INVALID",
                message="invalid token header",
            )
        signing_input = f"{header_part}.{claims_part}"
        if not _rsa_verify(signing_input, signature, load_system_public_key()):
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
    except Exception:
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
    auth_context = AuthContext(
        jti=stored.jti,
        sub=stored.sub,
        exp=stored.exp,
        delegated_user=stored.delegated_user,
        agent_id=stored.agent_id,
        actor_type=stored.actor_type,  # type: ignore[arg-type]
        capabilities=stored.capabilities,
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
