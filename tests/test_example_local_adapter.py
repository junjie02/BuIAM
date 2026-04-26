from __future__ import annotations

import asyncio

from app.gateway.local_adapter import call_local_agent
from app.protocol import AuthContext, DelegationEnvelope, DelegationHop
from example.agent import doc_agent
from example.agent.feishu_openapi import FeishuOpenAPIClient, FeishuOpenAPISettings


class FakeLLMProvider:
    async def complete(self, system: str, user: str) -> str:
        return "# 本地链路验证\n总结：local adapter 已连接到真实 doc_agent。"


def make_doc_envelope() -> DelegationEnvelope:
    return DelegationEnvelope(
        trace_id="trace_local_adapter",
        request_id="req_local_adapter",
        caller_agent_id="doc_agent",
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
            jti="tok_local_adapter",
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
        payload={"topic": "本地注册表集成测试"},
    )


def test_local_adapter_dispatches_to_real_doc_agent(monkeypatch) -> None:
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
        return {"document_id": "doc_local_001", "title": title}

    async def fake_append_docx_plain_text(self, *, document_id: str, content: str, root_block_id=None, batch_size=20):
        return {"document_id": document_id, "appended_blocks": 2}

    monkeypatch.setattr(FeishuOpenAPIClient, "list_department_users", fake_list_department_users)
    monkeypatch.setattr(FeishuOpenAPIClient, "list_calendar_events", fake_list_calendar_events)
    monkeypatch.setattr(FeishuOpenAPIClient, "search_bitable_records", fake_search_bitable_records)
    monkeypatch.setattr(FeishuOpenAPIClient, "create_docx_document", fake_create_docx_document)
    monkeypatch.setattr(FeishuOpenAPIClient, "append_docx_plain_text", fake_append_docx_plain_text)

    response = asyncio.run(call_local_agent("local://doc_agent", make_doc_envelope()))

    assert response.agent_id == "doc_agent"
    assert response.result["document"]["document_id"] == "doc_local_001"
    assert response.result["enterprise_data"]["contacts"][0]["name"] == "Alice"
