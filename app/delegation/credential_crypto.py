from __future__ import annotations

import os
import time

from app.identity.did import build_did, build_verification_method_id
from app.identity.crypto import canonical_json, mldsa_sign_with_kid, mldsa_verify_with_kid, rsa_sign_with_kid, rsa_verify_with_kid, sha256_hex
from app.protocol import AuthContext, DelegationCredential

ROOT_CREDENTIAL_PARENT = "ROOT"
VC_CONTEXT = ["https://www.w3.org/2018/credentials/v1", "https://buiam.local/credentials/delegation/v1"]
VC_TYPE = ["VerifiableCredential", "BuIAMDelegationCredential"]
AGENT_VC_TYPE = ["VerifiableCredential", "BuIAMAgentCapabilityCredential"]
GATEWAY_SYSTEM_ID = "buiam-auth-system"


def current_signature_alg() -> str:
    return os.getenv("BUIAM_AUTH_SIGNATURE_ALG", "BUIAM-RS256")


def credential_self_content(credential: DelegationCredential) -> dict:
    root_credential_id = None if credential.parent_credential_id is None else credential.root_credential_id
    return {
        "protocol_version": "buiam.delegation_credential.v2.vc-shaped",
        "parent_credential_id": credential.parent_credential_id,
        "root_credential_id": root_credential_id,
        "issuer_id": credential.issuer_id,
        "subject_id": credential.subject_id,
        "issuer_did": credential.issuer_did,
        "subject_did": credential.subject_did,
        "delegated_user": credential.delegated_user,
        "capabilities": sorted(credential.capabilities),
        "user_capabilities": sorted(credential.user_capabilities),
        "iat": credential.iat,
        "exp": credential.exp,
        "trace_id": credential.trace_id,
        "request_id": credential.request_id,
        "vc_context": credential.vc_context,
        "vc_type": credential.vc_type,
        "credential_subject": credential.credential_subject,
        "proof_verification_method": credential.proof_verification_method,
        "signature_alg": credential.signature_alg,
    }


def content_hash(credential: DelegationCredential) -> str:
    return sha256_hex(canonical_json(credential_self_content(credential)))


def compute_credential_id(credential: DelegationCredential) -> str:
    parent_id = credential.parent_credential_id or ROOT_CREDENTIAL_PARENT
    return sha256_hex(parent_id + canonical_json(credential_self_content(credential)))


def build_delegation_credential(*, issuer_id: str, subject_id: str, delegated_user: str, capabilities: list[str], user_capabilities: list[str], exp: int, parent: DelegationCredential | None = None, trace_id: str | None = None, request_id: str | None = None, iat: int | None = None) -> DelegationCredential:
    issued_at = int(time.time()) if iat is None else iat
    parent_id = parent.credential_id if parent is not None else None
    root_id = parent.root_credential_id if parent is not None else ""
    bounded_exp = min(exp, parent.exp) if parent is not None else exp
    issuer_did = build_did(issuer_id)
    subject_did = build_did(subject_id)
    proof_vm = build_verification_method_id(issuer_did)
    signature_alg = current_signature_alg()
    credential_subject = {
        "id": subject_did,
        "subject_id": subject_id,
        "delegated_user": delegated_user,
        "capabilities": sorted(capabilities),
        "user_capabilities": sorted(user_capabilities),
        "trace_id": trace_id,
        "request_id": request_id,
        "parent_credential_id": parent_id,
        "root_credential_id": root_id,
    }
    unsigned = DelegationCredential(
        credential_id="",
        parent_credential_id=parent_id,
        root_credential_id=root_id,
        issuer_id=issuer_id,
        subject_id=subject_id,
        issuer_did=issuer_did,
        subject_did=subject_did,
        delegated_user=delegated_user,
        capabilities=sorted(capabilities),
        user_capabilities=sorted(user_capabilities),
        iat=issued_at,
        exp=bounded_exp,
        trace_id=trace_id,
        request_id=request_id,
        vc_context=VC_CONTEXT,
        vc_type=VC_TYPE,
        credential_subject=credential_subject,
        proof_verification_method=proof_vm,
        proof_signature="",
        content_hash="",
        signature="",
        signature_alg=signature_alg,
    )
    signed_content = canonical_json(credential_self_content(unsigned))
    if signature_alg.startswith("BUIAM-MLDSA"):
        proof_signature = mldsa_sign_with_kid(signed_content, proof_vm)
    else:
        proof_signature = rsa_sign_with_kid(signed_content, proof_vm)
    signed = unsigned.model_copy(update={"content_hash": sha256_hex(signed_content), "proof_signature": proof_signature, "signature": proof_signature})
    credential_id = compute_credential_id(signed)
    return signed.model_copy(update={"credential_id": credential_id, "root_credential_id": root_id or credential_id})


def verify_credential_integrity(credential: DelegationCredential) -> bool:
    try:
        if credential.signature_alg not in {"BUIAM-RS256", "BUIAM-MLDSA-65"}:
            return False
        if credential.proof_signature and credential.signature and credential.proof_signature != credential.signature:
            return False
        if content_hash(credential) != credential.content_hash:
            return False
        if compute_credential_id(credential) != credential.credential_id:
            return False
        proof_vm = credential.proof_verification_method or build_verification_method_id(build_did(credential.issuer_id))
        signature = credential.proof_signature or credential.signature
        if credential.signature_alg.startswith("BUIAM-MLDSA"):
            return mldsa_verify_with_kid(canonical_json(credential_self_content(credential)), signature, proof_vm)
        return rsa_verify_with_kid(canonical_json(credential_self_content(credential)), signature, proof_vm)
    except Exception:
        return False


def auth_context_from_credential(credential: DelegationCredential, *, jti: str | None = None, actor_type: str | None = None) -> AuthContext:
    inferred_actor_type = actor_type or ("user" if credential.subject_id == credential.delegated_user else "agent")
    subject_did = credential.subject_did or build_did(credential.subject_id)
    issuer_did = credential.issuer_did or build_did(credential.issuer_id)
    return AuthContext(
        jti=jti or credential.credential_id,
        sub=credential.subject_id,
        exp=credential.exp,
        delegated_user=credential.delegated_user,
        agent_id=credential.subject_id,
        actor_type=inferred_actor_type,
        subject_did=subject_did,
        agent_did=subject_did,
        signing_kid=credential.proof_verification_method or f"{issuer_did}#key-1",
        capabilities=credential.capabilities,
        user_capabilities=credential.user_capabilities,
        credential_id=credential.credential_id,
        parent_credential_id=credential.parent_credential_id,
        root_credential_id=credential.root_credential_id,
        sig=credential.proof_signature or credential.signature,
    )


def build_agent_capability_vc(
    *,
    agent_id: str,
    capabilities: list[str],
    endpoint: str,
    agent_type: str = "other",
    ttl_days: int = 30,
) -> DelegationCredential:
    """Issue an Agent Capability VC signed by the Gateway system identity.

    This VC declares what capabilities an agent possesses and where it can be
    reached. It is the cryptographically-verifiable equivalent of the
    ``static_capabilities`` column — other agents can verify the VC signature
    to confirm the agent's capabilities without trusting the database directly.
    """
    now = int(time.time())
    exp = now + ttl_days * 86400
    agent_did = build_did(agent_id)
    issuer_did = build_did(GATEWAY_SYSTEM_ID)
    proof_vm = build_verification_method_id(issuer_did)
    signature_alg = current_signature_alg()
    credential_subject = {
        "id": agent_did,
        "subject_id": agent_id,
        "capabilities": sorted(capabilities),
        "service_endpoint": endpoint,
        "agent_type": agent_type,
    }
    unsigned = DelegationCredential(
        credential_id="",
        parent_credential_id=None,
        root_credential_id="",
        issuer_id=GATEWAY_SYSTEM_ID,
        subject_id=agent_id,
        issuer_did=issuer_did,
        subject_did=agent_did,
        delegated_user=agent_id,
        capabilities=sorted(capabilities),
        user_capabilities=sorted(capabilities),
        iat=now,
        exp=exp,
        trace_id=None,
        request_id=None,
        vc_context=VC_CONTEXT,
        vc_type=AGENT_VC_TYPE,
        credential_subject=credential_subject,
        proof_verification_method=proof_vm,
        proof_signature="",
        content_hash="",
        signature="",
        signature_alg=signature_alg,
    )
    signed_content = canonical_json(credential_self_content(unsigned))
    if signature_alg.startswith("BUIAM-MLDSA"):
        proof_signature = mldsa_sign_with_kid(signed_content, proof_vm)
    else:
        proof_signature = rsa_sign_with_kid(signed_content, proof_vm)
    signed = unsigned.model_copy(update={
        "content_hash": sha256_hex(signed_content),
        "proof_signature": proof_signature,
        "signature": proof_signature,
    })
    credential_id = compute_credential_id(signed)
    return signed.model_copy(update={
        "credential_id": credential_id,
        "root_credential_id": credential_id,
    })


def get_agent_vc(agent_id: str) -> DelegationCredential | None:
    """Retrieve the Agent Capability VC for *agent_id* from the credential store."""
    from app.store.delegation_credentials import get_credential_by_subject_and_type
    return get_credential_by_subject_and_type(agent_id, AGENT_VC_TYPE)
