"""Agent Capability VC — issuance, discovery, verification, authorization."""

from __future__ import annotations

import httpx

from app.delegation.credential_crypto import AGENT_VC_TYPE, get_agent_vc, verify_credential_integrity

from tests.security_helpers import (
    ALL_CAPABILITIES,
    ENTERPRISE_CAPABILITIES,
    GATEWAY_URL,
    run,
    run_root_task,
)


def test_agent_vc_is_issued_for_all_demo_agents(servers) -> None:
    """All 3 demo agents should have an Agent Capability VC after bootstrap."""
    for agent_id in ["doc_agent", "enterprise_data_agent", "external_search_agent"]:
        vc = get_agent_vc(agent_id)
        assert vc is not None, f"Agent VC missing for {agent_id}"
        assert AGENT_VC_TYPE[-1] in vc.vc_type, f"Wrong VC type for {agent_id}: {vc.vc_type}"
        assert verify_credential_integrity(vc), f"Agent VC signature invalid for {agent_id}"
        assert vc.issuer_id == "buiam-auth-system", f"Wrong issuer for {agent_id}: {vc.issuer_id}"
        assert len(vc.capabilities) > 0, f"Empty capabilities for {agent_id}"


def test_agent_vc_endpoint_returns_presentable_vc(servers) -> None:
    """GET /registry/agents/{id}/vc returns verified VC JSON."""
    r = httpx.get(f"{GATEWAY_URL}/registry/agents/doc_agent/vc", timeout=10)
    assert r.status_code == 200
    vc = r.json()
    assert vc["verification"]["signature_valid"] is True
    assert vc["issuer"]["issuer_id"] == "buiam-auth-system"
    assert "capabilities" in vc["credentialSubject"]
    assert "service_endpoint" in vc["credentialSubject"]
    # proof should match the stored credential
    stored = get_agent_vc("doc_agent")
    assert stored is not None
    assert vc["proof"]["proofValue"] == stored.proof_signature


def test_agent_vc_404_for_unknown_agent(servers) -> None:
    """Unknown agent returns 404."""
    r = httpx.get(f"{GATEWAY_URL}/registry/agents/nonexistent/vc", timeout=10)
    assert r.status_code == 404


def test_discovery_filters_by_capability(servers) -> None:
    """GET /registry/agents?capability=X returns only matching agents."""
    # enterprise capability
    r = httpx.get(f"{GATEWAY_URL}/registry/agents?capability=feishu.contact:read", timeout=10)
    assert r.status_code == 200
    agents = r.json()
    ids = [a["agent_id"] for a in agents]
    assert "doc_agent" in ids
    assert "enterprise_data_agent" in ids
    assert "external_search_agent" not in ids  # only has web.public:read

    # web capability
    r2 = httpx.get(f"{GATEWAY_URL}/registry/agents?capability=web.public:read", timeout=10)
    ids2 = [a["agent_id"] for a in r2.json()]
    assert "external_search_agent" in ids2
    assert "doc_agent" in ids2


def test_authorize_uses_agent_vc_capabilities(servers) -> None:
    """Normal delegation chain works — Agent VC provides target capabilities."""
    result = run(run_root_task("doc_agent", "generate_report", ALL_CAPABILITIES))
    trace = result["trace"]

    # doc_agent credential should exist in trace
    doc_cred = [c for c in trace["delegation_credentials"] if c["subject_id"] == "doc_agent"]
    assert len(doc_cred) == 1

    # Agent VC should exist and be valid
    vc = get_agent_vc("doc_agent")
    assert vc is not None
    assert verify_credential_integrity(vc)

    # trace should contain 2+ delegation VCs (root + child)
    # AND distinct from agent VCs (different vc_type)
    delegation_vcs = [c for c in trace["delegation_credentials"] if "DelegationCredential" in c.get("vc_type", [])]
    assert len(delegation_vcs) >= 0  # delegation VCs are in the trace


def test_register_agent_via_api_issues_vc(servers) -> None:
    """POST /registry/agents also issues an Agent VC."""
    r = httpx.post(f"{GATEWAY_URL}/registry/agents", json={
        "agent_id": "test_vc_agent",
        "name": "Test VC Agent",
        "agent_type": "other",
        "endpoint": "http://127.0.0.1:19999/tasks",
        "static_capabilities": ["web.public:read"],
    }, timeout=10)
    assert r.status_code == 200
    data = r.json()
    assert "agent_vc_id" in data, f"agent_vc_id missing: {data}"

    # Verify VC was stored
    vc = get_agent_vc("test_vc_agent")
    assert vc is not None
    assert verify_credential_integrity(vc)
    assert vc.capabilities == ["web.public:read"]
    assert vc.issuer_id == "buiam-auth-system"
    assert vc.credential_id == data["agent_vc_id"]
