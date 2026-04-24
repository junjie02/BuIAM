from __future__ import annotations

from app.config.agents import AGENTS


agent_registry: dict[str, frozenset[str]] = {
    agent_id: config.static_capabilities for agent_id, config in AGENTS.items()
}

blacklist: set[str] = set()
jti_seen: set[str] = set()

user_caps: dict[str, frozenset[str]] = {
    "user_123": frozenset(
        {
            "report:write",
            "feishu.contact:read",
            "feishu.wiki:read",
            "feishu.bitable:read",
            "web.public:read",
        }
    )
}


def get_target_agent_caps(agent_id: str) -> frozenset[str] | None:
    return agent_registry.get(agent_id)


def get_user_caps(user_id: str) -> frozenset[str] | None:
    return user_caps.get(user_id)


def revoke_jti(jti: str) -> None:
    blacklist.add(jti)


def clear_mock_state() -> None:
    blacklist.clear()
    jti_seen.clear()
