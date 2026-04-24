from __future__ import annotations

from uuid import uuid4

from collections.abc import Sequence

from app.protocol import AuthContext, DelegationEnvelope, DelegationHop


class DelegationClient:
    def build_envelope(
        self,
        *,
        trace_id: str,
        caller_agent_id: str,
        target_agent_id: str,
        task_type: str,
        requested_capabilities: Sequence[str],
        delegation_chain: list[DelegationHop],
        auth_context: AuthContext,
        payload: dict,
    ) -> DelegationEnvelope:
        return DelegationEnvelope(
            trace_id=trace_id,
            request_id=str(uuid4()),
            caller_agent_id=caller_agent_id,
            target_agent_id=target_agent_id,
            task_type=task_type,
            requested_capabilities=list(requested_capabilities),
            delegation_chain=delegation_chain,
            auth_context=auth_context,
            payload=payload,
        )


delegation_client = DelegationClient()
