"""
# services/utils/probe_context.py
Probe and canary job isolation (plan C-39, contract K-57 CT-VC-PROBE).

A job whose LiveKit room is 'jarvis-probe-<epoch>' (scripts/smoke/voice_probe.py) or 'jarvis-canary-<epoch>'
(K-57 v4, the VPS-brain canary) sets bridge_suppressed=True before it creates any task. Every task it creates and
every asyncio.to_thread call it makes inherits the value (contextvars), and nothing else does: the user's own job in
the same process (the Windows THREAD executor) runs in its own context. While it is set nothing leaves for the UI
bridge (notify_event, confirm requests, the UI log handler) and memory and identity writes are no-ops.

Standard library only, so jarvis_bridge, jarvis_logger and the memory modules can all import it without a cycle.
jarvis_bridge re-exports bridge_suppressed and is_probe_room (the names K-57 uses).
"""
from __future__ import annotations

import re
from contextvars import ContextVar
from typing import Any

PROBE_ROOM_RE = re.compile(r"^jarvis-(probe|canary)-\d+$")

bridge_suppressed: ContextVar[bool] = ContextVar("bridge_suppressed", default=False)


def is_probe_room(room_name: Any) -> bool:
    """True for a probe or canary room name (K-57); every other room, the JARVIS room included, is a normal job."""
    return isinstance(room_name, str) and bool(PROBE_ROOM_RE.match(room_name))


def is_probe_job() -> bool:
    """True inside a probe or canary job's context."""
    return bridge_suppressed.get()
