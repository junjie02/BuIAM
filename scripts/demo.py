from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import httpx
import uvicorn
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
load_dotenv()

os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ.setdefault("INTENT_GENERATOR_PROVIDER", "mock")
os.environ.setdefault("INTENT_JUDGE_PROVIDER", "mock")

from app.main import app as gateway_app
from app.protocol import RootTaskRequest
from examples.agent.doc_service import app as doc_app
from examples.agent.enterprise_data_service import app as enterprise_app
from examples.agent.external_search_service import app as external_app


GATEWAY_URL = os.getenv("BUIAM_GATEWAY_URL", "http://127.0.0.1:8000")
USER_ID = os.getenv("BUIAM_DEMO_USER_ID", "user_123")
DOC_AGENT_ENDPOINT = os.getenv("DOC_AGENT_ENDPOINT", "http://127.0.0.1:8011/a2a/tasks")
ENTERPRISE_DATA_AGENT_ENDPOINT = os.getenv("ENTERPRISE_DATA_AGENT_ENDPOINT", "http://127.0.0.1:8012/a2a/tasks")
EXTERNAL_SEARCH_AGENT_ENDPOINT = os.getenv("EXTERNAL_SEARCH_AGENT_ENDPOINT", "http://127.0.0.1:8013/a2a/tasks")
ALL_CAPABILITIES = [
    "report:write",
    "feishu.doc:write",
    "feishu.contact:read",
    "feishu.calendar:read",
    "feishu.wiki:read",
    "feishu.bitable:read",
    "web.public:read",
]


def port_from_url(url: str, default: int) -> int:
    parsed = urlparse(url)
    return parsed.port or default


@dataclass
class ManagedServer:
    name: str
    app: object
    port: int
    server: uvicorn.Server | None = None
    thread: threading.Thread | None = None

    async def ensure_running(self) -> None:
        if await is_healthy(f"http://127.0.0.1:{self.port}/health"):
            return
        config = uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="warning")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, name=f"{self.name}-server", daemon=True)
        self.thread.start()
        deadline = time.time() + 15
        while time.time() < deadline:
            if await is_healthy(f"http://127.0.0.1:{self.port}/health"):
                return
            await asyncio.sleep(0.1)
        raise RuntimeError(f"{self.name} did not start on port {self.port}")

    def stop(self) -> None:
        if self.server is not None:
            self.server.should_exit = True
        if self.thread is not None:
            self.thread.join(timeout=5)


async def is_healthy(url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=1) as client:
            response = await client.get(url)
        return response.status_code == 200
    except httpx.HTTPError:
        return False


async def issue_user_token(client: httpx.AsyncClient, capabilities: list[str]) -> str:
    response = await client.post(
        "/identity/tokens",
        json={
            "agent_id": USER_ID,
            "delegated_user": USER_ID,
            "actor_type": "user",
            "capabilities": capabilities,
            "user_capabilities": capabilities,
            "ttl_seconds": 3600,
        },
    )
    response.raise_for_status()
    return response.json()["access_token"]


async def root_task(
    client: httpx.AsyncClient,
    *,
    token: str,
    request: RootTaskRequest,
) -> httpx.Response:
    return await client.post(
        "/a2a/root-tasks",
        json=request.model_dump(),
        headers={"Authorization": f"Bearer {token}"},
    )


async def main() -> None:
    os.environ.setdefault("BUIAM_GATEWAY_URL", GATEWAY_URL)
    servers = [
        ManagedServer("gateway", gateway_app, port_from_url(GATEWAY_URL, 8000)),
        ManagedServer("doc_agent", doc_app, port_from_url(DOC_AGENT_ENDPOINT, 8011)),
        ManagedServer("enterprise_data_agent", enterprise_app, port_from_url(ENTERPRISE_DATA_AGENT_ENDPOINT, 8012)),
        ManagedServer("external_search_agent", external_app, port_from_url(EXTERNAL_SEARCH_AGENT_ENDPOINT, 8013)),
    ]
    try:
        for server in servers:
            await server.ensure_running()

        async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=60) as client:
            print("== Agent Registry ==")
            print(json.dumps((await client.get("/registry/agents")).json(), ensure_ascii=False, indent=2))

            print("\n== Normal Chain: user -> doc_agent -> enterprise_data_agent ==")
            normal_trace_id = str(uuid4())
            normal_token = await issue_user_token(client, ALL_CAPABILITIES)
            normal_response = await root_task(
                client,
                token=normal_token,
                request=RootTaskRequest(
                    trace_id=normal_trace_id,
                    target_agent_id="doc_agent",
                    task_type="generate_report",
                    user_task="Generate a Feishu collaboration report from enterprise data.",
                    requested_capabilities=ALL_CAPABILITIES,
                    payload={"topic": "A2A Delegation Demo Report"},
                ),
            )
            print(f"HTTP {normal_response.status_code}")
            print(json.dumps(normal_response.json(), ensure_ascii=False, indent=2))
            normal_response.raise_for_status()

            print("\n== Denied Chain: user -> external_search_agent -> enterprise_data_agent ==")
            denied_trace_id = str(uuid4())
            denied_token = await issue_user_token(client, ["web.public:read"])
            denied_response = await root_task(
                client,
                token=denied_token,
                request=RootTaskRequest(
                    trace_id=denied_trace_id,
                    target_agent_id="external_search_agent",
                    task_type="search_then_read_enterprise",
                    user_task="Search public information, then try to read enterprise data.",
                    requested_capabilities=["web.public:read"],
                    payload={"query": "public Feishu weather"},
                ),
            )
            print(f"HTTP {denied_response.status_code}")
            print(json.dumps(denied_response.json(), ensure_ascii=False, indent=2))
            denied_response.raise_for_status()

            print("\n== Audit Trace Summary ==")
            for trace_id in [normal_trace_id, denied_trace_id]:
                trace = (await client.get(f"/audit/traces/{trace_id}")).json()
                print(
                    json.dumps(
                        {
                            "trace_id": trace_id,
                            "audit_decisions": [
                                {
                                    "caller": log["caller_agent_id"],
                                    "target": log["target_agent_id"],
                                    "decision": log["decision"],
                                    "reason": log["reason"],
                                }
                                for log in trace["logs"]
                            ],
                            "chain": trace["chain"],
                            "credential_count": len(trace["delegation_credentials"]),
                            "intent_node_count": len(trace["intent_tree"]),
                            "auth_event_count": len(trace["auth_events"]),
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
    finally:
        if os.getenv("BUIAM_DEMO_KEEP_SERVERS", "0") != "1":
            for server in reversed(servers):
                server.stop()


if __name__ == "__main__":
    asyncio.run(main())
