from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from app.protocol import IntentCommitment


PROMPT_PATH = Path("app/intent/prompts/intent_generator.md")


class IntentGenerationError(Exception):
    pass


@dataclass(frozen=True)
class GeneratedIntent:
    commitment: IntentCommitment
    model: str
    provider: str


def load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


async def generate_intent_commitment(
    *,
    user_task: str,
    actor_id: str,
    actor_type: str,
    target_agent_id: str,
    task_type: str,
    payload: dict[str, Any],
) -> GeneratedIntent:
    provider = os.getenv("INTENT_GENERATOR_PROVIDER", os.getenv("LLM_PROVIDER", "mock")).lower()
    user_payload = json.dumps(
        {
            "user_task": user_task,
            "actor_id": actor_id,
            "actor_type": actor_type,
            "target_agent_id": target_agent_id,
            "task_type": task_type,
            "payload_summary": summarize_payload(payload),
        },
        ensure_ascii=False,
    )
    if provider in {"mock", "demo"}:
        model = "mock-intent-generator"
        raw = json.dumps(
            {
                "intent": f"{actor_id} requests {target_agent_id} to run {task_type}: {user_task}",
                "description": "Deterministic demo intent commitment.",
                "data_refs": sorted(str(key) for key in payload.keys()),
                "constraints": ["demo intent provider", "preserve delegated capability boundary"],
            },
            ensure_ascii=False,
        )
    elif provider == "anthropic":
        model = os.getenv("ANTHROPIC_MODEL", "claude-3-5-haiku-latest")
        raw = await call_anthropic(load_prompt(), user_payload, model)
    elif provider == "openai":
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        raw = await call_openai(load_prompt(), user_payload, model)
    else:
        raise IntentGenerationError(f"unsupported intent generator provider: {provider}")
    return GeneratedIntent(commitment=parse_intent_response(raw), model=model, provider=provider)


def summarize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: str(value)[:200] for key, value in payload.items()}


async def call_openai(system_prompt: str, user_payload: str, model: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise IntentGenerationError("OPENAI_API_KEY is not configured")
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_payload},
                    ],
                    "response_format": {"type": "json_object"},
                },
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
    except httpx.HTTPError as error:
        raise IntentGenerationError(f"OpenAI-compatible intent generation request failed: {error}") from error


async def call_anthropic(system_prompt: str, user_payload: str, model: str) -> str:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise IntentGenerationError("ANTHROPIC_API_KEY is not configured")
    base_url = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{base_url}/messages",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                json={
                    "model": model,
                    "max_tokens": 500,
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": user_payload}],
                },
            )
            response.raise_for_status()
            content = response.json()["content"]
            return "".join(block.get("text", "") for block in content)
    except httpx.HTTPError as error:
        raise IntentGenerationError(f"Anthropic intent generation request failed: {error}") from error


def parse_intent_response(raw: str) -> IntentCommitment:
    try:
        payload = json.loads(extract_json_object(raw))
    except json.JSONDecodeError as error:
        raise IntentGenerationError("intent generator returned non-json output") from error
    intent = str(payload.get("intent", "")).strip()
    if not intent:
        raise IntentGenerationError("intent generator returned empty intent")
    return IntentCommitment(
        intent=intent,
        description=str(payload.get("description", "")),
        data_refs=[str(item) for item in payload.get("data_refs", [])],
        constraints=[str(item) for item in payload.get("constraints", [])],
    )


def extract_json_object(raw: str) -> str:
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        return fenced.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return text
    return text[start : end + 1]
