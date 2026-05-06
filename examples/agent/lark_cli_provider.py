from __future__ import annotations

import asyncio
import json
import os
import shlex
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid5

from examples.agent.errors import ProviderError


def _cli_binary_and_extra_args() -> list[str]:
    command = os.getenv("BUIAM_LARK_CLI_BIN", "lark-cli").strip()
    if not command:
        raise ProviderError("LARK_CLI_BIN_MISSING", "BUIAM_LARK_CLI_BIN is empty")

    extra_args = os.getenv("BUIAM_LARK_CLI_EXTRA_ARGS", "").strip()
    parts = [command]
    if extra_args:
        parts.extend(shlex.split(extra_args, posix=False))
    return parts


def _identity_args() -> list[str]:
    identity = os.getenv("BUIAM_LARK_CLI_AS", "user").strip().lower()
    if identity not in {"user", "bot"}:
        raise ProviderError("LARK_CLI_AS_INVALID", f"unsupported lark-cli identity: {identity}")
    return ["--as", identity]


def _format_args(arguments: list[str]) -> list[str]:
    return ["--format", "json"] if _supports_format_flag(arguments) else []


def _cli_command(arguments: list[str]) -> list[str]:
    # lark-cli documents identity and output flags as command-level options.
    return [*_cli_binary_and_extra_args(), *arguments, *_identity_args(), *_format_args(arguments)]


def _timeout_seconds() -> float:
    raw = os.getenv("BUIAM_LARK_CLI_TIMEOUT_SECONDS", "30").strip()
    try:
        timeout = float(raw)
    except ValueError as exc:
        raise ProviderError("LARK_CLI_TIMEOUT_INVALID", f"invalid BUIAM_LARK_CLI_TIMEOUT_SECONDS: {raw}") from exc
    if timeout <= 0:
        raise ProviderError("LARK_CLI_TIMEOUT_INVALID", "BUIAM_LARK_CLI_TIMEOUT_SECONDS must be positive")
    return timeout


async def _run_cli_json(arguments: list[str], *, data: dict | None = None) -> object:
    command = _cli_command(arguments)
    stdin = None
    if data is not None:
        stdin = asyncio.subprocess.PIPE

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=stdin,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise ProviderError("LARK_CLI_NOT_FOUND", f"lark-cli executable not found: {command[0]}") from exc

    payload = json.dumps(data, ensure_ascii=False).encode("utf-8") if data is not None else None
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(payload), timeout=_timeout_seconds())
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise ProviderError("LARK_CLI_TIMEOUT", "lark-cli request timed out") from exc

    if process.returncode != 0:
        detail = (stderr or stdout).decode("utf-8", errors="replace").strip()
        raise ProviderError("LARK_CLI_FAILED", detail or "lark-cli exited with a non-zero status")

    text = stdout.decode("utf-8", errors="replace").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProviderError("LARK_CLI_OUTPUT_INVALID", f"failed to parse lark-cli JSON output: {text[:200]}") from exc


def _unwrap_data(payload: object) -> object:
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


def _iter_items(value: object) -> list[dict]:
    data = _unwrap_data(value)
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("items", "records", "users", "events", "nodes", "data", "list"):
            nested = data.get(key)
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]
        return [data]
    return []


def _supports_format_flag(_arguments: list[str]) -> bool:
    return not _arguments or _arguments[0] not in {"docs", "version"}


def _coerce_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _normalize_contacts(payload: object) -> list[dict[str, str]]:
    contacts = []
    for item in _iter_items(payload)[:10]:
        name = _coerce_text(item.get("name") or item.get("en_name") or item.get("display_name"))
        department = item.get("department_name") or item.get("department_names") or item.get("department")
        if isinstance(department, list):
            department_text = ", ".join(_coerce_text(part) for part in department if part)
        else:
            department_text = _coerce_text(department)
        role = _coerce_text(item.get("job_title") or item.get("title") or item.get("position"))
        email = _coerce_text(item.get("email"))
        if not any([name, department_text, role, email]):
            continue
        contact = {
            "name": name or email or "unknown",
            "department": department_text or "unknown",
            "role": role or "unknown",
        }
        if email:
            contact["email"] = email
        contacts.append(contact)
    return contacts


def _normalize_calendar_events(payload: object) -> list[dict[str, str]]:
    events = []
    for item in _iter_items(payload)[:10]:
        owner = item.get("organizer") or item.get("owner") or {}
        if isinstance(owner, dict):
            owner_name = _coerce_text(owner.get("display_name") or owner.get("name") or owner.get("id"))
        else:
            owner_name = _coerce_text(owner)
        start = item.get("start_time") or item.get("start") or item.get("display_time")
        if isinstance(start, dict):
            start_value = _coerce_text(start.get("date") or start.get("date_time") or start.get("timestamp"))
        else:
            start_value = _coerce_text(start)
        summary = _coerce_text(item.get("summary") or item.get("title") or item.get("name"))
        if not any([summary, start_value, owner_name]):
            continue
        events.append(
            {
                "summary": summary or "untitled event",
                "start": start_value or "unknown",
                "owner": owner_name or "unknown",
            }
        )
    return events


def _normalize_wiki_pages(payload: object) -> list[dict[str, str]]:
    pages = []
    for item in _iter_items(payload)[:10]:
        title = _coerce_text(item.get("title") or item.get("obj_token") or item.get("space_id"))
        updated_by = item.get("owner") or item.get("creator") or item.get("updated_by") or {}
        if isinstance(updated_by, dict):
            updated_by_text = _coerce_text(
                updated_by.get("display_name") or updated_by.get("name") or updated_by.get("user_id")
            )
        else:
            updated_by_text = _coerce_text(updated_by)
        if not title:
            continue
        pages.append({"title": title, "updated_by": updated_by_text or "unknown"})
    return pages


def _normalize_bitable_records(payload: object) -> list[dict[str, str]]:
    records = []
    for item in _iter_items(payload)[:10]:
        fields = item.get("fields", item)
        if not isinstance(fields, dict):
            fields = {"value": fields}
        record_id = _coerce_text(item.get("record_id") or item.get("id") or item.get("recordId"))
        metric = _coerce_text(fields.get("metric") or fields.get("name") or fields.get("title"))
        value = fields.get("value")
        if isinstance(value, list):
            value_text = ", ".join(_coerce_text(part) for part in value if part is not None)
        elif isinstance(value, dict):
            value_text = json.dumps(value, ensure_ascii=False)
        else:
            value_text = _coerce_text(value)
        if not any([record_id, metric, value_text]):
            continue
        records.append(
            {
                "record_id": record_id or f"record_{len(records) + 1}",
                "metric": metric or "unnamed_field",
                "value": value_text or "",
            }
        )
    return records


async def _query_contacts() -> object:
    query = os.getenv("BUIAM_LARK_CLI_CONTACT_QUERY", "").strip()
    path = "/open-apis/contact/v3/users"
    params: dict[str, object] = {"page_size": int(os.getenv("BUIAM_LARK_CLI_CONTACT_PAGE_SIZE", "10"))}
    if query:
        params["query"] = query
    return await _run_cli_json(["api", "GET", path, "--params", json.dumps(params, ensure_ascii=False)])


async def _query_calendar() -> object:
    return await _run_cli_json(["calendar", "+agenda"])


async def _query_wiki() -> object:
    path = "/open-apis/wiki/v2/spaces"
    params = {"page_size": int(os.getenv("BUIAM_LARK_CLI_WIKI_PAGE_SIZE", "10"))}
    return await _run_cli_json(["api", "GET", path, "--params", json.dumps(params, ensure_ascii=False)])


async def _query_bitable() -> object:
    app_token = os.getenv("BUIAM_LARK_CLI_BITABLE_APP_TOKEN", "").strip()
    table_id = os.getenv("BUIAM_LARK_CLI_BITABLE_TABLE_ID", "").strip()
    if not app_token or not table_id:
        raise ProviderError(
            "LARK_CLI_BITABLE_CONFIG_MISSING",
            "BUIAM_LARK_CLI_BITABLE_APP_TOKEN and BUIAM_LARK_CLI_BITABLE_TABLE_ID are required in lark_cli mode",
        )
    path = f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"
    params = {"page_size": int(os.getenv("BUIAM_LARK_CLI_BITABLE_PAGE_SIZE", "10"))}
    return await _run_cli_json(["api", "GET", path, "--params", json.dumps(params, ensure_ascii=False)])


async def _optional_query(
    label: str,
    query: Callable[[], Awaitable[object]],
    normalize: Callable[[object], list[dict[str, str]]],
    warnings: list[str],
) -> list[dict[str, str]]:
    try:
        payload = await query()
    except ProviderError as exc:
        warnings.append(f"{label}: {exc.message}")
        return []
    return normalize(payload)


async def enterprise_snapshot(*, topic: str, user_task: str, trace_id: str) -> dict:
    warnings: list[str] = []

    contacts, calendar_events, wiki_pages, bitable_records = await asyncio.gather(
        _optional_query("contacts", _query_contacts, _normalize_contacts, warnings),
        _optional_query("calendar", _query_calendar, _normalize_calendar_events, warnings),
        _optional_query("wiki", _query_wiki, _normalize_wiki_pages, warnings),
        _optional_query("bitable", _query_bitable, _normalize_bitable_records, warnings),
    )

    if warnings and not any([contacts, calendar_events, wiki_pages, bitable_records]):
        raise ProviderError("LARK_CLI_ENTERPRISE_READ_FAILED", "; ".join(warnings))

    return {
        "source": "lark_cli_enterprise_provider",
        "topic": topic,
        "contacts": contacts,
        "calendar_events": calendar_events,
        "wiki_pages": wiki_pages,
        "bitable_records": bitable_records,
        "provider_metadata": {
            "mode": "lark_cli",
            "trace_id": trace_id,
            "user_task": user_task,
            "warnings": warnings,
        },
    }


async def write_document(*, title: str, content: str, trace_id: str) -> dict:
    formatted_content = f"<title>{title}</title>\n{content}"
    folder_token = os.getenv("BUIAM_LARK_CLI_DOC_FOLDER_TOKEN", "").strip()
    create_variants = [
        [
            "docs",
            "+create",
            "--api-version",
            os.getenv("BUIAM_LARK_CLI_DOC_API_VERSION", "v2"),
            "--doc-format",
            os.getenv("BUIAM_LARK_CLI_DOC_FORMAT", "markdown"),
            "--content",
            formatted_content,
        ],
        [
            "docs",
            "+create",
            "--title",
            title,
            "--markdown",
            content,
        ],
    ]
    if folder_token:
        for arguments in create_variants:
            arguments.extend(["--folder-token", folder_token])

    response = None
    last_error: ProviderError | None = None
    for arguments in create_variants:
        try:
            response = await _run_cli_json(arguments)
        except ProviderError as exc:
            last_error = exc
            if exc.code not in {"LARK_CLI_FAILED", "LARK_CLI_OUTPUT_INVALID"}:
                raise
        else:
            break
    if response is None:
        assert last_error is not None
        raise last_error

    payload = _unwrap_data(response)
    document_id = f"doc_cli_{uuid5(NAMESPACE_URL, trace_id).hex[:12]}"

    if isinstance(payload, dict):
        document_id = _coerce_text(
            payload.get("document_id")
            or payload.get("doc_token")
            or payload.get("token")
            or payload.get("obj_token")
            or payload.get("id")
        ) or document_id
        url = _coerce_text(payload.get("url") or payload.get("doc_url") or payload.get("link"))
    else:
        url = ""

    return {
        "document_id": document_id,
        "title": title,
        "url": url or f"https://feishu.cn/docx/{document_id}",
        "content_length": len(content),
        "provider": "lark_cli_doc_provider",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
