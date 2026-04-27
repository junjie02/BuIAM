from __future__ import annotations

from common import (
    CheckResult,
    SecurityContext,
    find_credential,
    find_intent,
    require,
    run_root_task,
    cli_main,
)


async def run_check(context: SecurityContext) -> CheckResult:
    async with context.client() as client:
        result = await run_root_task(client, trace_id=context.args.trace_id)
    trace = result["trace"]
    doc_credential = find_credential(trace, "doc_agent")
    enterprise_credential = find_credential(trace, "enterprise_data_agent")
    root_intent = find_intent(trace, "user_123", "doc_agent")
    child_intent = find_intent(trace, "doc_agent", "enterprise_data_agent")

    require(enterprise_credential["trace_id"] == child_intent["trace_id"], "credential and intent trace mismatch")
    require(enterprise_credential["request_id"] == child_intent["request_id"], "credential and intent request mismatch")
    require(child_intent["parent_node_id"] == root_intent["node_id"], "child intent parent mismatch")
    require(
        any(
            log["request_id"] == child_intent["request_id"]
            and log["decision_detail"]["credential_id"] == doc_credential["credential_id"]
            and log["decision_detail"]["intent_node_id"] == child_intent["node_id"]
            for log in trace["logs"]
        ),
        "audit log does not bind caller credential and child intent",
    )
    return CheckResult(
        name="verify_chain_binding",
        passed=True,
        details={
            "trace_id": trace["trace_id"],
            "caller_credential_id": doc_credential["credential_id"],
            "child_credential_id": enterprise_credential["credential_id"],
            "root_intent_node_id": root_intent["node_id"],
            "child_intent_node_id": child_intent["node_id"],
            "shared_request_id": child_intent["request_id"],
        },
    )


if __name__ == "__main__":
    cli_main(
        check_name="verify_chain_binding",
        description="验证同一 A2A hop 的 credential、intent 和 audit decision 是否共享 trace/request 绑定。",
        check=run_check,
    )
