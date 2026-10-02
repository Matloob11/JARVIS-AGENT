"""Live probes of a running UI bridge (plan task C-14; marker 'live').

Skipped unless JARVIS_LIVE_BRIDGE_URL names the bridge, for example
    JARVIS_LIVE_BRIDGE_URL=https://jarvis.srv1802669.hstgr.cloud/vortex $PY -m pytest -m live tests/infrastructure -q
Credentials come from the environment through jarvis_config (the root .env): VORTEX_SECURITY_TOKEN for the legacy
probe, AGENT_TOKEN + BRIDGE_HMAC_SECRET for the signed one; a probe whose credentials are missing is skipped. No
credential is printed. The only events sent are 'qa_ping' (no UI effect) and forgeries the bridge must refuse.
"""
from __future__ import annotations

import json
import os
import secrets
import time

import httpx
import pytest

from services.utils.jarvis_config import config
from services.utils.jarvis_security import security_manager, sign_notify

BRIDGE_URL = (os.getenv("JARVIS_LIVE_BRIDGE_URL") or "").strip().rstrip("/")
pytestmark = [pytest.mark.live,
              pytest.mark.skipif(not BRIDGE_URL, reason="set JARVIS_LIVE_BRIDGE_URL to probe a running bridge")]
TIMEOUT = 15.0


def _body(n: int) -> bytes:
    return json.dumps({"type": "qa_ping", "payload": {"n": n}}, separators=(",", ":")).encode()


def _agent_headers(raw: bytes, token: str) -> dict[str, str]:
    ts, nonce = str(int(time.time())), secrets.token_hex(16)
    return {"Content-Type": "application/json", "X-Agent-Token": token, "X-Agent-Timestamp": ts,
            "X-Agent-Nonce": nonce, "X-Agent-Signature": sign_notify(ts, nonce, raw)}


async def test_health_answers():
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.get(f"{BRIDGE_URL}/health")
    assert resp.status_code == 200


async def test_forged_notifies_are_refused():
    raw = _body(0)
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        forged_agent = await client.post(f"{BRIDGE_URL}/notify", content=raw, headers={
            "Content-Type": "application/json", "X-Agent-Token": "c14-wrong-" + secrets.token_hex(8),
            "X-Agent-Timestamp": str(int(time.time())), "X-Agent-Nonce": secrets.token_hex(16),
            "X-Agent-Signature": "0" * 64})
        forged_legacy = await client.post(f"{BRIDGE_URL}/notify", content=raw, headers={
            "Content-Type": "application/json", "X-Vortex-Token": "c14-wrong-" + secrets.token_hex(8),
            "X-Vortex-Signature": "0" * 64})
        nothing = await client.post(f"{BRIDGE_URL}/notify", content=raw,
                                    headers={"Content-Type": "application/json"})
    assert (forged_agent.status_code, forged_legacy.status_code, nothing.status_code) == (401, 401, 401)


async def test_the_legacy_headers_still_work():
    if not config.security_token:
        pytest.skip("VORTEX_SECURITY_TOKEN is not set here")
    raw = _body(1)
    signature = security_manager.generate_signature(json.dumps(json.loads(raw), sort_keys=True))
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(f"{BRIDGE_URL}/notify", content=raw, headers={
            "Content-Type": "application/json", "X-Vortex-Token": config.security_token,
            "X-Vortex-Signature": signature})
    assert resp.status_code == 200, "legacy auth is off on this bridge (LEGACY_TOKEN_AUTH=0) or the token differs"


async def test_a_signed_notify_is_accepted_once():
    if not (config.agent_token and config.bridge_hmac_secret):
        pytest.skip("AGENT_TOKEN and BRIDGE_HMAC_SECRET are not both set here")
    raw = _body(2)
    headers = _agent_headers(raw, config.agent_token)
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        first = await client.post(f"{BRIDGE_URL}/notify", content=raw, headers=headers)
        replay = await client.post(f"{BRIDGE_URL}/notify", content=raw, headers=headers)
    assert (first.status_code, replay.status_code) == (200, 409)
