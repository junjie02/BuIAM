from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app


async def main() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        print("== 正常委托：doc_agent -> enterprise_data_agent ==")
        report_response = await client.post(
            "/agents/doc_agent/tasks",
            json={
                "task_type": "generate_report",
                "payload": {"topic": "飞书 AI 协作季度报告"},
            },
        )
        print(json.dumps(report_response.json(), ensure_ascii=False, indent=2))

        print("\n== 越权拦截：external_search_agent -> enterprise_data_agent ==")
        denied_response = await client.post(
            "/agents/external_search_agent/tasks",
            json={
                "task_type": "attempt_enterprise_data_access",
                "payload": {"query": "企业内部数据"},
            },
        )
        print(f"HTTP {denied_response.status_code}")
        print(json.dumps(denied_response.json(), ensure_ascii=False, indent=2))

        print("\n== 审计日志 ==")
        logs_response = await client.get("/audit/logs")
        print(json.dumps(logs_response.json(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
