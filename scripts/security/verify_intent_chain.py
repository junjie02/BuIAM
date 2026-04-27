from __future__ import annotations

from common import (
    CheckResult,
    SecurityContext,
    find_intent,
    intent_path,
    require,
    run_root_task,
    summarize_intent_path,
    cli_main,
)


async def run_check(context: SecurityContext) -> CheckResult:
    async with context.client() as client:
        result = await run_root_task(client, trace_id=context.args.trace_id)
    trace = result["trace"]
    child = find_intent(trace, "doc_agent", "enterprise_data_agent")
    root = find_intent(trace, "user_123", "doc_agent")
    path = intent_path(child["node_id"])
    require(child["parent_node_id"] == root["node_id"], "child intent does not point to root task intent")
    require([node.actor_id for node in path] == ["user_123", "doc_agent"], "unexpected intent path")
    return CheckResult(
        name="verify_intent_chain",
        passed=True,
        details={"trace_id": trace["trace_id"], "intent_path": summarize_intent_path(path), "intent_tree": trace["intent_tree"]},
    )


if __name__ == "__main__":
    cli_main(
        check_name="verify_intent_chain",
        description="验证 intent node 构造、签名、parent/root 关系和溯源路径。",
        check=run_check,
    )
