from __future__ import annotations

import asyncio
import os
import time
from uuid import uuid4

import httpx
from fastapi import APIRouter, Header, HTTPException

from app.delegation.service import CredentialValidationError, delegation_service, raise_for_denied
from app.identity.jwt_service import TokenVerificationResult, inspect_token, token_fingerprint
from app.intent.crypto import build_signed_intent_node
from app.intent.generator import IntentGenerationError, generate_intent_commitment
from app.intent.service import IntentValidationError, validate_and_record_intent_node
from app.protocol import AgentTaskResponse, DelegationDecision, DelegationEnvelope, DelegationHop, RootTaskRequest
from app.runtime.tasks import register_task, unregister_task
from app.store.audit import record_decision
from app.store.auth_events import record_auth_event
from app.store.registry import get_agent


router = APIRouter(prefix="/a2a", tags=["a2a"])


@router.post("/root-tasks")
async def root_task(
    request: RootTaskRequest,
    authorization: str | None = Header(default=None),
) -> AgentTaskResponse:
    trace_id = request.trace_id or str(uuid4())
    request_id = request.request_id or str(uuid4())
    provisional = DelegationEnvelope(
        trace_id=trace_id,
        request_id=request_id,
        caller_agent_id="user",
        target_agent_id=request.target_agent_id,
        task_type=request.task_type,
        requested_capabilities=request.requested_capabilities,
        payload={**request.payload, "user_task": request.user_task},
    )
    token_result = verify_bearer_for_envelope(envelope=provisional, authorization=authorization)
    auth_context = token_result.auth_context
    if auth_context.actor_type != "user":
        raise HTTPException(
            status_code=403,
            detail={"error_code": "AUTH_ACTOR_TYPE_INVALID", "message": "root task requires a user token"},
        )

    target = get_active_agent(request.target_agent_id)
    generated = await generate_root_intent(request=request, auth_context=auth_context)
    root_node = build_signed_intent_node(
        parent_node_id=None,
        actor_id=auth_context.delegated_user or auth_context.agent_id,
        actor_type="user",
        target_agent_id=request.target_agent_id,
        task_type=request.task_type,
        intent_commitment=generated.commitment,
    )
    root_hop = DelegationHop(
        from_actor=auth_context.delegated_user or auth_context.agent_id,
        to_agent_id=request.target_agent_id,
        task_type=request.task_type,
        delegated_capabilities=request.requested_capabilities,
        decision="root",
    )
    trusted = provisional.model_copy(
        update={
            "caller_agent_id": auth_context.agent_id,
            "auth_context": auth_context,
            "delegation_chain": [root_hop],
            "intent_node": root_node,
        }
    )
    try:
        intent_result = await validate_and_record_intent_node(
            node=root_node,
            trace_id=trace_id,
            request_id=request_id,
            auth_context=auth_context,
        )
    except IntentValidationError as error:
        record_decision(trusted, intent_error_decision(error, trusted, generated.model))
        raise http_error(error.error_code, error.message) from error

    try:
        target_auth_context = delegation_service.build_child_auth_context(
            parent_auth_context=auth_context,
            issuer_id=auth_context.agent_id,
            subject_id=request.target_agent_id,
            capabilities=request.requested_capabilities,
            trace_id=trace_id,
            request_id=request_id,
        )
    except CredentialValidationError as error:
        raise http_error(error.error_code, error.message) from error

    decision = DelegationDecision(
        decision="allow",
        reason="root user task accepted",
        effective_capabilities=request.requested_capabilities,
        requested_capabilities=request.requested_capabilities,
        caller_token_capabilities=auth_context.capabilities,
        user_capabilities=auth_context.user_capabilities or auth_context.capabilities,
        intent_node_id=root_node.node_id,
        parent_intent_node_id=None,
        root_intent=intent_result.root_intent,
        parent_intent=intent_result.parent_intent,
        child_intent=intent_result.child_intent,
        intent_generation_model=generated.model,
        intent_judge_decision=intent_result.judge_decision,
        intent_judge_reason=intent_result.judge_reason,
    )
    record_decision(trusted, decision)
    forward_envelope = trusted.model_copy(
        update={
            "caller_agent_id": request.target_agent_id,
            "auth_context": target_auth_context,
        }
    )
    return await forward_to_agent(target.endpoint, forward_envelope)


@router.post("/agents/{target_agent_id}/tasks")
async def agent_task(
    target_agent_id: str,
    envelope: DelegationEnvelope,
    authorization: str | None = Header(default=None),
) -> AgentTaskResponse:
    target = get_active_agent(target_agent_id)
    token_result = verify_bearer_for_envelope(envelope=envelope, authorization=authorization)
    try:
        auth_context = trusted_auth_context_for_envelope(envelope, token_result.auth_context)
    except HTTPException as error:
        detail = error.detail if isinstance(error.detail, dict) else {}
        record_decision(
            envelope,
            auth_failure_decision(
                str(detail.get("error_code", "AUTH_CREDENTIAL_INVALID")),
                str(detail.get("message", "credential identity validation failed")),
                envelope,
            ),
        )
        raise
    trusted = envelope.model_copy(
        update={
            "caller_agent_id": auth_context.agent_id,
            "target_agent_id": target_agent_id,
            "auth_context": auth_context,
        }
    )
    if auth_context.actor_type != "agent":
        record_decision(
            trusted,
            auth_failure_decision(
                "AUTH_ACTOR_TYPE_INVALID",
                "agent-to-agent task requires an agent bearer token",
                trusted,
            ),
        )
        raise HTTPException(
            status_code=403,
            detail={
                "error_code": "AUTH_ACTOR_TYPE_INVALID",
                "message": "agent-to-agent task requires an agent bearer token",
            },
        )
    generated_model = None
    if trusted.intent_node is None:
        trusted, generated_model = await attach_generated_intent_node(
            envelope=trusted,
            auth_context=auth_context,
            user_task=str(trusted.payload.get("user_task", trusted.task_type)),
        )

    intent_result = None
    if trusted.intent_node is not None:
        try:
            intent_result = await validate_and_record_intent_node(
                node=trusted.intent_node,
                trace_id=trusted.trace_id,
                request_id=trusted.request_id,
                auth_context=auth_context,
            )
        except IntentValidationError as error:
            record_decision(trusted, intent_error_decision(error, trusted, generated_model))
            raise http_error(error.error_code, error.message) from error

    decision = delegation_service.authorize(trusted)
    if intent_result is not None:
        decision.intent_node_id = intent_result.node.node_id
        decision.parent_intent_node_id = intent_result.node.parent_node_id
        decision.root_intent = intent_result.root_intent
        decision.parent_intent = intent_result.parent_intent
        decision.child_intent = intent_result.child_intent
        decision.intent_generation_model = generated_model
        decision.intent_judge_decision = intent_result.judge_decision
        decision.intent_judge_reason = intent_result.judge_reason
    record_decision(trusted, decision)
    raise_for_denied(decision)

    try:
        authorized = delegation_service.append_hop(trusted, decision.effective_capabilities)
    except CredentialValidationError as error:
        raise http_error(error.error_code, error.message) from error

    try:
        return await forward_to_agent(target.endpoint, authorized)
    except HTTPException as error:
        record_decision(
            trusted,
            DelegationDecision(
                decision="deny",
                reason=f"target agent unreachable or returned error: {error.detail}",
                effective_capabilities=decision.effective_capabilities,
                requested_capabilities=decision.requested_capabilities,
                caller_token_capabilities=decision.caller_token_capabilities,
                target_agent_capabilities=decision.target_agent_capabilities,
                user_capabilities=decision.user_capabilities,
            ),
        )
        raise


def get_active_agent(agent_id: str):
    agent = get_agent(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail={"error_code": "AGENT_NOT_REGISTERED", "agent_id": agent_id})
    if agent.status != "active":
        raise HTTPException(status_code=403, detail={"error_code": "AGENT_INACTIVE", "agent_id": agent_id})
    return agent


def bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail={"error_code": "AUTH_TOKEN_MISSING"})
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail={"error_code": "AUTH_TOKEN_INVALID"})
    return token


def verify_bearer_for_envelope(
    *,
    envelope: DelegationEnvelope,
    authorization: str | None,
) -> TokenVerificationResult:
    try:
        token = bearer_token(authorization)
    except HTTPException as error:
        detail = error.detail if isinstance(error.detail, dict) else {}
        record_auth_event(
            trace_id=envelope.trace_id,
            request_id=envelope.request_id,
            caller_agent_id=envelope.caller_agent_id,
            claimed_agent_id=envelope.caller_agent_id,
            token_fingerprint=token_fingerprint(authorization.partition(" ")[2] if authorization else None),
            verified_at=int(time.time()),
            identity_decision="deny",
            error_code=str(detail.get("error_code", "AUTH_TOKEN_INVALID")),
            reason="missing or invalid Authorization header",
        )
        record_decision(
            envelope,
            auth_failure_decision(
                str(detail.get("error_code", "AUTH_TOKEN_INVALID")),
                "missing or invalid Authorization header",
                envelope,
            ),
        )
        raise

    result = inspect_token(token)
    record_token_result(envelope, result)
    if result.auth_context is None:
        record_decision(
            envelope,
            auth_failure_decision(
                str(result.error_code or "AUTH_TOKEN_INVALID"),
                result.message,
                envelope,
            ),
        )
        raise HTTPException(status_code=401, detail={"error_code": result.error_code, "message": result.message})
    return result


def record_token_result(envelope: DelegationEnvelope, result: TokenVerificationResult) -> None:
    auth_context = result.auth_context
    record_auth_event(
        trace_id=envelope.trace_id,
        request_id=envelope.request_id,
        caller_agent_id=auth_context.agent_id if auth_context is not None else result.token_agent_id,
        claimed_agent_id=envelope.caller_agent_id,
        token_jti=result.token_jti,
        token_sub=result.token_sub,
        token_agent_id=result.token_agent_id,
        delegated_user=result.delegated_user,
        token_fingerprint=result.token_fingerprint,
        token_issued_at=result.token_issued_at,
        token_expires_at=result.token_expires_at,
        verified_at=result.verified_at,
        is_expired=result.is_expired,
        is_revoked=result.is_revoked,
        is_jti_registered=result.is_jti_registered,
        signature_valid=result.signature_valid,
        issuer_valid=result.issuer_valid,
        audience_valid=result.audience_valid,
        identity_decision="allow" if result.allowed else "deny",
        error_code=result.error_code,
        reason=result.message,
    )


def trusted_auth_context_for_envelope(envelope: DelegationEnvelope, bearer_auth_context):
    current_auth_context = envelope.auth_context
    if current_auth_context is None or current_auth_context.credential_id is None:
        return bearer_auth_context
    try:
        credential = delegation_service.validate_auth_context_credential(current_auth_context)
    except CredentialValidationError as error:
        raise http_error(error.error_code, error.message) from error
    if credential is not None and credential.trace_id is not None and credential.trace_id != envelope.trace_id:
        raise HTTPException(
            status_code=403,
            detail={
                "error_code": "AUTH_CREDENTIAL_INVALID",
                "message": "delegation credential belongs to a different trace",
            },
        )
    if credential is None or credential.subject_id != bearer_auth_context.agent_id:
        raise HTTPException(
            status_code=403,
            detail={
                "error_code": "AUTH_CREDENTIAL_SUBJECT_MISMATCH",
                "message": "delegation credential subject does not match bearer token",
            },
        )
    return current_auth_context


async def generate_root_intent(*, request: RootTaskRequest, auth_context):
    try:
        return await generate_intent_commitment(
            user_task=request.user_task,
            actor_id=auth_context.delegated_user or auth_context.agent_id,
            actor_type="user",
            target_agent_id=request.target_agent_id,
            task_type=request.task_type,
            payload=request.payload,
        )
    except IntentGenerationError as error:
        provisional = DelegationEnvelope(
            trace_id=request.trace_id or str(uuid4()),
            request_id=request.request_id or str(uuid4()),
            caller_agent_id=auth_context.agent_id,
            target_agent_id=request.target_agent_id,
            task_type=request.task_type,
            requested_capabilities=request.requested_capabilities,
            auth_context=auth_context,
            payload={**request.payload, "user_task": request.user_task},
        )
        record_decision(provisional, intent_generation_error_decision(str(error), provisional))
        raise HTTPException(status_code=403, detail={"error_code": "INTENT_GENERATION_FAILED", "message": str(error)}) from error


async def attach_generated_intent_node(
    *,
    envelope: DelegationEnvelope,
    auth_context,
    user_task: str,
) -> tuple[DelegationEnvelope, str]:
    parent_node_id = envelope.payload.get("parent_intent_node_id")
    try:
        generated = await generate_intent_commitment(
            user_task=user_task,
            actor_id=auth_context.agent_id,
            actor_type="agent",
            target_agent_id=envelope.target_agent_id,
            task_type=envelope.task_type,
            payload=envelope.payload,
        )
    except IntentGenerationError as error:
        record_decision(envelope, intent_generation_error_decision(str(error), envelope))
        raise HTTPException(status_code=403, detail={"error_code": "INTENT_GENERATION_FAILED", "message": str(error)}) from error
    node = build_signed_intent_node(
        parent_node_id=str(parent_node_id) if parent_node_id else None,
        actor_id=auth_context.agent_id,
        actor_type="agent",
        target_agent_id=envelope.target_agent_id,
        task_type=envelope.task_type,
        intent_commitment=generated.commitment,
    )
    return envelope.model_copy(update={"intent_node": node}), generated.model


async def forward_to_agent(endpoint: str, envelope: DelegationEnvelope) -> AgentTaskResponse:
    timeout = float(os.getenv("A2A_FORWARD_TIMEOUT_SECONDS", "30"))

    async def post_envelope() -> AgentTaskResponse:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                endpoint,
                json=envelope.model_dump(),
                headers={"X-BuIAM-Gateway": "true"},
            )
            response.raise_for_status()
            return AgentTaskResponse.model_validate(response.json())

    task = asyncio.create_task(post_envelope())
    register_task(envelope.trace_id, task)
    try:
        return await task
    except asyncio.CancelledError as error:
        reason = str(error) or "token_revoked"
        record_decision(
            envelope,
            DelegationDecision(
                decision="deny",
                reason=f"TASK_CANCELLED/{reason}",
                effective_capabilities=[],
                missing_capabilities=sorted(envelope.requested_capabilities),
                requested_capabilities=sorted(envelope.requested_capabilities),
            ),
        )
        raise HTTPException(
            status_code=409,
            detail={"error_code": "TASK_CANCELLED", "reason": reason},
        ) from error
    except httpx.HTTPStatusError as error:
        raise HTTPException(
            status_code=502,
            detail={
                "error_code": "TARGET_AGENT_ERROR",
                "status_code": error.response.status_code,
                "message": error.response.text,
            },
        ) from error
    except httpx.HTTPError as error:
        raise HTTPException(
            status_code=502,
            detail={"error_code": "TARGET_AGENT_UNREACHABLE", "message": str(error)},
        ) from error
    finally:
        unregister_task(envelope.trace_id, task)


def intent_error_decision(
    error: IntentValidationError,
    envelope: DelegationEnvelope,
    generation_model: str | None = None,
) -> DelegationDecision:
    requested = sorted(envelope.requested_capabilities)
    return DelegationDecision(
        decision="deny",
        reason=f"{error.error_code}: {error.message}",
        effective_capabilities=[],
        missing_capabilities=requested,
        requested_capabilities=requested,
        intent_node_id=error.node.node_id if error.node is not None else None,
        parent_intent_node_id=error.node.parent_node_id if error.node is not None else None,
        root_intent=error.root_intent,
        parent_intent=error.parent_intent,
        child_intent=error.child_intent or (envelope.intent_node.intent_commitment.intent if envelope.intent_node else None),
        intent_generation_model=generation_model,
        intent_judge_decision=error.judge_decision,
        intent_judge_reason=error.judge_reason or error.message,
    )


def intent_generation_error_decision(message: str, envelope: DelegationEnvelope) -> DelegationDecision:
    requested = sorted(envelope.requested_capabilities)
    return DelegationDecision(
        decision="deny",
        reason=f"INTENT_GENERATION_FAILED: {message}",
        effective_capabilities=[],
        missing_capabilities=requested,
        requested_capabilities=requested,
        child_intent=None,
        intent_judge_decision="GenerationFailed",
        intent_judge_reason=message,
    )


def auth_failure_decision(error_code: str, message: str, envelope: DelegationEnvelope) -> DelegationDecision:
    requested = sorted(envelope.requested_capabilities)
    return DelegationDecision(
        decision="deny",
        reason=f"{error_code}: {message}",
        effective_capabilities=[],
        missing_capabilities=requested,
        requested_capabilities=requested,
    )


def http_error(error_code: str, message: str) -> HTTPException:
    return HTTPException(status_code=403, detail={"error_code": error_code, "message": message})
