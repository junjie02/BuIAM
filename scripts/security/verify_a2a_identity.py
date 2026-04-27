from __future__ import annotations

from uuid import uuid4

from common import (
    CheckResult,
    SecurityContext,
    agent_envelope,
    find_credential,
    issue_agent_token,
    require,
    run_root_task,
    cli_main,
)


async def run_check(context: SecurityContext) -> CheckResult:
    results = {}
    async with context.client() as client:
        missing_trace = str(uuid4())
        missing = await client.post(
            "/a2a/agents/enterprise_data_agent/tasks",
            json=agent_envelope(
                trace_id=missing_trace,
                caller_agent_id="doc_agent",
                target_agent_id="enterprise_data_agent",
                task_type="read_enterprise_data",
                requested_capabilities=["feishu.contact:read"],
            ).model_dump(),
        )
        missing_audit = (await client.get(f"/audit/traces/{missing_trace}")).json()
        require(missing.status_code == 401, "missing bearer was not rejected", {"status": missing.status_code})
        require(
            any(event["error_code"] == "AUTH_TOKEN_MISSING" for event in missing_audit["auth_events"]),
            "missing bearer auth event not recorded",
        )
        results["missing_bearer"] = missing.json()

        malformed_trace = str(uuid4())
        malformed = await client.post(
            "/a2a/agents/enterprise_data_agent/tasks",
            json=agent_envelope(
                trace_id=malformed_trace,
                caller_agent_id="doc_agent",
                target_agent_id="enterprise_data_agent",
                task_type="read_enterprise_data",
                requested_capabilities=["feishu.contact:read"],
            ).model_dump(),
            headers={"Authorization": "Bearer forged-token"},
        )
        require(malformed.status_code == 401, "malformed bearer was not rejected", {"status": malformed.status_code})
        results["malformed_bearer"] = malformed.json()

        normal = await run_root_task(client)
        doc_credential = find_credential(normal["trace"], "doc_agent")
        external = issue_agent_token("external_search_agent", ["web.public:read"])
        mismatch = await client.post(
            "/a2a/agents/enterprise_data_agent/tasks",
            json=agent_envelope(
                trace_id=normal["trace_id"],
                caller_agent_id="doc_agent",
                target_agent_id="enterprise_data_agent",
                task_type="read_enterprise_data",
                requested_capabilities=["feishu.contact:read"],
                credential_id=doc_credential["credential_id"],
            ).model_dump(),
            headers={"Authorization": f"Bearer {external['access_token']}"},
        )
        require(mismatch.status_code == 403, "bearer/credential subject mismatch was not rejected")
        require(mismatch.json()["detail"]["error_code"] == "AUTH_CREDENTIAL_SUBJECT_MISMATCH", "unexpected mismatch code")
        results["subject_mismatch"] = mismatch.json()

        unknown = await client.post(
            "/a2a/agents/not_registered/tasks",
            json=agent_envelope(
                trace_id=str(uuid4()),
                caller_agent_id="doc_agent",
                target_agent_id="not_registered",
                task_type="read_enterprise_data",
                requested_capabilities=["feishu.contact:read"],
            ).model_dump(),
            headers={"Authorization": f"Bearer {issue_agent_token('doc_agent', ['feishu.contact:read'])['access_token']}"},
        )
        require(unknown.status_code == 404, "unknown target was not rejected")
        results["unknown_target"] = unknown.json()

    return CheckResult(name="verify_a2a_identity", passed=True, details=results)


if __name__ == "__main__":
    cli_main(
        check_name="verify_a2a_identity",
        description="验证 A2A Bearer、credential subject、actor type 和 target agent 身份校验。",
        check=run_check,
    )
