from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from app.delegation.capabilities import intersect_capabilities, parse_capabilities
from app.delegation.service import DelegationService
from app.identity.mock_store import blacklist, clear_mock_state, user_caps
from app.identity.mock_token import sign_token, verify_sig, verify_token_source
from app.main import app
from app.protocol import AuthContext, DelegationEnvelope, DelegationHop


def make_auth_context(
    agent_id: str = "doc_agent",
    jti: str = "tok_test",
    capabilities: list[str] | None = None,
) -> AuthContext:
    return AuthContext(
        jti=jti,
        sub=agent_id,
        exp=9999999999,
        delegated_user="user_123",
        agent_id=agent_id,
        capabilities=capabilities
        or [
            "report:write",
            "feishu.contact:read",
            "feishu.wiki:read",
            "feishu.bitable:read",
            "web.public:read",
        ],
    )


def make_enterprise_envelope(
    auth_context: AuthContext | None = None,
    chain: list[DelegationHop] | None = None,
) -> DelegationEnvelope:
    return DelegationEnvelope(
        trace_id="trace_test",
        request_id="req_test",
        caller_agent_id="doc_agent",
        target_agent_id="enterprise_data_agent",
        task_type="read_enterprise_data",
        requested_capabilities=[
            "feishu.contact:read",
            "feishu.wiki:read",
            "feishu.bitable:read",
        ],
        delegation_chain=chain
        or [
            DelegationHop(
                from_actor="user",
                to_agent_id="doc_agent",
                task_type="generate_report",
                delegated_capabilities=[],
            )
        ],
        auth_context=auth_context or make_auth_context(),
        payload={},
    )


def setup_function() -> None:
    clear_mock_state()


def test_capability_parser_and_intersection() -> None:
    requested = parse_capabilities(["feishu.contact:read", "feishu.wiki:read"])
    effective = intersect_capabilities(
        requested,
        frozenset({"feishu.contact:read", "web.public:read"}),
    )
    assert effective == {"feishu.contact:read"}


def test_envelope_validation_requires_core_fields() -> None:
    with pytest.raises(Exception):
        DelegationEnvelope.model_validate({"trace_id": "missing-other-fields"})


def test_mock_token_signature_and_source_placeholder() -> None:
    raw = {
        "agent_id": "doc_agent",
        "capabilities": ["report:write", "feishu.contact:read"],
    }
    signed = sign_token(raw)
    token = make_auth_context(capabilities=signed["capabilities"])
    token.sig = signed["sig"]
    assert verify_sig(token)
    assert verify_token_source(token)


def test_blacklisted_jti_is_denied() -> None:
    blacklist.add("tok_revoked")
    decision = DelegationService().authorize(
        make_enterprise_envelope(auth_context=make_auth_context(jti="tok_revoked"))
    )
    assert decision.decision == "deny"
    assert "revoked" in decision.reason


def test_user_caps_participate_in_intersection() -> None:
    original = user_caps["user_123"]
    user_caps["user_123"] = frozenset({"feishu.contact:read", "feishu.wiki:read"})
    try:
        decision = DelegationService().authorize(make_enterprise_envelope())
        assert decision.decision == "deny"
        assert "feishu.bitable:read" in decision.reason
    finally:
        user_caps["user_123"] = original


def test_delegation_chain_must_be_continuous() -> None:
    forged_chain = [
        DelegationHop(
            from_actor="user",
            to_agent_id="external_search_agent",
            task_type="search_public_web",
            delegated_capabilities=[],
        )
    ]
    decision = DelegationService().authorize(make_enterprise_envelope(chain=forged_chain))
    assert decision.decision == "deny"
    assert "not continuous" in decision.reason


def test_append_hop_shrinks_capabilities_and_context() -> None:
    envelope = make_enterprise_envelope()
    decision = DelegationService().authorize(envelope)
    authorized = DelegationService().append_hop(envelope, decision.effective_capabilities)
    assert authorized.delegation_chain[-1].delegated_capabilities == decision.effective_capabilities
    assert authorized.delegation_chain[-1].missing_capabilities == []
    assert authorized.auth_context.capabilities == decision.effective_capabilities
    assert authorized.auth_context.agent_id == "enterprise_data_agent"


def test_doc_agent_delegation_is_allowed() -> None:
    client = TestClient(app)
    response = client.post(
        "/agents/doc_agent/tasks",
        json={"task_type": "generate_report", "payload": {"topic": "测试报告"}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["agent_id"] == "doc_agent"
    assert "report" in body["result"]

    trace_logs = client.get(f"/audit/traces/{body['trace_id']}").json()
    assert any(log["decision"] == "allow" for log in trace_logs)
    decision_detail = trace_logs[-1]["decision_detail"]
    assert decision_detail["requested_capabilities"] == [
        "feishu.bitable:read",
        "feishu.contact:read",
        "feishu.wiki:read",
    ]
    assert decision_detail["effective_capabilities"] == [
        "feishu.bitable:read",
        "feishu.contact:read",
        "feishu.wiki:read",
    ]


def test_external_search_agent_delegation_is_denied() -> None:
    client = TestClient(app)
    response = client.post(
        "/agents/external_search_agent/tasks",
        json={"task_type": "attempt_enterprise_data_access", "payload": {}},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "delegation_denied"
    assert "feishu.contact:read" in response.json()["detail"]["missing_capabilities"]

    logs = client.get("/audit/logs").json()
    assert any(
        log["caller_agent_id"] == "external_search_agent" and log["decision"] == "deny"
        for log in logs
    )
    latest_deny = next(
        log
        for log in reversed(logs)
        if log["caller_agent_id"] == "external_search_agent" and log["decision"] == "deny"
    )
    denied_hop = latest_deny["delegation_chain"][-1]
    assert denied_hop["delegated_capabilities"] == []
    assert denied_hop["missing_capabilities"] == [
        "feishu.bitable:read",
        "feishu.contact:read",
        "feishu.wiki:read",
    ]
    assert latest_deny["decision_detail"]["missing_by"]["caller_token"] == [
        "feishu.bitable:read",
        "feishu.contact:read",
        "feishu.wiki:read",
    ]
    assert latest_deny["decision_detail"]["missing_by"]["target_agent"] == []
    assert latest_deny["decision_detail"]["missing_by"]["user"] == []


def test_root_user_hop_records_initial_capabilities() -> None:
    client = TestClient(app)
    response = client.post(
        "/agents/doc_agent/tasks",
        json={"task_type": "generate_report", "payload": {"topic": "测试报告"}},
    )
    logs = client.get(f"/audit/traces/{response.json()['trace_id']}").json()
    root_hop = logs[-1]["delegation_chain"][0]
    assert root_hop["from_actor"] == "user"
    assert root_hop["decision"] == "root"
    assert "report:write" in root_hop["delegated_capabilities"]


def test_audit_logs_include_decision_detail() -> None:
    client = TestClient(app)
    response = client.post(
        "/agents/doc_agent/tasks",
        json={"task_type": "generate_report", "payload": {"topic": "测试报告"}},
    )
    trace_id = response.json()["trace_id"]
    trace_logs = client.get(f"/audit/traces/{trace_id}").json()
    all_logs = client.get("/audit/logs").json()
    assert trace_logs[-1]["decision_detail"]["decision"] == "allow"
    assert any(log.get("decision_detail") for log in all_logs)


def test_agents_do_not_import_authorization_service() -> None:
    for module_name in [
        "app.agents.doc",
        "app.agents.enterprise_data",
        "app.agents.external_search",
    ]:
        module = importlib.import_module(module_name)
        assert "app.delegation.service" not in repr(module.__dict__.values())
