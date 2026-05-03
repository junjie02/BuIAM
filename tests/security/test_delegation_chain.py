from __future__ import annotations

import httpx
import pytest

from app.delegation.credential_crypto import auth_context_from_credential, verify_credential_integrity
from app.store.delegation_credentials import get_credential, upsert_credential
from tests.security_helpers import (
    ALL_CAPABILITIES,
    ENTERPRISE_CAPABILITIES,
    GATEWAY_URL,
    agent_envelope,
    credential_path,
    find_trace_credential,
    find_trace_intent,
    issue_agent_token,
    root_hop_to_doc,
    run,
    run_root_task,
)


def test_delegation_chain_can_be_constructed_and_traced_to_root(servers) -> None:
    result = run(run_root_task("doc_agent", "generate_report", ALL_CAPABILITIES))
    trace = result["trace"]
    doc_credential = find_trace_credential(trace, subject_id="doc_agent")
    enterprise_credential = find_trace_credential(trace, subject_id="enterprise_data_agent")

    path = credential_path(enterprise_credential["credential_id"])
    assert [credential.subject_id for credential in path] == [
        "user_123",
        "doc_agent",
        "enterprise_data_agent",
    ]
    assert path[0].parent_credential_id is None
    assert path[1].parent_credential_id == path[0].credential_id
    assert path[2].parent_credential_id == path[1].credential_id
    assert path[1].root_credential_id == path[0].credential_id
    assert path[2].root_credential_id == path[0].credential_id
    assert all(verify_credential_integrity(credential) for credential in path)
    assert set(path[2].capabilities).issubset(set(path[1].capabilities))
    assert [hop["to_agent_id"] for hop in trace["chain"][:2]] == ["doc_agent", "enterprise_data_agent"]
    assert doc_credential["credential_id"] == path[1].credential_id


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("capabilities", ["feishu.contact:read"]),
        ("exp", 4_102_444_800),
        ("parent_credential_id", "missing-parent"),
        ("root_credential_id", "wrong-root"),
        ("signature", "not-a-valid-signature"),
        ("content_hash", "bad-content-hash"),
        ("subject_id", "external_search_agent"),
    ],
)
def test_tampered_delegation_credential_fields_are_rejected(servers, field: str, value) -> None:
    result = run(run_root_task("doc_agent", "generate_report", ALL_CAPABILITIES))
    trace = result["trace"]
    doc_credential_row = find_trace_credential(trace, subject_id="doc_agent")
    original = get_credential(doc_credential_row["credential_id"])
    assert original is not None
    auth_context = auth_context_from_credential(original)
    upsert_credential(original.model_copy(update={field: value}))

    issued = issue_agent_token("doc_agent", capabilities=ALL_CAPABILITIES)
    response = httpx.post(
        f"{GATEWAY_URL}/a2a/agents/enterprise_data_agent/tasks",
        json=agent_envelope(
            trace_id=trace["trace_id"],
            caller_agent_id="doc_agent",
            target_agent_id="enterprise_data_agent",
            task_type="read_enterprise_data",
            requested_capabilities=["feishu.contact:read"],
            auth_context=auth_context,
            delegation_chain=[root_hop_to_doc()],
        ).model_dump(),
        headers={"Authorization": f"Bearer {issued['access_token']}"},
        timeout=10,
    )

    assert response.status_code == 403
    assert response.json()["detail"]["error_code"] == "AUTH_CREDENTIAL_INVALID"


def test_cross_trace_delegation_credential_is_rejected(servers) -> None:
    first = run(run_root_task("doc_agent", "generate_report", ALL_CAPABILITIES))
    second = run(run_root_task("doc_agent", "generate_report", ALL_CAPABILITIES))
    first_doc_credential = get_credential(
        find_trace_credential(first["trace"], subject_id="doc_agent")["credential_id"]
    )
    assert first_doc_credential is not None
    issued = issue_agent_token("doc_agent", capabilities=ALL_CAPABILITIES)

    response = httpx.post(
        f"{GATEWAY_URL}/a2a/agents/enterprise_data_agent/tasks",
        json=agent_envelope(
            trace_id=second["trace_id"],
            caller_agent_id="doc_agent",
            target_agent_id="enterprise_data_agent",
            task_type="read_enterprise_data",
            requested_capabilities=["feishu.contact:read"],
            auth_context=auth_context_from_credential(first_doc_credential),
            delegation_chain=[root_hop_to_doc()],
        ).model_dump(),
        headers={"Authorization": f"Bearer {issued['access_token']}"},
        timeout=10,
    )

    assert response.status_code == 403
    assert response.json()["detail"]["error_code"] == "AUTH_CREDENTIAL_INVALID"


def test_capability_denial_is_recoverable_and_suggests_alternative_agents(servers) -> None:
    """Capability-based denial should be recoverable and suggest alternative agents."""
    # Scenario: doc_agent (has all caps) tries to call external_search_agent
    # with enterprise capabilities. external_search_agent only has web.public:read,
    # so target_agent is the bottleneck → suggested_agents should list alternatives.
    result = run(run_root_task("doc_agent", "generate_report", ALL_CAPABILITIES))
    doc_credential = get_credential(
        find_trace_credential(result["trace"], subject_id="doc_agent")["credential_id"]
    )
    assert doc_credential is not None
    auth_context = auth_context_from_credential(doc_credential)
    issued = issue_agent_token("doc_agent", capabilities=ALL_CAPABILITIES)

    # Pass the root intent node ID so the auto-generated child intent is valid
    root_intent = find_trace_intent(
        result["trace"], actor_id="user_123", target_agent_id="doc_agent"
    )

    response = httpx.post(
        f"{GATEWAY_URL}/a2a/agents/external_search_agent/tasks",
        json=agent_envelope(
            trace_id=result["trace_id"],
            caller_agent_id="doc_agent",
            target_agent_id="external_search_agent",
            task_type="search_enterprise",
            requested_capabilities=["feishu.contact:read"],
            auth_context=auth_context,
            delegation_chain=[root_hop_to_doc()],
            payload={"parent_intent_node_id": root_intent["node_id"]},
        ).model_dump(),
        headers={"Authorization": f"Bearer {issued['access_token']}"},
        timeout=10,
    )
    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail["error_code"] == "AUTH_DELEGATION_DENIED"
    assert detail.get("recoverable") is True
    # external_search_agent lacks feishu.contact:read; enterprise_data_agent has it
    suggested = detail.get("suggested_agents", [])
    assert "enterprise_data_agent" in suggested, (
        f"expected enterprise_data_agent in suggested_agents, got {suggested}"
    )


def test_tampered_credential_failure_is_fatal(servers) -> None:
    """Credential integrity failure should be non-recoverable (FATAL)."""
    result = run(run_root_task("doc_agent", "generate_report", ALL_CAPABILITIES))
    trace = result["trace"]
    doc_credential_row = find_trace_credential(trace, subject_id="doc_agent")
    original = get_credential(doc_credential_row["credential_id"])
    assert original is not None
    auth_context = auth_context_from_credential(original)
    upsert_credential(original.model_copy(update={"content_hash": "bad-content-hash"}))

    issued = issue_agent_token("doc_agent", capabilities=ALL_CAPABILITIES)
    response = httpx.post(
        f"{GATEWAY_URL}/a2a/agents/enterprise_data_agent/tasks",
        json=agent_envelope(
            trace_id=trace["trace_id"],
            caller_agent_id="doc_agent",
            target_agent_id="enterprise_data_agent",
            task_type="read_enterprise_data",
            requested_capabilities=["feishu.contact:read"],
            auth_context=auth_context,
            delegation_chain=[root_hop_to_doc()],
        ).model_dump(),
        headers={"Authorization": f"Bearer {issued['access_token']}"},
        timeout=10,
    )

    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail["error_code"] == "AUTH_CREDENTIAL_INVALID"
    # FATAL failures should not be recoverable
    assert detail.get("recoverable") is False or detail.get("recoverable") is None


def test_capability_escalation_reports_missing_boundaries(servers) -> None:
    result = run(
        run_root_task(
            "external_search_agent",
            "search_then_read_enterprise",
            ["web.public:read"],
            user_capabilities=["web.public:read"],
            payload={"query": "public-only task"},
        )
    )

    deny_logs = [
        log
        for log in result["trace"]["logs"]
        if log["caller_agent_id"] == "external_search_agent"
        and log["target_agent_id"] == "enterprise_data_agent"
        and log["decision"] == "deny"
    ]
    assert deny_logs
    detail = deny_logs[-1]["decision_detail"]
    assert set(ENTERPRISE_CAPABILITIES).issubset(set(detail["missing_capabilities"]))
    assert set(ENTERPRISE_CAPABILITIES).issubset(set(detail["missing_by"]["caller_token"]))
    assert set(ENTERPRISE_CAPABILITIES).issubset(set(detail["missing_by"]["user"]))
