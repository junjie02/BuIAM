from __future__ import annotations

import importlib
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.delegation.capabilities import intersect_capabilities, parse_capabilities
from app.delegation.service import DelegationService
from app.identity.keys import ensure_agent_keypair, private_key_path, public_key_path
from app.identity.jwt_service import issue_token, verify_token
from app.main import app, on_startup
from app.protocol import AuthContext, DelegationEnvelope, DelegationHop
from app.store.registry import get_agent, upsert_agent
from app.store.tokens import revoke_token


def make_auth_context(
    agent_id: str = "doc_agent",
    capabilities: list[str] | None = None,
) -> AuthContext:
    return AuthContext(
        jti="tok_test",
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
    on_startup()
    register_demo_agents()


def register_demo_agents() -> None:
    demo_agents = {
        "doc_agent": ("飞书文档助手 Agent", "local://doc_agent", ["report:write"]),
        "enterprise_data_agent": (
            "企业数据 Agent",
            "local://enterprise_data_agent",
            ["feishu.contact:read", "feishu.wiki:read", "feishu.bitable:read"],
        ),
        "external_search_agent": ("外部检索 Agent", "local://external_search_agent", ["web.public:read"]),
    }
    for agent_id, (name, endpoint, capabilities) in demo_agents.items():
        ensure_agent_keypair(agent_id)
        upsert_agent(agent_id, name, endpoint, capabilities)


def issue_demo_token(agent_id: str, capabilities: list[str]) -> str:
    return issue_token(
        agent_id=agent_id,
        delegated_user="user_123",
        capabilities=capabilities,
        ttl_seconds=3600,
    )["access_token"]


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


def test_demo_agent_keys_exist() -> None:
    ensure_agent_keypair("doc_agent")
    assert private_key_path("doc_agent").exists()
    assert public_key_path("doc_agent").exists()


def test_jwt_issue_verify_and_revoke() -> None:
    issued = issue_token(
        agent_id="doc_agent",
        delegated_user="user_123",
        capabilities=["report:write"],
    )
    auth_context = verify_token(issued["access_token"])
    assert auth_context.agent_id == "doc_agent"
    assert auth_context.capabilities == ["report:write"]
    assert revoke_token(issued["jti"])
    with pytest.raises(Exception):
        verify_token(issued["access_token"])


def test_registry_can_register_agents() -> None:
    agent = get_agent("enterprise_data_agent")
    assert agent is not None
    assert agent.endpoint == "local://enterprise_data_agent"
    assert "feishu.wiki:read" in agent.static_capabilities


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
    assert authorized.auth_context is not None
    assert authorized.auth_context.capabilities == decision.effective_capabilities
    assert authorized.auth_context.agent_id == "enterprise_data_agent"


def test_doc_agent_delegation_is_allowed() -> None:
    client = TestClient(app)
    on_startup()
    register_demo_agents()
    token = issue_demo_token(
        "doc_agent",
        [
            "report:write",
            "feishu.contact:read",
            "feishu.wiki:read",
            "feishu.bitable:read",
            "web.public:read",
        ],
    )
    trace_id = f"trace_doc_allowed_{uuid4()}"
    response = client.post(
        "/delegate/call",
        headers={"Authorization": f"Bearer {token}"},
        json=DelegationEnvelope(
            trace_id=trace_id,
            request_id=f"req_doc_allowed_{uuid4()}",
            caller_agent_id="doc_agent",
            target_agent_id="enterprise_data_agent",
            task_type="read_enterprise_data",
            requested_capabilities=[
                "feishu.contact:read",
                "feishu.wiki:read",
                "feishu.bitable:read",
            ],
            delegation_chain=[
                DelegationHop(
                    from_actor="user",
                    to_agent_id="doc_agent",
                    task_type="generate_report",
                    delegated_capabilities=[
                        "report:write",
                        "feishu.contact:read",
                        "feishu.wiki:read",
                        "feishu.bitable:read",
                        "web.public:read",
                    ],
                    decision="root",
                )
            ],
            payload={},
        ).model_dump(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["agent_id"] == "enterprise_data_agent"
    assert "contacts" in body["result"]

    trace = client.get(f"/audit/traces/{body['trace_id']}").json()
    assert any(log["decision"] == "allow" for log in trace["logs"])
    assert len(trace["chain"]) >= 2
    assert trace["chain"][0]["from_actor"] == "user"
    assert trace["chain"][0]["to_agent_id"] == "doc_agent"
    assert any(hop["to_agent_id"] == "enterprise_data_agent" for hop in trace["chain"])
    decision_detail = trace["logs"][-1]["decision_detail"]
    assert decision_detail["effective_capabilities"] == [
        "feishu.bitable:read",
        "feishu.contact:read",
        "feishu.wiki:read",
    ]


def test_external_search_agent_delegation_is_denied() -> None:
    client = TestClient(app)
    on_startup()
    register_demo_agents()
    token = issue_demo_token("external_search_agent", ["web.public:read"])
    response = client.post(
        "/delegate/call",
        headers={"Authorization": f"Bearer {token}"},
            json=DelegationEnvelope(
                trace_id=f"trace_external_denied_{uuid4()}",
                request_id=f"req_external_denied_{uuid4()}",
            caller_agent_id="external_search_agent",
            target_agent_id="enterprise_data_agent",
            task_type="read_enterprise_data",
            requested_capabilities=[
                "feishu.contact:read",
                "feishu.wiki:read",
                "feishu.bitable:read",
            ],
            delegation_chain=[
                DelegationHop(
                    from_actor="user",
                    to_agent_id="external_search_agent",
                    task_type="attempt_enterprise_data_access",
                    delegated_capabilities=["web.public:read"],
                    decision="root",
                )
            ],
            payload={},
        ).model_dump(),
    )
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "delegation_denied"

    logs = client.get("/audit/logs").json()
    latest_deny = next(
        log
        for log in reversed(logs)
        if log["caller_agent_id"] == "external_search_agent" and log["decision"] == "deny"
    )
    assert latest_deny["decision_detail"]["missing_by"]["caller_token"] == [
        "feishu.bitable:read",
        "feishu.contact:read",
        "feishu.wiki:read",
    ]
    chain = client.get(f"/audit/traces/{latest_deny['trace_id']}/chain").json()["delegation_chain"]
    assert len(chain) >= 2
    assert chain[0]["from_actor"] == "user"
    assert chain[0]["to_agent_id"] == "external_search_agent"
    assert chain[-1]["delegated_capabilities"] == []
    assert chain[-1]["missing_capabilities"] == [
        "feishu.bitable:read",
        "feishu.contact:read",
        "feishu.wiki:read",
    ]


def test_audit_logs_include_decision_detail() -> None:
    client = TestClient(app)
    on_startup()
    register_demo_agents()
    token = issue_demo_token(
        "doc_agent",
        [
            "report:write",
            "feishu.contact:read",
            "feishu.wiki:read",
            "feishu.bitable:read",
            "web.public:read",
        ],
    )
    trace_id = f"trace_audit_detail_{uuid4()}"
    response = client.post(
        "/delegate/call",
        headers={"Authorization": f"Bearer {token}"},
        json=DelegationEnvelope(
            trace_id=trace_id,
            request_id=f"req_audit_detail_{uuid4()}",
            caller_agent_id="doc_agent",
            target_agent_id="enterprise_data_agent",
            task_type="read_enterprise_data",
            requested_capabilities=[
                "feishu.contact:read",
                "feishu.wiki:read",
                "feishu.bitable:read",
            ],
            delegation_chain=[
                DelegationHop(
                    from_actor="user",
                    to_agent_id="doc_agent",
                    task_type="generate_report",
                    delegated_capabilities=[
                        "report:write",
                        "feishu.contact:read",
                        "feishu.wiki:read",
                        "feishu.bitable:read",
                        "web.public:read",
                    ],
                    decision="root",
                )
            ],
            payload={},
        ).model_dump(),
    )
    assert response.status_code == 200
    trace = client.get(f"/audit/traces/{trace_id}").json()
    all_logs = client.get("/audit/logs").json()
    assert trace["logs"][-1]["decision_detail"]["decision"] == "allow"
    assert trace["chain"]
    assert any(log.get("decision_detail") for log in all_logs)


def test_app_core_does_not_import_examples_except_local_adapter() -> None:
    for module_name in [
        "app.delegation.service",
        "app.identity.jwt_service",
        "app.store.audit",
    ]:
        module = importlib.import_module(module_name)
        assert "examples." not in repr(module.__dict__.values())
