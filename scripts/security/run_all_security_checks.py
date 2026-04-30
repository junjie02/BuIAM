from __future__ import annotations

import argparse
import asyncio
import json

from common import SecurityContext, add_common_args, build_servers, emit_result
from verify_a2a_identity import run_check as run_identity
from verify_chain_binding import run_check as run_binding
from verify_delegation_chain import run_check as run_delegation
from verify_intent_chain import run_check as run_intent
from verify_token_lifecycle import run_check as run_token_lifecycle


CHECKS = [
    run_delegation,
    run_intent,
    run_binding,
    run_identity,
    run_token_lifecycle,
]


async def main(args: argparse.Namespace) -> list:
    results = []
    async with SecurityContext(args=args, servers=build_servers()) as context:
        for check in CHECKS:
            try:
                results.append(await check(context))
            except Exception as error:
                results.append(
                    type("FailedResult", (), {"name": check.__module__, "passed": False, "details": {"reason": str(error)}})()
                )
                break
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="顺序运行全部安全验证脚本。")
    add_common_args(parser)
    args = parser.parse_args()
    results = asyncio.run(main(args))
    if args.json:
        print(
            json.dumps(
                [{"check": result.name, "passed": result.passed, **result.details} for result in results],
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        for result in results:
            emit_result(result, as_json=False)
        passed = sum(1 for result in results if result.passed)
        print(f"\nSummary: {passed}/{len(results)} checks passed")
    if not all(result.passed for result in results):
        raise SystemExit(1)
