from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

import httpx


PROMPT_PATH = Path("app/intent/prompts/intent_judge.md")


class IntentJudgeError(Exception):
    pass


@dataclass(frozen=True)
class IntentJudgeResult:
    decision: str
    reason: str


def load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


async def judge_intent(
    *,
    root_intent: str,
    parent_intent: str,
    child_intent: str,
    task_type: str,
    target_agent_id: str,
) -> IntentJudgeResult:
    provider = os.getenv("INTENT_JUDGE_PROVIDER", os.getenv("LLM_PROVIDER", "mock")).lower()
    user_payload = json.dumps(
        {
            "root_intent": root_intent,
            "parent_intent": parent_intent,
            "child_intent": child_intent,
            "task_type": task_type,
            "target_agent_id": target_agent_id,
        },
        ensure_ascii=False,
    )
    if provider in {"mock", "demo"}:
        return IntentJudgeResult(
            decision="Consistent",
            reason="Deterministic demo judge accepted the child intent.",
        )
    if provider == "anthropic":
        raw = await call_anthropic(load_prompt(), user_payload)
    elif provider == "openai":
        raw = await call_openai(load_prompt(), user_payload)
    else:
        raise IntentJudgeError(f"unsupported intent judge provider: {provider}")
    return parse_judge_response(raw)


async def call_openai(system_prompt: str, user_payload: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise IntentJudgeError("OPENAI_API_KEY is not configured")
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_payload},
                ],
                "response_format": {"type": "json_object"},
            },
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]


async def call_anthropic(system_prompt: str, user_payload: str) -> str:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise IntentJudgeError("ANTHROPIC_API_KEY is not configured")
    base_url = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1").rstrip("/")
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{base_url}/messages",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            json={
                "model": os.getenv("ANTHROPIC_MODEL", "claude-3-5-haiku-latest"),
                "max_tokens": 400,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_payload}],
            },
        )
        response.raise_for_status()
        content = response.json()["content"]
        return "".join(block.get("text", "") for block in content)


def parse_judge_response(raw: str) -> IntentJudgeResult:
    try:
        payload = json.loads(extract_json_object(raw))
    except json.JSONDecodeError as error:
        raise IntentJudgeError("intent judge returned non-json output") from error
    decision = payload.get("decision")
    reason = str(payload.get("reason", ""))
    if decision not in {"Consistent", "Drifted"}:
        raise IntentJudgeError("intent judge returned invalid decision")
    return IntentJudgeResult(decision=decision, reason=reason)


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
