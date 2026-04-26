from __future__ import annotations

import asyncio
from typing import Any

from app.protocol import AgentTaskResponse, DelegationEnvelope

from example.agent.feishu_openapi import (
    FeishuAPIError,
    FeishuConfigError,
    FeishuOpenAPIClient,
    FeishuOpenAPISettings,
)


AGENT_ID = "enterprise_data_agent"


async def handle_task(envelope: DelegationEnvelope) -> AgentTaskResponse:
    if envelope.task_type != "read_enterprise_data":
        return _unsupported_task(envelope)

    try:
        result = await fetch_enterprise_snapshot(envelope.payload)
    except (FeishuConfigError, FeishuAPIError) as error:
        result = {
            "error_code": error.__class__.__name__,
            "message": str(error),
            "data_owner": AGENT_ID,
            "source": "feishu_openapi",
        }
    return AgentTaskResponse(
        agent_id=AGENT_ID,
        trace_id=envelope.trace_id,
        task_type=envelope.task_type,
        result=result,
    )


async def fetch_enterprise_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    settings = FeishuOpenAPISettings.from_env()
    client = FeishuOpenAPIClient(settings)
    contacts, calendar_events, bitable_records = await asyncio.gather(
        read_contacts(payload, client=client),
        read_calendar_events(payload, client=client),
        read_bitable_records(payload, client=client),
    )
    return {
        "contacts": contacts,
        "calendar_events": calendar_events,
        "bitable_records": bitable_records,
        "data_owner": AGENT_ID,
        "source": "feishu_openapi",
    }


async def read_contacts(
    payload: dict[str, Any],
    *,
    client: FeishuOpenAPIClient,
) -> list[dict[str, Any]]:
    return await client.list_department_users(
        department_id=_optional_str(payload, "department_id"),
        page_size=_page_size(payload, key="contact_page_size"),
    )


async def read_calendar_events(
    payload: dict[str, Any],
    *,
    client: FeishuOpenAPIClient,
) -> list[dict[str, Any]]:
    return await client.list_calendar_events(
        calendar_id=_optional_str(payload, "calendar_id"),
        start_time=_optional_str(payload, "calendar_start_time"),
        end_time=_optional_str(payload, "calendar_end_time"),
        page_size=_page_size(payload, key="calendar_page_size"),
    )


async def read_bitable_records(
    payload: dict[str, Any],
    *,
    client: FeishuOpenAPIClient,
) -> list[dict[str, Any]]:
    return await client.search_bitable_records(
        app_token=_optional_str(payload, "bitable_app_token"),
        table_id=_optional_str(payload, "bitable_table_id"),
        view_id=_optional_str(payload, "bitable_view_id"),
        page_size=_page_size(payload, key="bitable_page_size"),
    )


def _optional_str(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    return str(value)


def _page_size(payload: dict[str, Any], *, key: str) -> int:
    value = payload.get(key, 50)
    try:
        return max(1, min(int(value), 200))
    except (TypeError, ValueError):
        return 50


def _unsupported_task(envelope: DelegationEnvelope) -> AgentTaskResponse:
    return AgentTaskResponse(
        agent_id=AGENT_ID,
        trace_id=envelope.trace_id,
        task_type=envelope.task_type,
        result={"error": f"unsupported task_type: {envelope.task_type}"},
    )
