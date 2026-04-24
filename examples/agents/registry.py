from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.protocol import AgentTaskResponse, DelegationEnvelope
from examples.agents import doc_agent, enterprise_data_agent, external_search_agent


AgentHandler = Callable[[DelegationEnvelope], Awaitable[AgentTaskResponse]]


AGENT_HANDLERS: dict[str, AgentHandler] = {
    "doc_agent": doc_agent.handle_task,
    "enterprise_data_agent": enterprise_data_agent.handle_task,
    "external_search_agent": external_search_agent.handle_task,
}


def get_agent_handler(agent_id: str) -> AgentHandler | None:
    return AGENT_HANDLERS.get(agent_id)
