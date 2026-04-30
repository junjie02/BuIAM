from __future__ import annotations

import asyncio
import time
from uuid import uuid4

import httpx

from app.delegation.credential_crypto import auth_context_from_credential
from app.protocol import RootTaskRequest
from app.store.delegation_credentials import get_credential
from tests.security_helpers import (
    ALL_CAPABILITIES,
    ENTERPRISE_CAPABILITIES,
    GATEWAY_URL,
    agent_envelope,
    find_trace_credential,
    issue_agent_token,
    issue_user_token,
    root_hop_to_doc,
    run,
    run_root_task,
)


def test_revoked_root_token_cascades_and_descendant_cannot_delegate(servers) -> None:
    result = run(run_root_task("doc_agent", "generate_report", ALL_CAPABILITIES))
    trace = result["trace"]
    doc_credential = get_credential(find_trace_credential(trace, subject_id="doc_agent")["credential_id"])
    enterprise_credential = get_credential(
        find_trace_credential(trace, subject_id="enterprise_data_agent")["credential_id"]
    )
    assert doc_credential is not None
    assert enterprise_credential is not None

    revoke = httpx.post(
        f"{GATEWAY_URL}/identity/tokens/{result['token_jti']}/revoke",
        json={"reason": "security_test_revoke"},
        timeout=10,
    )
    revoke.raise_for_status()
    assert get_credential(doc_credential.credential_id).revoked is True
    assert get_credential(enterprise_credential.credential_id).revoked is True

    issued = issue_agent_token("doc_agent", capabilities=ALL_CAPABILITIES)
    response = httpx.post(
        f"{GATEWAY_URL}/a2a/agents/enterprise_data_agent/tasks",
        json=agent_envelope(
            trace_id=trace["trace_id"],
            caller_agent_id="doc_agent",
            target_agent_id="enterprise_data_agent",
            task_type="read_enterprise_data",
            requested_capabilities=["feishu.contact:read"],
            auth_context=auth_context_from_credential(doc_credential),
            delegation_chain=[root_hop_to_doc()],
        ).model_dump(),
        headers={"Authorization": f"Bearer {issued['access_token']}"},
        timeout=10,
    )
    assert response.status_code == 403
    assert response.json()["detail"]["error_code"] == "AUTH_CREDENTIAL_REVOKED"


def test_expired_token_blocks_new_request_without_revoking_credential(servers) -> None:
    issued = issue_user_token(capabilities=["web.public:read"], ttl_seconds=1)
    time.sleep(1.2)
    response = httpx.post(
        f"{GATEWAY_URL}/a2a/root-tasks",
        json=RootTaskRequest(
            trace_id=str(uuid4()),
            target_agent_id="external_search_agent",
            task_type="search_public_web",
            user_task="expired token request",
            requested_capabilities=["web.public:read"],
            payload={"query": "expired"},
        ).model_dump(),
        headers={"Authorization": f"Bearer {issued['access_token']}"},
        timeout=10,
    )

    assert response.status_code == 401
    assert response.json()["detail"]["error_code"] == "AUTH_TOKEN_EXPIRED"
    assert get_credential(issued["credential_id"]).revoked is False


def test_natural_expiry_does_not_cancel_already_started_sleep_task(servers) -> None:
    issued = issue_user_token(capabilities=ENTERPRISE_CAPABILITIES, ttl_seconds=2)
    trace_id = str(uuid4())
    response = httpx.post(
        f"{GATEWAY_URL}/a2a/root-tasks",
        json=RootTaskRequest(
            trace_id=trace_id,
            target_agent_id="enterprise_data_agent",
            task_type="sleep",
            user_task="sleep through natural expiry",
            requested_capabilities=["feishu.contact:read"],
            payload={"seconds": 2.5},
        ).model_dump(),
        headers={"Authorization": f"Bearer {issued['access_token']}"},
        timeout=5,
    )

    assert response.status_code == 200
    assert response.json()["result"]["slept_seconds"] == 2.5
    assert get_credential(issued["credential_id"]).revoked is False


def test_revocation_cancels_running_sleep_task_and_records_audit(servers) -> None:
    result = run(_run_revoked_sleep())
    assert result["sleep_status"] == 409
    assert result["sleep_body"]["detail"]["error_code"] == "TASK_CANCELLED"
    assert result["revoke_body"]["cancelled_tasks"] >= 1
    assert any("TASK_CANCELLED/token_revoked" in log["reason"] for log in result["trace"]["logs"])


async def _run_revoked_sleep() -> dict:
    issued = issue_user_token(capabilities=ENTERPRISE_CAPABILITIES, ttl_seconds=3600)
    trace_id = str(uuid4())
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=20) as client:
        sleep_request = asyncio.create_task(
            client.post(
                "/a2a/root-tasks",
                json=RootTaskRequest(
                    trace_id=trace_id,
                    target_agent_id="enterprise_data_agent",
                    task_type="sleep",
                    user_task="sleep until token revoke",
                    requested_capabilities=["feishu.contact:read"],
                    payload={"seconds": 8},
                ).model_dump(),
                headers={"Authorization": f"Bearer {issued['access_token']}"},
            )
        )
        await asyncio.sleep(0.5)
        revoke = await client.post(f"/identity/tokens/{issued['jti']}/revoke", json={"reason": "token_revoked"})
        sleep_response = await sleep_request
        trace = (await client.get(f"/audit/traces/{trace_id}")).json()
    return {
        "sleep_status": sleep_response.status_code,
        "sleep_body": sleep_response.json(),
        "revoke_body": revoke.json(),
        "trace": trace,
    }
