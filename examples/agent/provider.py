from __future__ import annotations

import os

from examples.agent import demo_provider


class ProviderError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def provider_mode() -> str:
    mode = os.getenv("BUIAM_AGENT_PROVIDER_MODE", "mock").strip().lower()
    if mode not in {"mock", "lark_cli"}:
        raise ProviderError("PROVIDER_MODE_INVALID", f"unsupported provider mode: {mode}")
    return mode


async def enterprise_snapshot(*, topic: str, user_task: str, trace_id: str) -> dict:
    if provider_mode() == "lark_cli":
        from examples.agent import lark_cli_provider

        return await lark_cli_provider.enterprise_snapshot(topic=topic, user_task=user_task, trace_id=trace_id)
    return demo_provider.enterprise_snapshot(topic)


async def write_document(*, title: str, content: str, trace_id: str) -> dict:
    if provider_mode() == "lark_cli":
        from examples.agent import lark_cli_provider

        return await lark_cli_provider.write_document(title=title, content=content, trace_id=trace_id)
    return demo_provider.write_mock_document(title=title, content=content, trace_id=trace_id)
