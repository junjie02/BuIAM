from __future__ import annotations

import asyncio
from collections import defaultdict


_running_tasks: dict[str, set[asyncio.Task]] = defaultdict(set)
_cancel_reasons: dict[str, str] = {}


def register_task(trace_id: str, task: asyncio.Task) -> None:
    _running_tasks[trace_id].add(task)
    reason = _cancel_reasons.get(trace_id)
    if reason is not None and not task.done():
        task.get_loop().call_soon_threadsafe(task.cancel, reason)


def unregister_task(trace_id: str, task: asyncio.Task) -> None:
    tasks = _running_tasks.get(trace_id)
    if tasks is None:
        return
    tasks.discard(task)
    if not tasks:
        _running_tasks.pop(trace_id, None)


def cancel_trace(trace_id: str, reason: str) -> int:
    _cancel_reasons[trace_id] = reason
    cancelled = 0
    for task in list(_running_tasks.get(trace_id, set())):
        if not task.done():
            task.get_loop().call_soon_threadsafe(task.cancel, reason)
            cancelled += 1
    return cancelled


def cancel_traces(trace_ids: list[str], reason: str) -> int:
    return sum(cancel_trace(trace_id, reason) for trace_id in trace_ids)


def cancel_reason(trace_id: str) -> str | None:
    return _cancel_reasons.get(trace_id)
