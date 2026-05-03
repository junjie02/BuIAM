from __future__ import annotations

import asyncio
from uuid import uuid4

import httpx
import pytest

from app.delegation.credential_crypto import auth_context_from_credential
from app.protocol import DelegationEnvelope, DelegationHop
from app.store.delegation_credentials import get_credential, upsert_credential

from tests.security_helpers import (
    ALL_CAPABILITIES,
    GATEWAY_URL,
    USER_ID,
    agent_envelope,
    find_trace_credential,
    find_trace_intent,
    issue_agent_token,
    issue_user_token,
    root_hop_to_doc,
    run,
    run_root_task,
)


class TestFailureHandling:
    """FATAL vs recoverable error classification, suggested_agents, audit gap fixes."""

    def test_missing_bearer_is_fatal(self, servers) -> None:
        t = str(uuid4())
        r = httpx.post(
            f"{GATEWAY_URL}/a2a/agents/enterprise_data_agent/tasks",
            json=agent_envelope(
                trace_id=t, caller_agent_id="doc_agent",
                target_agent_id="enterprise_data_agent",
                task_type="read_enterprise_data",
                requested_capabilities=["feishu.contact:read"],
                auth_context=None,
            ).model_dump(),
            timeout=10,
        )
        audit = httpx.get(f"{GATEWAY_URL}/audit/traces/{t}", timeout=10).json()
        assert r.status_code == 401
        detail = r.json()["detail"]
        assert "recoverable" not in detail, "missing bearer should be FATAL"
        assert any(e["error_code"] == "AUTH_TOKEN_MISSING" for e in audit["auth_events"])

    def test_expired_token_is_fatal(self, servers) -> None:
        short = issue_user_token(capabilities=ALL_CAPABILITIES, ttl_seconds=1)
        run(asyncio.sleep(1.5))
        r = httpx.post(
            f"{GATEWAY_URL}/a2a/root-tasks",
            json={
                "trace_id": str(uuid4()), "target_agent_id": "doc_agent",
                "task_type": "generate_report", "user_task": "expired",
                "requested_capabilities": ["web.public:read"], "payload": {},
            },
            headers={"Authorization": f"Bearer {short['access_token']}"},
            timeout=10,
        )
        assert r.status_code == 401
        detail = r.json()["detail"]
        assert detail["error_code"] == "AUTH_TOKEN_EXPIRED"
        assert "recoverable" not in detail

    def test_malformed_token_is_fatal(self, servers) -> None:
        t = str(uuid4())
        r = httpx.post(
            f"{GATEWAY_URL}/a2a/agents/enterprise_data_agent/tasks",
            json=agent_envelope(
                trace_id=t, caller_agent_id="doc_agent",
                target_agent_id="enterprise_data_agent",
                task_type="read_enterprise_data",
                requested_capabilities=["feishu.contact:read"],
                auth_context=None,
            ).model_dump(),
            headers={"Authorization": "Bearer not-a-valid-jwt"},
            timeout=10,
        )
        assert r.status_code == 401
        detail = r.json()["detail"]
        assert detail["error_code"] == "AUTH_TOKEN_MALFORMED"
        assert "recoverable" not in detail

    def test_cap_escalation_is_recoverable(self, servers) -> None:
        limited = issue_user_token(capabilities=["web.public:read"])
        r = httpx.post(
            f"{GATEWAY_URL}/a2a/root-tasks",
            json={
                "trace_id": str(uuid4()), "target_agent_id": "doc_agent",
                "task_type": "generate_report", "user_task": "escalation",
                "requested_capabilities": ["feishu.contact:read"],
                "payload": {"topic": "test"},
            },
            headers={"Authorization": f"Bearer {limited['access_token']}"},
            timeout=10,
        )
        assert r.status_code == 403
        detail = r.json()["detail"]
        assert detail.get("recoverable") is True, f"cap escalation should be recoverable: {detail}"

    def test_cap_denied_suggests_alternative_agents(self, servers) -> None:
        result = run(run_root_task("doc_agent", "generate_report", ALL_CAPABILITIES))
        doc_cred = find_trace_credential(result["trace"], subject_id="doc_agent")
        root_intent = find_trace_intent(result["trace"], actor_id="user_123", target_agent_id="doc_agent")
        doc_tok = issue_agent_token(agent_id="doc_agent", capabilities=ALL_CAPABILITIES)
        cred = get_credential(doc_cred["credential_id"])
        assert cred is not None

        r = httpx.post(
            f"{GATEWAY_URL}/a2a/agents/external_search_agent/tasks",
            json=agent_envelope(
                trace_id=result["trace_id"], caller_agent_id="doc_agent",
                target_agent_id="external_search_agent", task_type="search",
                requested_capabilities=["feishu.contact:read"],
                auth_context=auth_context_from_credential(cred),
                delegation_chain=[root_hop_to_doc()],
                payload={"parent_intent_node_id": root_intent["node_id"]},
            ).model_dump(),
            headers={"Authorization": f"Bearer {doc_tok['access_token']}"},
            timeout=10,
        )
        assert r.status_code == 403
        detail = r.json()["detail"]
        assert detail["error_code"] == "AUTH_DELEGATION_DENIED"
        assert detail.get("recoverable") is True
        assert len(detail.get("suggested_agents", [])) > 0
        assert "enterprise_data_agent" in detail["suggested_agents"]
        assert "missing_by" in detail

    def test_tampered_credential_is_fatal(self, servers) -> None:
        result = run(run_root_task("doc_agent", "generate_report", ALL_CAPABILITIES))
        doc_cred = find_trace_credential(result["trace"], subject_id="doc_agent")
        root_intent = find_trace_intent(result["trace"], actor_id="user_123", target_agent_id="doc_agent")
        doc_tok = issue_agent_token(agent_id="doc_agent", capabilities=ALL_CAPABILITIES)
        cred = get_credential(doc_cred["credential_id"])
        assert cred is not None
        upsert_credential(cred.model_copy(update={"content_hash": "bad-hash"}))

        r = httpx.post(
            f"{GATEWAY_URL}/a2a/agents/enterprise_data_agent/tasks",
            json=agent_envelope(
                trace_id=result["trace_id"], caller_agent_id="doc_agent",
                target_agent_id="enterprise_data_agent", task_type="read",
                requested_capabilities=["feishu.contact:read"],
                auth_context=auth_context_from_credential(cred),
                delegation_chain=[root_hop_to_doc()],
                payload={"parent_intent_node_id": root_intent["node_id"]},
            ).model_dump(),
            headers={"Authorization": f"Bearer {doc_tok['access_token']}"},
            timeout=10,
        )
        assert r.status_code == 403
        detail = r.json()["detail"]
        assert detail["error_code"] == "AUTH_CREDENTIAL_INVALID"
        assert detail.get("recoverable") is not True

    def test_cross_trace_credential_is_fatal(self, servers) -> None:
        result1 = run(run_root_task("doc_agent", "generate_report", ALL_CAPABILITIES))
        result2 = run(run_root_task("doc_agent", "generate_report", ALL_CAPABILITIES))
        doc_cred2 = find_trace_credential(result2["trace"], subject_id="doc_agent")
        root_intent1 = find_trace_intent(result1["trace"], actor_id="user_123", target_agent_id="doc_agent")
        doc_tok = issue_agent_token(agent_id="doc_agent", capabilities=ALL_CAPABILITIES)
        cred2 = get_credential(doc_cred2["credential_id"])
        assert cred2 is not None

        r = httpx.post(
            f"{GATEWAY_URL}/a2a/agents/enterprise_data_agent/tasks",
            json=agent_envelope(
                trace_id=result1["trace_id"], caller_agent_id="doc_agent",
                target_agent_id="enterprise_data_agent", task_type="read",
                requested_capabilities=["feishu.contact:read"],
                auth_context=auth_context_from_credential(cred2),
                delegation_chain=[root_hop_to_doc()],
                payload={"parent_intent_node_id": root_intent1["node_id"]},
            ).model_dump(),
            headers={"Authorization": f"Bearer {doc_tok['access_token']}"},
            timeout=10,
        )
        assert r.status_code == 403
        detail = r.json()["detail"]
        assert detail["error_code"] == "AUTH_CREDENTIAL_INVALID"
        assert detail.get("recoverable") is not True

    def test_unknown_agent_has_audit_log(self, servers) -> None:
        doc_tok = issue_agent_token(agent_id="doc_agent", capabilities=ALL_CAPABILITIES)
        t = str(uuid4())
        r = httpx.post(
            f"{GATEWAY_URL}/a2a/agents/not_registered_xyz/tasks",
            json=agent_envelope(
                trace_id=t, caller_agent_id="doc_agent",
                target_agent_id="not_registered_xyz", task_type="test",
                requested_capabilities=["web.public:read"],
                auth_context=None,
            ).model_dump(),
            headers={"Authorization": f"Bearer {doc_tok['access_token']}"},
            timeout=10,
        )
        assert r.status_code == 404
        assert r.json()["detail"]["error_code"] == "AGENT_NOT_REGISTERED"
        audit = httpx.get(f"{GATEWAY_URL}/audit/traces/{t}", timeout=10).json()
        assert len(audit["logs"]) > 0, "AGENT_NOT_REGISTERED should produce audit log"
        assert any("AGENT_NOT_REGISTERED" in log["reason"] for log in audit["logs"])

    def test_actor_mismatch_has_audit_log(self, servers) -> None:
        user_tok = issue_user_token(capabilities=ALL_CAPABILITIES)
        t = str(uuid4())
        r = httpx.post(
            f"{GATEWAY_URL}/a2a/agents/enterprise_data_agent/tasks",
            json=DelegationEnvelope(
                trace_id=t, request_id=str(uuid4()), caller_agent_id="doc_agent",
                target_agent_id="enterprise_data_agent", task_type="read",
                requested_capabilities=["feishu.contact:read"],
                delegation_chain=[root_hop_to_doc()],
                auth_context=None,
                payload={"user_task": "actor mismatch test"},
            ).model_dump(),
            headers={"Authorization": f"Bearer {user_tok['access_token']}"},
            timeout=10,
        )
        assert r.status_code == 403
        detail = r.json()["detail"]
        assert detail["error_code"] == "AUTH_ACTOR_TYPE_INVALID"
        assert "recoverable" not in detail
        audit = httpx.get(f"{GATEWAY_URL}/audit/traces/{t}", timeout=10).json()
        assert len(audit["logs"]) > 0, "AUTH_ACTOR_TYPE_INVALID should produce audit log"
