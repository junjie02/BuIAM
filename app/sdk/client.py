from __future__ import annotations

from uuid import uuid4

import httpx

from app.intent.crypto import build_signed_intent_node
from app.protocol import AgentTaskResponse, DelegationEnvelope, DelegationHop, IntentCommitment


class BuIAMClient:
    def __init__(self, base_url: str, access_token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.access_token = access_token

    async def call_agent(
        self,
        *,
        caller_agent_id: str,
        target_agent_id: str,
        task_type: str,
        requested_capabilities: list[str],
        payload: dict,
        intent_commitment: IntentCommitment | None = None,
        parent_intent_node_id: str | None = None,
        actor_private_key_id: str | None = None,
        actor_type: str = "agent",
        trace_id: str | None = None,
        delegation_chain: list[DelegationHop] | None = None,
    ) -> AgentTaskResponse:
        intent_node = None
        if intent_commitment is not None:
            actor_id = actor_private_key_id or caller_agent_id
            intent_node = build_signed_intent_node(
                parent_node_id=parent_intent_node_id,
                actor_id=actor_id,
                actor_type=actor_type,
                target_agent_id=target_agent_id,
                task_type=task_type,
                intent_commitment=intent_commitment,
            )
        envelope = DelegationEnvelope(
            trace_id=trace_id or str(uuid4()),
            request_id=str(uuid4()),
            caller_agent_id=caller_agent_id,
            target_agent_id=target_agent_id,
            task_type=task_type,
            requested_capabilities=requested_capabilities,
            delegation_chain=delegation_chain or [],
            intent_node=intent_node,
            payload=payload,
        )
        async with httpx.AsyncClient(base_url=self.base_url, timeout=30) as client:
            response = await client.post(
                "/delegate/call",
                json=envelope.model_dump(),
                headers={"Authorization": f"Bearer {self.access_token}"},
            )
            response.raise_for_status()
            return AgentTaskResponse.model_validate(response.json())
