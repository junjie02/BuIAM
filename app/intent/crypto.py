from __future__ import annotations

from app.identity.crypto import canonical_json, rsa_sign, rsa_verify, sha256_hex
from app.protocol import IntentNode


ROOT_PARENT_ID = "ROOT"
SIGNATURE_ALG = "BUIAM-RS256"


def intent_self_content(node: IntentNode) -> dict:
    return {
        "protocol_version": "buiam.intent.v1",
        "parent_node_id": node.parent_node_id,
        "actor_id": node.actor_id,
        "actor_type": node.actor_type,
        "target_agent_id": node.target_agent_id,
        "task_type": node.task_type,
        "intent_commitment": node.intent_commitment.model_dump(),
    }


def content_hash(node: IntentNode) -> str:
    return sha256_hex(canonical_json(intent_self_content(node)))


def compute_node_id(node: IntentNode) -> str:
    parent_id = node.parent_node_id or ROOT_PARENT_ID
    raw = parent_id + canonical_json(intent_self_content(node))
    return sha256_hex(raw)


def sign_intent_node_content(actor_id: str, self_content: dict) -> str:
    return rsa_sign(canonical_json(self_content), actor_id)


def verify_intent_node_signature(node: IntentNode) -> bool:
    if node.signature_alg != SIGNATURE_ALG:
        return False
    try:
        return rsa_verify(canonical_json(intent_self_content(node)), node.signature, node.actor_id)
    except Exception:
        return False


def build_signed_intent_node(
    *,
    parent_node_id: str | None,
    actor_id: str,
    actor_type: str,
    target_agent_id: str,
    task_type: str,
    intent_commitment,
) -> IntentNode:
    unsigned = IntentNode(
        node_id="",
        parent_node_id=parent_node_id,
        actor_id=actor_id,
        actor_type=actor_type,
        target_agent_id=target_agent_id,
        task_type=task_type,
        intent_commitment=intent_commitment,
        signature="",
    )
    signature = sign_intent_node_content(actor_id, intent_self_content(unsigned))
    signed = unsigned.model_copy(update={"signature": signature})
    return signed.model_copy(update={"node_id": compute_node_id(signed)})
