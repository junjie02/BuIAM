from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI

from app.protocol import AgentTaskResponse, DelegationEnvelope


AgentHandler = Callable[[DelegationEnvelope], Awaitable[AgentTaskResponse]]


def create_agent_app(*, title: str, handler: AgentHandler) -> FastAPI:
    app = FastAPI(title=title)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/a2a/tasks")
    async def a2a_tasks(envelope: DelegationEnvelope) -> AgentTaskResponse:
        return await handler(envelope)

    return app
