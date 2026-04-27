from __future__ import annotations

import os
import json
from typing import Any

from app.delegation.client import delegation_client
from app.protocol import AgentTaskResponse, DelegationEnvelope
from example.agent.enterprise_data_agent import handle_task as handle_enterprise_data_task
from example.agent.feishu_openapi import (
    FeishuAPIError,
    FeishuConfigError,
    FeishuOpenAPIClient,
    FeishuOpenAPISettings,
)
from examples.llm.client import get_llm_provider


AGENT_ID = "doc_agent"


async def handle_task(envelope: DelegationEnvelope) -> AgentTaskResponse:
    if envelope.task_type == "ask_weather":
        return _build_weather_delegation(envelope)
    if envelope.task_type != "generate_report":
        return _unsupported_task(envelope)

    report_topic = str(envelope.payload.get("topic", "Weekly business report"))
    user_task = str(
        envelope.payload.get(
            "user_task",
            "Generate a report from enterprise data and write it into a Feishu document.",
        )
    )

    enterprise_envelope = delegation_client.build_envelope(
        trace_id=envelope.trace_id,
        caller_agent_id=AGENT_ID,
        target_agent_id="enterprise_data_agent",
        task_type="read_enterprise_data",
        requested_capabilities=[
            "feishu.contact:read",
            "feishu.calendar:read",
            "feishu.bitable:read",
        ],
        delegation_chain=envelope.delegation_chain,
        auth_context=envelope.auth_context,
        payload={
            "report_topic": report_topic,
            "user_task": user_task,
            "department_id": envelope.payload.get("department_id"),
            "calendar_id": envelope.payload.get("calendar_id"),
            "calendar_start_time": envelope.payload.get("calendar_start_time"),
            "calendar_end_time": envelope.payload.get("calendar_end_time"),
            "bitable_app_token": envelope.payload.get("bitable_app_token"),
            "bitable_table_id": envelope.payload.get("bitable_table_id"),
            "bitable_view_id": envelope.payload.get("bitable_view_id"),
            "parent_intent_node_id": envelope.intent_node.node_id if envelope.intent_node else None,
        },
    )
    enterprise_response = await handle_enterprise_data_task(enterprise_envelope)
    enterprise_result = enterprise_response.result
    if enterprise_result.get("error_code"):
        return AgentTaskResponse(
            agent_id=AGENT_ID,
            trace_id=envelope.trace_id,
            task_type=envelope.task_type,
            result={
                "error_code": "UPSTREAM_ENTERPRISE_DATA_FAILED",
                "message": "enterprise_data_agent failed to fetch data",
                "upstream": enterprise_result,
                "delegation_envelope": enterprise_envelope.model_dump(),
            },
        )

    report_content = await compile_report(
        topic=report_topic,
        user_task=user_task,
        enterprise_data=enterprise_result,
    )
    try:
        doc_result = await write_feishu_doc(title=report_topic, content=report_content)
    except (FeishuConfigError, FeishuAPIError) as error:
        return AgentTaskResponse(
            agent_id=AGENT_ID,
            trace_id=envelope.trace_id,
            task_type=envelope.task_type,
            result={
                "error_code": error.__class__.__name__,
                "message": str(error),
                "report_preview": report_content[:500],
                "enterprise_data": enterprise_result,
            },
        )

    return AgentTaskResponse(
        agent_id=AGENT_ID,
        trace_id=envelope.trace_id,
        task_type=envelope.task_type,
        result={
            "message": "report generated and written to Feishu doc",
            "document": doc_result,
            "enterprise_data": enterprise_result,
            "report_preview": report_content[:500],
        },
    )


def _build_weather_delegation(envelope: DelegationEnvelope) -> AgentTaskResponse:
    search_envelope = delegation_client.build_envelope(
        trace_id=envelope.trace_id,
        caller_agent_id=AGENT_ID,
        target_agent_id="external_search_agent",
        task_type="search_public_web",
        requested_capabilities=["web.public:read"],
        delegation_chain=envelope.delegation_chain,
        auth_context=envelope.auth_context,
        payload={
            "query": envelope.payload.get("query", "today weather"),
            "user_task": envelope.payload.get("user_task", "Search public weather information."),
            "parent_intent_node_id": envelope.intent_node.node_id if envelope.intent_node else None,
        },
    )
    return AgentTaskResponse(
        agent_id=AGENT_ID,
        trace_id=envelope.trace_id,
        task_type=envelope.task_type,
        result={
            "message": "doc_agent accepted weather task and prepared delegation to external_search_agent",
            "delegation_envelope": search_envelope.model_dump(),
        },
    )


async def compile_report(
    *,
    topic: str,
    user_task: str,
    enterprise_data: dict[str, Any],
) -> str:
    llm = get_llm_provider()
    prompt = build_report_prompt(topic=topic, user_task=user_task, enterprise_data=enterprise_data)
    return await llm.complete(
        system=(
            "You are a Feishu document assistant. "
            "Write a concise business report in Chinese using the provided enterprise data. "
            "Use plain text headings and short bullet-style lines."
        ),
        user=prompt,
    )


def build_report_prompt(
    *,
    topic: str,
    user_task: str,
    enterprise_data: dict[str, Any],
) -> str:
    return (
        f"Report topic: {topic}\n"
        f"User task: {user_task}\n"
        "Enterprise data (JSON):\n"
        f"{json.dumps(enterprise_data, ensure_ascii=False, indent=2)}\n"
        "Write sections for Summary, Key Findings, Calendar Signals, and Suggested Actions."
    )


async def write_feishu_doc(*, title: str, content: str) -> dict[str, Any]:
    settings = FeishuOpenAPISettings.from_env()
    client = FeishuOpenAPIClient(settings)
    document = await client.create_docx_document(title=title)
    document_id = _extract_document_id(document)
    append_result = await client.append_docx_plain_text(document_id=document_id, content=content)
    return {
        "document_id": document_id,
        "title": document.get("title", title),
        "revision_id": document.get("revision_id"),
        "url": build_doc_url(document_id),
        "write_result": append_result,
    }


def build_doc_url(document_id: str) -> str | None:
    base_url = os.getenv("FEISHU_DOC_WEB_BASE_URL")
    if not base_url:
        return None
    return f"{base_url.rstrip('/')}/{document_id}"


def _extract_document_id(document: dict[str, Any]) -> str:
    document_id = document.get("document_id") or document.get("obj_token")
    if not document_id:
        raise FeishuAPIError("Feishu doc create response did not include a document id")
    return str(document_id)


def _unsupported_task(envelope: DelegationEnvelope) -> AgentTaskResponse:
    return AgentTaskResponse(
        agent_id=AGENT_ID,
        trace_id=envelope.trace_id,
        task_type=envelope.task_type,
        result={"error": f"unsupported task_type: {envelope.task_type}"},
    )
