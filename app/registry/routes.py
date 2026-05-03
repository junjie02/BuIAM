from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.delegation.credential_crypto import AGENT_VC_TYPE, GATEWAY_SYSTEM_ID, build_agent_capability_vc, get_agent_vc, verify_credential_integrity
from app.identity.did import build_did, build_did_document
from app.identity.keys import ensure_system_keypair
from app.store.delegation_credentials import upsert_credential
from app.store.did_registry import get_did_document, upsert_did_document
from app.store.registry import RegisteredAgent, get_agent, get_agent_by_name, list_agents, upsert_agent


class AgentRegisterRequest(BaseModel):
    agent_id: str
    name: str
    agent_type: Literal["doc_agent", "enterprise_data_agent", "external_search_agent", "other"]
    endpoint: str
    description: str = ""
    owner_org: str = "local"
    allowed_resource_domains: list[str] = Field(default_factory=list)
    static_capabilities: list[str] = Field(default_factory=list)
    status: Literal["active", "inactive"] = "active"


router = APIRouter(prefix="/registry", tags=["registry"])


@router.post("/agents")
def register_agent(request: AgentRegisterRequest) -> dict:
    existing = get_agent_by_name(request.name)
    if existing is not None and existing.agent_id != request.agent_id:
        raise HTTPException(status_code=400, detail={"error_code": "AGENT_NAME_ALREADY_EXISTS"})
    agent = upsert_agent(
        agent_id=request.agent_id,
        name=request.name,
        agent_type=request.agent_type,
        description=request.description,
        owner_org=request.owner_org,
        allowed_resource_domains=request.allowed_resource_domains,
        status=request.status,
        endpoint=request.endpoint,
        static_capabilities=request.static_capabilities,
    )
    # Issue Agent Capability VC signed by the Gateway system identity
    ensure_system_keypair()
    system_did = build_did(GATEWAY_SYSTEM_ID)
    if get_did_document(system_did) is None:
        system_doc = build_did_document(GATEWAY_SYSTEM_ID)
        upsert_did_document(did=system_did, subject_id=GATEWAY_SYSTEM_ID, document=system_doc)
    vc = build_agent_capability_vc(
        agent_id=request.agent_id,
        capabilities=request.static_capabilities,
        endpoint=request.endpoint,
        agent_type=request.agent_type,
    )
    upsert_credential(vc)
    result = agent_to_dict(agent)
    result["agent_vc_id"] = vc.credential_id
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
    """Return the Agent Capability VC as a presentable Verifiable Credential.

    The response includes recomputed hashes and signature verification so
    callers can independently verify the agent's declared capabilities.
    """
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
