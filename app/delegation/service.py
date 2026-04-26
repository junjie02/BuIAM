from __future__ import annotations

from fastapi import HTTPException

from app.delegation.capabilities import intersect_capabilities, parse_capabilities
from app.protocol import DelegationDecision, DelegationEnvelope, DelegationHop
from app.store.audit import record_decision
from app.store.registry import get_agent


class DelegationService:
    def authorize(self, envelope: DelegationEnvelope) -> DelegationDecision:
        requested_for_error = sorted(envelope.requested_capabilities)
        target_agent = get_agent(envelope.target_agent_id)
        if target_agent is None:
            return DelegationDecision(
                decision="deny",
                reason=f"unknown target agent: {envelope.target_agent_id}",
                effective_capabilities=[],
                missing_capabilities=requested_for_error,
                requested_capabilities=requested_for_error,
            )

        target_caps = target_agent.static_capabilities
        auth_context = envelope.auth_context
        if auth_context is None:
            return DelegationDecision(
                decision="deny",
                reason="missing trusted auth context",
                effective_capabilities=[],
                missing_capabilities=requested_for_error,
                requested_capabilities=requested_for_error,
            )

        if auth_context.sub != envelope.caller_agent_id or auth_context.agent_id != envelope.caller_agent_id:
            return DelegationDecision(
                decision="deny",
                reason="auth context subject does not match caller agent",
                effective_capabilities=[],
                missing_capabilities=requested_for_error,
                requested_capabilities=requested_for_error,
            )

        if not self.is_chain_continuous(envelope):
            return DelegationDecision(
                decision="deny",
                reason="delegation chain is not continuous with caller agent",
                effective_capabilities=[],
                missing_capabilities=requested_for_error,
                requested_capabilities=requested_for_error,
            )

        try:
            requested = parse_capabilities(envelope.requested_capabilities)
            caller_token_caps = parse_capabilities(auth_context.capabilities)
            delegated_user_caps = parse_capabilities(auth_context.user_capabilities or auth_context.capabilities)
        except ValueError as error:
            return DelegationDecision(
                decision="deny",
                reason=str(error),
                effective_capabilities=[],
                missing_capabilities=requested_for_error,
                requested_capabilities=requested_for_error,
            )

        effective = intersect_capabilities(
            caller_token_caps,
            target_caps,
            requested,
            delegated_user_caps,
        )
        missing = requested - effective
        missing_by = self.build_missing_by(
            requested=requested,
            caller_token_caps=caller_token_caps,
            target_caps=target_caps,
            delegated_user_caps=delegated_user_caps,
        )
        if missing:
            return DelegationDecision(
                decision="deny",
                reason=f"requested capabilities not covered by token, target, request, and user intersection: {sorted(missing)}",
                effective_capabilities=sorted(effective),
                missing_capabilities=sorted(missing),
                requested_capabilities=sorted(requested),
                caller_token_capabilities=sorted(caller_token_caps),
                target_agent_capabilities=sorted(target_caps),
                user_capabilities=sorted(delegated_user_caps),
                missing_by=missing_by,
            )

        return DelegationDecision(
            decision="allow",
            reason="requested capabilities are covered by token, target, request, and user intersection",
            effective_capabilities=sorted(effective),
            missing_capabilities=[],
            requested_capabilities=sorted(requested),
            caller_token_capabilities=sorted(caller_token_caps),
            target_agent_capabilities=sorted(target_caps),
            user_capabilities=sorted(delegated_user_caps),
            missing_by=missing_by,
        )

    def build_missing_by(
        self,
        *,
        requested: set[str],
        caller_token_caps: set[str],
        target_caps: set[str] | frozenset[str],
        delegated_user_caps: set[str] | frozenset[str],
    ) -> dict[str, list[str]]:
        return {
            "caller_token": sorted(requested - set(caller_token_caps)),
            "target_agent": sorted(requested - set(target_caps)),
            "user": sorted(requested - set(delegated_user_caps)),
        }

    def is_chain_continuous(self, envelope: DelegationEnvelope) -> bool:
        if not envelope.delegation_chain:
            return True
        return envelope.delegation_chain[-1].to_agent_id == envelope.caller_agent_id

    def authorize_and_record(self, envelope: DelegationEnvelope) -> DelegationDecision:
        decision = self.authorize(envelope)
        record_decision(envelope, decision)
        return decision

    def append_hop(
        self,
        envelope: DelegationEnvelope,
        effective_capabilities: list[str],
    ) -> DelegationEnvelope:
        hop = self.build_decision_hop(
            envelope=envelope,
            effective_capabilities=effective_capabilities,
            missing_capabilities=[],
            decision="allow",
        )
        next_auth_context = envelope.auth_context.model_copy(
            update={
                "jti": f"{envelope.auth_context.jti}:{envelope.request_id}",
                "sub": envelope.target_agent_id,
                "agent_id": envelope.target_agent_id,
                "capabilities": effective_capabilities,
                "sig": None,
            }
        )
        return envelope.model_copy(
            update={
                "delegation_chain": [*envelope.delegation_chain, hop],
                "auth_context": next_auth_context,
            }
        )

    def build_decision_hop(
        self,
        envelope: DelegationEnvelope,
        effective_capabilities: list[str],
        missing_capabilities: list[str],
        decision: str,
    ) -> DelegationHop:
        return DelegationHop(
            from_actor=envelope.caller_agent_id,
            to_agent_id=envelope.target_agent_id,
            task_type=envelope.task_type,
            delegated_capabilities=effective_capabilities if decision == "allow" else [],
            missing_capabilities=missing_capabilities,
            decision=decision,
        )


delegation_service = DelegationService()


def raise_for_denied(decision: DelegationDecision) -> None:
    if decision.decision == "deny":
        raise HTTPException(
            status_code=403,
            detail={
                "error": "delegation_denied",
                "reason": decision.reason,
                "effective_capabilities": decision.effective_capabilities,
                "missing_capabilities": decision.missing_capabilities,
            },
        )
