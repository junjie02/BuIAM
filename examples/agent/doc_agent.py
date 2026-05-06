from __future__ import annotations

from app.protocol import AgentTaskResponse, DelegationEnvelope
from app.sdk.client import A2AClient
from examples.agent.provider import ProviderError, write_document


AGENT_ID = "doc_agent"


async def handle_task(envelope: DelegationEnvelope) -> AgentTaskResponse:
    if envelope.task_type == "ask_weather":
        return await search_public_web(envelope)
    if envelope.task_type != "generate_report":
        return unsupported(envelope)
    if envelope.auth_context is None:
        return auth_missing(envelope)

    topic = str(envelope.payload.get("topic", "Feishu A2A Demo Report"))
    user_task = str(envelope.payload.get("user_task", "Generate a report from delegated enterprise data."))
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
        payload={"report_topic": topic, "user_task": user_task},
    )
    if "error_code" in enterprise.result:
        return AgentTaskResponse(
            agent_id=AGENT_ID,
            trace_id=envelope.trace_id,
            task_type=envelope.task_type,
            result={"error_code": enterprise.result["error_code"], "message": enterprise.result.get("message", "")},
        )
    content = render_report(topic=topic, enterprise_data=enterprise.result)
    try:
        document = await write_document(title=topic, content=content, trace_id=envelope.trace_id)
    except ProviderError as exc:
        return provider_error(envelope, exc)
    return AgentTaskResponse(
        agent_id=AGENT_ID,
        trace_id=envelope.trace_id,
        task_type=envelope.task_type,
        result={
            "message": f"{document['provider']} report generated and written",
            "document": document,
            "enterprise_data": enterprise.result,
            "report_preview": content[:800],
        },
    )


async def search_public_web(envelope: DelegationEnvelope) -> AgentTaskResponse:
    if envelope.auth_context is None:
        return auth_missing(envelope)
    query = str(envelope.payload.get("query", "today weather"))
    response = await A2AClient().call_agent(
        caller_agent_id=AGENT_ID,
        target_agent_id="external_search_agent",
        task_type="search_public_web",
        requested_capabilities=["web.public:read"],
        auth_context=envelope.auth_context,
        delegation_chain=envelope.delegation_chain,
        trace_id=envelope.trace_id,
        parent_intent_node_id=envelope.intent_node.node_id if envelope.intent_node else None,
        payload={"query": query, "user_task": envelope.payload.get("user_task", query)},
    )
    return AgentTaskResponse(
        agent_id=AGENT_ID,
        trace_id=envelope.trace_id,
        task_type=envelope.task_type,
        result={"message": "public search completed", "search": response.result},
    )


def auth_missing(envelope: DelegationEnvelope) -> AgentTaskResponse:
    return AgentTaskResponse(
        agent_id=AGENT_ID,
        trace_id=envelope.trace_id,
        task_type=envelope.task_type,
        result={"error_code": "AUTH_CONTEXT_MISSING", "message": "gateway did not forward auth context"},
    )


def unsupported(envelope: DelegationEnvelope) -> AgentTaskResponse:
    return AgentTaskResponse(
        agent_id=AGENT_ID,
        trace_id=envelope.trace_id,
        task_type=envelope.task_type,
        result={"error_code": "UNSUPPORTED_TASK", "message": f"unsupported task_type: {envelope.task_type}"},
    )


def provider_error(envelope: DelegationEnvelope, error: ProviderError) -> AgentTaskResponse:
    return AgentTaskResponse(
        agent_id=AGENT_ID,
        trace_id=envelope.trace_id,
        task_type=envelope.task_type,
        result={"error_code": error.code, "message": error.message},
    )


def render_report(*, topic: str, enterprise_data: dict) -> str:
    contacts = ", ".join(item.get("name", "unknown") for item in enterprise_data.get("contacts", []))
    events = ", ".join(item.get("summary", "unknown") for item in enterprise_data.get("calendar_events", []))
    pages = ", ".join(item.get("title", "unknown") for item in enterprise_data.get("wiki_pages", []))
    blocked = next(
        (item.get("value", "0") for item in enterprise_data.get("bitable_records", []) if item.get("metric") == "blocked_escalations"),
        "0",
    )
    return "\n".join(
        [
            f"# {topic}",
            "",
            "## Summary",
            "The enterprise snapshot was collected through the authorized A2A chain.",
            "",
            "## Signals",
            f"- Key collaborators: {contacts or 'none'}",
            f"- Calendar signals: {events or 'none'}",
            f"- Wiki pages: {pages or 'none'}",
            f"- Blocked escalation count: {blocked}",
            "",
            "## Suggested Actions",
            "- Keep delegation capabilities narrow.",
            "- Review audit traces after sensitive cross-agent calls.",
        ]
    )
