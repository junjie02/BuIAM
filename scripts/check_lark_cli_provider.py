from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
from typing import Any

from dotenv import load_dotenv

from examples.agent import lark_cli_provider
from examples.agent.errors import ProviderError


async def _check_cli_version() -> dict[str, Any]:
    command = lark_cli_provider._cli_binary_and_extra_args()
    if shutil.which(command[0]) is None:
        raise ProviderError("LARK_CLI_NOT_FOUND", f"lark-cli executable not found: {command[0]}")
    process = await asyncio.create_subprocess_exec(
        *command,
        "version",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    text = (stdout or stderr).decode("utf-8", errors="replace").strip()
    if process.returncode != 0:
        raise ProviderError("LARK_CLI_FAILED", text or "lark-cli version failed")
    return {"ok": True, "version": text}


async def _check_contacts() -> dict[str, Any]:
    payload = await lark_cli_provider._query_contacts()
    return {"ok": True, "count": len(lark_cli_provider._normalize_contacts(payload))}


async def _check_calendar() -> dict[str, Any]:
    payload = await lark_cli_provider._query_calendar()
    return {"ok": True, "count": len(lark_cli_provider._normalize_calendar_events(payload))}


async def _check_wiki() -> dict[str, Any]:
    payload = await lark_cli_provider._query_wiki()
    return {"ok": True, "count": len(lark_cli_provider._normalize_wiki_pages(payload))}


async def _check_bitable() -> dict[str, Any]:
    if not os.getenv("BUIAM_LARK_CLI_BITABLE_APP_TOKEN") or not os.getenv("BUIAM_LARK_CLI_BITABLE_TABLE_ID"):
        return {
            "ok": False,
            "skipped": True,
            "message": "set BUIAM_LARK_CLI_BITABLE_APP_TOKEN and BUIAM_LARK_CLI_BITABLE_TABLE_ID to check bitable reads",
        }
    payload = await lark_cli_provider._query_bitable()
    return {"ok": True, "count": len(lark_cli_provider._normalize_bitable_records(payload))}


async def _run_check(name: str, check) -> tuple[str, dict[str, Any]]:
    try:
        return name, await check()
    except ProviderError as exc:
        return name, {"ok": False, "error_code": exc.code, "message": exc.message}


async def run_checks() -> dict[str, Any]:
    checks = [
        ("cli_version", _check_cli_version),
        ("contacts", _check_contacts),
        ("calendar", _check_calendar),
        ("wiki", _check_wiki),
        ("bitable", _check_bitable),
    ]
    results = dict(await asyncio.gather(*[_run_check(name, check) for name, check in checks]))
    return {
        "lark_cli_bin": os.getenv("BUIAM_LARK_CLI_BIN", "lark-cli"),
        "lark_cli_as": os.getenv("BUIAM_LARK_CLI_AS", "user"),
        "checks": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Check local lark-cli access for BuIAM Feishu provider.")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    args = parser.parse_args()

    load_dotenv()
    report = asyncio.run(run_checks())
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    print(f"lark_cli_bin: {report['lark_cli_bin']}")
    print(f"lark_cli_as: {report['lark_cli_as']}")
    for name, result in report["checks"].items():
        status = "ok" if result.get("ok") else "skipped" if result.get("skipped") else "failed"
        detail = result.get("message") or result.get("error_code") or f"count={result.get('count', 'n/a')}"
        print(f"{name}: {status} ({detail})")


if __name__ == "__main__":
    main()
