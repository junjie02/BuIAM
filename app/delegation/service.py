from __future__ import annotations

import time

from fastapi import HTTPException

from app.delegation.capabilities import intersect_capabilities, known_capabilities, parse_capabilities
from app.delegation.credential_crypto import auth_context_from_credential, build_delegation_credential, get_agent_vc, verify_credential_integrity
from app.identity.did import build_did, build_verification_method_id
from app.protocol import DelegationDecision, DelegationEnvelope, DelegationHop
from app.store.audit import record_decision
from app.store.delegation_credentials import get_credential, upsert_credential
from app.store.registry import find_agents_with_capabilities, get_agent


class CredentialValidationError(Exception):
    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


class DelegationService:
    def authorize(self, envelope: DelegationEnvelope) -> DelegationDecision:
        requested_for_error = sorted(envelope.requested_capabilities)
        _denied = lambda **kw: DelegationDecision(
            decision="deny",
            effective_capabilities=[],
            missing_capabilities=requested_for_error,
            requested_capabilities=requested_for_error,
            **kw,
        )

        target_agent = get_agent(envelope.target_agent_id)
        if target_agent is None:
            return _denied(
                reason=f"unknown target agent: {envelope.target_agent_id}",
                recoverable=False,
            )

        agent_vc = get_agent_vc(envelope.target_agent_id)
        target_caps: set[str] | frozenset[str]
        if agent_vc is not None:
            target_caps = frozenset(agent_vc.capabilities)
        else:
            target_caps = target_agent.static_capabilities
        auth_context = envelope.auth_context
        if auth_context is None:
            return _denied(reason="missing trusted auth context", recoverable=False)

        if auth_context.sub != envelope.caller_agent_id or auth_context.agent_id != envelope.caller_agent_id:
            return _denied(reason="auth context subject does not match caller agent", recoverable=False)

        if not self.is_chain_continuous(envelope):
            return _denied(reason="delegation chain is not continuous with caller agent", recoverable=False)

        try:
            self.validate_auth_context_credential(auth_context)
        except CredentialValidationError as error:
            return _denied(reason=f"{error.error_code}: {error.message}", recoverable=False)

        try:
            known_caps = known_capabilities()
            requested = parse_capabilities(envelope.requested_capabilities, known_caps)
            caller_token_caps = parse_capabilities(auth_context.capabilities, known_caps)
            delegated_user_caps = parse_capabilities(auth_context.user_capabilities or auth_context.capabilities, known_caps)
        except ValueError as error:
            return _denied(reason=str(error), recoverable=True)

        effective = intersect_capabilities(caller_token_caps, target_caps, requested, delegated_user_caps)
        missing = requested - effective
        missing_by = self.build_missing_by(requested=requested, caller_token_caps=caller_token_caps, target_caps=target_caps, delegated_user_caps=delegated_user_caps)
        if missing:
            target_missing = missing_by.get("target_agent", [])
            suggested_agents: list[dict[str, str]] = []
            if target_missing:
                suggested_agents = find_agents_with_capabilities(
                    target_missing, exclude_agent_id=envelope.target_agent_id
                )
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
                recoverable=True,
                suggested_agents=[a["agent_id"] for a in suggested_agents],
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
            recoverable=True,
        )

    def build_missing_by(self, *, requested: set[str], caller_token_caps: set[str], target_caps: set[str] | frozenset[str], delegated_user_caps: set[str] | frozenset[str]) -> dict[str, list[str]]:
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

    def append_hop(self, envelope: DelegationEnvelope, effective_capabilities: list[str]) -> DelegationEnvelope:
        hop = self.build_decision_hop(envelope=envelope, effective_capabilities=effective_capabilities, missing_capabilities=[], decision="allow")
        next_auth_context = self.build_child_auth_context(parent_auth_context=envelope.auth_context, issuer_id=envelope.caller_agent_id, subject_id=envelope.target_agent_id, capabilities=effective_capabilities, trace_id=envelope.trace_id, request_id=envelope.request_id)
        return envelope.model_copy(update={"delegation_chain": [*envelope.delegation_chain, hop], "auth_context": next_auth_context})

    def build_decision_hop(self, envelope: DelegationEnvelope, effective_capabilities: list[str], missing_capabilities: list[str], decision: str) -> DelegationHop:
        return DelegationHop(
            from_actor=envelope.caller_agent_id,
            to_agent_id=envelope.target_agent_id,
            task_type=envelope.task_type,
            delegated_capabilities=effective_capabilities if decision == "allow" else [],
            missing_capabilities=missing_capabilities,
            decision=decision,
        )

    def build_child_auth_context(self, *, parent_auth_context, issuer_id: str, subject_id: str, capabilities: list[str], trace_id: str, request_id: str):
        parent_credential = self.validate_auth_context_credential(parent_auth_context)
        if parent_credential is None:
            child_did = build_did(subject_id)
            return parent_auth_context.model_copy(update={
                "jti": f"{parent_auth_context.jti}:{request_id}",
                "sub": subject_id,
                "agent_id": subject_id,
                "subject_did": child_did,
                "agent_did": child_did,
                "signing_kid": build_verification_method_id(build_did(issuer_id)),
                "capabilities": capabilities,
                "sig": None,
            })
        if not set(capabilities).issubset(set(parent_credential.capabilities)):
            raise CredentialValidationError("AUTH_CREDENTIAL_INVALID", "child delegation capabilities exceed parent")

        child_credential = build_delegation_credential(
            issuer_id=issuer_id,
            subject_id=subject_id,
            delegated_user=parent_credential.delegated_user,
            capabilities=capabilities,
            user_capabilities=parent_credential.user_capabilities,
            exp=parent_credential.exp,
            parent=parent_credential,
            trace_id=trace_id,
            request_id=request_id,
        )
        upsert_credential(child_credential)
        return auth_context_from_credential(child_credential)

    def validate_auth_context_credential(self, auth_context):
        if auth_context is None or auth_context.credential_id is None:
            return None
        credential = get_credential(auth_context.credential_id)
        if credential is None:
            raise CredentialValidationError("AUTH_CREDENTIAL_INVALID", "delegation credential is not registered")
        self.validate_credential_branch(credential)

        if credential.subject_id != auth_context.agent_id or credential.subject_id != auth_context.sub:
            raise CredentialValidationError("AUTH_CREDENTIAL_INVALID", "delegation credential subject does not match auth context")

        expected_subject_did = credential.subject_did or build_did(credential.subject_id)
        if auth_context.subject_did is not None and auth_context.subject_did != expected_subject_did:
            raise CredentialValidationError("AUTH_CREDENTIAL_INVALID", "delegation credential DID does not match auth context")

        if set(auth_context.capabilities) != set(credential.capabilities):
            raise CredentialValidationError("AUTH_CREDENTIAL_INVALID", "delegation credential capabilities do not match auth context")
        return credential

    def validate_credential_branch(self, credential, *, current: bool = True) -> None:
        now = int(time.time())
        if not verify_credential_integrity(credential):
            raise CredentialValidationError("AUTH_CREDENTIAL_INVALID", "delegation credential integrity verification failed")
        if credential.revoked:
            raise CredentialValidationError("AUTH_CREDENTIAL_REVOKED" if current else "AUTH_PARENT_CREDENTIAL_REVOKED", "delegation credential has been revoked")
        if credential.exp <= now:
            raise CredentialValidationError("AUTH_CREDENTIAL_EXPIRED" if current else "AUTH_PARENT_CREDENTIAL_EXPIRED", "delegation credential has expired")
        if credential.parent_credential_id is None:
            if credential.root_credential_id != credential.credential_id:
                raise CredentialValidationError("AUTH_CREDENTIAL_INVALID", "root delegation credential id mismatch")
            return

        parent = get_credential(credential.parent_credential_id)
        if parent is None:
            raise CredentialValidationError("AUTH_CREDENTIAL_INVALID", "parent delegation credential is not registered")
        if credential.root_credential_id != parent.root_credential_id:
            raise CredentialValidationError("AUTH_CREDENTIAL_INVALID", "delegation credential root does not match parent")
        if credential.exp > parent.exp:
            raise CredentialValidationError("AUTH_CREDENTIAL_INVALID", "delegation credential expires after parent")
        if not set(credential.capabilities).issubset(set(parent.capabilities)):
            raise CredentialValidationError("AUTH_CREDENTIAL_INVALID", "delegation credential capabilities exceed parent")
        if not set(credential.user_capabilities).issubset(set(parent.user_capabilities)):
            raise CredentialValidationError("AUTH_CREDENTIAL_INVALID", "delegation credential user capabilities exceed parent")
        self.validate_credential_branch(parent, current=False)


delegation_service = DelegationService()


def raise_for_denied(decision: DelegationDecision) -> None:
    if decision.decision == "deny":
        raise HTTPException(
            status_code=403,
            detail={
                "error": "delegation_denied",
                "error_code": "AUTH_DELEGATION_DENIED",
                "reason": decision.reason,
                "effective_capabilities": decision.effective_capabilities,
                "missing_capabilities": decision.missing_capabilities,
                "missing_by": decision.missing_by,
                "recoverable": decision.recoverable,
                "suggested_agents": decision.suggested_agents,
            },
        )
