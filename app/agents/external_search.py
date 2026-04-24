from __future__ import annotations

from app.delegation.client import delegation_client
from app.protocol import AgentTaskResponse, DelegationEnvelope
from app.tools.web import search_public_web


AGENT_ID = "external_search_agent"


async def handle_task(envelope: DelegationEnvelope) -> AgentTaskResponse:
    if envelope.task_type == "search_public_web":
        query = str(envelope.payload.get("query", "飞书 AI 助手"))
        result = {"items": search_public_web(query)}
    elif envelope.task_type == "attempt_enterprise_data_access":
        child_envelope = delegation_client.build_envelope(
            trace_id=envelope.trace_id,
            caller_agent_id=AGENT_ID,
            target_agent_id="enterprise_data_agent",
            task_type="read_enterprise_data",
            requested_capabilities=[
                "feishu.contact:read",
                "feishu.wiki:read",
                "feishu.bitable:read",
            ],
            delegation_chain=envelope.delegation_chain,
            auth_context=envelope.auth_context,
            payload={"reason": "external agent tries to read enterprise data"},
        )
        result = {"delegation_envelope": child_envelope.model_dump()}
    else:
        result = {"error": f"unsupported task_type: {envelope.task_type}"}

    return AgentTaskResponse(
        agent_id=AGENT_ID,
        trace_id=envelope.trace_id,
        task_type=envelope.task_type,
        result=result,
    )
