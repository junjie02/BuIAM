from __future__ import annotations

import base64
import hashlib
import json
import time
from uuid import uuid4

from app.identity.keys import load_private_key, load_public_key
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
    delegated_user: str,
    capabilities: list[str],
    ttl_seconds: int = 3600,
) -> dict:
    now = int(time.time())
    exp = now + ttl_seconds
    jti = f"tok_{uuid4()}"
    header = {"alg": "BUIAM-RS256", "typ": "JWT", "kid": agent_id}
    claims = {
        "jti": jti,
        "sub": agent_id,
        "agent_id": agent_id,
        "delegated_user": delegated_user,
        "capabilities": capabilities,
        "iat": now,
        "exp": exp,
        "iss": ISSUER,
        "aud": AUDIENCE,
    }
    signing_input = f"{_json_b64(header)}.{_json_b64(claims)}"
    token = f"{signing_input}.{_rsa_sign(signing_input, load_private_key(agent_id))}"
    store_token(
        jti=jti,
        sub=agent_id,
        agent_id=agent_id,
        delegated_user=delegated_user,
        capabilities=capabilities,
        exp=exp,
    )
    return {"access_token": token, "token_type": "bearer", "jti": jti, "exp": exp}


def verify_token(token: str) -> AuthContext:
    try:
        header_part, claims_part, signature = token.split(".")
        header = json.loads(_b64url_decode(header_part))
        claims = json.loads(_b64url_decode(claims_part))
        agent_id = str(header.get("kid", ""))
        if header.get("alg") != "BUIAM-RS256" or not agent_id:
            raise TokenError("AUTH_TOKEN_INVALID", "invalid token header")
        signing_input = f"{header_part}.{claims_part}"
        if not _rsa_verify(signing_input, signature, load_public_key(agent_id)):
            raise TokenError("AUTH_TOKEN_INVALID", "token signature verification failed")
        if claims.get("iss") != ISSUER or claims.get("aud") != AUDIENCE:
            raise TokenError("AUTH_TOKEN_INVALID", "token issuer or audience mismatch")
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
