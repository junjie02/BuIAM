from __future__ import annotations

import json
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.store.registry import get_agent, get_agent_by_name, list_agents, upsert_agent


class AgentRegisterRequest(BaseModel):
    agent_name: str
    agent_type: Literal["doc_agent", "enterprise_data_agent", "external_search_agent", "other"]
    description: str = ""
    owner_org: str = "local"
    allowed_resource_domains: list[str] = Field(default_factory=list)
    endpoint: str
    static_capabilities: list[str] = Field(default_factory=list)
    status: Literal["active", "inactive"] = "active"


router = APIRouter(prefix="/registry", tags=["registry"])


def agent_to_dict(agent) -> dict:
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


@router.post("/agents")
def register_agent(request: AgentRegisterRequest) -> dict:
    existing_agent = get_agent_by_name(request.agent_name)
    if existing_agent is not None:
        raise HTTPException(status_code=400, detail={"error_code": "AGENT_NAME_ALREADY_EXISTS"})
    agent_id = str(uuid.uuid4())
    return agent_to_dict(
        upsert_agent(
            agent_id=agent_id,
            name=request.agent_name,
            agent_type=request.agent_type,
            description=request.description,
            owner_org=request.owner_org,
            allowed_resource_domains=",".join(request.allowed_resource_domains),
            status=request.status,
            endpoint=request.endpoint,
            static_capabilities=json.dumps(sorted(request.static_capabilities), ensure_ascii=False),
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
