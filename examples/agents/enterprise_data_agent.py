from __future__ import annotations

from app.protocol import AgentTaskResponse, DelegationEnvelope
from examples.tools.enterprise import read_bitable, read_contacts, read_wiki


AGENT_ID = "enterprise_data_agent"


async def handle_task(envelope: DelegationEnvelope) -> AgentTaskResponse:
    if envelope.task_type != "read_enterprise_data":
        result = {"error": f"unsupported task_type: {envelope.task_type}"}
    else:
        result = {
            "contacts": read_contacts(),
            "wiki": read_wiki(),
            "bitable": read_bitable(),
        }
    return AgentTaskResponse(
        agent_id=AGENT_ID,
        trace_id=envelope.trace_id,
        task_type=envelope.task_type,
        result=result,
    )
