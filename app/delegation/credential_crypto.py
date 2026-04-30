from __future__ import annotations

import time

from app.identity.crypto import canonical_json, rsa_sign, rsa_verify, sha256_hex
from app.protocol import AuthContext, DelegationCredential


ROOT_CREDENTIAL_PARENT = "ROOT"
SIGNATURE_ALG = "BUIAM-RS256"


def credential_self_content(credential: DelegationCredential) -> dict:
    root_credential_id = (
        None if credential.parent_credential_id is None else credential.root_credential_id
    )
    return {
        "protocol_version": "buiam.delegation_credential.v1",
        "parent_credential_id": credential.parent_credential_id,
        "root_credential_id": root_credential_id,
        "issuer_id": credential.issuer_id,
        "subject_id": credential.subject_id,
        "delegated_user": credential.delegated_user,
        "capabilities": sorted(credential.capabilities),
        "user_capabilities": sorted(credential.user_capabilities),
        "iat": credential.iat,
        "exp": credential.exp,
        "trace_id": credential.trace_id,
        "request_id": credential.request_id,
    }


def content_hash(credential: DelegationCredential) -> str:
    return sha256_hex(canonical_json(credential_self_content(credential)))


def compute_credential_id(credential: DelegationCredential) -> str:
    parent_id = credential.parent_credential_id or ROOT_CREDENTIAL_PARENT
    return sha256_hex(parent_id + canonical_json(credential_self_content(credential)))


def build_delegation_credential(
    *,
    issuer_id: str,
    subject_id: str,
    delegated_user: str,
    capabilities: list[str],
    user_capabilities: list[str],
    exp: int,
    parent: DelegationCredential | None = None,
    trace_id: str | None = None,
    request_id: str | None = None,
    iat: int | None = None,
) -> DelegationCredential:
    issued_at = int(time.time()) if iat is None else iat
    parent_id = parent.credential_id if parent is not None else None
    root_id = parent.root_credential_id if parent is not None else ""
    bounded_exp = min(exp, parent.exp) if parent is not None else exp
    unsigned = DelegationCredential(
        credential_id="",
        parent_credential_id=parent_id,
        root_credential_id=root_id,
        issuer_id=issuer_id,
        subject_id=subject_id,
        delegated_user=delegated_user,
        capabilities=sorted(capabilities),
        user_capabilities=sorted(user_capabilities),
        iat=issued_at,
        exp=bounded_exp,
        trace_id=trace_id,
        request_id=request_id,
        content_hash="",
        signature="",
        signature_alg=SIGNATURE_ALG,
    )
    signed_content = canonical_json(credential_self_content(unsigned))
    signed = unsigned.model_copy(
        update={
            "content_hash": sha256_hex(signed_content),
            "signature": rsa_sign(signed_content, issuer_id),
        }
    )
    credential_id = compute_credential_id(signed)
    return signed.model_copy(
        update={
            "credential_id": credential_id,
            "root_credential_id": root_id or credential_id,
        }
    )


def verify_credential_integrity(credential: DelegationCredential) -> bool:
    try:
        if credential.signature_alg != SIGNATURE_ALG:
            return False
        if content_hash(credential) != credential.content_hash:
            return False
        if compute_credential_id(credential) != credential.credential_id:
            return False
        return rsa_verify(
            canonical_json(credential_self_content(credential)),
            credential.signature,
            credential.issuer_id,
        )
    except Exception:
        return False


def auth_context_from_credential(
    credential: DelegationCredential,
    *,
    jti: str | None = None,
    actor_type: str | None = None,
) -> AuthContext:
    inferred_actor_type = actor_type or (
        "user" if credential.subject_id == credential.delegated_user else "agent"
    )
    return AuthContext(
        jti=jti or credential.credential_id,
        sub=credential.subject_id,
        exp=credential.exp,
        delegated_user=credential.delegated_user,
        agent_id=credential.subject_id,
        actor_type=inferred_actor_type,
        capabilities=credential.capabilities,
        user_capabilities=credential.user_capabilities,
        credential_id=credential.credential_id,
        parent_credential_id=credential.parent_credential_id,
        root_credential_id=credential.root_credential_id,
        sig=credential.signature,
    )
