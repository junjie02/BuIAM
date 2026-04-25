from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.identity.keys import ensure_agent_keypair
from app.main import app, on_startup
from app.protocol import DelegationEnvelope, DelegationHop
from app.store.registry import upsert_agent


DEMO_AGENTS = {
    "doc_agent": {
        "name": "飞书文档助手 Agent",
        "endpoint": "local://doc_agent",
        "static_capabilities": ["report:write"],
    },
    "enterprise_data_agent": {
        "name": "企业数据 Agent",
        "endpoint": "local://enterprise_data_agent",
        "static_capabilities": ["feishu.contact:read", "feishu.wiki:read", "feishu.bitable:read"],
    },
    "external_search_agent": {
        "name": "外部检索 Agent",
        "endpoint": "local://external_search_agent",
        "static_capabilities": ["web.public:read"],
    },
}


def bootstrap_demo_agents() -> None:
    ensure_agent_keypair("user_123")
    for agent_id, config in DEMO_AGENTS.items():
        ensure_agent_keypair(agent_id)
        upsert_agent(agent_id, config["name"], config["endpoint"], config["static_capabilities"])


async def issue_token(
    client: httpx.AsyncClient,
    actor_id: str,
    capabilities: list[str],
    *,
    actor_type: str = "agent",
) -> str:
    response = await client.post(
        "/identity/tokens",
        json={
            "agent_id": actor_id,
            "delegated_user": "user_123" if actor_type == "agent" else actor_id,
            "actor_type": actor_type,
            "capabilities": capabilities,
            "ttl_seconds": 3600,
        },
    )
    response.raise_for_status()
    return response.json()["access_token"]


async def root_task_call(
    client: httpx.AsyncClient,
    token: str,
    *,
    trace_id: str,
    target_agent_id: str,
    task_type: str,
    user_task: str,
    requested_capabilities: list[str],
    payload: dict,
) -> httpx.Response:
    return await client.post(
        "/delegate/root-task",
        json={
            "trace_id": trace_id,
            "target_agent_id": target_agent_id,
            "task_type": task_type,
            "user_task": user_task,
            "requested_capabilities": requested_capabilities,
            "payload": payload,
        },
        headers={"Authorization": f"Bearer {token}"},
    )


async def gateway_call(client: httpx.AsyncClient, token: str, envelope: DelegationEnvelope) -> httpx.Response:
    return await client.post(
        "/delegate/call",
        json=envelope.model_dump(),
        headers={"Authorization": f"Bearer {token}"},
    )


def latest_intent_node_id(trace: dict) -> str:
    return trace["intent_tree"][-1]["node_id"]


def first_chain_hop(trace: dict) -> DelegationHop:
    return DelegationHop.model_validate(trace["chain"][0])


def last_chain_hop(trace: dict) -> DelegationHop:
    return DelegationHop.model_validate(trace["chain"][-1])


async def main() -> None:
    on_startup()
    bootstrap_demo_agents()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        print("== Agent Registry ==")
        print(json.dumps((await client.get("/registry/agents")).json(), ensure_ascii=False, indent=2))

        print("\n== 正常委托：user -> doc_agent -> enterprise_data_agent ==")
        report_trace_id = str(uuid4())
        user_report_token = await issue_token(
            client,
            "user_123",
            ["report:write", "feishu.contact:read", "feishu.wiki:read", "feishu.bitable:read", "web.public:read"],
            actor_type="user",
        )
        root_report = await root_task_call(
            client,
            user_report_token,
            trace_id=report_trace_id,
            target_agent_id="doc_agent",
            task_type="generate_report",
            user_task="请基于企业通讯录、知识库和多维表格生成一份飞书协作报告",
            requested_capabilities=["report:write", "feishu.contact:read", "feishu.wiki:read", "feishu.bitable:read", "web.public:read"],
            payload={"topic": "飞书 AI 协作季度报告"},
        )
        print("user_123 -> doc_agent")
        print(f"HTTP {root_report.status_code}")
        print(json.dumps(root_report.json(), ensure_ascii=False, indent=2))
        root_report.raise_for_status()
        report_trace = (await client.get(f"/audit/traces/{report_trace_id}")).json()
        doc_token = await issue_token(
            client,
            "doc_agent",
            ["report:write", "feishu.contact:read", "feishu.wiki:read", "feishu.bitable:read", "web.public:read"],
        )
        report_response = await gateway_call(
            client,
            doc_token,
            DelegationEnvelope(
                trace_id=report_trace_id,
                request_id=str(uuid4()),
                caller_agent_id="doc_agent",
                target_agent_id="enterprise_data_agent",
                task_type="read_enterprise_data",
                requested_capabilities=["feishu.contact:read", "feishu.wiki:read", "feishu.bitable:read"],
                delegation_chain=[first_chain_hop(report_trace)],
                payload={
                    "report_topic": "飞书 AI 协作季度报告",
                    "user_task": "请基于企业通讯录、知识库和多维表格生成一份飞书协作报告",
                    "parent_intent_node_id": latest_intent_node_id(report_trace),
                },
            ),
        )
        print("doc_agent -> enterprise_data_agent")
        print(f"HTTP {report_response.status_code}")
        print(json.dumps(report_response.json(), ensure_ascii=False, indent=2))

        print("\n== 越权委托：user -> doc_agent -> external_search_agent -> enterprise_data_agent ==")
        weather_trace_id = str(uuid4())
        user_weather_token = await issue_token(client, "user_123", ["report:write", "web.public:read"], actor_type="user")
        weather_root = await root_task_call(
            client,
            user_weather_token,
            trace_id=weather_trace_id,
            target_agent_id="doc_agent",
            task_type="ask_weather",
            user_task="请检索今天的公开天气信息",
            requested_capabilities=["report:write", "web.public:read"],
            payload={"query": "今天的天气怎么样"},
        )
        print("user_123 -> doc_agent")
        print(f"HTTP {weather_root.status_code}")
        print(json.dumps(weather_root.json(), ensure_ascii=False, indent=2))
        weather_root.raise_for_status()
        weather_trace = (await client.get(f"/audit/traces/{weather_trace_id}")).json()
        doc_search_token = await issue_token(client, "doc_agent", ["report:write", "web.public:read"])
        external_response = await gateway_call(
            client,
            doc_search_token,
            DelegationEnvelope(
                trace_id=weather_trace_id,
                request_id=str(uuid4()),
                caller_agent_id="doc_agent",
                target_agent_id="external_search_agent",
                task_type="search_public_web",
                requested_capabilities=["web.public:read"],
                delegation_chain=[first_chain_hop(weather_trace)],
                payload={
                    "query": "今天的天气怎么样",
                    "user_task": "请检索今天的公开天气信息",
                    "parent_intent_node_id": latest_intent_node_id(weather_trace),
                },
            ),
        )
        print("doc_agent -> external_search_agent")
        print(f"HTTP {external_response.status_code}")
        print(json.dumps(external_response.json(), ensure_ascii=False, indent=2))

        weather_trace = (await client.get(f"/audit/traces/{weather_trace_id}")).json()
        external_token = await issue_token(client, "external_search_agent", ["web.public:read"])
        denied_response = await gateway_call(
            client,
            external_token,
            DelegationEnvelope(
                trace_id=weather_trace_id,
                request_id=str(uuid4()),
                caller_agent_id="external_search_agent",
                target_agent_id="enterprise_data_agent",
                task_type="read_enterprise_data",
                requested_capabilities=["feishu.contact:read", "feishu.wiki:read", "feishu.bitable:read"],
                delegation_chain=[first_chain_hop(weather_trace), last_chain_hop(weather_trace)],
                payload={
                    "reason": "external agent tries to read enterprise data after web search",
                    "user_task": "请检索今天的公开天气信息",
                    "parent_intent_node_id": latest_intent_node_id(weather_trace),
                },
            ),
        )
        print("external_search_agent -> enterprise_data_agent")
        print(f"HTTP {denied_response.status_code}")
        print(json.dumps(denied_response.json(), ensure_ascii=False, indent=2))

        print("\n== 审计、身份验证、独立 Chain 与 Intent Tree ==")
        for current_trace_id in [report_trace_id, weather_trace_id]:
            trace_response = await client.get(f"/audit/traces/{current_trace_id}")
            print(json.dumps(trace_response.json(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
