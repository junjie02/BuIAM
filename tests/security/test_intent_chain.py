from __future__ import annotations

import asyncio

import pytest

from app.intent.crypto import build_signed_intent_node, verify_intent_node_signature
from app.intent.judge import IntentJudgeResult
from app.intent.service import IntentValidationError, validate_and_record_intent_node
from app.protocol import IntentCommitment
from app.store.intent_tree import get_intent_node, row_to_intent_node
from tests.security_helpers import (
    ALL_CAPABILITIES,
    USER_ID,
    auth_context_for_credential,
    find_trace_credential,
    find_trace_intent,
    intent_path,
    run,
    run_root_task,
)


def test_intent_chain_can_be_constructed_and_traced_to_root(servers) -> None:
    result = run(run_root_task("doc_agent", "generate_report", ALL_CAPABILITIES))
    trace = result["trace"]
    root_row = find_trace_intent(trace, actor_id=USER_ID, target_agent_id="doc_agent")
    child_row = find_trace_intent(trace, actor_id="doc_agent", target_agent_id="enterprise_data_agent")

    path = intent_path(child_row["node_id"])
    assert [node.actor_id for node in path] == [USER_ID, "doc_agent"]
    assert child_row["parent_node_id"] == root_row["node_id"]
    assert child_row["root_node_id"] == root_row["node_id"]
    assert all(verify_intent_node_signature(node) for node in path)
    assert get_intent_node(root_row["node_id"])["trace_id"] == trace["trace_id"]


@pytest.mark.parametrize(
    ("mutation", "error_code"),
    [
        ("node_id", "INTENT_CHAIN_INVALID"),
        ("content", "INTENT_CHAIN_INVALID"),
        ("signature", "INTENT_SIGNATURE_INVALID"),
        ("missing_parent", "INTENT_PARENT_NOT_FOUND"),
        ("actor", "INTENT_ACTOR_MISMATCH"),
    ],
)
def test_malicious_intent_nodes_are_rejected(servers, mutation: str, error_code: str) -> None:
    result = run(run_root_task("doc_agent", "generate_report", ALL_CAPABILITIES))
    trace = result["trace"]
    root_row = find_trace_intent(trace, actor_id=USER_ID, target_agent_id="doc_agent")
    doc_credential = find_trace_credential(trace, subject_id="doc_agent")
    doc_auth_context = auth_context_for_credential(doc_credential["credential_id"])

    node = build_signed_intent_node(
        parent_node_id=root_row["node_id"],
        actor_id="doc_agent",
        actor_type="agent",
        target_agent_id="enterprise_data_agent",
        task_type="read_enterprise_data",
        intent_commitment=IntentCommitment(intent="doc_agent reads delegated enterprise data"),
    )
    if mutation == "node_id":
        node = node.model_copy(update={"node_id": "bad-node-id"})
    elif mutation == "content":
        node = node.model_copy(
            update={
                "intent_commitment": IntentCommitment(intent="silently read unrelated payroll records"),
            }
        )
    elif mutation == "signature":
        node = node.model_copy(update={"signature": "bad-signature"})
    elif mutation == "missing_parent":
        node = build_signed_intent_node(
            parent_node_id="missing-parent",
            actor_id="doc_agent",
            actor_type="agent",
            target_agent_id="enterprise_data_agent",
            task_type="read_enterprise_data",
            intent_commitment=IntentCommitment(intent="doc_agent reads delegated enterprise data"),
        )
    elif mutation == "actor":
        node = build_signed_intent_node(
            parent_node_id=root_row["node_id"],
            actor_id="external_search_agent",
            actor_type="agent",
            target_agent_id="enterprise_data_agent",
            task_type="read_enterprise_data",
            intent_commitment=IntentCommitment(intent="wrong actor tries to reuse intent"),
        )

    with pytest.raises(IntentValidationError) as raised:
        asyncio.run(
            validate_and_record_intent_node(
                node=node,
                trace_id=trace["trace_id"],
                request_id="intent-malicious-test",
                auth_context=doc_auth_context,
            )
        )
    assert raised.value.error_code == error_code


def test_intent_drift_is_rejected_and_exposed(monkeypatch, servers) -> None:
    result = run(run_root_task("doc_agent", "generate_report", ALL_CAPABILITIES))
    trace = result["trace"]
    root_row = find_trace_intent(trace, actor_id=USER_ID, target_agent_id="doc_agent")
    doc_credential = find_trace_credential(trace, subject_id="doc_agent")
    doc_auth_context = auth_context_for_credential(doc_credential["credential_id"])
    node = build_signed_intent_node(
        parent_node_id=root_row["node_id"],
        actor_id="doc_agent",
        actor_type="agent",
        target_agent_id="enterprise_data_agent",
        task_type="read_enterprise_data",
        intent_commitment=IntentCommitment(intent="drifted test intent"),
    )

    async def drifted_judge(**_kwargs):
        return IntentJudgeResult(decision="Drifted", reason="forced drift in security test")

    monkeypatch.setattr("app.intent.service.judge_intent", drifted_judge)
    with pytest.raises(IntentValidationError) as raised:
        asyncio.run(
            validate_and_record_intent_node(
                node=node,
                trace_id=trace["trace_id"],
                request_id="intent-drift-test",
                auth_context=doc_auth_context,
            )
        )
    assert raised.value.error_code == "INTENT_DRIFTED"
    assert raised.value.judge_decision == "Drifted"
