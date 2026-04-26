from __future__ import annotations

import importlib
import threading
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import app.delegation.capabilities as capability_module
from app.delegation.capabilities import intersect_capabilities, parse_capabilities
from app.delegation.service import DelegationService
from app.intent.crypto import build_signed_intent_node
from app.intent.judge import IntentJudgeResult
from app.delegation.credential_crypto import verify_credential_integrity
from app.identity.keys import ensure_agent_keypair, private_key_path, public_key_path
from app.identity.jwt_service import issue_token, verify_token
from app.main import app, on_startup
from app.protocol import AuthContext, DelegationEnvelope, DelegationHop, IntentCommitment
from app.store.delegation_credentials import get_credential, list_credentials, upsert_credential
from app.store.registry import get_agent, upsert_agent
from app.store.tokens import get_token, revoke_token


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
    ensure_agent_keypair("user_123")


async def consistent_judge(**kwargs) -> IntentJudgeResult:
    return IntentJudgeResult(decision="Consistent", reason="test consistent")


async def drifted_judge(**kwargs) -> IntentJudgeResult:
    return IntentJudgeResult(decision="Drifted", reason="test drifted")


def issue_demo_token(agent_id: str, capabilities: list[str]) -> str:
    return issue_token(
        agent_id=agent_id,
        delegated_user="user_123",
        capabilities=capabilities,
        ttl_seconds=3600,
    )["access_token"]


def issue_user_token(capabilities: list[str]) -> str:
    return issue_token(
        agent_id="user_123",
        delegated_user="user_123",
        actor_type="user",
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
        user_capabilities=["report:write", "web.public:read"],
    )
    auth_context = verify_token(issued["access_token"])
    assert auth_context.agent_id == "doc_agent"
    assert auth_context.capabilities == ["report:write"]
    assert auth_context.user_capabilities == ["report:write", "web.public:read"]
    stored = get_token(issued["jti"])
    assert stored is not None
    assert stored.user_capabilities == ["report:write", "web.public:read"]
    assert stored.credential_id == issued["credential_id"]
    credential = get_credential(issued["credential_id"])
    assert credential is not None
    assert verify_credential_integrity(credential)
    assert auth_context.credential_id == credential.credential_id
    assert revoke_token(issued["jti"])
    revoked_credential = get_credential(issued["credential_id"])
    assert revoked_credential is not None
    assert revoked_credential.revoked
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


def test_authorize_reuses_known_capabilities_within_request(monkeypatch) -> None:
    calls = 0
    original_list_agents = capability_module.list_agents

    def counting_list_agents():
        nonlocal calls
        calls += 1
        return original_list_agents()

    monkeypatch.setattr(capability_module, "list_agents", counting_list_agents)

    decision = DelegationService().authorize(make_enterprise_envelope())

    assert decision.decision == "allow"
    assert calls == 1


def test_append_hop_shrinks_capabilities_and_context() -> None:
    envelope = make_enterprise_envelope()
    decision = DelegationService().authorize(envelope)
    authorized = DelegationService().append_hop(envelope, decision.effective_capabilities)
    assert authorized.delegation_chain[-1].delegated_capabilities == decision.effective_capabilities
    assert authorized.auth_context is not None
    assert authorized.auth_context.capabilities == decision.effective_capabilities
    assert authorized.auth_context.agent_id == "enterprise_data_agent"


def test_append_hop_creates_signed_child_credential() -> None:
    issued = issue_token(
        agent_id="doc_agent",
        delegated_user="user_123",
        capabilities=[
            "report:write",
            "feishu.contact:read",
            "feishu.wiki:read",
            "feishu.bitable:read",
        ],
        user_capabilities=[
            "report:write",
            "feishu.contact:read",
            "feishu.wiki:read",
            "feishu.bitable:read",
        ],
    )
    auth_context = verify_token(issued["access_token"])
    envelope = make_enterprise_envelope(auth_context=auth_context)
    decision = DelegationService().authorize(envelope)

    authorized = DelegationService().append_hop(envelope, decision.effective_capabilities)

    assert authorized.auth_context is not None
    assert authorized.auth_context.credential_id is not None
    child = get_credential(authorized.auth_context.credential_id)
    parent = get_credential(auth_context.credential_id)
    assert child is not None
    assert parent is not None
    assert child.parent_credential_id == parent.credential_id
    assert child.root_credential_id == parent.root_credential_id
    assert child.subject_id == "enterprise_data_agent"
    assert child.exp == parent.exp
    assert set(child.capabilities) == set(decision.effective_capabilities)
    assert verify_credential_integrity(child)


def test_tampered_child_credential_is_rejected() -> None:
    issued = issue_token(
        agent_id="doc_agent",
        delegated_user="user_123",
        capabilities=[
            "report:write",
            "feishu.contact:read",
            "feishu.wiki:read",
            "feishu.bitable:read",
            "web.public:read",
        ],
    )
    auth_context = verify_token(issued["access_token"])
    envelope = make_enterprise_envelope(auth_context=auth_context)
    decision = DelegationService().authorize(envelope)
    authorized = DelegationService().append_hop(envelope, decision.effective_capabilities)
    child = get_credential(authorized.auth_context.credential_id)
    assert child is not None
    upsert_credential(child.model_copy(update={"capabilities": [*child.capabilities, "web.public:read"]}))

    decision = DelegationService().authorize(
        make_enterprise_envelope(
            auth_context=authorized.auth_context,
            chain=[
                *authorized.delegation_chain,
            ],
        ).model_copy(update={"caller_agent_id": "enterprise_data_agent"})
    )

    assert decision.decision == "deny"
    assert "AUTH_CREDENTIAL_INVALID" in decision.reason


def test_child_credential_cannot_outlive_parent() -> None:
    issued = issue_token(
        agent_id="doc_agent",
        delegated_user="user_123",
        capabilities=[
            "report:write",
            "feishu.contact:read",
            "feishu.wiki:read",
            "feishu.bitable:read",
        ],
    )
    auth_context = verify_token(issued["access_token"])
    envelope = make_enterprise_envelope(auth_context=auth_context)
    decision = DelegationService().authorize(envelope)
    authorized = DelegationService().append_hop(envelope, decision.effective_capabilities)
    child = get_credential(authorized.auth_context.credential_id)
    assert child is not None
    upsert_credential(child.model_copy(update={"exp": auth_context.exp + 60}))

    decision = DelegationService().authorize(
        make_enterprise_envelope(
            auth_context=authorized.auth_context,
            chain=[*authorized.delegation_chain],
        ).model_copy(update={"caller_agent_id": "enterprise_data_agent"})
    )

    assert decision.decision == "deny"
    assert "AUTH_CREDENTIAL_INVALID" in decision.reason


def test_revoking_root_credential_revokes_descendants() -> None:
    issued = issue_token(
        agent_id="doc_agent",
        delegated_user="user_123",
        capabilities=[
            "report:write",
            "feishu.contact:read",
            "feishu.wiki:read",
            "feishu.bitable:read",
        ],
    )
    auth_context = verify_token(issued["access_token"])
    envelope = make_enterprise_envelope(auth_context=auth_context)
    decision = DelegationService().authorize(envelope)
    authorized = DelegationService().append_hop(envelope, decision.effective_capabilities)

    assert revoke_token(issued["jti"], reason="test_revoke")

    root = get_credential(auth_context.credential_id)
    child = get_credential(authorized.auth_context.credential_id)
    assert root is not None and root.revoked
    assert child is not None and child.revoked


def test_revoke_token_cancels_running_sleep_task() -> None:
    client = TestClient(app)
    issued = issue_token(
        agent_id="doc_agent",
        delegated_user="user_123",
        capabilities=["report:write"],
    )
    trace_id = f"trace_sleep_cancel_{uuid4()}"
    result: dict[str, object] = {}

    def call_sleep() -> None:
        result["response"] = client.post(
            "/delegate/call",
            headers={"Authorization": f"Bearer {issued['access_token']}"},
            json=dict(
                trace_id=trace_id,
                request_id=f"req_sleep_cancel_{uuid4()}",
                caller_agent_id="doc_agent",
                target_agent_id="doc_agent",
                task_type="sleep_task",
                requested_capabilities=["report:write"],
                delegation_chain=[],
                payload={"seconds": 30},
            ),
        )

    thread = threading.Thread(target=call_sleep)
    thread.start()
    deadline = time.time() + 5
    while time.time() < deadline and not list_credentials(trace_id=trace_id):
        time.sleep(0.05)

    revoke_response = client.post(
        f"/identity/tokens/{issued['jti']}/revoke",
        json={"reason": "test_revoke"},
    )
    thread.join(timeout=5)

    assert revoke_response.status_code == 200
    assert not thread.is_alive()
    response = result["response"]
    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "TASK_CANCELLED"


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


def test_delegation_is_denied_when_user_capabilities_do_not_cover_request() -> None:
    client = TestClient(app)
    on_startup()
    register_demo_agents()
    issued = issue_token(
        agent_id="doc_agent",
        delegated_user="user_123",
        capabilities=["report:write", "feishu.contact:read"],
        user_capabilities=["report:write"],
        ttl_seconds=3600,
    )
    trace_id = f"trace_user_caps_denied_{uuid4()}"
    response = client.post(
        "/delegate/call",
        headers={"Authorization": f"Bearer {issued['access_token']}"},
        json=DelegationEnvelope(
            trace_id=trace_id,
            request_id=f"req_user_caps_denied_{uuid4()}",
            caller_agent_id="doc_agent",
            target_agent_id="enterprise_data_agent",
            task_type="read_enterprise_data",
            requested_capabilities=["feishu.contact:read"],
            delegation_chain=[
                DelegationHop(
                    from_actor="user",
                    to_agent_id="doc_agent",
                    task_type="generate_report",
                    delegated_capabilities=["report:write", "feishu.contact:read"],
                    decision="root",
                )
            ],
            payload={},
        ).model_dump(),
    )
    assert response.status_code == 403
    latest_log = client.get(f"/audit/traces/{trace_id}").json()["logs"][-1]
    detail = latest_log["decision_detail"]
    assert detail["caller_token_capabilities"] == ["feishu.contact:read", "report:write"]
    assert detail["user_capabilities"] == ["report:write"]
    assert detail["missing_by"]["user"] == ["feishu.contact:read"]


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
    assert trace["logs"][-1]["decision_detail"]["auth_event_recorded"] is True
    assert trace["auth_events"][-1]["identity_decision"] == "allow"
    assert trace["auth_events"][-1]["token_fingerprint"]
    assert "access_token" not in trace["auth_events"][-1]
    assert trace["chain"]
    assert any(log.get("decision_detail") for log in all_logs)


def test_missing_authorization_is_recorded_as_auth_event() -> None:
    client = TestClient(app)
    on_startup()
    register_demo_agents()
    trace_id = f"trace_auth_missing_{uuid4()}"
    request_id = f"req_auth_missing_{uuid4()}"
    response = client.post(
        "/delegate/call",
        json=DelegationEnvelope(
            trace_id=trace_id,
            request_id=request_id,
            caller_agent_id="doc_agent",
            target_agent_id="enterprise_data_agent",
            task_type="read_enterprise_data",
            requested_capabilities=["feishu.wiki:read"],
            payload={},
        ).model_dump(),
    )
    assert response.status_code == 401
    auth_events = client.get(f"/audit/auth-events?trace_id={trace_id}").json()
    assert auth_events[-1]["request_id"] == request_id
    assert auth_events[-1]["identity_decision"] == "deny"
    assert auth_events[-1]["error_code"] == "AUTH_TOKEN_MISSING"


def test_revoked_token_is_recorded_as_auth_event() -> None:
    client = TestClient(app)
    on_startup()
    register_demo_agents()
    issued = issue_token(
        agent_id="doc_agent",
        delegated_user="user_123",
        capabilities=["feishu.wiki:read"],
    )
    assert revoke_token(issued["jti"])
    trace_id = f"trace_auth_revoked_{uuid4()}"
    response = client.post(
        "/delegate/call",
        headers={"Authorization": f"Bearer {issued['access_token']}"},
        json=DelegationEnvelope(
            trace_id=trace_id,
            request_id=f"req_auth_revoked_{uuid4()}",
            caller_agent_id="doc_agent",
            target_agent_id="enterprise_data_agent",
            task_type="read_enterprise_data",
            requested_capabilities=["feishu.wiki:read"],
            payload={},
        ).model_dump(),
    )
    assert response.status_code == 401
    auth_event = client.get(f"/audit/auth-events?trace_id={trace_id}").json()[-1]
    assert auth_event["identity_decision"] == "deny"
    assert auth_event["error_code"] == "AUTH_TOKEN_REVOKED"
    assert auth_event["is_revoked"] is True


def test_malformed_token_is_recorded_as_auth_event() -> None:
    client = TestClient(app)
    on_startup()
    register_demo_agents()
    trace_id = f"trace_auth_malformed_{uuid4()}"
    response = client.post(
        "/delegate/call",
        headers={"Authorization": "Bearer not-a-jwt"},
        json=DelegationEnvelope(
            trace_id=trace_id,
            request_id=f"req_auth_malformed_{uuid4()}",
            caller_agent_id="doc_agent",
            target_agent_id="enterprise_data_agent",
            task_type="read_enterprise_data",
            requested_capabilities=["feishu.wiki:read"],
            payload={},
        ).model_dump(),
    )
    assert response.status_code == 401
    auth_event = client.get(f"/audit/auth-events?trace_id={trace_id}").json()[-1]
    assert auth_event["identity_decision"] == "deny"
    assert auth_event["error_code"] == "AUTH_TOKEN_MALFORMED"


def test_intent_node_hash_signature_and_tree_are_recorded(monkeypatch) -> None:
    import app.intent.service as intent_service

    monkeypatch.setattr(intent_service, "judge_intent", consistent_judge)
    client = TestClient(app)
    on_startup()
    register_demo_agents()
    token = issue_demo_token("doc_agent", ["web.public:read"])
    trace_id = f"trace_intent_tree_{uuid4()}"
    root_node = build_signed_intent_node(
        parent_node_id=None,
        actor_id="user_123",
        actor_type="user",
        target_agent_id="doc_agent",
        task_type="ask_weather",
        intent_commitment=IntentCommitment(
            intent="查询今天公开天气信息",
            description="用户想了解今天的天气情况",
            constraints=["仅使用公开网页信息"],
        ),
    )
    root_response = client.post(
        "/delegate/call",
        headers={"Authorization": f"Bearer {token}"},
        json=DelegationEnvelope(
            trace_id=trace_id,
            request_id=f"req_intent_root_{uuid4()}",
            caller_agent_id="doc_agent",
            target_agent_id="external_search_agent",
            task_type="search_public_web",
            requested_capabilities=["web.public:read"],
            intent_node=root_node,
            delegation_chain=[
                DelegationHop(
                    from_actor="user",
                    to_agent_id="doc_agent",
                    task_type="ask_weather",
                    delegated_capabilities=["web.public:read"],
                    decision="root",
                )
            ],
            payload={"query": "today weather"},
        ).model_dump(),
    )
    assert root_response.status_code == 200

    child_node = build_signed_intent_node(
        parent_node_id=root_node.node_id,
        actor_id="doc_agent",
        actor_type="agent",
        target_agent_id="external_search_agent",
        task_type="search_public_web",
        intent_commitment=IntentCommitment(
            intent="搜索公开网页获取今天的天气信息",
            description="调用外部检索 Agent 获取公开天气结果",
            constraints=["仅使用公开网页信息"],
        ),
    )
    child_response = client.post(
        "/delegate/call",
        headers={"Authorization": f"Bearer {token}"},
        json=DelegationEnvelope(
            trace_id=trace_id,
            request_id=f"req_intent_child_{uuid4()}",
            caller_agent_id="doc_agent",
            target_agent_id="external_search_agent",
            task_type="search_public_web",
            requested_capabilities=["web.public:read"],
            intent_node=child_node,
            delegation_chain=[
                DelegationHop(
                    from_actor="user",
                    to_agent_id="doc_agent",
                    task_type="ask_weather",
                    delegated_capabilities=["web.public:read"],
                    decision="root",
                )
            ],
            payload={"query": "today weather"},
        ).model_dump(),
    )
    assert child_response.status_code == 200
    intent_tree = client.get(f"/audit/traces/{trace_id}/intent-tree").json()["intent_tree"]
    assert {node["node_id"] for node in intent_tree} >= {root_node.node_id, child_node.node_id}
    trace = client.get(f"/audit/traces/{trace_id}").json()
    assert trace["logs"][-1]["decision_detail"]["intent_judge_decision"] == "Consistent"


def test_intent_drift_rejects_only_current_branch(monkeypatch) -> None:
    import app.intent.service as intent_service

    async def conditional_judge(**kwargs) -> IntentJudgeResult:
        if "企业" in kwargs["child_intent"]:
            return IntentJudgeResult(decision="Drifted", reason="test drifted")
        return IntentJudgeResult(decision="Consistent", reason="test consistent")

    monkeypatch.setattr(intent_service, "judge_intent", conditional_judge)
    client = TestClient(app)
    on_startup()
    register_demo_agents()
    doc_token = issue_demo_token("doc_agent", ["web.public:read"])
    external_token = issue_demo_token("external_search_agent", ["web.public:read"])
    trace_id = f"trace_intent_drift_{uuid4()}"
    root_node = build_signed_intent_node(
        parent_node_id=None,
        actor_id="user_123",
        actor_type="user",
        target_agent_id="doc_agent",
        task_type="ask_weather",
        intent_commitment=IntentCommitment(intent="查询今天公开天气信息"),
    )
    assert client.post(
        "/delegate/call",
        headers={"Authorization": f"Bearer {doc_token}"},
        json=DelegationEnvelope(
            trace_id=trace_id,
            request_id=f"req_intent_root_{uuid4()}",
            caller_agent_id="doc_agent",
            target_agent_id="external_search_agent",
            task_type="search_public_web",
            requested_capabilities=["web.public:read"],
            intent_node=root_node,
            payload={"query": "today weather"},
        ).model_dump(),
    ).status_code == 200

    drift_node = build_signed_intent_node(
        parent_node_id=root_node.node_id,
        actor_id="external_search_agent",
        actor_type="agent",
        target_agent_id="enterprise_data_agent",
        task_type="read_enterprise_data",
        intent_commitment=IntentCommitment(intent="读取企业通讯录和多维表格数据"),
    )
    response = client.post(
        "/delegate/call",
        headers={"Authorization": f"Bearer {external_token}"},
        json=DelegationEnvelope(
            trace_id=trace_id,
            request_id=f"req_intent_drift_{uuid4()}",
            caller_agent_id="external_search_agent",
            target_agent_id="enterprise_data_agent",
            task_type="read_enterprise_data",
            requested_capabilities=["feishu.contact:read"],
            intent_node=drift_node,
            delegation_chain=[
                DelegationHop(
                    from_actor="user",
                    to_agent_id="doc_agent",
                    task_type="ask_weather",
                    delegated_capabilities=["web.public:read"],
                    decision="root",
                ),
                DelegationHop(
                    from_actor="doc_agent",
                    to_agent_id="external_search_agent",
                    task_type="search_public_web",
                    delegated_capabilities=["web.public:read"],
                    decision="allow",
                ),
            ],
            payload={},
        ).model_dump(),
    )
    assert response.status_code == 403
    assert response.json()["detail"]["error_code"] == "INTENT_DRIFTED"
    intent_tree = client.get(f"/audit/traces/{trace_id}/intent-tree").json()["intent_tree"]
    assert any(node["node_id"] == drift_node.node_id and node["judge_decision"] == "Drifted" for node in intent_tree)


def test_tampered_intent_node_is_rejected(monkeypatch) -> None:
    import app.intent.service as intent_service

    monkeypatch.setattr(intent_service, "judge_intent", consistent_judge)
    client = TestClient(app)
    on_startup()
    register_demo_agents()
    token = issue_demo_token("doc_agent", ["web.public:read"])
    node = build_signed_intent_node(
        parent_node_id=None,
        actor_id="user_123",
        actor_type="user",
        target_agent_id="doc_agent",
        task_type="ask_weather",
        intent_commitment=IntentCommitment(intent="查询今天公开天气信息"),
    )
    tampered = node.model_copy(
        update={"intent_commitment": IntentCommitment(intent="读取企业通讯录")}
    )
    response = client.post(
        "/delegate/call",
        headers={"Authorization": f"Bearer {token}"},
        json=DelegationEnvelope(
            trace_id=f"trace_intent_tamper_{uuid4()}",
            request_id=f"req_intent_tamper_{uuid4()}",
            caller_agent_id="doc_agent",
            target_agent_id="external_search_agent",
            task_type="search_public_web",
            requested_capabilities=["web.public:read"],
            intent_node=tampered,
            payload={"query": "today weather"},
        ).model_dump(),
    )
    assert response.status_code == 403
    assert response.json()["detail"]["error_code"] == "INTENT_CHAIN_INVALID"


def test_real_llm_root_task_and_normal_delegation_records_intents() -> None:
    client = TestClient(app)
    on_startup()
    register_demo_agents()
    trace_id = f"trace_real_llm_normal_{uuid4()}"
    user_token = issue_user_token([
        "report:write",
        "feishu.contact:read",
        "feishu.wiki:read",
        "feishu.bitable:read",
        "web.public:read",
    ])
    root_response = client.post(
        "/delegate/root-task",
        headers={"Authorization": f"Bearer {user_token}"},
        json={
            "trace_id": trace_id,
            "target_agent_id": "doc_agent",
            "task_type": "generate_report",
            "user_task": "请基于企业通讯录、知识库和多维表格生成一份飞书协作报告",
            "requested_capabilities": [
                "report:write",
                "feishu.contact:read",
                "feishu.wiki:read",
                "feishu.bitable:read",
                "web.public:read",
            ],
            "payload": {"topic": "飞书 AI 协作季度报告"},
        },
    )
    assert root_response.status_code == 200
    root_trace = client.get(f"/audit/traces/{trace_id}").json()
    root_node_id = root_trace["intent_tree"][-1]["node_id"]
    doc_token = issue_demo_token(
        "doc_agent",
        ["report:write", "feishu.contact:read", "feishu.wiki:read", "feishu.bitable:read", "web.public:read"],
    )
    child_response = client.post(
        "/delegate/call",
        headers={"Authorization": f"Bearer {doc_token}"},
        json=DelegationEnvelope(
            trace_id=trace_id,
            request_id=f"req_real_llm_normal_{uuid4()}",
            caller_agent_id="doc_agent",
            target_agent_id="enterprise_data_agent",
            task_type="read_enterprise_data",
            requested_capabilities=["feishu.contact:read", "feishu.wiki:read", "feishu.bitable:read"],
            delegation_chain=[DelegationHop.model_validate(root_trace["chain"][0])],
            payload={
                "user_task": "请基于企业通讯录、知识库和多维表格生成一份飞书协作报告",
                "parent_intent_node_id": root_node_id,
            },
        ).model_dump(),
    )
    assert child_response.status_code == 200
    trace = client.get(f"/audit/traces/{trace_id}").json()
    assert len(trace["auth_events"]) >= 2
    assert len(trace["intent_tree"]) >= 2
    assert trace["chain"][0]["from_actor"] == "user_123"
    latest_detail = trace["logs"][-1]["decision_detail"]
    assert latest_detail["root_intent"]
    assert latest_detail["parent_intent"]
    assert latest_detail["child_intent"]
    assert latest_detail["intent_generation_model"]
    assert latest_detail["intent_judge_decision"] in {"Consistent", "Drifted"}


def test_real_llm_root_task_and_unauthorized_branch_records_intents() -> None:
    client = TestClient(app)
    on_startup()
    register_demo_agents()
    trace_id = f"trace_real_llm_deny_{uuid4()}"
    user_token = issue_user_token(["report:write", "web.public:read"])
    root_response = client.post(
        "/delegate/root-task",
        headers={"Authorization": f"Bearer {user_token}"},
        json={
            "trace_id": trace_id,
            "target_agent_id": "doc_agent",
            "task_type": "ask_weather",
            "user_task": "请检索今天的公开天气信息",
            "requested_capabilities": ["report:write", "web.public:read"],
            "payload": {"query": "今天的天气怎么样"},
        },
    )
    assert root_response.status_code == 200
    trace = client.get(f"/audit/traces/{trace_id}").json()
    doc_token = issue_demo_token("doc_agent", ["report:write", "web.public:read"])
    external_response = client.post(
        "/delegate/call",
        headers={"Authorization": f"Bearer {doc_token}"},
        json=DelegationEnvelope(
            trace_id=trace_id,
            request_id=f"req_real_llm_external_{uuid4()}",
            caller_agent_id="doc_agent",
            target_agent_id="external_search_agent",
            task_type="search_public_web",
            requested_capabilities=["web.public:read"],
            delegation_chain=[DelegationHop.model_validate(trace["chain"][0])],
            payload={"user_task": "请检索今天的公开天气信息", "parent_intent_node_id": trace["intent_tree"][-1]["node_id"]},
        ).model_dump(),
    )
    assert external_response.status_code == 200
    trace = client.get(f"/audit/traces/{trace_id}").json()
    external_token = issue_demo_token("external_search_agent", ["web.public:read"])
    denied_response = client.post(
        "/delegate/call",
        headers={"Authorization": f"Bearer {external_token}"},
        json=DelegationEnvelope(
            trace_id=trace_id,
            request_id=f"req_real_llm_deny_{uuid4()}",
            caller_agent_id="external_search_agent",
            target_agent_id="enterprise_data_agent",
            task_type="read_enterprise_data",
            requested_capabilities=["feishu.contact:read", "feishu.wiki:read", "feishu.bitable:read"],
            delegation_chain=[
                DelegationHop.model_validate(trace["chain"][0]),
                DelegationHop.model_validate(trace["chain"][-1]),
            ],
            payload={"user_task": "请检索今天的公开天气信息", "parent_intent_node_id": trace["intent_tree"][-1]["node_id"]},
        ).model_dump(),
    )
    assert denied_response.status_code == 403
    trace = client.get(f"/audit/traces/{trace_id}").json()
    assert len(trace["auth_events"]) >= 3
    latest_detail = trace["logs"][-1]["decision_detail"]
    assert latest_detail["root_intent"]
    assert latest_detail["parent_intent"]
    assert latest_detail["child_intent"]
    assert len(trace["intent_tree"]) >= (3 if latest_detail["intent_judge_decision"] == "Drifted" else 2)
    assert latest_detail["intent_judge_decision"] or latest_detail["missing_capabilities"]


def test_app_core_does_not_import_examples_except_local_adapter() -> None:
    for module_name in [
        "app.delegation.service",
        "app.identity.jwt_service",
        "app.store.audit",
    ]:
        module = importlib.import_module(module_name)
        assert "examples." not in repr(module.__dict__.values())
