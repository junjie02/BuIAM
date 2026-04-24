from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


Capability = Literal[
    "report:write",
    "feishu.contact:read",
    "feishu.wiki:read",
    "feishu.bitable:read",
    "web.public:read",
]


class DelegationHop(BaseModel):
    from_actor: str
    to_agent_id: str
    task_type: str
    delegated_capabilities: list[str] = Field(default_factory=list)
    missing_capabilities: list[str] = Field(default_factory=list)
    decision: Literal["allow", "deny", "root"] = "allow"


class DecisionDetail(BaseModel):
    requested_capabilities: list[str] = Field(default_factory=list)
    caller_token_capabilities: list[str] = Field(default_factory=list)
    target_agent_capabilities: list[str] = Field(default_factory=list)
    user_capabilities: list[str] = Field(default_factory=list)
    effective_capabilities: list[str] = Field(default_factory=list)
    missing_capabilities: list[str] = Field(default_factory=list)
    missing_by: dict[str, list[str]] = Field(default_factory=dict)
    decision: Literal["allow", "deny"]
    reason: str


class AuthContext(BaseModel):
    jti: str
    sub: str
    exp: int
    delegated_user: str
    agent_id: str
    capabilities: list[str] = Field(default_factory=list)
    sig: str | None = None


class DelegationEnvelope(BaseModel):
    protocol_version: str = "buiam.delegation.v1"
    trace_id: str
    request_id: str
    caller_agent_id: str
    target_agent_id: str
    task_type: str
    requested_capabilities: list[str] = Field(default_factory=list)
    delegation_chain: list[DelegationHop] = Field(default_factory=list)
    auth_context: AuthContext | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class AgentTaskRequest(BaseModel):
    task_type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class AgentTaskResponse(BaseModel):
    agent_id: str
    trace_id: str
    task_type: str
    result: dict[str, Any]


class DelegationDecision(BaseModel):
    decision: Literal["allow", "deny"]
    reason: str
    effective_capabilities: list[str]
    missing_capabilities: list[str] = Field(default_factory=list)
    requested_capabilities: list[str] = Field(default_factory=list)
    caller_token_capabilities: list[str] = Field(default_factory=list)
    target_agent_capabilities: list[str] = Field(default_factory=list)
    user_capabilities: list[str] = Field(default_factory=list)
    missing_by: dict[str, list[str]] = Field(default_factory=dict)

    def to_detail(self) -> DecisionDetail:
        return DecisionDetail(
            requested_capabilities=self.requested_capabilities,
            caller_token_capabilities=self.caller_token_capabilities,
            target_agent_capabilities=self.target_agent_capabilities,
            user_capabilities=self.user_capabilities,
            effective_capabilities=self.effective_capabilities,
            missing_capabilities=self.missing_capabilities,
            missing_by=self.missing_by,
            decision=self.decision,
            reason=self.reason,
        )


class AuditLog(BaseModel):
    id: int
    trace_id: str
    request_id: str
    caller_agent_id: str
    target_agent_id: str
    requested_capabilities: list[str]
    effective_capabilities: list[str]
    decision: str
    reason: str
    decision_detail: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class AgentRegistrationRequest(BaseModel):
    agent_id: str
    name: str
    endpoint: str
    static_capabilities: list[str] = Field(default_factory=list)


class TokenIssueRequest(BaseModel):
    agent_id: str
    delegated_user: str = "user_123"
    capabilities: list[str] = Field(default_factory=list)
    ttl_seconds: int = 3600
