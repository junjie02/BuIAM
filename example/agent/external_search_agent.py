from __future__ import annotations

from app.protocol import AgentTaskResponse, DelegationEnvelope


AGENT_ID = "external_search_agent"


async def handle_task(envelope: DelegationEnvelope) -> AgentTaskResponse:
    if envelope.task_type != "search_public_web":
        return _unsupported_task(envelope)

    query = str(envelope.payload.get("query", "Feishu public updates"))
    result = {
        "query": query,
        "items": await search_public_web(query),
        "source": "public_web_only",
        "restrictions": ["no internal Feishu data access"],
    }
    return AgentTaskResponse(
        agent_id=AGENT_ID,
        trace_id=envelope.trace_id,
        task_type=envelope.task_type,
        result=result,
    )


async def search_public_web(query: str) -> list[dict[str, str]]:
    # TODO: replace with a real public search client or crawler.
    return [
        {
            "title": f"Placeholder public result for: {query}",
            "url": "https://example.com/public-result",
            "summary": "Replace this mock with a real public web lookup.",
        }
    ]


def _unsupported_task(envelope: DelegationEnvelope) -> AgentTaskResponse:
    return AgentTaskResponse(
        agent_id=AGENT_ID,
        trace_id=envelope.trace_id,
        task_type=envelope.task_type,
        result={"error": f"unsupported task_type: {envelope.task_type}"},
    )
