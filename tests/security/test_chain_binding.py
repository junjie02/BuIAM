from __future__ import annotations

import httpx

from app.delegation.credential_crypto import auth_context_from_credential
from app.store.delegation_credentials import get_credential
from tests.security_helpers import (
    ALL_CAPABILITIES,
    GATEWAY_URL,
    USER_ID,
    agent_envelope,
    find_trace_credential,
    find_trace_intent,
    issue_agent_token,
    root_hop_to_doc,
    run,
    run_root_task,
)


def test_credential_intent_and_audit_share_trace_and_request_for_same_hop(servers) -> None:
    result = run(run_root_task("doc_agent", "generate_report", ALL_CAPABILITIES))
    trace = result["trace"]
    enterprise_credential = find_trace_credential(trace, subject_id="enterprise_data_agent")
    child_intent = find_trace_intent(trace, actor_id="doc_agent", target_agent_id="enterprise_data_agent")
    root_intent = find_trace_intent(trace, actor_id=USER_ID, target_agent_id="doc_agent")

    assert enterprise_credential["trace_id"] == trace["trace_id"]
    assert child_intent["trace_id"] == trace["trace_id"]
    assert enterprise_credential["request_id"] == child_intent["request_id"]
    assert child_intent["parent_node_id"] == root_intent["node_id"]
    assert any(
        log["request_id"] == child_intent["request_id"]
        and log["decision_detail"]["credential_id"] == find_trace_credential(trace, subject_id="doc_agent")["credential_id"]
        and log["decision_detail"]["intent_node_id"] == child_intent["node_id"]
        for log in trace["logs"]
    )


def test_cross_trace_parent_intent_is_rejected(servers) -> None:
    first = run(run_root_task("doc_agent", "generate_report", ALL_CAPABILITIES))
    second = run(run_root_task("doc_agent", "generate_report", ALL_CAPABILITIES))
    foreign_root_intent = find_trace_intent(first["trace"], actor_id=USER_ID, target_agent_id="doc_agent")
    second_doc_credential = get_credential(
        find_trace_credential(second["trace"], subject_id="doc_agent")["credential_id"]
    )
    assert second_doc_credential is not None
    issued = issue_agent_token("doc_agent", capabilities=ALL_CAPABILITIES)

    response = httpx.post(
        f"{GATEWAY_URL}/a2a/agents/enterprise_data_agent/tasks",
        json=agent_envelope(
            trace_id=second["trace_id"],
            caller_agent_id="doc_agent",
            target_agent_id="enterprise_data_agent",
            task_type="read_enterprise_data",
            requested_capabilities=["feishu.contact:read"],
            auth_context=auth_context_from_credential(second_doc_credential),
            delegation_chain=[root_hop_to_doc()],
            payload={
                "parent_intent_node_id": foreign_root_intent["node_id"],
                "user_task": "cross-trace parent intent should fail",
            },
        ).model_dump(),
        headers={"Authorization": f"Bearer {issued['access_token']}"},
        timeout=10,
    )

    assert response.status_code == 403
    assert response.json()["detail"]["error_code"] == "INTENT_CHAIN_INVALID"
    trace = httpx.get(f"{GATEWAY_URL}/audit/traces/{second['trace_id']}", timeout=10).json()
    assert any("different trace" in log["reason"] for log in trace["logs"] if log["decision"] == "deny")
