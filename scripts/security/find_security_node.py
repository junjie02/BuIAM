from __future__ import annotations

import argparse

from common import (
    CheckResult,
    SecurityContext,
    credential_path,
    find_credential,
    find_intent,
    get_credential,
    get_intent_node,
    intent_path,
    require,
    run_root_task,
    summarize_credential_path,
    summarize_intent_path,
    cli_main,
)


def add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--credential-id", help="要定位的 delegation credential id")
    parser.add_argument("--intent-node-id", help="要定位的 intent node id")


async def run_check(context: SecurityContext) -> CheckResult:
    credential_id = context.args.credential_id
    intent_node_id = context.args.intent_node_id
    generated_trace_id = None
    if not credential_id and not intent_node_id:
        async with context.client() as client:
            result = await run_root_task(client, trace_id=context.args.trace_id)
        generated_trace_id = result["trace_id"]
        credential_id = find_credential(result["trace"], "enterprise_data_agent")["credential_id"]
        intent_node_id = find_intent(result["trace"], "doc_agent", "enterprise_data_agent")["node_id"]

    details = {"generated_trace_id": generated_trace_id}
    if credential_id:
        credential = get_credential(credential_id)
        require(credential is not None, "credential id not found", {"credential_id": credential_id})
        details["credential_trace_id"] = credential.trace_id
        details["credential_path"] = summarize_credential_path(credential_path(credential_id))
    if intent_node_id:
        row = get_intent_node(intent_node_id)
        require(row is not None, "intent node id not found", {"intent_node_id": intent_node_id})
        details["intent_trace_id"] = row["trace_id"]
        details["intent_path"] = summarize_intent_path(intent_path(intent_node_id))
    return CheckResult(name="find_security_node", passed=True, details=details)


if __name__ == "__main__":
    cli_main(
        check_name="find_security_node",
        description="定位指定 credential_id 或 intent_node_id 所在 trace，并打印链路路径与签名验证结果。",
        check=run_check,
        extra_args=add_args,
    )
