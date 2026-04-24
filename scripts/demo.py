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
        "static_capabilities": [
            "feishu.contact:read",
            "feishu.wiki:read",
            "feishu.bitable:read",
        ],
    },
    "external_search_agent": {
        "name": "外部检索 Agent",
        "endpoint": "local://external_search_agent",
        "static_capabilities": ["web.public:read"],
    },
}


def bootstrap_demo_agents() -> None:
    for agent_id, config in DEMO_AGENTS.items():
        ensure_agent_keypair(agent_id)
        upsert_agent(
            agent_id=agent_id,
            name=config["name"],
            endpoint=config["endpoint"],
            static_capabilities=config["static_capabilities"],
        )


async def issue_token(client: httpx.AsyncClient, agent_id: str, capabilities: list[str]) -> str:
    response = await client.post(
        "/identity/tokens",
        json={
            "agent_id": agent_id,
            "delegated_user": "user_123",
            "capabilities": capabilities,
            "ttl_seconds": 3600,
        },
    )
    response.raise_for_status()
    return response.json()["access_token"]


async def gateway_call(
    client: httpx.AsyncClient,
    token: str,
    envelope: DelegationEnvelope,
) -> httpx.Response:
    return await client.post(
        "/delegate/call",
        json=envelope.model_dump(),
        headers={"Authorization": f"Bearer {token}"},
    )


async def main() -> None:
    on_startup()
    bootstrap_demo_agents()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        print("== Agent Registry ==")
        print(json.dumps((await client.get("/registry/agents")).json(), ensure_ascii=False, indent=2))

        print("\n== 正常委托：doc_agent -> enterprise_data_agent ==")
        doc_token = await issue_token(
            client,
            "doc_agent",
            [
                "report:write",
                "feishu.contact:read",
                "feishu.wiki:read",
                "feishu.bitable:read",
                "web.public:read",
            ],
        )
        trace_id = str(uuid4())
        root_hop = DelegationHop(
            from_actor="user",
            to_agent_id="doc_agent",
            task_type="generate_report",
            delegated_capabilities=[
                "report:write",
                "feishu.contact:read",
                "feishu.wiki:read",
                "feishu.bitable:read",
                "web.public:read",
            ],
            decision="root",
        )
        report_response = await gateway_call(
            client,
            doc_token,
            DelegationEnvelope(
                trace_id=trace_id,
                request_id=str(uuid4()),
                caller_agent_id="doc_agent",
                target_agent_id="enterprise_data_agent",
                task_type="read_enterprise_data",
                requested_capabilities=[
                    "feishu.contact:read",
                    "feishu.wiki:read",
                    "feishu.bitable:read",
                ],
                delegation_chain=[root_hop],
                payload={"report_topic": "飞书 AI 协作季度报告"},
            ),
        )
        print(json.dumps(report_response.json(), ensure_ascii=False, indent=2))

        print("\n== 长链路越权拦截：user -> doc_agent -> external_search_agent -> enterprise_data_agent ==")
        doc_search_token = await issue_token(
            client,
            "doc_agent",
            ["report:write", "web.public:read"],
        )
        deny_trace_id = str(uuid4())
        root_to_doc = DelegationHop(
            from_actor="user",
            to_agent_id="doc_agent",
            task_type="ask_weather",
            delegated_capabilities=["report:write", "web.public:read"],
            decision="root",
        )
        external_response = await gateway_call(
            client,
            doc_search_token,
            DelegationEnvelope(
                trace_id=deny_trace_id,
                request_id=str(uuid4()),
                caller_agent_id="doc_agent",
                target_agent_id="external_search_agent",
                task_type="search_public_web",
                requested_capabilities=["web.public:read"],
                delegation_chain=[root_to_doc],
                payload={"query": "今天的天气怎么样"},
            ),
        )
        print("doc_agent -> external_search_agent")
        print(json.dumps(external_response.json(), ensure_ascii=False, indent=2))

        external_token = await issue_token(client, "external_search_agent", ["web.public:read"])
        denied_response = await gateway_call(
            client,
            external_token,
            DelegationEnvelope(
                trace_id=deny_trace_id,
                request_id=str(uuid4()),
                caller_agent_id="external_search_agent",
                target_agent_id="enterprise_data_agent",
                task_type="read_enterprise_data",
                requested_capabilities=[
                    "feishu.contact:read",
                    "feishu.wiki:read",
                    "feishu.bitable:read",
                ],
                delegation_chain=[
                    root_to_doc,
                    DelegationHop(
                        from_actor="doc_agent",
                        to_agent_id="external_search_agent",
                        task_type="search_public_web",
                        delegated_capabilities=["web.public:read"],
                        decision="allow",
                    ),
                ],
                payload={"reason": "external agent tries to read enterprise data after web search"},
            ),
        )
        print("external_search_agent -> enterprise_data_agent")
        print(f"HTTP {denied_response.status_code}")
        print(json.dumps(denied_response.json(), ensure_ascii=False, indent=2))

        print("\n== 审计与独立 Chain ==")
        for current_trace_id in [trace_id, deny_trace_id]:
            trace_response = await client.get(f"/audit/traces/{current_trace_id}")
            print(json.dumps(trace_response.json(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
