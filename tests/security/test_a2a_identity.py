from __future__ import annotations

from uuid import uuid4

import httpx

from app.delegation.credential_crypto import auth_context_from_credential
from app.store.delegation_credentials import get_credential
from tests.security_helpers import (
    ALL_CAPABILITIES,
    GATEWAY_URL,
    agent_envelope,
    find_trace_credential,
    issue_agent_token,
    issue_user_token,
    root_hop_to_doc,
    run,
    run_root_task,
)


def test_agent_call_without_bearer_is_rejected_and_audited(servers) -> None:
    trace_id = str(uuid4())
    response = httpx.post(
        f"{GATEWAY_URL}/a2a/agents/enterprise_data_agent/tasks",
        json=agent_envelope(
            trace_id=trace_id,
            caller_agent_id="doc_agent",
            target_agent_id="enterprise_data_agent",
            task_type="read_enterprise_data",
            requested_capabilities=["feishu.contact:read"],
            auth_context=None,
        ).model_dump(),
        timeout=10,
    )
    trace = httpx.get(f"{GATEWAY_URL}/audit/traces/{trace_id}", timeout=10).json()

    assert response.status_code == 401
    assert response.json()["detail"]["error_code"] == "AUTH_TOKEN_MISSING"
    assert any(event["identity_decision"] == "deny" for event in trace["auth_events"])
    assert any(log["decision"] == "deny" and "AUTH_TOKEN_MISSING" in log["reason"] for log in trace["logs"])


def test_malformed_bearer_is_rejected_and_audited(servers) -> None:
    trace_id = str(uuid4())
    response = httpx.post(
        f"{GATEWAY_URL}/a2a/agents/enterprise_data_agent/tasks",
        json=agent_envelope(
            trace_id=trace_id,
            caller_agent_id="doc_agent",
            target_agent_id="enterprise_data_agent",
            task_type="read_enterprise_data",
            requested_capabilities=["feishu.contact:read"],
            auth_context=None,
        ).model_dump(),
        headers={"Authorization": "Bearer not-a-jwt"},
        timeout=10,
    )
    trace = httpx.get(f"{GATEWAY_URL}/audit/traces/{trace_id}", timeout=10).json()

    assert response.status_code == 401
    assert response.json()["detail"]["error_code"] == "AUTH_TOKEN_MALFORMED"
    assert any(event["error_code"] == "AUTH_TOKEN_MALFORMED" for event in trace["auth_events"])


def test_bearer_agent_must_match_current_credential_subject(servers) -> None:
    result = run(run_root_task("doc_agent", "generate_report", ALL_CAPABILITIES))
    doc_credential = get_credential(find_trace_credential(result["trace"], subject_id="doc_agent")["credential_id"])
    assert doc_credential is not None
    external_token = issue_agent_token("external_search_agent", capabilities=["web.public:read"])

    response = httpx.post(
        f"{GATEWAY_URL}/a2a/agents/enterprise_data_agent/tasks",
        json=agent_envelope(
            trace_id=result["trace_id"],
            caller_agent_id="doc_agent",
            target_agent_id="enterprise_data_agent",
            task_type="read_enterprise_data",
            requested_capabilities=["feishu.contact:read"],
            auth_context=auth_context_from_credential(doc_credential),
            delegation_chain=[root_hop_to_doc()],
        ).model_dump(),
        headers={"Authorization": f"Bearer {external_token['access_token']}"},
        timeout=10,
    )

    assert response.status_code == 403
    assert response.json()["detail"]["error_code"] == "AUTH_CREDENTIAL_SUBJECT_MISMATCH"


def test_user_token_cannot_be_used_as_agent_call_identity(servers) -> None:
    user_token = issue_user_token(capabilities=ALL_CAPABILITIES)
    trace_id = str(uuid4())
    response = httpx.post(
        f"{GATEWAY_URL}/a2a/agents/enterprise_data_agent/tasks",
        json=agent_envelope(
            trace_id=trace_id,
            caller_agent_id="doc_agent",
            target_agent_id="enterprise_data_agent",
            task_type="read_enterprise_data",
            requested_capabilities=["feishu.contact:read"],
            auth_context=None,
            delegation_chain=[root_hop_to_doc()],
        ).model_dump(),
        headers={"Authorization": f"Bearer {user_token['access_token']}"},
        timeout=10,
    )
    trace = httpx.get(f"{GATEWAY_URL}/audit/traces/{trace_id}", timeout=10).json()

    assert response.status_code == 403
    assert response.json()["detail"]["error_code"] == "AUTH_ACTOR_TYPE_INVALID"
    assert any("AUTH_ACTOR_TYPE_INVALID" in log["reason"] for log in trace["logs"])


def test_unknown_target_agent_is_rejected(servers) -> None:
    token = issue_agent_token("doc_agent", capabilities=ALL_CAPABILITIES)
    response = httpx.post(
        f"{GATEWAY_URL}/a2a/agents/not_registered/tasks",
        json=agent_envelope(
            trace_id=str(uuid4()),
            caller_agent_id="doc_agent",
            target_agent_id="not_registered",
            task_type="read_enterprise_data",
            requested_capabilities=["feishu.contact:read"],
            auth_context=None,
        ).model_dump(),
        headers={"Authorization": f"Bearer {token['access_token']}"},
        timeout=10,
    )

    assert response.status_code == 404
    assert response.json()["detail"]["error_code"] == "AGENT_NOT_REGISTERED"
