from __future__ import annotations

from examples.agent import lark_cli_provider
from examples.agent.errors import ProviderError


async def enterprise_snapshot(*, topic: str, user_task: str, trace_id: str) -> dict:
    return await lark_cli_provider.enterprise_snapshot(topic=topic, user_task=user_task, trace_id=trace_id)


async def write_document(*, title: str, content: str, trace_id: str) -> dict:
    return await lark_cli_provider.write_document(title=title, content=content, trace_id=trace_id)
