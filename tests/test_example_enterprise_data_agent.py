from __future__ import annotations

import asyncio

from app.protocol import DelegationEnvelope
from example.agent.enterprise_data_agent import handle_task
from example.agent.feishu_openapi import FeishuConfigError, FeishuOpenAPIClient, FeishuOpenAPISettings


def make_envelope(payload: dict | None = None) -> DelegationEnvelope:
    return DelegationEnvelope(
        trace_id="trace_example_enterprise",
        request_id="req_example_enterprise",
        caller_agent_id="doc_agent",
        target_agent_id="enterprise_data_agent",
        task_type="read_enterprise_data",
        requested_capabilities=[
            "feishu.contact:read",
            "feishu.calendar:read",
            "feishu.bitable:read",
        ],
        payload=payload or {},
    )


def test_enterprise_data_agent_reads_three_feishu_sources(monkeypatch) -> None:
    monkeypatch.setattr(
        FeishuOpenAPISettings,
        "from_env",
        classmethod(
            lambda cls: FeishuOpenAPISettings(
                app_id="app_id",
                app_secret="app_secret",
                contact_department_id="dep_001",
                calendar_id="cal_001",
                bitable_app_token="bitable_app",
                bitable_table_id="tbl_001",
            )
        ),
    )

    async def fake_list_department_users(self, *, department_id=None, page_size=50):
        return [{"name": "Alice", "department_id": department_id or "dep_001"}]

    async def fake_list_calendar_events(self, *, calendar_id=None, start_time=None, end_time=None, page_size=50):
        return [{"summary": "Weekly sync", "calendar_id": calendar_id or "cal_001"}]

    async def fake_search_bitable_records(self, *, app_token=None, table_id=None, view_id=None, page_size=50):
        return [{"record_id": "rec_001", "table_id": table_id or "tbl_001"}]

    monkeypatch.setattr(FeishuOpenAPIClient, "list_department_users", fake_list_department_users)
    monkeypatch.setattr(FeishuOpenAPIClient, "list_calendar_events", fake_list_calendar_events)
    monkeypatch.setattr(FeishuOpenAPIClient, "search_bitable_records", fake_search_bitable_records)

    response = asyncio.run(handle_task(make_envelope()))

    assert response.agent_id == "enterprise_data_agent"
    assert response.result["source"] == "feishu_openapi"
    assert response.result["contacts"][0]["name"] == "Alice"
    assert response.result["calendar_events"][0]["summary"] == "Weekly sync"
    assert response.result["bitable_records"][0]["record_id"] == "rec_001"


def test_enterprise_data_agent_returns_config_error_when_env_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        FeishuOpenAPISettings,
        "from_env",
        classmethod(lambda cls: (_ for _ in ()).throw(FeishuConfigError("missing settings"))),
    )

    response = asyncio.run(handle_task(make_envelope()))

    assert response.agent_id == "enterprise_data_agent"
    assert response.result["error_code"] == "FeishuConfigError"
    assert "missing settings" in response.result["message"]
