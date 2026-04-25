from __future__ import annotations

from dataclasses import dataclass

from app.intent.crypto import compute_node_id, content_hash, verify_intent_node_signature
from app.intent.judge import IntentJudgeError, IntentJudgeResult, judge_intent
from app.protocol import AuthContext, IntentNode
from app.store.intent_tree import get_intent_node, row_to_intent_node, upsert_intent_node


class IntentValidationError(Exception):
    def __init__(
        self,
        error_code: str,
        message: str,
        *,
        node: IntentNode | None = None,
        root_intent: str | None = None,
        parent_intent: str | None = None,
        child_intent: str | None = None,
        judge_decision: str | None = None,
        judge_reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.node = node
        self.root_intent = root_intent
        self.parent_intent = parent_intent
        self.child_intent = child_intent
        self.judge_decision = judge_decision
        self.judge_reason = judge_reason


@dataclass(frozen=True)
class IntentValidationResult:
    node: IntentNode
    root_node: IntentNode
    parent_node: IntentNode
    root_intent: str
    parent_intent: str
    child_intent: str
    judge_decision: str
    judge_reason: str


async def validate_and_record_intent_node(
    *,
    node: IntentNode,
    trace_id: str,
    request_id: str,
    auth_context: AuthContext,
) -> IntentValidationResult:
    validate_node_hash_and_signature(node)
    validate_actor(node, auth_context)
    root_node, parent_node = validate_branch(node)
    try:
        judge_result = await judge_intent(
            root_intent=root_node.intent_commitment.intent,
            parent_intent=parent_node.intent_commitment.intent,
            child_intent=node.intent_commitment.intent,
            task_type=node.task_type,
            target_agent_id=node.target_agent_id,
        )
    except IntentJudgeError as error:
        record_node(node, trace_id, request_id, root_node.node_id, "JudgeFailed", str(error))
        raise IntentValidationError(
            "INTENT_JUDGE_FAILED",
            str(error),
            node=node,
            root_intent=root_node.intent_commitment.intent,
            parent_intent=parent_node.intent_commitment.intent,
            child_intent=node.intent_commitment.intent,
            judge_decision="JudgeFailed",
            judge_reason=str(error),
        ) from error

    record_node(node, trace_id, request_id, root_node.node_id, judge_result.decision, judge_result.reason)
    if judge_result.decision == "Drifted":
        raise IntentValidationError(
            "INTENT_DRIFTED",
            judge_result.reason,
            node=node,
            root_intent=root_node.intent_commitment.intent,
            parent_intent=parent_node.intent_commitment.intent,
            child_intent=node.intent_commitment.intent,
            judge_decision=judge_result.decision,
            judge_reason=judge_result.reason,
        )

    return IntentValidationResult(
        node=node,
        root_node=root_node,
        parent_node=parent_node,
        root_intent=root_node.intent_commitment.intent,
        parent_intent=parent_node.intent_commitment.intent,
        child_intent=node.intent_commitment.intent,
        judge_decision=judge_result.decision,
        judge_reason=judge_result.reason,
    )


def validate_node_hash_and_signature(node: IntentNode) -> None:
    if compute_node_id(node) != node.node_id:
        raise IntentValidationError("INTENT_CHAIN_INVALID", "intent node_id does not match node content")
    if not verify_intent_node_signature(node):
        raise IntentValidationError("INTENT_SIGNATURE_INVALID", "intent node signature is invalid")


def validate_actor(node: IntentNode, auth_context: AuthContext) -> None:
    if node.parent_node_id is None:
        if node.actor_type != "user" or node.actor_id != auth_context.delegated_user:
            raise IntentValidationError("INTENT_ACTOR_MISMATCH", "root intent must be signed by delegated user")
        return
    if node.actor_type != "agent" or node.actor_id != auth_context.agent_id:
        raise IntentValidationError("INTENT_ACTOR_MISMATCH", "child intent must be signed by caller agent")


def validate_branch(node: IntentNode) -> tuple[IntentNode, IntentNode]:
    if node.parent_node_id is None:
        return node, node

    parent_row = get_intent_node(node.parent_node_id)
    if parent_row is None:
        raise IntentValidationError("INTENT_PARENT_NOT_FOUND", "parent intent node does not exist")
    parent_node = row_to_intent_node(parent_row)
    validate_node_hash_and_signature(parent_node)
    if parent_row["content_hash"] != content_hash(parent_node):
        raise IntentValidationError("INTENT_CHAIN_INVALID", "parent intent content hash mismatch")

    current_row = parent_row
    current_node = parent_node
    visited = {node.node_id}
    while current_node.parent_node_id is not None:
        if current_node.node_id in visited:
            raise IntentValidationError("INTENT_CHAIN_INVALID", "intent branch contains a cycle")
        visited.add(current_node.node_id)
        next_row = get_intent_node(current_node.parent_node_id)
        if next_row is None:
            raise IntentValidationError("INTENT_CHAIN_INVALID", "intent branch is missing an ancestor")
        next_node = row_to_intent_node(next_row)
        validate_node_hash_and_signature(next_node)
        current_row = next_row
        current_node = next_node
    return current_node, parent_node


def record_node(
    node: IntentNode,
    trace_id: str,
    request_id: str,
    root_node_id: str,
    judge_decision: str,
    judge_reason: str,
) -> None:
    upsert_intent_node(
        node=node,
        trace_id=trace_id,
        request_id=request_id,
        root_node_id=root_node_id,
        judge_decision=judge_decision,
        judge_reason=judge_reason,
    )
