from __future__ import annotations

import os
import time
from uuid import uuid4

import httpx

from app.protocol import AgentTaskResponse, AuthContext, DelegationEnvelope, DelegationHop


class A2AClient:
    def __init__(self, *, gateway_url: str | None = None, access_token: str | None = None) -> None:
        self.gateway_url = (gateway_url or os.getenv("BUIAM_GATEWAY_URL", "http://127.0.0.1:8000")).rstrip("/")
        self.access_token = access_token

    async def call_agent(
        self,
        *,
        caller_agent_id: str,
        target_agent_id: str,
        task_type: str,
        requested_capabilities: list[str],
        payload: dict,
        auth_context: AuthContext,
        delegation_chain: list[DelegationHop],
        trace_id: str,
        parent_intent_node_id: str | None = None,
    ) -> AgentTaskResponse:
        prepared_payload = dict(payload)
        if parent_intent_node_id:
            prepared_payload["parent_intent_node_id"] = parent_intent_node_id
        if "user_task" not in prepared_payload:
            prepared_payload["user_task"] = task_type

        envelope = DelegationEnvelope(
            trace_id=trace_id,
            request_id=str(uuid4()),
            caller_agent_id=caller_agent_id,
            target_agent_id=target_agent_id,
            task_type=task_type,
            requested_capabilities=requested_capabilities,
            delegation_chain=delegation_chain,
            auth_context=auth_context,
            payload=prepared_payload,
        )
        async with httpx.AsyncClient(base_url=self.gateway_url, timeout=30) as client:
            response = await client.post(
                f"/a2a/agents/{target_agent_id}/tasks",
                json=envelope.model_dump(),
                headers={"Authorization": f"Bearer {await self.token_for(auth_context)}"},
            )
            response.raise_for_status()
            return AgentTaskResponse.model_validate(response.json())

    async def token_for(self, auth_context: AuthContext) -> str:
        if self.access_token:
            return self.access_token

        env_name = f"{auth_context.agent_id.upper()}_ACCESS_TOKEN"
        env_token = os.getenv(env_name)
        if env_token:
            return env_token

        async with httpx.AsyncClient(base_url=self.gateway_url, timeout=30) as client:
            max_ttl = int(os.getenv("A2A_AGENT_TOKEN_TTL_SECONDS", "3600"))
            remaining_context_ttl = max(1, auth_context.exp - int(time.time()))
            response = await client.post(
                "/identity/tokens",
                json={
                    "agent_id": auth_context.agent_id,
                    "delegated_user": auth_context.delegated_user or "user_123",
                    "actor_type": "agent",
                    "capabilities": auth_context.capabilities,
                    "user_capabilities": auth_context.user_capabilities or auth_context.capabilities,
                    "ttl_seconds": min(max_ttl, remaining_context_ttl),
                },
            )
            response.raise_for_status()
            self.access_token = response.json()["access_token"]
            return self.access_token
