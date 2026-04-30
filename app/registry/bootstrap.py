from __future__ import annotations

import os

from app.identity.keys import ensure_agent_keypair
from app.store.registry import upsert_agent


USER_ID = os.getenv("BUIAM_DEMO_USER_ID", "user_123")


DEMO_AGENTS = [
    {
        "agent_id": "doc_agent",
        "name": "Feishu Doc Agent",
        "agent_type": "doc_agent",
        "description": "Coordinates report generation and writes the final Feishu document.",
        "endpoint_env": "DOC_AGENT_ENDPOINT",
        "default_endpoint": "http://127.0.0.1:8011/a2a/tasks",
        "static_capabilities": [
            "report:write",
            "feishu.doc:write",
            "feishu.contact:read",
            "feishu.calendar:read",
            "feishu.wiki:read",
            "feishu.bitable:read",
            "web.public:read",
        ],
    },
    {
        "agent_id": "enterprise_data_agent",
        "name": "Enterprise Data Agent",
        "agent_type": "enterprise_data_agent",
        "description": "Provides mock enterprise data for the demo flow.",
        "endpoint_env": "ENTERPRISE_DATA_AGENT_ENDPOINT",
        "default_endpoint": "http://127.0.0.1:8012/a2a/tasks",
        "static_capabilities": [
            "feishu.contact:read",
            "feishu.calendar:read",
            "feishu.wiki:read",
            "feishu.bitable:read",
        ],
    },
    {
        "agent_id": "external_search_agent",
        "name": "External Search Agent",
        "agent_type": "external_search_agent",
        "description": "Provides mock public web results and demonstrates denied escalation.",
        "endpoint_env": "EXTERNAL_SEARCH_AGENT_ENDPOINT",
        "default_endpoint": "http://127.0.0.1:8013/a2a/tasks",
        "static_capabilities": ["web.public:read"],
    },
]


def register_demo_agents() -> None:
    ensure_agent_keypair(USER_ID)
    for agent in DEMO_AGENTS:
        agent_id = str(agent["agent_id"])
        ensure_agent_keypair(agent_id)
        upsert_agent(
            agent_id=agent_id,
            name=str(agent["name"]),
            agent_type=str(agent["agent_type"]),
            description=str(agent["description"]),
            owner_org="demo",
            allowed_resource_domains=["feishu", "public_web"],
            status="active",
            endpoint=os.getenv(str(agent["endpoint_env"]), str(agent["default_endpoint"])),
            static_capabilities=list(agent["static_capabilities"]),
        )
