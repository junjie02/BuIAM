from __future__ import annotations

from uuid import uuid5, NAMESPACE_URL


def enterprise_snapshot(topic: str) -> dict:
    return {
        "source": "mock_enterprise_provider",
        "topic": topic,
        "contacts": [
            {"name": "Alice Chen", "department": "Product", "role": "PM"},
            {"name": "Bo Zhang", "department": "Engineering", "role": "Tech Lead"},
        ],
        "calendar_events": [
            {"summary": "Q2 Planning", "start": "2026-04-27 10:00", "owner": "Alice Chen"},
            {"summary": "Agent Security Review", "start": "2026-04-28 15:00", "owner": "Bo Zhang"},
        ],
        "wiki_pages": [
            {"title": "Agent Delegation Policy", "updated_by": "Security Team"},
            {"title": "Feishu Report Workflow", "updated_by": "Operations Team"},
        ],
        "bitable_records": [
            {"record_id": "rec_mock_001", "metric": "delegation_success_rate", "value": "98%"},
            {"record_id": "rec_mock_002", "metric": "blocked_escalations", "value": "3"},
        ],
    }


def public_search_results(query: str) -> list[dict[str, str]]:
    return [
        {
            "title": f"Public result for {query}",
            "url": "https://example.com/public-agent-report",
            "summary": "Mock public web result used by the runnable demo.",
        },
        {
            "title": "A2A Security Pattern",
            "url": "https://example.com/a2a-security",
            "summary": "Public guidance about delegating capabilities across agent boundaries.",
        },
    ]


def render_report(*, topic: str, enterprise_data: dict) -> str:
    contacts = ", ".join(item["name"] for item in enterprise_data.get("contacts", []))
    events = ", ".join(item["summary"] for item in enterprise_data.get("calendar_events", []))
    blocked = next(
        (item["value"] for item in enterprise_data.get("bitable_records", []) if item.get("metric") == "blocked_escalations"),
        "0",
    )
    return "\n".join(
        [
            f"# {topic}",
            "",
            "## Summary",
            "The mock enterprise snapshot was collected through the authorized A2A chain.",
            "",
            "## Signals",
            f"- Key collaborators: {contacts or 'none'}",
            f"- Calendar signals: {events or 'none'}",
            f"- Blocked escalation count: {blocked}",
            "",
            "## Suggested Actions",
            "- Keep delegation capabilities narrow.",
            "- Review audit traces after sensitive cross-agent calls.",
        ]
    )


def write_mock_document(*, title: str, content: str, trace_id: str) -> dict:
    document_id = f"doc_mock_{uuid5(NAMESPACE_URL, trace_id).hex[:12]}"
    return {
        "document_id": document_id,
        "title": title,
        "url": f"https://feishu.example.com/docx/{document_id}",
        "content_length": len(content),
        "provider": "mock_doc_provider",
    }
