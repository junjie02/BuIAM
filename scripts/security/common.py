from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse
from uuid import uuid4

import httpx
import uvicorn
from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
os.chdir(REPO_ROOT)
load_dotenv()

GATEWAY_URL = os.getenv("BUIAM_GATEWAY_URL", "http://127.0.0.1:8000").rstrip("/")
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
ENTERPRISE_CAPABILITIES = [
    "feishu.contact:read",
    "feishu.calendar:read",
    "feishu.wiki:read",
    "feishu.bitable:read",
]

os.environ.setdefault("BUIAM_GATEWAY_URL", GATEWAY_URL)
os.environ.setdefault("BUIAM_DEMO_USER_ID", USER_ID)
os.environ.setdefault("DOC_AGENT_ENDPOINT", DOC_AGENT_ENDPOINT)
os.environ.setdefault("ENTERPRISE_DATA_AGENT_ENDPOINT", ENTERPRISE_DATA_AGENT_ENDPOINT)
os.environ.setdefault("EXTERNAL_SEARCH_AGENT_ENDPOINT", EXTERNAL_SEARCH_AGENT_ENDPOINT)
os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ.setdefault("INTENT_GENERATOR_PROVIDER", "mock")
os.environ.setdefault("INTENT_JUDGE_PROVIDER", "mock")
os.environ.setdefault("A2A_FORWARD_TIMEOUT_SECONDS", "10")

from app.delegation.credential_crypto import auth_context_from_credential, verify_credential_integrity
from app.identity.jwt_service import issue_token
from app.intent.crypto import verify_intent_node_signature
from app.main import app as gateway_app
from app.protocol import DelegationEnvelope, DelegationHop, RootTaskRequest
from app.registry.bootstrap import register_demo_agents
from app.store.delegation_credentials import get_credential, list_credentials
from app.store.intent_tree import get_intent_node, row_to_intent_node
from app.store.schema import DB_PATH, init_schema
from examples.agent.doc_service import app as doc_app
from examples.agent.enterprise_data_service import app as enterprise_app
from examples.agent.external_search_service import app as external_app


def port_from_url(url: str, default: int) -> int:
    parsed = urlparse(url)
    return parsed.port or default


def base_url_from_endpoint(endpoint: str, default: str) -> str:
    parsed = urlparse(endpoint)
    if not parsed.scheme or not parsed.netloc:
        return default
    return f"{parsed.scheme}://{parsed.netloc}"


@dataclass
class CheckResult:
    name: str
    passed: bool
    details: dict[str, Any] = field(default_factory=dict)


class CheckFailure(AssertionError):
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


@dataclass
class ManagedServer:
    name: str
    app: object
    url: str
    port: int
    server: uvicorn.Server | None = None
    thread: threading.Thread | None = None

    async def ensure_running(self) -> None:
        if await is_healthy(f"{self.url}/health"):
            return
        config = uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="warning")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, name=f"{self.name}-security-script", daemon=True)
        self.thread.start()
        deadline = time.time() + 15
        while time.time() < deadline:
            if await is_healthy(f"{self.url}/health"):
                return
            await asyncio.sleep(0.1)
        raise RuntimeError(f"{self.name} did not start on port {self.port}")

    def stop(self) -> None:
        if self.server is not None:
            self.server.should_exit = True
        if self.thread is not None:
            self.thread.join(timeout=5)


@dataclass
class SecurityContext:
    args: argparse.Namespace
    servers: list[ManagedServer]

    async def __aenter__(self):
        if not self.args.keep_db:
            reset_runtime_db()
        else:
            init_schema()
            register_demo_agents()
        for server in self.servers:
            await server.ensure_running()
        register_demo_agents()
        return self

    async def __aexit__(self, *_exc):
        if not self.args.keep_db:
            for server in reversed(self.servers):
                server.stop()

    def client(self, *, timeout: float = 60) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=GATEWAY_URL, timeout=timeout)


def build_servers() -> list[ManagedServer]:
    return [
        ManagedServer("gateway", gateway_app, GATEWAY_URL, port_from_url(GATEWAY_URL, 8000)),
        ManagedServer(
            "doc_agent",
            doc_app,
            base_url_from_endpoint(DOC_AGENT_ENDPOINT, "http://127.0.0.1:8011"),
            port_from_url(DOC_AGENT_ENDPOINT, 8011),
        ),
        ManagedServer(
            "enterprise_data_agent",
            enterprise_app,
            base_url_from_endpoint(ENTERPRISE_DATA_AGENT_ENDPOINT, "http://127.0.0.1:8012"),
            port_from_url(ENTERPRISE_DATA_AGENT_ENDPOINT, 8012),
        ),
        ManagedServer(
            "external_search_agent",
            external_app,
            base_url_from_endpoint(EXTERNAL_SEARCH_AGENT_ENDPOINT, "http://127.0.0.1:8013"),
            port_from_url(EXTERNAL_SEARCH_AGENT_ENDPOINT, 8013),
        ),
    ]


def reset_runtime_db() -> None:
    if Path(DB_PATH).exists():
        Path(DB_PATH).unlink()
    init_schema()
    register_demo_agents()


async def is_healthy(url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=1) as client:
            response = await client.get(url)
        return response.status_code == 200
    except httpx.HTTPError:
        return False


def require(condition: bool, message: str, details: dict[str, Any] | None = None) -> None:
    if not condition:
        raise CheckFailure(message, details)


async def issue_user_token_http(
    client: httpx.AsyncClient,
    capabilities: list[str],
    *,
    ttl_seconds: int = 3600,
    user_capabilities: list[str] | None = None,
) -> dict:
    response = await client.post(
        "/identity/tokens",
        json={
            "agent_id": USER_ID,
            "delegated_user": USER_ID,
            "actor_type": "user",
            "capabilities": capabilities,
            "user_capabilities": user_capabilities or capabilities,
            "ttl_seconds": ttl_seconds,
        },
    )
    response.raise_for_status()
    return response.json()


def issue_agent_token(agent_id: str, capabilities: list[str], *, ttl_seconds: int = 3600) -> dict:
    return issue_token(
        agent_id=agent_id,
        delegated_user=USER_ID,
        actor_type="agent",
        capabilities=capabilities,
        user_capabilities=capabilities,
        ttl_seconds=ttl_seconds,
    )


async def run_root_task(
    client: httpx.AsyncClient,
    *,
    target_agent_id: str = "doc_agent",
    task_type: str = "generate_report",
    capabilities: list[str] | None = None,
    payload: dict[str, Any] | None = None,
    trace_id: str | None = None,
    user_task: str | None = None,
    ttl_seconds: int = 3600,
) -> dict:
    requested = capabilities or ALL_CAPABILITIES
    trace = trace_id or str(uuid4())
    token = await issue_user_token_http(client, requested, ttl_seconds=ttl_seconds)
    response = await client.post(
        "/a2a/root-tasks",
        json=RootTaskRequest(
            trace_id=trace,
            target_agent_id=target_agent_id,
            task_type=task_type,
            user_task=user_task or f"security script {task_type} {trace}",
            requested_capabilities=requested,
            payload=payload or {"topic": "Security Script Report"},
        ).model_dump(),
        headers={"Authorization": f"Bearer {token['access_token']}"},
    )
    response.raise_for_status()
    audit_trace = (await client.get(f"/audit/traces/{trace}")).json()
    return {
        "trace_id": trace,
        "body": response.json(),
        "trace": audit_trace,
        "token": token,
    }


def credential_path(credential_id: str) -> list:
    path = []
    current_id: str | None = credential_id
    while current_id:
        credential = get_credential(current_id)
        require(credential is not None, "credential not found", {"credential_id": current_id})
        path.append(credential)
        current_id = credential.parent_credential_id
    return list(reversed(path))


def intent_path(node_id: str) -> list:
    path = []
    current_id: str | None = node_id
    while current_id:
        row = get_intent_node(current_id)
        require(row is not None, "intent node not found", {"node_id": current_id})
        path.append(row_to_intent_node(row))
        current_id = row["parent_node_id"]
    return list(reversed(path))


def find_credential(trace: dict, subject_id: str) -> dict:
    for credential in trace["delegation_credentials"]:
        if credential["subject_id"] == subject_id:
            return credential
    raise CheckFailure("credential missing from trace", {"subject_id": subject_id})


def find_intent(trace: dict, actor_id: str, target_agent_id: str) -> dict:
    for node in trace["intent_tree"]:
        if node["actor_id"] == actor_id and node["target_agent_id"] == target_agent_id:
            return node
    raise CheckFailure("intent node missing from trace", {"actor_id": actor_id, "target_agent_id": target_agent_id})


def root_hop_to_doc() -> DelegationHop:
    return DelegationHop(
        from_actor=USER_ID,
        to_agent_id="doc_agent",
        task_type="generate_report",
        delegated_capabilities=ALL_CAPABILITIES,
        decision="root",
    )


def agent_envelope(
    *,
    trace_id: str,
    caller_agent_id: str,
    target_agent_id: str,
    task_type: str,
    requested_capabilities: list[str],
    credential_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> DelegationEnvelope:
    credential = get_credential(credential_id) if credential_id else None
    return DelegationEnvelope(
        trace_id=trace_id,
        request_id=str(uuid4()),
        caller_agent_id=caller_agent_id,
        target_agent_id=target_agent_id,
        task_type=task_type,
        requested_capabilities=requested_capabilities,
        delegation_chain=[root_hop_to_doc()] if caller_agent_id == "doc_agent" else [],
        auth_context=auth_context_from_credential(credential) if credential else None,
        payload=payload or {"user_task": f"security script {task_type}"},
    )


def summarize_credential_path(path: list) -> list[dict]:
    return [
        {
            "credential_id": credential.credential_id,
            "issuer_id": credential.issuer_id,
            "subject_id": credential.subject_id,
            "parent_credential_id": credential.parent_credential_id,
            "root_credential_id": credential.root_credential_id,
            "trace_id": credential.trace_id,
            "signature_valid": verify_credential_integrity(credential),
            "revoked": credential.revoked,
        }
        for credential in path
    ]


def summarize_intent_path(path: list) -> list[dict]:
    return [
        {
            "node_id": node.node_id,
            "parent_node_id": node.parent_node_id,
            "actor_id": node.actor_id,
            "actor_type": node.actor_type,
            "target_agent_id": node.target_agent_id,
            "task_type": node.task_type,
            "signature_valid": verify_intent_node_signature(node),
        }
        for node in path
    ]


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--keep-db", action="store_true", help="保留 data/audit.db，不在脚本开始时清空")
    parser.add_argument("--trace-id", help="复用或指定 trace_id；未提供时自动生成")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式结果")


async def run_with_context(args: argparse.Namespace, check: Callable[[SecurityContext], Awaitable[CheckResult]]) -> CheckResult:
    async with SecurityContext(args=args, servers=build_servers()) as context:
        try:
            return await check(context)
        except CheckFailure as error:
            return CheckResult(
                name=getattr(args, "check_name", "security_check"),
                passed=False,
                details={"reason": str(error), **error.details},
            )


def emit_result(result: CheckResult, *, as_json: bool) -> None:
    payload = {"check": result.name, "passed": result.passed, **result.details}
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    status = "PASS" if result.passed else "FAIL"
    print(f"[{status}] {result.name}")
    for key, value in result.details.items():
        print(f"- {key}: {json.dumps(value, ensure_ascii=False)}")


def cli_main(
    *,
    check_name: str,
    description: str,
    check: Callable[[SecurityContext], Awaitable[CheckResult]],
    extra_args: Callable[[argparse.ArgumentParser], None] | None = None,
) -> None:
    parser = argparse.ArgumentParser(description=description)
    add_common_args(parser)
    if extra_args is not None:
        extra_args(parser)
    args = parser.parse_args()
    args.check_name = check_name
    result = asyncio.run(run_with_context(args, check))
    emit_result(result, as_json=args.json)
    if not result.passed:
        raise SystemExit(1)
