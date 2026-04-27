from __future__ import annotations

import httpx

from app.delegation.credential_crypto import verify_credential_integrity
from app.intent.crypto import verify_intent_node_signature
from app.store.delegation_credentials import get_credential
from app.store.intent_tree import row_to_intent_node
from tests.security_helpers import ALL_CAPABILITIES, GATEWAY_URL, run, run_root_task


def test_signatures_prove_credential_and_intent_issuers(servers) -> None:
    result = run(run_root_task("doc_agent", "generate_report", ALL_CAPABILITIES))
    trace = result["trace"]

    for credential_row in trace["delegation_credentials"]:
        credential = get_credential(credential_row["credential_id"])
        assert credential is not None
        assert verify_credential_integrity(credential)
        assert credential.signature_alg == "BUIAM-RS256"

    for intent_row in trace["intent_tree"]:
        node = row_to_intent_node(intent_row)
        assert verify_intent_node_signature(node)
        assert node.signature_alg == "BUIAM-RS256"


def test_audit_trace_contains_security_views_and_decision_detail(servers) -> None:
    result = run(run_root_task("doc_agent", "generate_report", ALL_CAPABILITIES))
    trace = httpx.get(f"{GATEWAY_URL}/audit/traces/{result['trace_id']}", timeout=10).json()

    assert {"logs", "chain", "delegation_credentials", "auth_events", "intent_tree"} <= set(trace)
    assert trace["logs"]
    assert trace["chain"]
    assert trace["delegation_credentials"]
    assert trace["auth_events"]
    assert trace["intent_tree"]
    assert any(log["decision_detail"].get("credential_id") for log in trace["logs"])
    assert any(log["decision_detail"].get("intent_node_id") for log in trace["logs"])


def test_failed_request_writes_deny_audit_and_auth_event_reason(servers) -> None:
    result = run(
        run_root_task(
            "external_search_agent",
            "search_then_read_enterprise",
            ["web.public:read"],
            user_capabilities=["web.public:read"],
            payload={"query": "public-only"},
        )
    )
    trace = result["trace"]

    deny_logs = [log for log in trace["logs"] if log["decision"] == "deny"]
    assert deny_logs
    assert any(log["decision_detail"]["missing_capabilities"] for log in deny_logs)
    assert any(event["identity_decision"] == "allow" for event in trace["auth_events"])
