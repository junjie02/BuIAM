from __future__ import annotations

import asyncio

from app.protocol import AgentTaskResponse, DelegationEnvelope
from examples.agent.demo_provider import enterprise_snapshot


AGENT_ID = "enterprise_data_agent"


async def handle_task(envelope: DelegationEnvelope) -> AgentTaskResponse:
    if envelope.task_type == "sleep":
        return await sleep_task(envelope)
    if envelope.task_type != "read_enterprise_data":
        return unsupported(envelope)

    topic = str(envelope.payload.get("report_topic", envelope.payload.get("topic", "Demo enterprise report")))
    return AgentTaskResponse(
        agent_id=AGENT_ID,
        trace_id=envelope.trace_id,
        task_type=envelope.task_type,
        result=enterprise_snapshot(topic),
    )


async def sleep_task(envelope: DelegationEnvelope) -> AgentTaskResponse:
    seconds = float(envelope.payload.get("seconds", 5))
    await asyncio.sleep(max(0, seconds))
    return AgentTaskResponse(
        agent_id=AGENT_ID,
        trace_id=envelope.trace_id,
        task_type=envelope.task_type,
        result={"slept_seconds": seconds},
    )


def unsupported(envelope: DelegationEnvelope) -> AgentTaskResponse:
    return AgentTaskResponse(
        agent_id=AGENT_ID,
        trace_id=envelope.trace_id,
        task_type=envelope.task_type,
        result={"error_code": "UNSUPPORTED_TASK", "message": f"unsupported task_type: {envelope.task_type}"},
    )
