from __future__ import annotations

import os
from typing import Protocol

import httpx


class LLMProvider(Protocol):
    async def complete(self, system: str, user: str) -> str: ...


class MockLLMProvider:
    async def complete(self, system: str, user: str) -> str:
        return (
            "# 飞书报告\n\n"
            "## 摘要\n"
            "基于企业通讯录、知识库和多维表格 mock 数据，当前团队协作效率持续提升。\n\n"
            "## 关键发现\n"
            "- 项目负责人和数据接口人已明确。\n"
            "- 知识库显示协作效率提升 18%。\n"
            "- 多维表格显示 Q2 自动化报告数达到 128。\n\n"
            "## LLM Provider\n"
            "当前使用 mock provider，便于无 API Key 环境稳定演示。"
        )


class OpenAILLMProvider:
    def __init__(self) -> None:
        self.api_key = os.getenv("OPENAI_API_KEY")
        self.model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    async def complete(self, system: str, user: str) -> str:
        if not self.api_key:
            return await MockLLMProvider().complete(system, user)
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]


class AnthropicLLMProvider:
    def __init__(self) -> None:
        self.api_key = os.getenv("ANTHROPIC_API_KEY")
        self.model = os.getenv("ANTHROPIC_MODEL", "claude-3-5-haiku-latest")

    async def complete(self, system: str, user: str) -> str:
        if not self.api_key:
            return await MockLLMProvider().complete(system, user)
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                },
                json={
                    "model": self.model,
                    "max_tokens": 800,
                    "system": system,
                    "messages": [{"role": "user", "content": user}],
                },
            )
            response.raise_for_status()
            content = response.json()["content"]
            return "".join(block.get("text", "") for block in content)


def get_llm_provider() -> LLMProvider:
    provider = os.getenv("LLM_PROVIDER", "mock").lower()
    if provider == "openai":
        return OpenAILLMProvider()
    if provider == "anthropic":
        return AnthropicLLMProvider()
    return MockLLMProvider()
