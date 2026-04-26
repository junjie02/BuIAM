from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.gateway.local_adapter import call_local_agent
from app.protocol import AuthContext, DelegationEnvelope, DelegationHop


async def main() -> None:
    envelope = DelegationEnvelope(
        trace_id="trace_demo_real_agents",
        request_id="req_demo_real_agents",
        caller_agent_id="doc_agent",
        target_agent_id="doc_agent",
        task_type="generate_report",
        requested_capabilities=[
            "feishu.doc:write",
            "feishu.contact:read",
            "feishu.calendar:read",
            "feishu.bitable:read",
        ],
        delegation_chain=[
            DelegationHop(
                from_actor="user_123",
                to_agent_id="doc_agent",
                task_type="generate_report",
                delegated_capabilities=[
                    "feishu.doc:write",
                    "feishu.contact:read",
                    "feishu.calendar:read",
                    "feishu.bitable:read",
                ],
                decision="root",
            )
        ],
        auth_context=AuthContext(
            jti="tok_demo_real_agents",
            sub="doc_agent",
            exp=9999999999,
            agent_id="doc_agent",
            delegated_user="user_123",
            capabilities=[
                "feishu.doc:write",
                "feishu.contact:read",
                "feishu.calendar:read",
                "feishu.bitable:read",
            ],
        ),
        payload={
            "topic": "飞书协作业务周报",
            "user_task": "请汇总通讯录、日历和多维表格信息，并写入飞书文档。",
        },
    )

    response = await call_local_agent("local://doc_agent", envelope)
    print(json.dumps(response.model_dump(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
