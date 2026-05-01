from __future__ import annotations

import os

from app.identity.crypto import canonical_json, mldsa_sign_with_kid, mldsa_verify_with_kid, rsa_sign_with_kid, rsa_verify_with_kid, sha256_hex
from app.identity.did import build_did, build_verification_method_id
from app.protocol import IntentNode

ROOT_PARENT_ID = "ROOT"


def current_signature_alg() -> str:
    return os.getenv("BUIAM_AUTH_SIGNATURE_ALG", "BUIAM-RS256")


def intent_self_content(node: IntentNode) -> dict:
    return {
        "protocol_version": "buiam.intent.v2",
        "parent_node_id": node.parent_node_id,
        "actor_id": node.actor_id,
        "actor_did": build_did(node.actor_id),
        "actor_type": node.actor_type,
        "target_agent_id": node.target_agent_id,
        "task_type": node.task_type,
        "intent_commitment": node.intent_commitment.model_dump(),
        "proof_verification_method": build_verification_method_id(build_did(node.actor_id)),
    }


def content_hash(node: IntentNode) -> str:
    return sha256_hex(canonical_json(intent_self_content(node)))


def compute_node_id(node: IntentNode) -> str:
    parent_id = node.parent_node_id or ROOT_PARENT_ID
    raw = parent_id + canonical_json(intent_self_content(node))
    return sha256_hex(raw)


def verify_intent_node_signature(node: IntentNode) -> bool:
    if node.signature_alg not in {"BUIAM-RS256", "BUIAM-MLDSA-65"}:
        return False
    try:
        verification_method = build_verification_method_id(build_did(node.actor_id))
        signed_content = canonical_json(intent_self_content(node))
        if node.signature_alg.startswith("BUIAM-MLDSA"):
            return mldsa_verify_with_kid(signed_content, node.signature, verification_method)
        return rsa_verify_with_kid(signed_content, node.signature, verification_method)
    except Exception:
        return False


def build_signed_intent_node(*, parent_node_id: str | None, actor_id: str, actor_type: str, target_agent_id: str, task_type: str, intent_commitment) -> IntentNode:
    signature_alg = current_signature_alg()
    unsigned = IntentNode(
        node_id="",
        parent_node_id=parent_node_id,
        actor_id=actor_id,
        actor_type=actor_type,
        target_agent_id=target_agent_id,
        task_type=task_type,
        intent_commitment=intent_commitment,
        signature="",
        signature_alg=signature_alg,
    )
    verification_method = build_verification_method_id(build_did(actor_id))
    signed_content = canonical_json(intent_self_content(unsigned))
    if signature_alg.startswith("BUIAM-MLDSA"):
        signature = mldsa_sign_with_kid(signed_content, verification_method)
    else:
        signature = rsa_sign_with_kid(signed_content, verification_method)
    signed = unsigned.model_copy(update={"signature": signature})
    return signed.model_copy(update={"node_id": compute_node_id(signed)})
