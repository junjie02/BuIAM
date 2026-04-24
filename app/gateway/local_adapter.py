from __future__ import annotations

from fastapi import HTTPException

from app.protocol import AgentTaskResponse, DelegationEnvelope
from examples.agents.registry import get_agent_handler


async def call_local_agent(endpoint: str, envelope: DelegationEnvelope) -> AgentTaskResponse:
    agent_id = endpoint.removeprefix("local://")
    handler = get_agent_handler(agent_id)
    if handler is None:
        raise HTTPException(
            status_code=404,
            detail={"error_code": "AGENT_NOT_REGISTERED", "agent_id": agent_id},
        )
    return await handler(envelope)
