from __future__ import annotations

import asyncio

from app.protocol import AgentTaskResponse, AuthContext, DelegationEnvelope
from examples.agent import lark_cli_provider
from examples.agent.doc_agent import handle_task
from examples.agent.errors import ProviderError


ORIGINAL_ENTERPRISE_SNAPSHOT = lark_cli_provider.enterprise_snapshot
ORIGINAL_WRITE_DOCUMENT = lark_cli_provider.write_document


def test_lark_cli_enterprise_snapshot_is_normalized(monkeypatch) -> None:
    monkeypatch.setattr(lark_cli_provider, "enterprise_snapshot", ORIGINAL_ENTERPRISE_SNAPSHOT)

    async def fake_contacts() -> object:
        return {"data": {"items": [{"name": "Alice Chen", "department_name": "Product", "job_title": "PM"}]}}

    async def fake_calendar() -> object:
        return {"data": {"items": [{"summary": "Q2 Planning", "start_time": "2026-04-27 10:00", "owner": "Alice"}]}}

    async def fake_wiki() -> object:
        return {"data": {"items": [{"title": "Agent Delegation Policy", "updated_by": "Security Team"}]}}

    async def fake_bitable() -> object:
        return {
            "data": {
                "items": [
                    {
                        "record_id": "rec_001",
                        "fields": {"metric": "delegation_success_rate", "value": "98%"},
                    }
                ]
            }
        }

    monkeypatch.setattr(lark_cli_provider, "_query_contacts", fake_contacts)
    monkeypatch.setattr(lark_cli_provider, "_query_calendar", fake_calendar)
    monkeypatch.setattr(lark_cli_provider, "_query_wiki", fake_wiki)
    monkeypatch.setattr(lark_cli_provider, "_query_bitable", fake_bitable)

    result = asyncio.run(
        lark_cli_provider.enterprise_snapshot(
            topic="Live Enterprise Report",
            user_task="Collect live enterprise data",
            trace_id="trace-live",
        )
    )

    assert result["source"] == "lark_cli_enterprise_provider"
    assert result["contacts"][0]["name"] == "Alice Chen"
    assert result["calendar_events"][0]["summary"] == "Q2 Planning"
    assert result["wiki_pages"][0]["title"] == "Agent Delegation Policy"
    assert result["bitable_records"][0]["metric"] == "delegation_success_rate"
    assert result["provider_metadata"]["mode"] == "lark_cli"


def test_lark_cli_enterprise_snapshot_keeps_partial_results(monkeypatch) -> None:
    monkeypatch.setattr(lark_cli_provider, "enterprise_snapshot", ORIGINAL_ENTERPRISE_SNAPSHOT)

    async def fake_contacts() -> object:
        raise ProviderError("LARK_CLI_FAILED", "contact denied")

    async def fake_calendar() -> object:
        return {"data": {"items": [{"summary": "Q2 Planning", "start_time": "2026-04-27 10:00"}]}}

    async def fake_wiki() -> object:
        return {"data": {"items": []}}

    async def fake_bitable() -> object:
        raise ProviderError("LARK_CLI_BITABLE_CONFIG_MISSING", "bitable config missing")

    monkeypatch.setattr(lark_cli_provider, "_query_contacts", fake_contacts)
    monkeypatch.setattr(lark_cli_provider, "_query_calendar", fake_calendar)
    monkeypatch.setattr(lark_cli_provider, "_query_wiki", fake_wiki)
    monkeypatch.setattr(lark_cli_provider, "_query_bitable", fake_bitable)

    result = asyncio.run(
        lark_cli_provider.enterprise_snapshot(
            topic="Partial Enterprise Report",
            user_task="Collect live enterprise data",
            trace_id="trace-partial",
        )
    )

    assert result["calendar_events"][0]["summary"] == "Q2 Planning"
    assert result["contacts"] == []
    assert result["bitable_records"] == []
    assert "contacts: contact denied" in result["provider_metadata"]["warnings"]
    assert "bitable: bitable config missing" in result["provider_metadata"]["warnings"]


def test_lark_cli_bitable_falls_back_to_base_record_list(monkeypatch) -> None:
    calls: list[list[str]] = []

    async def fake_run(arguments: list[str], *, data: dict | None = None) -> object:
        calls.append(arguments)
        if arguments[:3] == ["api", "GET", "/open-apis/bitable/v1/apps/app_123/tables/tbl_123/records"]:
            raise ProviderError("LARK_CLI_FAILED", "Permission denied")
        return {
            "data": {
                "data": [["Alice", ["25"], "Engineering"]],
                "fields": ["姓名", "年龄", "部门"],
                "record_id_list": ["rec_001"],
            }
        }

    monkeypatch.setenv("BUIAM_LARK_CLI_BITABLE_APP_TOKEN", "app_123")
    monkeypatch.setenv("BUIAM_LARK_CLI_BITABLE_TABLE_ID", "tbl_123")
    monkeypatch.setenv("BUIAM_LARK_CLI_BITABLE_VIEW_ID", "vew_123")
    monkeypatch.setattr(lark_cli_provider, "_run_cli_json", fake_run)

    payload = asyncio.run(lark_cli_provider._query_bitable())
    records = lark_cli_provider._normalize_bitable_records(payload)

    assert calls[1] == [
        "base",
        "+record-list",
        "--base-token",
        "app_123",
        "--table-id",
        "tbl_123",
        "--limit",
        "10",
        "--view-id",
        "vew_123",
    ]
    assert records == [
        {
            "record_id": "rec_001",
            "metric": "姓名, 年龄, 部门",
            "value": '{"姓名": "Alice", "年龄": "25", "部门": "Engineering"}',
        }
    ]


def test_lark_cli_enterprise_snapshot_fails_when_all_sources_fail(monkeypatch) -> None:
    monkeypatch.setattr(lark_cli_provider, "enterprise_snapshot", ORIGINAL_ENTERPRISE_SNAPSHOT)

    async def fake_failure() -> object:
        raise ProviderError("LARK_CLI_FAILED", "not authenticated")

    monkeypatch.setattr(lark_cli_provider, "_query_contacts", fake_failure)
    monkeypatch.setattr(lark_cli_provider, "_query_calendar", fake_failure)
    monkeypatch.setattr(lark_cli_provider, "_query_wiki", fake_failure)
    monkeypatch.setattr(lark_cli_provider, "_query_bitable", fake_failure)

    try:
        asyncio.run(
            lark_cli_provider.enterprise_snapshot(
                topic="Failed Enterprise Report",
                user_task="Collect live enterprise data",
                trace_id="trace-failed",
            )
        )
    except ProviderError as exc:
        assert exc.code == "LARK_CLI_ENTERPRISE_READ_FAILED"
        assert "not authenticated" in exc.message
    else:
        raise AssertionError("ProviderError was not raised when every source failed")


def test_lark_cli_write_document_uses_cli_response(monkeypatch) -> None:
    monkeypatch.setattr(lark_cli_provider, "write_document", ORIGINAL_WRITE_DOCUMENT)

    calls: list[list[str]] = []

    async def fake_run(arguments: list[str], *, data: dict | None = None) -> object:
        calls.append(arguments)
        assert data is None
        return {"data": {"document_id": "doc_real_123", "url": "https://feishu.cn/docx/doc_real_123"}}

    monkeypatch.setattr(lark_cli_provider, "_run_cli_json", fake_run)

    result = asyncio.run(
        lark_cli_provider.write_document(
            title="Real Report",
            content="# Report",
            trace_id="trace-doc",
        )
    )

    assert calls[0][0:2] == ["docs", "+create"]
    assert result["document_id"] == "doc_real_123"
    assert result["url"] == "https://feishu.cn/docx/doc_real_123"
    assert result["provider"] == "lark_cli_doc_provider"


def test_doc_agent_propagates_downstream_provider_error(monkeypatch) -> None:
    async def fake_call_agent(self, **kwargs) -> AgentTaskResponse:
        return AgentTaskResponse(
            agent_id="enterprise_data_agent",
            trace_id=kwargs["trace_id"],
            task_type=kwargs["task_type"],
            result={"error_code": "LARK_CLI_NOT_FOUND", "message": "lark-cli executable not found"},
        )

    monkeypatch.setattr("examples.agent.doc_agent.A2AClient.call_agent", fake_call_agent)
    envelope = DelegationEnvelope(
        trace_id="trace-doc-agent",
        request_id="request-doc-agent",
        caller_agent_id="user_123",
        target_agent_id="doc_agent",
        task_type="generate_report",
        auth_context=AuthContext(
            jti="jti",
            sub="user_123",
            exp=9999999999,
            agent_id="doc_agent",
            actor_type="agent",
            delegated_user="user_123",
            capabilities=["report:write", "feishu.doc:write"],
            user_capabilities=["report:write", "feishu.doc:write"],
        ),
        payload={"topic": "Error Propagation", "user_task": "Attempt report generation"},
    )

    result = asyncio.run(handle_task(envelope))

    assert result.result["error_code"] == "LARK_CLI_NOT_FOUND"
