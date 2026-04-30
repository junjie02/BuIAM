from __future__ import annotations

from common import (
    CheckResult,
    SecurityContext,
    credential_path,
    find_credential,
    require,
    run_root_task,
    summarize_credential_path,
    cli_main,
)


async def run_check(context: SecurityContext) -> CheckResult:
    async with context.client() as client:
        result = await run_root_task(client, trace_id=context.args.trace_id)
    trace = result["trace"]
    enterprise = find_credential(trace, "enterprise_data_agent")
    path = credential_path(enterprise["credential_id"])
    require([node.subject_id for node in path] == ["user_123", "doc_agent", "enterprise_data_agent"], "unexpected credential path")
    require(path[1].parent_credential_id == path[0].credential_id, "doc credential parent mismatch")
    require(path[2].parent_credential_id == path[1].credential_id, "enterprise credential parent mismatch")
    require(set(path[2].capabilities).issubset(set(path[1].capabilities)), "capability narrowing failed")
    return CheckResult(
        name="verify_delegation_chain",
        passed=True,
        details={"trace_id": trace["trace_id"], "credential_path": summarize_credential_path(path), "human_chain": trace["chain"]},
    )


if __name__ == "__main__":
    cli_main(
        check_name="verify_delegation_chain",
        description="验证 signed delegation credential 构造、哈希、签名、父子关系和溯源路径。",
        check=run_check,
    )
