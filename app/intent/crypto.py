from __future__ import annotations

import base64
import hashlib
import json

from app.identity.keys import load_private_key, load_public_key
from app.protocol import IntentNode


ROOT_PARENT_ID = "ROOT"
SIGNATURE_ALG = "BUIAM-RS256"


def canonical_json(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


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
    return hashlib.sha256(canonical_json(intent_self_content(node)).encode()).hexdigest()


def compute_node_id(node: IntentNode) -> str:
    parent_id = node.parent_node_id or ROOT_PARENT_ID
    raw = parent_id + canonical_json(intent_self_content(node))
    return hashlib.sha256(raw.encode()).hexdigest()


def sign_intent_node_content(actor_id: str, self_content: dict) -> str:
    signing_input = canonical_json(self_content)
    digest = hashlib.sha256(signing_input.encode()).digest()
    private_key = load_private_key(actor_id)
    signature_int = pow(int.from_bytes(digest, "big"), int(private_key["d"]), int(private_key["n"]))
    length = (int(private_key["n"]).bit_length() + 7) // 8
    return base64.urlsafe_b64encode(signature_int.to_bytes(length, "big")).rstrip(b"=").decode()


def verify_intent_node_signature(node: IntentNode) -> bool:
    if node.signature_alg != SIGNATURE_ALG:
        return False
    try:
        padding = "=" * (-len(node.signature) % 4)
        signature_int = int.from_bytes(base64.urlsafe_b64decode(node.signature + padding), "big")
        public_key = load_public_key(node.actor_id)
        verified = pow(signature_int, int(public_key["e"]), int(public_key["n"]))
        expected = int.from_bytes(hashlib.sha256(canonical_json(intent_self_content(node)).encode()).digest(), "big")
        return verified == expected
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
