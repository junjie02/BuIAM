from __future__ import annotations
from typing import List, Literal, Optional
import uuid
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.store.registry import get_agent, list_agents, upsert_agent

class AgentRegisterRequest(BaseModel):
    agent_name: str
    agent_type: Literal["doc_agent", "enterprise_data_agent", "external_search_agent", "other"]
    description: str
    owner_org: str
    allowed_resource_domains: List[str]
    endpoint: str
    static_capabilities: List[str]
    status: Literal["active", "inactive"] = "active"

def agent_to_dict(agent) -> dict:
    return {
        "agent_id": agent.agent_id,
        "name": agent.name,
        "agent_type": agent.agent_type,
        "description": agent.description,
        "owner_org": agent.owner_org,
        "allowed_resource_domains": agent.allowed_resource_domains.split(","),
        "status": agent.status,
        "endpoint": agent.endpoint,
        "static_capabilities": sorted(agent.static_capabilities.split(",")),
        "created_at": agent.created_at,
        "updated_at": agent.updated_at
    }


router = APIRouter(prefix="/registry", tags=["registry"])


def agent_to_dict(agent) -> dict:
    return {
        "agent_id": agent.agent_id,
        "name": agent.name,
        "endpoint": agent.endpoint,
        "static_capabilities": sorted(agent.static_capabilities),
    }


@router.post("/agents")
def register_agent(request: AgentRegisterRequest) -> dict:
    existing_agent = get_agent_by_name(request.agent_name)
    if existing_agent:
        raise HTTPException(
            status_code=400,
            detail={"error_code": "AGENT_NAME_ALREADY_EXISTS", "message": "Agent名称已存在"}
        )
    
    agent_id = str(uuid.uuid4())
    return agent_to_dict(
        upsert_agent(
            agent_id,
            request.agent_name,
            request.agent_type,
            request.description,
            request.owner_org,
            ",".join(request.allowed_resource_domains),
            request.status,
            request.endpoint,
            ",".join(request.static_capabilities),
        )
    )


@router.get("/agents")
def get_agents() -> list[dict]:
    return [agent_to_dict(agent) for agent in list_agents()]


@router.get("/agents/{agent_id}")
def get_registered_agent(agent_id: str) -> dict:
    agent = get_agent(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail={"error_code": "AGENT_NOT_REGISTERED"})
    return agent_to_dict(agent)
