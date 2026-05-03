from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.delegation.credential_crypto import (
    AGENT_VC_TYPE,
    GATEWAY_SYSTEM_ID,
    build_agent_capability_vc,
    get_agent_vc,
    verify_credential_integrity,
)
from app.identity.did import build_did, build_did_document
from app.identity.keys import ensure_system_keypair
from app.registry.policy import evaluate_capability_request
from app.store.capabilities import add_capability, list_capabilities, remove_capability
from app.store.capability_policies import list_policies, upsert_policy
from app.store.delegation_credentials import upsert_credential
from app.store.did_registry import get_did_document, upsert_did_document
from app.store.registry import (
    RegisteredAgent,
    get_agent,
    get_agent_by_name,
    list_agents,
    upsert_agent,
)


class AgentRegisterRequest(BaseModel):
    agent_id: str
    name: str
    agent_type: Literal["doc_agent", "enterprise_data_agent", "external_search_agent", "other"]
    endpoint: str
    description: str = ""
    owner_org: str = "local"
    allowed_resource_domains: list[str] = Field(default_factory=list)
    status: Literal["active", "inactive"] = "active"
    requested_capabilities: list[str] = Field(default_factory=list)
    # backward compat: falls back to requested_capabilities if empty
    static_capabilities: list[str] = Field(default_factory=list)


class PolicyUpdateRequest(BaseModel):
    subject_type: str
    agent_type: str
    allowed_capabilities: list[str]


router = APIRouter(prefix="/registry", tags=["registry"])


def _resolve_requested_capabilities(request: AgentRegisterRequest) -> list[str]:
    """Resolve capabilities from the request, preferring the new field."""
    if request.requested_capabilities:
        return request.requested_capabilities
    return request.static_capabilities


def _ensure_system_did() -> None:
    ensure_system_keypair()
    system_did = build_did(GATEWAY_SYSTEM_ID)
    if get_did_document(system_did) is None:
        system_doc = build_did_document(GATEWAY_SYSTEM_ID)
        upsert_did_document(did=system_did, subject_id=GATEWAY_SYSTEM_ID, document=system_doc)


@router.post("/agents")
def register_agent(request: AgentRegisterRequest) -> dict:
    existing = get_agent_by_name(request.name)
    if existing is not None and existing.agent_id != request.agent_id:
        raise HTTPException(status_code=400, detail={"error_code": "AGENT_NAME_ALREADY_EXISTS"})

    requested = _resolve_requested_capabilities(request)

    # Approval: evaluate against capability policy
    approval = evaluate_capability_request(
        subject_type="agent",
        agent_type=request.agent_type,
        requested=requested,
    )
    if approval["decision"] == "denied":
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "CAPABILITY_DENIED",
                "reason": approval["reason"],
                "requested_capabilities": requested,
                "missing_capabilities": approval["missing"],
            },
        )

    granted = approval["granted"]
    agent = upsert_agent(
        agent_id=request.agent_id,
        name=request.name,
        agent_type=request.agent_type,
        description=request.description,
        owner_org=request.owner_org,
        allowed_resource_domains=request.allowed_resource_domains,
        status=request.status,
        endpoint=request.endpoint,
        static_capabilities=granted,
    )

    _ensure_system_did()
    vc = build_agent_capability_vc(
        agent_id=request.agent_id,
        capabilities=granted,
        endpoint=request.endpoint,
        agent_type=request.agent_type,
    )
    upsert_credential(vc)
    result = agent_to_dict(agent)
    result["agent_vc_id"] = vc.credential_id
    result["decision"] = approval["decision"]
    result["requested_capabilities"] = sorted(requested)
    result["granted_capabilities"] = granted
    result["missing_capabilities"] = approval["missing"]
    return result


@router.get("/agents")
def get_agents(
    capability: str | None = Query(default=None, description="Filter by capability"),
) -> list[dict]:
    agents = list_agents()
    results = []
    for agent in agents:
        if capability and capability not in agent.static_capabilities:
            continue
        info = agent_to_dict(agent)
        vc = get_agent_vc(agent.agent_id)
        if vc is not None:
            info["agent_vc_id"] = vc.credential_id
        results.append(info)
    return results


@router.get("/agents/{agent_id}")
def get_registered_agent(agent_id: str) -> dict:
    agent = get_agent(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail={"error_code": "AGENT_NOT_REGISTERED"})
    info = agent_to_dict(agent)
    vc = get_agent_vc(agent_id)
    if vc is not None:
        info["agent_vc_id"] = vc.credential_id
    return info


@router.get("/agents/{agent_id}/vc")
def get_agent_vc_endpoint(agent_id: str) -> dict:
    agent = get_agent(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail={"error_code": "AGENT_NOT_REGISTERED"})
    vc = get_agent_vc(agent_id)
    if vc is None:
        raise HTTPException(status_code=404, detail={"error_code": "AGENT_VC_NOT_FOUND"})
    return {
        "@context": vc.vc_context,
        "type": vc.vc_type,
        "id": vc.credential_id,
        "issuer": {"id": vc.issuer_did, "issuer_id": vc.issuer_id},
        "issuanceDate": vc.iat,
        "expirationDate": vc.exp,
        "credentialSubject": vc.credential_subject,
        "proof": {
            "type": vc.signature_alg,
            "verificationMethod": vc.proof_verification_method,
            "proofValue": vc.proof_signature,
        },
        "verification": {
            "signature_valid": verify_credential_integrity(vc),
            "content_hash_valid": vc.content_hash,
        },
    }


# ── Capability Master List ────────────────────────────────────────────────


class CapabilityAddRequest(BaseModel):
    name: str
    description: str = ""


@router.get("/capabilities")
def get_available_capabilities() -> dict:
    """Return all valid capabilities that can be requested during registration."""
    return {"capabilities": [c["name"] for c in list_capabilities()]}


@router.post("/capabilities")
def create_capability(request: CapabilityAddRequest) -> dict:
    """Add a new capability to the master list."""
    if not request.name.strip():
        raise HTTPException(status_code=400, detail={"error_code": "CAPABILITY_NAME_REQUIRED"})
    ok = add_capability(request.name.strip(), request.description)
    if not ok:
        raise HTTPException(status_code=409, detail={"error_code": "CAPABILITY_ALREADY_EXISTS"})
    return {"name": request.name.strip(), "status": "created"}


@router.delete("/capabilities/{name}")
def delete_capability(name: str) -> dict:
    """Remove a capability from the master list."""
    ok = remove_capability(name)
    if not ok:
        raise HTTPException(status_code=404, detail={"error_code": "CAPABILITY_NOT_FOUND"})
    return {"name": name, "status": "removed"}


# ── Capability Policy Management ──────────────────────────────────────────


@router.get("/capability-policies")
def get_capability_policies() -> list[dict]:
    return list_policies()


@router.put("/capability-policies")
def update_capability_policy(request: PolicyUpdateRequest) -> dict:
    if not request.subject_type or not request.agent_type:
        raise HTTPException(status_code=400, detail={"error_code": "POLICY_INVALID_SUBJECT_OR_AGENT"})
    upsert_policy(request.subject_type, request.agent_type, request.allowed_capabilities)
    return {
        "subject_type": request.subject_type,
        "agent_type": request.agent_type,
        "allowed_capabilities": sorted(request.allowed_capabilities),
        "status": "updated",
    }


# ── Helpers ────────────────────────────────────────────────────────────────


def agent_to_dict(agent: RegisteredAgent) -> dict:
    return {
        "agent_id": agent.agent_id,
        "name": agent.name,
        "agent_type": agent.agent_type,
        "description": agent.description,
        "owner_org": agent.owner_org,
        "allowed_resource_domains": sorted(agent.allowed_resource_domains),
        "status": agent.status,
        "endpoint": agent.endpoint,
        "static_capabilities": sorted(agent.static_capabilities),
        "created_at": agent.created_at,
        "updated_at": agent.updated_at,
        "last_seen_at": agent.last_seen_at,
    }
