from __future__ import annotations

import asyncio

from app.protocol import AuthContext, DelegationEnvelope, DelegationHop
from example.agent import doc_agent
from example.agent.feishu_openapi import FeishuOpenAPIClient, FeishuOpenAPISettings


class FakeLLMProvider:
    async def complete(self, system: str, user: str) -> str:
        return (
            "# 飞书协作周报\n"
            "总结：企业协作数据已汇总。\n"
            "发现：通讯录、日历、多维表格均返回有效数据。\n"
            "建议：继续推进自动化报表。"
        )


def make_envelope() -> DelegationEnvelope:
    return DelegationEnvelope(
        trace_id="trace_example_doc",
        request_id="req_example_doc",
        caller_agent_id="user_123",
        target_agent_id="doc_agent",
        task_type="generate_report",
        requested_capabilities=[
            "feishu.doc:write",
            "feishu.contact:read",
            "feishu.calendar:read",
            "feishu.bitable:read",
        ],
        delegation_chain=[
            DelegationHop(
                from_actor="user_123",
                to_agent_id="doc_agent",
                task_type="generate_report",
                delegated_capabilities=[
                    "feishu.doc:write",
                    "feishu.contact:read",
                    "feishu.calendar:read",
                    "feishu.bitable:read",
                ],
                decision="root",
            )
        ],
        auth_context=AuthContext(
            jti="tok_doc_agent",
            sub="doc_agent",
            exp=9999999999,
            agent_id="doc_agent",
            delegated_user="user_123",
            capabilities=[
                "feishu.doc:write",
                "feishu.contact:read",
                "feishu.calendar:read",
                "feishu.bitable:read",
            ],
        ),
        payload={"topic": "飞书协作周报"},
    )


def test_doc_agent_generates_report_and_writes_doc(monkeypatch) -> None:
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
                doc_folder_token="fld_001",
            )
        ),
    )
    monkeypatch.setattr(doc_agent, "get_llm_provider", lambda: FakeLLMProvider())

    async def fake_list_department_users(self, *, department_id=None, page_size=50):
        return [{"name": "Alice"}]

    async def fake_list_calendar_events(self, *, calendar_id=None, start_time=None, end_time=None, page_size=50):
        return [{"summary": "Weekly sync"}]

    async def fake_search_bitable_records(self, *, app_token=None, table_id=None, view_id=None, page_size=50):
        return [{"record_id": "rec_001"}]

    async def fake_create_docx_document(self, *, title: str, folder_token=None):
        return {"document_id": "doc_001", "title": title, "revision_id": 7}

    async def fake_append_docx_plain_text(self, *, document_id: str, content: str, root_block_id=None, batch_size=20):
        assert "飞书协作周报" in content
        return {"document_id": document_id, "appended_blocks": 3}

    monkeypatch.setattr(FeishuOpenAPIClient, "list_department_users", fake_list_department_users)
    monkeypatch.setattr(FeishuOpenAPIClient, "list_calendar_events", fake_list_calendar_events)
    monkeypatch.setattr(FeishuOpenAPIClient, "search_bitable_records", fake_search_bitable_records)
    monkeypatch.setattr(FeishuOpenAPIClient, "create_docx_document", fake_create_docx_document)
    monkeypatch.setattr(FeishuOpenAPIClient, "append_docx_plain_text", fake_append_docx_plain_text)

    response = asyncio.run(doc_agent.handle_task(make_envelope()))

    assert response.agent_id == "doc_agent"
    assert response.result["document"]["document_id"] == "doc_001"
    assert response.result["document"]["write_result"]["appended_blocks"] == 3
    assert response.result["enterprise_data"]["contacts"][0]["name"] == "Alice"
    assert "report generated and written to Feishu doc" == response.result["message"]
