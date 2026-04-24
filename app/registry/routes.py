from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.protocol import AgentRegistrationRequest
from app.store.registry import get_agent, list_agents, upsert_agent


router = APIRouter(prefix="/registry", tags=["registry"])


def agent_to_dict(agent) -> dict:
    return {
        "agent_id": agent.agent_id,
        "name": agent.name,
        "endpoint": agent.endpoint,
        "static_capabilities": sorted(agent.static_capabilities),
    }


@router.post("/agents")
def register_agent(request: AgentRegistrationRequest) -> dict:
    return agent_to_dict(
        upsert_agent(
            request.agent_id,
            request.name,
            request.endpoint,
            request.static_capabilities,
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
