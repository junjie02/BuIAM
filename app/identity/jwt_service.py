from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from uuid import uuid4

from app.delegation.credential_crypto import auth_context_from_credential, build_delegation_credential, verify_credential_integrity
from app.identity.crypto import (
    b64url_decode,
    b64url_encode,
    mldsa_sign_with_kid,
    mldsa_verify_with_kid,
    rsa_sign,
    rsa_sign_with_kid,
    rsa_verify,
    rsa_verify_with_kid,
)
from app.identity.did import build_did, build_verification_method_id
from app.identity.did_resolver import DidResolutionError, resolve_verification_method
from app.protocol import AuthContext
from app.store.delegation_credentials import get_credential, upsert_credential
from app.store.tokens import get_token, mark_jti_seen, store_token

ISSUER = "buiam.local"
AUDIENCE = "buiam.a2a"
SIGNATURE_ALG = os.getenv("BUIAM_AUTH_SIGNATURE_ALG", "BUIAM-RS256")


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


def issue_token(*, agent_id: str, delegated_user: str, capabilities: list[str], user_capabilities: list[str] | None = None, actor_type: str = "agent", ttl_seconds: int = 3600) -> dict:
    now = int(time.time())
    exp = now + max(1, ttl_seconds)
    jti = f"tok_{uuid4()}"
    stored_user_capabilities = capabilities if user_capabilities is None else user_capabilities

    agent_did = build_did(agent_id)
    signing_kid = build_verification_method_id(agent_did)
    delegated_user_did = build_did(delegated_user)

    header = {"alg": SIGNATURE_ALG, "typ": "JWT", "kid": signing_kid}
    claims = {
        "jti": jti,
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": agent_id,
        "agent_id": agent_id,
        "actor_type": actor_type,
        "delegated_user": delegated_user,
        "capabilities": sorted(capabilities),
        "user_capabilities": sorted(stored_user_capabilities),
        "sub_did": agent_did,
        "agent_did": agent_did,
        "delegated_user_did": delegated_user_did,
        "signing_kid": signing_kid,
        "iat": now,
        "exp": exp,
    }
    signing_input = f"{json_b64(header)}.{json_b64(claims)}"
    if SIGNATURE_ALG.startswith("BUIAM-MLDSA"):
        token = f"{signing_input}.{mldsa_sign_with_kid(signing_input, signing_kid)}"
    else:
        token = f"{signing_input}.{rsa_sign_with_kid(signing_input, signing_kid)}"

    root_credential = build_delegation_credential(
        issuer_id=agent_id,
        subject_id=agent_id,
        delegated_user=delegated_user,
        capabilities=sorted(capabilities),
        user_capabilities=sorted(stored_user_capabilities),
        exp=exp,
        parent=None,
        trace_id=None,
        request_id=jti,
        iat=now,
    )
    upsert_credential(root_credential)
    store_token(jti=jti, sub=agent_id, agent_id=agent_id, actor_type=actor_type, delegated_user=delegated_user, capabilities=sorted(capabilities), user_capabilities=sorted(stored_user_capabilities), exp=exp, credential_id=root_credential.credential_id)
    return {"access_token": token, "token_type": "bearer", "jti": jti, "exp": exp, "credential_id": root_credential.credential_id}


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
        header = json.loads(b64url_decode(header_part))
        claims = json.loads(b64url_decode(claims_part))
        key_id = str(header.get("kid", ""))
        alg = str(header.get("alg", ""))
        allowed_algs = {"BUIAM-RS256", "BUIAM-MLDSA-65"}
        if alg not in allowed_algs or not key_id:
            return failed_token_result(token_fingerprint=fingerprint, verified_at=verified_at, claims=claims, error_code="AUTH_TOKEN_INVALID", message="invalid token header")
        signing_input = f"{header_part}.{claims_part}"

        if "#" in key_id:
            try:
                if alg.startswith("BUIAM-MLDSA"):
                    ok = mldsa_verify_with_kid(signing_input, signature, key_id)
                else:
                    ok = rsa_verify_with_kid(signing_input, signature, key_id)
                if not ok:
                    return failed_token_result(token_fingerprint=fingerprint, verified_at=verified_at, claims=claims, error_code="AUTH_TOKEN_SIGNATURE_INVALID", message="token signature verification failed", signature_valid=False)
                resolved = resolve_verification_method(key_id)
                kty = str(resolved.public_key_jwk.get("kty", ""))
                if alg.startswith("BUIAM-MLDSA") and kty != "ML-DSA":
                    return failed_token_result(token_fingerprint=fingerprint, verified_at=verified_at, claims=claims, error_code="AUTH_TOKEN_KID_INVALID", message="token kid key type mismatch for ML-DSA", signature_valid=False)
                if (not alg.startswith("BUIAM-MLDSA")) and kty == "ML-DSA":
                    return failed_token_result(token_fingerprint=fingerprint, verified_at=verified_at, claims=claims, error_code="AUTH_TOKEN_KID_INVALID", message="token kid key type mismatch for RSA", signature_valid=False)
                sub_did = str(claims.get("sub_did") or claims.get("agent_did") or "")
                if not sub_did or resolved.did != sub_did:
                    return failed_token_result(token_fingerprint=fingerprint, verified_at=verified_at, claims=claims, error_code="AUTH_TOKEN_SUBJECT_MISMATCH", message="token kid DID does not match claims subject DID", signature_valid=True)
            except DidResolutionError:
                return failed_token_result(token_fingerprint=fingerprint, verified_at=verified_at, claims=claims, error_code="AUTH_TOKEN_KID_INVALID", message="token kid cannot be resolved", signature_valid=False)
        else:
            ok = rsa_verify(signing_input, signature, key_id)
            if not ok:
                return failed_token_result(token_fingerprint=fingerprint, verified_at=verified_at, claims=claims, error_code="AUTH_TOKEN_SIGNATURE_INVALID", message="token signature verification failed", signature_valid=False)

        if claims.get("iss") != ISSUER:
            return failed_token_result(token_fingerprint=fingerprint, verified_at=verified_at, claims=claims, error_code="AUTH_TOKEN_ISSUER_MISMATCH", message="token issuer mismatch", signature_valid=True, issuer_valid=False)
        if claims.get("aud") != AUDIENCE:
            return failed_token_result(token_fingerprint=fingerprint, verified_at=verified_at, claims=claims, error_code="AUTH_TOKEN_AUDIENCE_MISMATCH", message="token audience mismatch", signature_valid=True, issuer_valid=True, audience_valid=False)
        if int(claims.get("exp", 0)) <= verified_at:
            return failed_token_result(token_fingerprint=fingerprint, verified_at=verified_at, claims=claims, error_code="AUTH_TOKEN_EXPIRED", message="token has expired", signature_valid=True, issuer_valid=True, audience_valid=True, is_expired=True)
    except Exception:
        return failed_token_result(token_fingerprint=fingerprint, verified_at=verified_at, claims=claims, error_code="AUTH_TOKEN_MALFORMED", message="token verification failed")

    stored = get_token(str(claims.get("jti", "")))
    if stored is None:
        return failed_token_result(token_fingerprint=fingerprint, verified_at=verified_at, claims=claims, error_code="AUTH_TOKEN_JTI_NOT_REGISTERED", message="token jti is not registered", signature_valid=True, issuer_valid=True, audience_valid=True, is_expired=False, is_jti_registered=False)
    if stored.revoked:
        return failed_token_result(token_fingerprint=fingerprint, verified_at=verified_at, claims=claims, error_code="AUTH_TOKEN_REVOKED", message="token has been revoked", signature_valid=True, issuer_valid=True, audience_valid=True, is_expired=False, is_jti_registered=True, is_revoked=True)

    mark_jti_seen(stored.jti)
    root_credential = get_credential(stored.credential_id) if stored.credential_id else None
    if stored.credential_id and root_credential is None:
        return failed_token_result(token_fingerprint=fingerprint, verified_at=verified_at, claims=claims, error_code="AUTH_CREDENTIAL_INVALID", message="token credential is not registered", signature_valid=True, issuer_valid=True, audience_valid=True, is_expired=False, is_jti_registered=True)

    if root_credential is not None:
        if not verify_credential_integrity(root_credential):
            return failed_token_result(token_fingerprint=fingerprint, verified_at=verified_at, claims=claims, error_code="AUTH_CREDENTIAL_INVALID", message="token credential integrity verification failed", signature_valid=True, issuer_valid=True, audience_valid=True, is_expired=False, is_jti_registered=True)
        if root_credential.revoked:
            return failed_token_result(token_fingerprint=fingerprint, verified_at=verified_at, claims=claims, error_code="AUTH_CREDENTIAL_REVOKED", message="token credential has been revoked", signature_valid=True, issuer_valid=True, audience_valid=True, is_expired=False, is_jti_registered=True, is_revoked=True)
        auth_context = auth_context_from_credential(root_credential, jti=stored.jti, actor_type=stored.actor_type)
    else:
        auth_context = AuthContext(jti=stored.jti, sub=stored.sub, exp=stored.exp, agent_id=stored.agent_id, actor_type=stored.actor_type, delegated_user=stored.delegated_user, capabilities=stored.capabilities, user_capabilities=stored.user_capabilities)

    return TokenVerificationResult(auth_context=auth_context, error_code=None, message="token verified", token_fingerprint=fingerprint, token_jti=stored.jti, token_sub=stored.sub, token_agent_id=stored.agent_id, actor_type=stored.actor_type, delegated_user=stored.delegated_user, token_issued_at=int(claims.get("iat")) if claims.get("iat") is not None else None, token_expires_at=stored.exp, verified_at=verified_at, is_expired=False, is_revoked=False, is_jti_registered=True, signature_valid=True, issuer_valid=True, audience_valid=True)


def json_b64(payload: dict) -> str:
    return b64url_encode(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode())


def token_fingerprint(token: str | None) -> str | None:
    if not token:
        return None
    return hashlib.sha256(token.encode()).hexdigest()


def failed_token_result(*, token_fingerprint: str | None, verified_at: int, claims: dict, error_code: str, message: str, signature_valid: bool | None = None, issuer_valid: bool | None = None, audience_valid: bool | None = None, is_expired: bool | None = None, is_revoked: bool | None = None, is_jti_registered: bool | None = None) -> TokenVerificationResult:
    return TokenVerificationResult(auth_context=None, error_code=error_code, message=message, token_fingerprint=token_fingerprint, token_jti=str(claims.get("jti")) if claims.get("jti") is not None else None, token_sub=str(claims.get("sub")) if claims.get("sub") is not None else None, token_agent_id=str(claims.get("agent_id")) if claims.get("agent_id") is not None else None, actor_type=str(claims.get("actor_type")) if claims.get("actor_type") is not None else None, delegated_user=str(claims.get("delegated_user")) if claims.get("delegated_user") is not None else None, token_issued_at=int(claims.get("iat")) if claims.get("iat") is not None else None, token_expires_at=int(claims.get("exp")) if claims.get("exp") is not None else None, verified_at=verified_at, is_expired=is_expired, is_revoked=is_revoked, is_jti_registered=is_jti_registered, signature_valid=signature_valid, issuer_valid=issuer_valid, audience_valid=audience_valid)
