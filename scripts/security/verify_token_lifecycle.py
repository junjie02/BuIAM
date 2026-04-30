from __future__ import annotations

import asyncio
import os
import time
from uuid import uuid4

from common import (
    CheckResult,
    ENTERPRISE_CAPABILITIES,
    SecurityContext,
    find_credential,
    get_credential,
    issue_user_token_http,
    require,
    run_root_task,
    RootTaskRequest,
    cli_main,
)


EXPIRED_TOKEN_TTL_SECONDS = int(os.getenv("BUIAM_SECURITY_EXPIRED_TOKEN_TTL_SECONDS", "1"))
EXPIRED_TOKEN_WAIT_SECONDS = float(os.getenv("BUIAM_SECURITY_EXPIRED_TOKEN_WAIT_SECONDS", "1.2"))
NATURAL_EXPIRY_TOKEN_TTL_SECONDS = int(os.getenv("BUIAM_SECURITY_NATURAL_EXPIRY_TOKEN_TTL_SECONDS", "2"))
NATURAL_EXPIRY_SLEEP_SECONDS = float(os.getenv("BUIAM_SECURITY_NATURAL_EXPIRY_SLEEP_SECONDS", "2.5"))
REVOKE_SLEEP_SECONDS = float(os.getenv("BUIAM_SECURITY_REVOKE_SLEEP_SECONDS", "8"))
REVOKE_DELAY_SECONDS = float(os.getenv("BUIAM_SECURITY_REVOKE_DELAY_SECONDS", "0.5"))
LONG_TOKEN_TTL_SECONDS = int(os.getenv("BUIAM_SECURITY_LONG_TOKEN_TTL_SECONDS", "3600"))


async def run_check(context: SecurityContext) -> CheckResult:
    details = {}
    async with context.client(timeout=20) as client:
        expired = await issue_user_token_http(client, ["web.public:read"], ttl_seconds=EXPIRED_TOKEN_TTL_SECONDS)
        time.sleep(EXPIRED_TOKEN_WAIT_SECONDS)
        expired_trace_id = str(uuid4())
        expired_response = await client.post(
            "/a2a/root-tasks",
            json=RootTaskRequest(
                trace_id=expired_trace_id,
                target_agent_id="external_search_agent",
                task_type="search_public_web",
                user_task="expired token should block new task",
                requested_capabilities=["web.public:read"],
                payload={"query": "expired"},
            ).model_dump(),
            headers={"Authorization": f"Bearer {expired['access_token']}"},
        )
        require(expired_response.status_code == 401, "expired token did not block a new request")
        require(get_credential(expired["credential_id"]).revoked is False, "expired token credential was revoked")
        details["expired_new_request"] = expired_response.json()

        normal = await run_root_task(client)
        credential_ids = [credential["credential_id"] for credential in normal["trace"]["delegation_credentials"]]
        revoke = await client.post(f"/identity/tokens/{normal['token']['jti']}/revoke", json={"reason": "security_script_revoke"})
        revoke.raise_for_status()
        require(all(get_credential(credential_id).revoked for credential_id in credential_ids), "revoke did not cascade")
        details["cascade_revoke"] = {"revoked_credentials": credential_ids, "response": revoke.json()}

        sleep_result = await revoked_sleep(client)
        details["running_cancel"] = sleep_result

        natural = await natural_expiry_sleep(client)
        details["natural_expiry_sleep"] = natural

    return CheckResult(name="verify_token_lifecycle", passed=True, details=details)


async def natural_expiry_sleep(client) -> dict:
    token = await issue_user_token_http(
        client,
        ENTERPRISE_CAPABILITIES,
        ttl_seconds=NATURAL_EXPIRY_TOKEN_TTL_SECONDS,
    )
    trace_id = str(uuid4())
    response = await client.post(
        "/a2a/root-tasks",
        json=RootTaskRequest(
            trace_id=trace_id,
            target_agent_id="enterprise_data_agent",
            task_type="sleep",
            user_task="natural expiry should not cancel running task",
            requested_capabilities=["feishu.contact:read"],
            payload={"seconds": NATURAL_EXPIRY_SLEEP_SECONDS},
        ).model_dump(),
        headers={"Authorization": f"Bearer {token['access_token']}"},
    )
    require(response.status_code == 200, "natural expiry cancelled or blocked an already started task")
    return {"trace_id": trace_id, "response": response.json()}


async def revoked_sleep(client) -> dict:
    token = await issue_user_token_http(client, ENTERPRISE_CAPABILITIES, ttl_seconds=LONG_TOKEN_TTL_SECONDS)
    trace_id = str(uuid4())
    sleep = asyncio.create_task(
        client.post(
            "/a2a/root-tasks",
            json=RootTaskRequest(
                trace_id=trace_id,
                target_agent_id="enterprise_data_agent",
                task_type="sleep",
                user_task="revoke should cancel running task",
                requested_capabilities=["feishu.contact:read"],
                payload={"seconds": REVOKE_SLEEP_SECONDS},
            ).model_dump(),
            headers={"Authorization": f"Bearer {token['access_token']}"},
        )
    )
    await asyncio.sleep(REVOKE_DELAY_SECONDS)
    revoke = await client.post(f"/identity/tokens/{token['jti']}/revoke", json={"reason": "token_revoked"})
    response = await sleep
    trace = (await client.get(f"/audit/traces/{trace_id}")).json()
    require(response.status_code == 409, "revoked token did not cancel running task", {"status": response.status_code})
    require(any("TASK_CANCELLED/token_revoked" in log["reason"] for log in trace["logs"]), "cancel audit was not recorded")
    return {"trace_id": trace_id, "sleep_response": response.json(), "revoke_response": revoke.json()}


if __name__ == "__main__":
    cli_main(
        check_name="verify_token_lifecycle",
        description="验证 token 过期、级联吊销和运行中任务取消语义。",
        check=run_check,
    )
