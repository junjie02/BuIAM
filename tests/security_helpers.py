from __future__ import annotations

import asyncio
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import httpx
import uvicorn


GATEWAY_PORT = 18000
DOC_PORT = 18011
ENTERPRISE_PORT = 18012
EXTERNAL_PORT = 18013
GATEWAY_URL = f"http://127.0.0.1:{GATEWAY_PORT}"
USER_ID = "user_123"
ALL_CAPABILITIES = [
    "report:write",
    "feishu.doc:write",
    "feishu.contact:read",
    "feishu.calendar:read",
    "feishu.wiki:read",
    "feishu.bitable:read",
    "web.public:read",
]
ENTERPRISE_CAPABILITIES = [
    "feishu.contact:read",
    "feishu.calendar:read",
    "feishu.wiki:read",
    "feishu.bitable:read",
]


def configure_test_environment() -> None:
    os.environ["BUIAM_GATEWAY_URL"] = GATEWAY_URL
    os.environ["BUIAM_DEMO_USER_ID"] = USER_ID
    os.environ["DOC_AGENT_ENDPOINT"] = f"http://127.0.0.1:{DOC_PORT}/a2a/tasks"
    os.environ["ENTERPRISE_DATA_AGENT_ENDPOINT"] = f"http://127.0.0.1:{ENTERPRISE_PORT}/a2a/tasks"
    os.environ["EXTERNAL_SEARCH_AGENT_ENDPOINT"] = f"http://127.0.0.1:{EXTERNAL_PORT}/a2a/tasks"
    os.environ["LLM_PROVIDER"] = "mock"
    os.environ["INTENT_GENERATOR_PROVIDER"] = "mock"
    os.environ["INTENT_JUDGE_PROVIDER"] = "mock"
    os.environ["A2A_FORWARD_TIMEOUT_SECONDS"] = "10"


configure_test_environment()

from app.delegation.credential_crypto import auth_context_from_credential
from app.identity.jwt_service import issue_token, verify_token
from app.main import app as gateway_app
from app.protocol import AuthContext, DelegationEnvelope, DelegationHop, RootTaskRequest
from app.registry.bootstrap import register_demo_agents
from app.store.delegation_credentials import get_credential
from app.store.intent_tree import get_intent_node, row_to_intent_node
from app.store.schema import DB_PATH, init_schema
from examples.agent.doc_service import app as doc_app
from examples.agent.enterprise_data_service import app as enterprise_app
from examples.agent.external_search_service import app as external_app


@dataclass
class ServerHandle:
    name: str
    app: object
    port: int
    server: uvicorn.Server | None = None
    thread: threading.Thread | None = None

    def start(self) -> None:
        if is_healthy(self.port):
            return
        config = uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="warning")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, name=f"{self.name}-test-server", daemon=True)
        self.thread.start()
        deadline = time.time() + 15
        while time.time() < deadline:
            if is_healthy(self.port):
                return
            time.sleep(0.1)
        raise RuntimeError(f"{self.name} did not start on port {self.port}")

    def stop(self) -> None:
        if self.server is not None:
            self.server.should_exit = True
        if self.thread is not None:
            self.thread.join(timeout=5)


def is_healthy(port: int) -> bool:
    try:
        response = httpx.get(f"http://127.0.0.1:{port}/health", timeout=1)
        return response.status_code == 200
    except httpx.HTTPError:
        return False


def reset_runtime_db() -> None:
    if Path(DB_PATH).exists():
        Path(DB_PATH).unlink()
    init_schema()
    register_demo_agents()


def build_server_handles() -> list[ServerHandle]:
    return [
        ServerHandle("gateway", gateway_app, GATEWAY_PORT),
        ServerHandle("doc_agent", doc_app, DOC_PORT),
        ServerHandle("enterprise_data_agent", enterprise_app, ENTERPRISE_PORT),
        ServerHandle("external_search_agent", external_app, EXTERNAL_PORT),
    ]


async def run_root_task(
    target_agent_id: str,
    task_type: str,
    capabilities: list[str],
    *,
    user_capabilities: list[str] | None = None,
    payload: dict | None = None,
    ttl_seconds: int = 3600,
    trace_id: str | None = None,
    user_task: str | None = None,
) -> dict:
    trace = trace_id or str(uuid4())
    issued = issue_user_token(
        capabilities=capabilities,
        user_capabilities=user_capabilities,
        ttl_seconds=ttl_seconds,
    )
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=60) as client:
        response = await client.post(
            "/a2a/root-tasks",
            json=RootTaskRequest(
                trace_id=trace,
                target_agent_id=target_agent_id,
                task_type=task_type,
                user_task=user_task or f"security test {task_type} {trace}",
                requested_capabilities=capabilities,
                payload=payload or {"topic": "Security Test Report"},
            ).model_dump(),
            headers={"Authorization": f"Bearer {issued['access_token']}"},
        )
        response.raise_for_status()
        audit_trace = (await client.get(f"/audit/traces/{trace}")).json()
        return {
            "body": response.json(),
            "trace": audit_trace,
            "trace_id": trace,
            "token": issued["access_token"],
            "token_jti": issued["jti"],
            "credential_id": issued["credential_id"],
        }


def issue_user_token(
    *,
    capabilities: list[str],
    user_capabilities: list[str] | None = None,
    ttl_seconds: int = 3600,
) -> dict:
    return issue_token(
        agent_id=USER_ID,
        delegated_user=USER_ID,
        actor_type="user",
        capabilities=capabilities,
        user_capabilities=user_capabilities or capabilities,
        ttl_seconds=ttl_seconds,
    )


def issue_agent_token(
    agent_id: str,
    *,
    capabilities: list[str],
    user_capabilities: list[str] | None = None,
    ttl_seconds: int = 3600,
) -> dict:
    return issue_token(
        agent_id=agent_id,
        delegated_user=USER_ID,
        actor_type="agent",
        capabilities=capabilities,
        user_capabilities=user_capabilities or capabilities,
        ttl_seconds=ttl_seconds,
    )


def auth_context_from_token(token: str) -> AuthContext:
    return verify_token(token)


def auth_context_for_credential(credential_id: str) -> AuthContext:
    credential = get_credential(credential_id)
    if credential is None:
        raise AssertionError(f"credential not found: {credential_id}")
    return auth_context_from_credential(credential)


def credential_path(credential_id: str) -> list:
    path = []
    current_id: str | None = credential_id
    while current_id:
        credential = get_credential(current_id)
        if credential is None:
            raise AssertionError(f"credential not found: {current_id}")
        path.append(credential)
        current_id = credential.parent_credential_id
    return list(reversed(path))


def intent_path(node_id: str) -> list:
    path = []
    current_id: str | None = node_id
    while current_id:
        row = get_intent_node(current_id)
        if row is None:
            raise AssertionError(f"intent node not found: {current_id}")
        path.append(row_to_intent_node(row))
        current_id = row["parent_node_id"]
    return list(reversed(path))


def find_trace_credential(trace: dict, *, subject_id: str) -> dict:
    for credential in trace["delegation_credentials"]:
        if credential["subject_id"] == subject_id:
            return credential
    raise AssertionError(f"credential for subject not found in trace: {subject_id}")


def find_trace_intent(trace: dict, *, actor_id: str, target_agent_id: str) -> dict:
    for node in trace["intent_tree"]:
        if node["actor_id"] == actor_id and node["target_agent_id"] == target_agent_id:
            return node
    raise AssertionError(f"intent node not found in trace: {actor_id} -> {target_agent_id}")


def root_hop_to_doc(capabilities: list[str] | None = None) -> DelegationHop:
    return DelegationHop(
        from_actor=USER_ID,
        to_agent_id="doc_agent",
        task_type="generate_report",
        delegated_capabilities=capabilities or ALL_CAPABILITIES,
        decision="root",
    )


def agent_envelope(
    *,
    trace_id: str,
    caller_agent_id: str,
    target_agent_id: str,
    task_type: str,
    requested_capabilities: list[str],
    auth_context: AuthContext | None,
    delegation_chain: list[DelegationHop] | None = None,
    payload: dict | None = None,
) -> DelegationEnvelope:
    return DelegationEnvelope(
        trace_id=trace_id,
        request_id=str(uuid4()),
        caller_agent_id=caller_agent_id,
        target_agent_id=target_agent_id,
        task_type=task_type,
        requested_capabilities=requested_capabilities,
        delegation_chain=delegation_chain or [],
        auth_context=auth_context,
        payload=payload or {"user_task": f"security test {task_type}"},
    )


def run(coro):
    return asyncio.run(coro)
