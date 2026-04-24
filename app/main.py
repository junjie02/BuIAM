from __future__ import annotations

from uuid import uuid4

from fastapi import FastAPI, HTTPException


from app.agents import doc
from app.agents.registry import get_agent_handler
from app.delegation.service import delegation_service, raise_for_denied
from app.protocol import AgentTaskRequest, AgentTaskResponse, AuthContext, DelegationEnvelope, DelegationHop
from app.store.audit import init_db, list_logs


app = FastAPI(title="BuIAM Delegation Protocol MVP")


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/delegate/call")
async def delegate_call(envelope: DelegationEnvelope) -> AgentTaskResponse:
    decision = delegation_service.authorize_and_record(envelope)
    raise_for_denied(decision)

    authorized_envelope = delegation_service.append_hop(
        envelope,
        decision.effective_capabilities,
    )
    handler = get_agent_handler(authorized_envelope.target_agent_id)
    if handler is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "unknown_agent", "agent_id": authorized_envelope.target_agent_id},
        )
    return await handler(authorized_envelope)


@app.post("/agents/{agent_id}/tasks")
async def agent_task(agent_id: str, request: AgentTaskRequest) -> AgentTaskResponse:
    handler = get_agent_handler(agent_id)
    if handler is None:
        raise HTTPException(status_code=404, detail={"error": "unknown_agent", "agent_id": agent_id})

    root_auth_context = build_mock_root_auth_context(agent_id)
    root_envelope = DelegationEnvelope(
        trace_id=str(uuid4()),
        request_id=str(uuid4()),
        caller_agent_id="user",
        target_agent_id=agent_id,
        task_type=request.task_type,
        requested_capabilities=[],
        delegation_chain=[
            DelegationHop(
                from_actor="user",
                to_agent_id=agent_id,
                task_type=request.task_type,
                delegated_capabilities=root_auth_context.capabilities,
                missing_capabilities=[],
                decision="root",
            )
        ],
        auth_context=root_auth_context,
        payload=request.payload,
    )
    response = await handler(root_envelope)

    child_envelope_data = response.result.get("delegation_envelope")
    if not child_envelope_data:
        return response

    child_response = await delegate_call(DelegationEnvelope.model_validate(child_envelope_data))

    if agent_id == "doc_agent" and request.task_type == "generate_report":
        report = await doc.generate_report(
            str(request.payload.get("topic", "飞书 AI 协作报告")),
            child_response.result,
        )
        return AgentTaskResponse(
            agent_id=agent_id,
            trace_id=root_envelope.trace_id,
            task_type=request.task_type,
            result={
                "report": report,
                "enterprise_data": child_response.result,
                "delegation_trace": child_response.trace_id,
            },
        )

    return child_response


@app.get("/audit/logs")
def audit_logs():
    return list_logs()


@app.get("/audit/traces/{trace_id}")
def audit_trace(trace_id: str):
    return list_logs(trace_id=trace_id)


def build_mock_root_auth_context(agent_id: str) -> AuthContext:
    capabilities_by_agent = {
        "doc_agent": [
            "report:write",
            "feishu.contact:read",
            "feishu.wiki:read",
            "feishu.bitable:read",
            "web.public:read",
        ],
        "enterprise_data_agent": [
            "feishu.contact:read",
            "feishu.wiki:read",
            "feishu.bitable:read",
        ],
        "external_search_agent": ["web.public:read"],
    }
    return AuthContext(
        jti=f"tok_{uuid4()}",
        sub=agent_id,
        exp=9999999999,
        delegated_user="user_123",
        agent_id=agent_id,
        capabilities=capabilities_by_agent.get(agent_id, []),
    )
