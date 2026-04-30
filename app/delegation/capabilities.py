from __future__ import annotations

from collections.abc import Sequence

from app.store.registry import list_agents


def known_capabilities() -> set[str]:
    capabilities: set[str] = set()
    for agent in list_agents():
        capabilities.update(agent.static_capabilities)
    return capabilities


def parse_capabilities(
    raw_capabilities: Sequence[str],
    known: set[str] | frozenset[str] | None = None,
) -> set[str]:
    parsed: set[str] = set(raw_capabilities)
    known_set = known_capabilities() if known is None else set(known)
    unknown = parsed - known_set
    if unknown:
        raise ValueError(f"unknown capabilities: {sorted(unknown)}")
    return parsed


def intersect_capabilities(*capability_sets: set[str] | frozenset[str]) -> set[str]:
    if not capability_sets:
        return set()
    result = set(capability_sets[0])
    for capability_set in capability_sets[1:]:
        result &= set(capability_set)
    return result
