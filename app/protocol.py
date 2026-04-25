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


class IntentCommitment(BaseModel):
    intent: str
    description: str = ""
    data_refs: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)


class IntentNode(BaseModel):
    node_id: str
    parent_node_id: str | None = None
    actor_id: str
    actor_type: Literal["user", "agent"]
    target_agent_id: str
    task_type: str
    intent_commitment: IntentCommitment
    signature: str
    signature_alg: str = "BUIAM-RS256"


class DecisionDetail(BaseModel):
    requested_capabilities: list[str] = Field(default_factory=list)
    caller_token_capabilities: list[str] = Field(default_factory=list)
    target_agent_capabilities: list[str] = Field(default_factory=list)
    user_capabilities: list[str] = Field(default_factory=list)
    effective_capabilities: list[str] = Field(default_factory=list)
    missing_capabilities: list[str] = Field(default_factory=list)
    missing_by: dict[str, list[str]] = Field(default_factory=dict)
    auth_event_recorded: bool = False
    token_jti: str | None = None
    token_agent_id: str | None = None
    intent_node_id: str | None = None
    parent_intent_node_id: str | None = None
    root_intent: str | None = None
    parent_intent: str | None = None
    child_intent: str | None = None
    intent_generation_model: str | None = None
    intent_judge_decision: str | None = None
    intent_judge_reason: str | None = None
    decision: Literal["allow", "deny"]
    reason: str


class AuthContext(BaseModel):
    jti: str
    sub: str
    exp: int
    delegated_user: str
    agent_id: str
    actor_type: Literal["user", "agent"] = "agent"
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
    intent_node: IntentNode | None = None
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
    intent_node_id: str | None = None
    parent_intent_node_id: str | None = None
    root_intent: str | None = None
    parent_intent: str | None = None
    child_intent: str | None = None
    intent_generation_model: str | None = None
    intent_judge_decision: str | None = None
    intent_judge_reason: str | None = None

    def to_detail(self) -> DecisionDetail:
        return DecisionDetail(
            requested_capabilities=self.requested_capabilities,
            caller_token_capabilities=self.caller_token_capabilities,
            target_agent_capabilities=self.target_agent_capabilities,
            user_capabilities=self.user_capabilities,
            effective_capabilities=self.effective_capabilities,
            missing_capabilities=self.missing_capabilities,
            missing_by=self.missing_by,
            auth_event_recorded=False,
            intent_node_id=self.intent_node_id,
            parent_intent_node_id=self.parent_intent_node_id,
            root_intent=self.root_intent,
            parent_intent=self.parent_intent,
            child_intent=self.child_intent,
            intent_generation_model=self.intent_generation_model,
            intent_judge_decision=self.intent_judge_decision,
            intent_judge_reason=self.intent_judge_reason,
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


class AuthEvent(BaseModel):
    id: int
    trace_id: str
    request_id: str
    caller_agent_id: str | None = None
    claimed_agent_id: str | None = None
    token_jti: str | None = None
    token_sub: str | None = None
    token_agent_id: str | None = None
    delegated_user: str | None = None
    token_fingerprint: str | None = None
    token_issued_at: int | None = None
    token_expires_at: int | None = None
    verified_at: int
    is_expired: bool | None = None
    is_revoked: bool | None = None
    is_jti_registered: bool | None = None
    signature_valid: bool | None = None
    issuer_valid: bool | None = None
    audience_valid: bool | None = None
    identity_decision: Literal["allow", "deny"]
    error_code: str | None = None
    reason: str
    created_at: str


class AgentRegistrationRequest(BaseModel):
    agent_id: str
    name: str
    endpoint: str
    static_capabilities: list[str] = Field(default_factory=list)


class TokenIssueRequest(BaseModel):
    agent_id: str
    delegated_user: str = "user_123"
    actor_type: Literal["user", "agent"] = "agent"
    capabilities: list[str] = Field(default_factory=list)
    ttl_seconds: int = 3600


class RootTaskRequest(BaseModel):
    trace_id: str | None = None
    request_id: str | None = None
    target_agent_id: str
    task_type: str
    user_task: str
    requested_capabilities: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
