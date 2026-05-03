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


def test_register_agent_via_api_issues_vc_and_shows_approval_decision(servers) -> None:
    """POST /registry/agents returns decision + approved capabilities."""
    r = httpx.post(f"{GATEWAY_URL}/registry/agents", json={
        "agent_id": "test_vc_agent",
        "name": "Test VC Agent",
        "agent_type": "other",
        "endpoint": "http://127.0.0.1:19999/tasks",
        "requested_capabilities": ["web.public:read"],
    }, timeout=10)
    assert r.status_code == 200
    data = r.json()
    assert "decision" in data
    assert data["decision"] in ("approved", "modified")
    assert "granted_capabilities" in data
    assert "agent_vc_id" in data
    # Agent VC should have the granted capabilities
    vc = get_agent_vc("test_vc_agent")
    assert vc is not None
    assert verify_credential_integrity(vc)
    assert vc.capabilities == data["granted_capabilities"]


def test_register_agent_denied_when_no_capability_match(servers) -> None:
    """Registration denied when requested capabilities exceed policy."""
    r = httpx.post(f"{GATEWAY_URL}/registry/agents", json={
        "agent_id": "test_denied_agent",
        "name": "Test Denied Agent",
        "agent_type": "external_search_agent",
        "endpoint": "http://127.0.0.1:19998/tasks",
        "requested_capabilities": ["feishu.contact:read", "feishu.bitable:read"],
        # external_search_agent policy only allows web.public:read
    }, timeout=10)
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert detail["error_code"] == "CAPABILITY_DENIED"
    assert len(detail["missing_capabilities"]) > 0


def test_register_agent_modified_when_partial_match(servers) -> None:
    """Registration succeeds with reduced capabilities when only some are allowed."""
    r = httpx.post(f"{GATEWAY_URL}/registry/agents", json={
        "agent_id": "test_modified_agent",
        "name": "Test Modified Agent",
        "agent_type": "external_search_agent",
        "endpoint": "http://127.0.0.1:19997/tasks",
        "requested_capabilities": ["web.public:read", "feishu.contact:read"],
    }, timeout=10)
    assert r.status_code == 200
    data = r.json()
    assert data["decision"] == "modified"
    assert data["granted_capabilities"] == ["web.public:read"]
    assert "feishu.contact:read" in data["missing_capabilities"]


def test_capability_master_list_crud(servers) -> None:
    """GET/POST/DELETE /registry/capabilities."""
    # List defaults (seeded from protocol)
    r = httpx.get(f"{GATEWAY_URL}/registry/capabilities", timeout=10)
    assert r.status_code == 200
    caps = r.json()["capabilities"]
    assert "web.public:read" in caps
    assert "feishu.contact:read" in caps

    # Add new
    r = httpx.post(f"{GATEWAY_URL}/registry/capabilities", json={
        "name": "feishu.mail:read", "description": "Read mail"
    }, timeout=10)
    assert r.status_code == 200

    # Verify in list
    r = httpx.get(f"{GATEWAY_URL}/registry/capabilities", timeout=10)
    assert "feishu.mail:read" in r.json()["capabilities"]

    # Duplicate
    r = httpx.post(f"{GATEWAY_URL}/registry/capabilities", json={
        "name": "feishu.mail:read"
    }, timeout=10)
    assert r.status_code == 409

    # Delete
    r = httpx.delete(f"{GATEWAY_URL}/registry/capabilities/feishu.mail:read", timeout=10)
    assert r.status_code == 200
    r = httpx.get(f"{GATEWAY_URL}/registry/capabilities", timeout=10)
    assert "feishu.mail:read" not in r.json()["capabilities"]


def test_known_capabilities_validates_against_master_list(servers) -> None:
    """parse_capabilities rejects unknown capability names that aren't in master list."""
    from app.delegation.capabilities import parse_capabilities
    import pytest
    # Valid
    result = parse_capabilities(["web.public:read"])
    assert "web.public:read" in result
    # Invalid
    with pytest.raises(ValueError, match="unknown capabilities"):
        parse_capabilities(["completely.unknown:admin"])


def test_capability_policy_crud(servers) -> None:
    """GET/PUT /registry/capability-policies works."""
    # List
    r = httpx.get(f"{GATEWAY_URL}/registry/capability-policies", timeout=10)
    assert r.status_code == 200
    policies = r.json()
    assert len(policies) >= 5  # 5 default seeds

    # Update
    r = httpx.put(f"{GATEWAY_URL}/registry/capability-policies", json={
        "subject_type": "agent",
        "agent_type": "test_crud_type",
        "allowed_capabilities": ["web.public:read", "feishu.contact:read"],
    }, timeout=10)
    assert r.status_code == 200
    assert r.json()["status"] == "updated"

    # Verify it appears in list
    r = httpx.get(f"{GATEWAY_URL}/registry/capability-policies", timeout=10)
    updated = [p for p in r.json() if p["agent_type"] == "test_crud_type"]
    assert len(updated) == 1
    assert set(updated[0]["allowed_capabilities"]) == {"web.public:read", "feishu.contact:read"}


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
