from __future__ import annotations

import asyncio

from app.protocol import AgentTaskResponse, AuthContext, DelegationEnvelope
from examples.agent import lark_cli_provider, provider
from examples.agent.doc_agent import handle_task


def test_default_provider_mode_uses_mock_snapshot(monkeypatch) -> None:
    monkeypatch.delenv("BUIAM_AGENT_PROVIDER_MODE", raising=False)

    result = asyncio.run(
        provider.enterprise_snapshot(
            topic="Provider Test",
            user_task="Read enterprise data",
            trace_id="trace-mock",
        )
    )

    assert result["source"] == "mock_enterprise_provider"
    assert result["contacts"]


def test_invalid_provider_mode_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("BUIAM_AGENT_PROVIDER_MODE", "bad-mode")

    try:
        asyncio.run(
            provider.write_document(
                title="Invalid Mode",
                content="content",
                trace_id="trace-invalid",
            )
        )
    except provider.ProviderError as exc:
        assert exc.code == "PROVIDER_MODE_INVALID"
    else:
        raise AssertionError("ProviderError was not raised for invalid mode")


def test_lark_cli_enterprise_snapshot_is_normalized(monkeypatch) -> None:
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


def test_lark_cli_write_document_uses_cli_response(monkeypatch) -> None:
    async def fake_run(arguments: list[str], *, data: dict | None = None) -> object:
        assert "docs" in arguments
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
    monkeypatch.delenv("BUIAM_AGENT_PROVIDER_MODE", raising=False)

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
