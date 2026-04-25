from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
import httpx
import time
from uuid import uuid4

from app.delegation.service import delegation_service, raise_for_denied
  from app.gateway.local_adapter import call_local_agent
  from app.identity.jwt_service import (
      TokenError,
      TokenVerificationResult,
      inspect_token,
      token_fingerprint,
      verify_token,
  )
  from app.intent.crypto import build_signed_intent_node
  from app.intent.generator import IntentGenerationError, generate_intent_commitment
  from app.intent.service import IntentValidationError, validate_and_record_intent_node
  from app.protocol import (
      AgentTaskResponse,
      DelegationDecision,
      DelegationEnvelope,
      DelegationHop,
      RootTaskRequest,
  )
  from app.store.audit import record_decision
  from app.store.auth_events import record_auth_event
  from app.store.registry import get_agent


router = APIRouter()


def bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail={"error_code": "AUTH_TOKEN_MISSING", "message": "missing Authorization header"},
        )
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=401,
            detail={"error_code": "AUTH_TOKEN_INVALID", "message": "invalid Authorization header"},
        )
    return token


def safe_trace_id(envelope: DelegationEnvelope | None) -> str:
    return envelope.trace_id if envelope is not None else f"trace_auth_{uuid4()}"


def safe_request_id(envelope: DelegationEnvelope | None) -> str:
    return envelope.request_id if envelope is not None else f"req_auth_{uuid4()}"


def record_missing_or_invalid_bearer(
    *,
    envelope: DelegationEnvelope,
    authorization: str | None,
    error_code: str,
    reason: str,
) -> None:
    record_auth_event(
        trace_id=safe_trace_id(envelope),
        request_id=safe_request_id(envelope),
        caller_agent_id=envelope.caller_agent_id,
        claimed_agent_id=envelope.caller_agent_id,
        token_fingerprint=token_fingerprint(authorization.partition(" ")[2] if authorization else None),
        verified_at=int(time.time()),
        identity_decision="deny",
        error_code=error_code,
        reason=reason,
    )


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


def verify_bearer_for_envelope(
    *,
    envelope: DelegationEnvelope,
    authorization: str | None,
) -> TokenVerificationResult:
    try:
        token = bearer_token(authorization)
    except HTTPException as error:
        detail = error.detail if isinstance(error.detail, dict) else {}
        record_missing_or_invalid_bearer(
            envelope=envelope,
            authorization=authorization,
            error_code=str(detail.get("error_code", "AUTH_TOKEN_INVALID")),
            reason=str(detail.get("message", "invalid Authorization header")),
        )
        raise

    token_result = inspect_token(token)
    record_token_result(envelope, token_result)
    if token_result.auth_context is None:
        raise HTTPException(
            status_code=401,
            detail={"error_code": token_result.error_code, "message": token_result.message},
        )
    return token_result


@router.post("/delegate/call")
async def delegate_call(
    envelope: DelegationEnvelope,
    authorization: str | None = Header(default=None),
) -> AgentTaskResponse:
    token_result = verify_bearer_for_envelope(envelope=envelope, authorization=authorization)
    auth_context = token_result.auth_context

    trusted_envelope = envelope.model_copy(
        update={
            "caller_agent_id": auth_context.agent_id,
            "auth_context": auth_context,
        }
    )
    generated_intent_model = None
    if trusted_envelope.intent_node is None and trusted_envelope.payload.get("user_task"):
        trusted_envelope, generated_intent_model = await attach_generated_intent_node(
            envelope=trusted_envelope,
            auth_context=auth_context,
            user_task=str(trusted_envelope.payload.get("user_task", trusted_envelope.task_type)),
        )
    intent_result = None
    if trusted_envelope.intent_node is not None:
        try:
            intent_result = await validate_and_record_intent_node(
                node=trusted_envelope.intent_node,
                trace_id=trusted_envelope.trace_id,
                request_id=trusted_envelope.request_id,
                auth_context=auth_context,
        )
        except IntentValidationError as error:
            record_decision(trusted_envelope, intent_error_decision(error, trusted_envelope, generated_intent_model))
            raise HTTPException(
                status_code=403,
                detail={"error_code": error.error_code, "message": error.message},
            ) from error
    target = get_agent(trusted_envelope.target_agent_id)
    if target is None:
        raise HTTPException(
            status_code=404,
            detail={"error_code": "AGENT_NOT_REGISTERED", "agent_id": trusted_envelope.target_agent_id},
        )

    decision = delegation_service.authorize(trusted_envelope)
    if intent_result is not None:
        decision.intent_node_id = intent_result.node.node_id
        decision.parent_intent_node_id = intent_result.node.parent_node_id
        decision.root_intent = intent_result.root_intent
        decision.parent_intent = intent_result.parent_intent
        decision.child_intent = intent_result.child_intent
        decision.intent_generation_model = generated_intent_model
        decision.intent_judge_decision = intent_result.judge_decision
        decision.intent_judge_reason = intent_result.judge_reason
    record_decision(trusted_envelope, decision)
    raise_for_denied(decision)
    authorized_envelope = delegation_service.append_hop(
        trusted_envelope,
        decision.effective_capabilities,
    )

    try:
        return await forward_to_agent(target.endpoint, authorized_envelope)
    except HTTPException as error:
        forward_error_decision = DelegationDecision(
            decision="deny",
            reason=f"target agent unreachable or returned error: {error.detail}",
            effective_capabilities=decision.effective_capabilities,
            missing_capabilities=[],
            requested_capabilities=decision.requested_capabilities,
            caller_token_capabilities=decision.caller_token_capabilities,
            target_agent_capabilities=decision.target_agent_capabilities,
            user_capabilities=decision.user_capabilities,
        )
        record_decision(trusted_envelope, forward_error_decision)
        raise


@router.post("/delegate/root-task")
async def root_task(
    request: RootTaskRequest,
    authorization: str | None = Header(default=None),
) -> AgentTaskResponse:
    trace_id = request.trace_id or str(uuid4())
    request_id = request.request_id or str(uuid4())
    provisional_envelope = DelegationEnvelope(
        trace_id=trace_id,
        request_id=request_id,
        caller_agent_id="user",
        target_agent_id=request.target_agent_id,
        task_type=request.task_type,
        requested_capabilities=request.requested_capabilities,
        payload={**request.payload, "user_task": request.user_task},
    )
    token_result = verify_bearer_for_envelope(envelope=provisional_envelope, authorization=authorization)
    auth_context = token_result.auth_context
    if auth_context.actor_type != "user":
        raise HTTPException(status_code=403, detail={"error_code": "AUTH_ACTOR_TYPE_INVALID", "message": "root-task requires user token"})

    try:
        generated = await generate_intent_commitment(
            user_task=request.user_task,
            actor_id=auth_context.delegated_user,
            actor_type="user",
            target_agent_id=request.target_agent_id,
            task_type=request.task_type,
            payload=request.payload,
        )
    except IntentGenerationError as error:
        record_decision(provisional_envelope, intent_generation_error_decision(str(error), provisional_envelope))
        raise HTTPException(status_code=403, detail={"error_code": "INTENT_GENERATION_FAILED", "message": str(error)}) from error
    root_node = build_signed_intent_node(
        parent_node_id=None,
        actor_id=auth_context.delegated_user,
        actor_type="user",
        target_agent_id=request.target_agent_id,
        task_type=request.task_type,
        intent_commitment=generated.commitment,
    )
    root_hop = DelegationHop(
        from_actor=auth_context.delegated_user,
        to_agent_id=request.target_agent_id,
        task_type=request.task_type,
        delegated_capabilities=request.requested_capabilities,
        decision="root",
    )
    trusted_envelope = provisional_envelope.model_copy(
        update={
            "caller_agent_id": auth_context.delegated_user,
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
        record_decision(trusted_envelope, intent_error_decision(error, trusted_envelope))
        raise HTTPException(status_code=403, detail={"error_code": error.error_code, "message": error.message}) from error

    target = get_agent(request.target_agent_id)
    if target is None:
        raise HTTPException(status_code=404, detail={"error_code": "AGENT_NOT_REGISTERED", "agent_id": request.target_agent_id})

    decision = DelegationDecision(
        decision="allow",
        reason="root user task accepted",
        effective_capabilities=request.requested_capabilities,
        requested_capabilities=request.requested_capabilities,
        caller_token_capabilities=auth_context.capabilities,
        user_capabilities=auth_context.capabilities,
        intent_node_id=root_node.node_id,
        parent_intent_node_id=None,
        root_intent=intent_result.root_intent,
        parent_intent=intent_result.parent_intent,
        child_intent=intent_result.child_intent,
        intent_generation_model=generated.model,
        intent_judge_decision=intent_result.judge_decision,
        intent_judge_reason=intent_result.judge_reason,
    )
    record_decision(trusted_envelope, decision)
    return await forward_to_agent(target.endpoint, trusted_envelope)


async def forward_to_agent(endpoint: str, envelope: DelegationEnvelope) -> AgentTaskResponse:
    if endpoint.startswith("local://"):
        return await call_local_agent(endpoint, envelope)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(endpoint, json=envelope.model_dump())
            response.raise_for_status()
            return AgentTaskResponse.model_validate(response.json())
    except httpx.HTTPError as error:
        raise HTTPException(
            status_code=502,
            detail={"error_code": "TARGET_AGENT_UNREACHABLE", "message": str(error)},
        ) from error


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


async def attach_generated_intent_node(
    *,
    envelope: DelegationEnvelope,
    auth_context,
    user_task: str,
) -> tuple[DelegationEnvelope, str]:
    parent_node_id = None
    if envelope.delegation_chain and envelope.payload.get("parent_intent_node_id"):
        parent_node_id = str(envelope.payload["parent_intent_node_id"])
    elif envelope.payload.get("parent_intent_node_id"):
        parent_node_id = str(envelope.payload["parent_intent_node_id"])
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
        parent_node_id=parent_node_id,
        actor_id=auth_context.agent_id,
        actor_type="agent",
        target_agent_id=envelope.target_agent_id,
        task_type=envelope.task_type,
        intent_commitment=generated.commitment,
    )
    return envelope.model_copy(update={"intent_node": node}), generated.model
