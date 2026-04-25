from __future__ import annotations

from app.delegation.client import delegation_client
from app.protocol import AgentTaskResponse, DelegationEnvelope
from examples.llm.client import get_llm_provider


AGENT_ID = "doc_agent"


async def handle_task(envelope: DelegationEnvelope) -> AgentTaskResponse:
    if envelope.task_type == "ask_weather":
        search_envelope = delegation_client.build_envelope(
            trace_id=envelope.trace_id,
            caller_agent_id=AGENT_ID,
            target_agent_id="external_search_agent",
            task_type="search_public_web",
            requested_capabilities=["web.public:read"],
            delegation_chain=envelope.delegation_chain,
            auth_context=envelope.auth_context,
            payload={
                "query": envelope.payload.get("query", "今天的天气怎么样"),
                "user_task": envelope.payload.get("user_task", "请检索今天的公开天气信息"),
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

    if envelope.task_type != "generate_report":
        return AgentTaskResponse(
            agent_id=AGENT_ID,
            trace_id=envelope.trace_id,
            task_type=envelope.task_type,
            result={"error": f"unsupported task_type: {envelope.task_type}"},
        )

    enterprise_envelope = delegation_client.build_envelope(
        trace_id=envelope.trace_id,
        caller_agent_id=AGENT_ID,
        target_agent_id="enterprise_data_agent",
        task_type="read_enterprise_data",
        requested_capabilities=[
            "feishu.contact:read",
            "feishu.wiki:read",
            "feishu.bitable:read",
        ],
        delegation_chain=envelope.delegation_chain,
        auth_context=envelope.auth_context,
        payload={"report_topic": envelope.payload.get("topic", "飞书 AI 协作报告")},
    )

    return AgentTaskResponse(
        agent_id=AGENT_ID,
        trace_id=envelope.trace_id,
        task_type=envelope.task_type,
        result={"delegation_envelope": enterprise_envelope.model_dump()},
    )


async def generate_report(topic: str, enterprise_data: dict) -> str:
    llm = get_llm_provider()
    return await llm.complete(
        system="你是飞书文档助手 Agent，负责根据企业数据生成简洁报告。",
        user=f"报告主题：{topic}\n企业数据：{enterprise_data}",
    )
