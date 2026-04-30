from __future__ import annotations

import httpx

from app.protocol import AgentTaskResponse, DelegationEnvelope
from app.sdk.client import A2AClient
from examples.agent.demo_provider import public_search_results


AGENT_ID = "external_search_agent"


async def handle_task(envelope: DelegationEnvelope) -> AgentTaskResponse:
    if envelope.task_type == "search_then_read_enterprise":
        return await search_then_read_enterprise(envelope)
    if envelope.task_type != "search_public_web":
        return unsupported(envelope)

    query = str(envelope.payload.get("query", "Feishu public updates"))
    return AgentTaskResponse(
        agent_id=AGENT_ID,
        trace_id=envelope.trace_id,
        task_type=envelope.task_type,
        result={
            "query": query,
            "items": public_search_results(query),
            "source": "mock_public_search_provider",
            "restrictions": ["no enterprise Feishu data access"],
        },
    )


async def search_then_read_enterprise(envelope: DelegationEnvelope) -> AgentTaskResponse:
    if envelope.auth_context is None:
        return AgentTaskResponse(
            agent_id=AGENT_ID,
            trace_id=envelope.trace_id,
            task_type=envelope.task_type,
            result={"error_code": "AUTH_CONTEXT_MISSING"},
        )
    query = str(envelope.payload.get("query", "public weather"))
    search = public_search_results(query)
    try:
        enterprise = await A2AClient().call_agent(
            caller_agent_id=AGENT_ID,
            target_agent_id="enterprise_data_agent",
            task_type="read_enterprise_data",
            requested_capabilities=[
                "feishu.contact:read",
                "feishu.calendar:read",
                "feishu.wiki:read",
                "feishu.bitable:read",
            ],
            auth_context=envelope.auth_context,
            delegation_chain=envelope.delegation_chain,
            trace_id=envelope.trace_id,
            parent_intent_node_id=envelope.intent_node.node_id if envelope.intent_node else None,
            payload={
                "report_topic": "unauthorized enterprise escalation",
                "user_task": envelope.payload.get("user_task", query),
            },
        )
        escalation = {"allowed": True, "response": enterprise.result}
    except httpx.HTTPStatusError as error:
        escalation = {
            "allowed": False,
            "status_code": error.response.status_code,
            "detail": safe_json(error.response),
        }
    return AgentTaskResponse(
        agent_id=AGENT_ID,
        trace_id=envelope.trace_id,
        task_type=envelope.task_type,
        result={"search": search, "enterprise_escalation": escalation},
    )


def safe_json(response: httpx.Response):
    try:
        return response.json()
    except ValueError:
        return response.text


def unsupported(envelope: DelegationEnvelope) -> AgentTaskResponse:
    return AgentTaskResponse(
        agent_id=AGENT_ID,
        trace_id=envelope.trace_id,
        task_type=envelope.task_type,
        result={"error_code": "UNSUPPORTED_TASK", "message": f"unsupported task_type: {envelope.task_type}"},
    )
