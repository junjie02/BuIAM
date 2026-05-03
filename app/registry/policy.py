"""Capability approval engine.

Evaluates registration requests against stored policies.
"""

from __future__ import annotations

from app.store.capability_policies import get_policy


def evaluate_capability_request(
    *,
    subject_type: str,
    agent_type: str,
    requested: list[str],
) -> dict:
    """Evaluate a capability request against the policy for (subject_type, agent_type).

    Returns a dict with decision details:
        - decision: "approved" | "modified" | "denied"
        - granted: capabilities actually granted
        - missing: capabilities denied
        - reason: human-readable explanation
    """
    if not requested:
        return {
            "decision": "denied",
            "granted": [],
            "missing": [],
            "reason": "no capabilities requested",
        }

    policy = get_policy(subject_type, agent_type)
    if policy is None:
        return {
            "decision": "denied",
            "granted": [],
            "missing": sorted(requested),
            "reason": f"no capability policy found for {subject_type}/{agent_type}",
        }

    allowed = frozenset(policy["allowed_capabilities"])
    requested_set = frozenset(requested)
    granted = sorted(requested_set & allowed)
    missing = sorted(requested_set - allowed)

    if not granted:
        return {
            "decision": "denied",
            "granted": [],
            "missing": missing,
            "reason": f"none of the requested capabilities are allowed for {subject_type}/{agent_type}",
        }

    if missing:
        return {
            "decision": "modified",
            "granted": granted,
            "missing": missing,
            "reason": f"some requested capabilities are not allowed for {subject_type}/{agent_type}",
        }

    return {
        "decision": "approved",
        "granted": granted,
        "missing": [],
        "reason": "all requested capabilities approved",
    }
