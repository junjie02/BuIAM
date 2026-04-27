from __future__ import annotations

import time
from uuid import uuid4

import httpx

from app.identity.jwt_service import issue_token, verify_token
from app.protocol import DelegationEnvelope, RootTaskRequest
from app.store.delegation_credentials import get_credential
from tests.security_helpers import (
    ALL_CAPABILITIES,
    GATEWAY_URL,
    run,
    run_root_task,
)


def test_services_import_and_registry_contains_demo_agents(servers) -> None:
    response = httpx.get(f"{GATEWAY_URL}/registry/agents", timeout=10)
    response.raise_for_status()
    agents = {agent["agent_id"]: agent for agent in response.json()}
    assert {"doc_agent", "enterprise_data_agent", "external_search_agent"} <= set(agents)
    assert agents["doc_agent"]["endpoint"].endswith(":18011/a2a/tasks")
    assert "local://" not in str(agents)


def test_normal_chain_records_credentials_intents_and_audit(servers) -> None:
    result = run(run_root_task("doc_agent", "generate_report", ALL_CAPABILITIES))
    body = result["body"]
    trace = result["trace"]
    assert body["agent_id"] == "doc_agent"
    assert body["result"]["document"]["provider"] == "mock_doc_provider"
    assert body["result"]["enterprise_data"]["source"] == "mock_enterprise_provider"

    decisions = [(log["caller_agent_id"], log["target_agent_id"], log["decision"]) for log in trace["logs"]]
    assert ("doc_agent", "enterprise_data_agent", "allow") in decisions
    assert any(hop["to_agent_id"] == "enterprise_data_agent" for hop in trace["chain"])
    assert len(trace["delegation_credentials"]) >= 2
    assert len(trace["intent_tree"]) >= 2
    assert len(trace["auth_events"]) >= 2


def test_external_agent_enterprise_escalation_is_denied_and_audited(servers) -> None:
    result = run(
        run_root_task(
            "external_search_agent",
            "search_then_read_enterprise",
            ["web.public:read"],
            user_capabilities=["web.public:read"],
            payload={"query": "public Feishu weather"},
        )
    )
    body = result["body"]
    trace = result["trace"]
    escalation = body["result"]["enterprise_escalation"]
    assert escalation["allowed"] is False
    assert escalation["status_code"] == 403
    assert any(
        log["caller_agent_id"] == "external_search_agent"
        and log["target_agent_id"] == "enterprise_data_agent"
        and log["decision"] == "deny"
        for log in trace["logs"]
    )


def test_tampered_credential_is_rejected(servers) -> None:
    issued = issue_token(
        agent_id="doc_agent",
        delegated_user="user_123",
        actor_type="agent",
        capabilities=["feishu.contact:read"],
        user_capabilities=["feishu.contact:read"],
        ttl_seconds=3600,
    )
    auth_context = verify_token(issued["access_token"])
    tampered = auth_context.model_copy(update={"capabilities": ["feishu.contact:read", "web.public:read"]})
    response = httpx.post(
        f"{GATEWAY_URL}/a2a/agents/enterprise_data_agent/tasks",
        json=DelegationEnvelope(
            trace_id=str(uuid4()),
            request_id=str(uuid4()),
            caller_agent_id="doc_agent",
            target_agent_id="enterprise_data_agent",
            task_type="read_enterprise_data",
            requested_capabilities=["feishu.contact:read"],
            auth_context=tampered,
            payload={"user_task": "tampered credential"},
        ).model_dump(),
        headers={"Authorization": f"Bearer {issued['access_token']}"},
        timeout=10,
    )
    assert response.status_code == 403
    assert response.json()["detail"]["error_code"] == "AUTH_CREDENTIAL_INVALID"


def test_token_revoke_cascades_to_descendant_credentials(servers) -> None:
    result = run(run_root_task("doc_agent", "generate_report", ALL_CAPABILITIES))
    jti = result["token_jti"]
    trace = result["trace"]
    credential_ids = [credential["credential_id"] for credential in trace["delegation_credentials"]]
    response = httpx.post(f"{GATEWAY_URL}/identity/tokens/{jti}/revoke", json={"reason": "test_revoke"}, timeout=10)
    response.raise_for_status()
    assert response.json()["revoked"] is True
    assert credential_ids
    assert all(get_credential(credential_id).revoked for credential_id in credential_ids)


def test_expired_token_blocks_new_root_task_without_marking_revoke(servers) -> None:
    issued = issue_token(
        agent_id="user_123",
        delegated_user="user_123",
        actor_type="user",
        capabilities=["web.public:read"],
        user_capabilities=["web.public:read"],
        ttl_seconds=1,
    )
    time.sleep(1.2)
    response = httpx.post(
        f"{GATEWAY_URL}/a2a/root-tasks",
        json=RootTaskRequest(
            target_agent_id="external_search_agent",
            task_type="search_public_web",
            user_task="search after expiry",
            requested_capabilities=["web.public:read"],
            payload={"query": "expired"},
        ).model_dump(),
        headers={"Authorization": f"Bearer {issued['access_token']}"},
        timeout=10,
    )
    assert response.status_code == 401
    assert response.json()["detail"]["error_code"] == "AUTH_TOKEN_EXPIRED"
    credential = get_credential(issued["credential_id"])
    assert credential is not None
    assert credential.revoked is False
