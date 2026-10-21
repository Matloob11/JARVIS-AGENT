"""C-86: the plan progress line end to end (K-02/K-40, K-39 v4, K-57).

The bridge routes notify 'plan_progress' {current, total, action, status, timestamp} to room 'ui' as 'plan_progress'
with the same flat payload (the 'others' rate class), never as an UNKNOWN EVENT and never storing it. The agent's
tool_report_plan_progress posts through jarvis_bridge.notify_event into the real bridge app (src/core/ui_bridge.py in
process through httpx.ASGITransport, temporary bridge.db, recorded sio.emit): on the internal :5002 listener without
any credential (the legacy token is refused there), signed with the v2 keys, or with the legacy pair on the public
listener; a refusal makes the tool say so, and a probe job sends nothing. The bridge answers a shown line {status:
'ok', emitted: 'plan_progress'}, and only that answer is a success (C-86 review): a bridge without the route (the one
deployed before this change answers 200 {status: 'ok'} after 'UNKNOWN EVENT') or a proxy's page is reported as not
shown. Throwaway credentials only: nothing reaches a network, the live bridge or a real env file.
"""
# pylint: disable=protected-access,redefined-outer-name
from __future__ import annotations

import importlib
import json
import logging
import secrets
import time
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from services.ai_core import autonomous_planner as planner
from services.utils import jarvis_bridge, jarvis_security, probe_context
from services.utils.jarvis_config import config
from src.core import bridge_security
from src.core.bridge_security import NonceCache
from src.core.bridge_store import BridgeStore

AGENT_TOKEN = "c86-test-agent-token-" + "a" * 24
HMAC_SECRET = "c86-test-hmac-secret-" + "b" * 24
LEGACY_TOKEN = "c86-test-legacy-token-" + "c" * 24
INTERNAL_PORT = 5002
SECRET_ACTION = "Ali ko PRIVATE-PLAN-TEXT wala email"  # stands in for the owner's own words
SHOWN = {"status": "ok", "emitted": "plan_progress"}  # the bridge's answer once it emitted the line


class _Keep(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def text(self) -> str:
        return "\n".join(record.getMessage() for record in self.records)


@pytest.fixture
async def bridge(monkeypatch, tmp_path):
    """ui_bridge with a temporary store, fresh volatile state, nonce cache and limiter, throwaway credentials, a
    recorded sio.emit (event, data, room) and the UI-BRIDGE log records (log)."""
    monkeypatch.setenv("BRIDGE_DB_PATH", str(tmp_path / "import-default.db"))
    module = importlib.import_module("src.core.ui_bridge")
    emitted: list[tuple[str, Any, Any]] = []

    async def fake_emit(event: str, data: Any = None, **kwargs: Any) -> None:
        emitted.append((event, data, kwargs.get("to") or kwargs.get("room")))

    store = BridgeStore(tmp_path / "bridge.db")
    for name, value in (("store", store), ("bridge_manager", module.BridgeStateManager()),
                        ("_notify_nonces", NonceCache()), ("VORTEX_SECURITY_TOKEN", LEGACY_TOKEN)):
        monkeypatch.setattr(module, name, value)
    monkeypatch.setattr(module.sio, "emit", fake_emit)
    monkeypatch.setattr(module.app.state, "notify_limiter", module.NotifyRateLimiter())
    monkeypatch.setattr(config, "agent_token", AGENT_TOKEN)
    monkeypatch.setattr(config, "bridge_hmac_secret", HMAC_SECRET)
    monkeypatch.setattr(config, "security_token", LEGACY_TOKEN)
    monkeypatch.setattr(config, "legacy_token_auth", True)
    monkeypatch.setattr(config, "env", "development")
    log = _Keep()
    old_level = module.logger.level
    module.logger.addHandler(log)
    module.logger.setLevel(logging.DEBUG)
    yield SimpleNamespace(module=module, emitted=emitted, store=store, log=log)
    module.logger.removeHandler(log)
    module.logger.setLevel(old_level)
    await store.close()


def _body(event_type: str, payload: Any) -> bytes:
    return json.dumps({"type": event_type, "payload": payload}, separators=(",", ":")).encode("utf-8")


async def _post(ns: Any, raw: bytes, headers: dict[str, str] | None = None, *, internal: bool) -> httpx.Response:
    base = f"http://bridge.test:{INTERNAL_PORT}" if internal else "http://bridge.test"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ns.module.app), base_url=base) as client:
        return await client.post("/notify", content=raw, headers=headers or {})


def _signed(raw: bytes) -> dict[str, str]:
    ts, nonce = str(int(time.time())), secrets.token_hex(16)
    return {"Content-Type": "application/json", "X-Agent-Token": AGENT_TOKEN, "X-Agent-Timestamp": ts,
            "X-Agent-Nonce": nonce, "X-Agent-Signature": jarvis_security.sign_notify(ts, nonce, raw)}


def _lines(ns: Any) -> list[tuple[Any, ...]]:
    return [(data["current"], data["total"], data["action"], data["status"]) for event, data, _room in ns.emitted
            if event == "plan_progress"]


# ---------------------------------------------------------------- the bridge's route


async def test_a_plan_progress_reaches_room_ui_with_the_same_flat_payload(bridge):
    payload = {"current": 1, "total": 3, "action": "Scan", "status": "IN_PROGRESS", "timestamp": 1791460000.5}
    resp = await _post(bridge, _body("plan_progress", payload), internal=True)  # :5002 needs no credential
    assert resp.status_code == 200 and resp.json() == SHOWN  # the answer names the emit (C-86 review)
    assert bridge.emitted == [("plan_progress", payload, "ui")]  # one emit; the UI's _update listener is an alias
    assert "UNKNOWN EVENT" not in bridge.log.text()
    state = await bridge.module.build_init_state()
    assert "plan_progress" not in state and state["logs"] == [] and state["memories"] == []  # live only


async def test_the_signed_public_path_takes_it_too(bridge):
    raw = _body("plan_progress", {"current": 2, "total": 3, "action": "Heal", "status": "running"})
    resp = await _post(bridge, raw, _signed(raw), internal=False)
    assert resp.status_code == 200 and _lines(bridge) == [(2, 3, "Heal", "IN_PROGRESS")]


async def test_the_bridge_normalizes_by_the_agents_rules(bridge):
    for payload in ({"current": 5, "total": 3, "action": "x" * 500, "status": "done", "timestamp": 1791460000123},
                    {"step_index": 0, "total_steps": "3", "action": " a\nb ", "status": "Error"}):
        assert (await _post(bridge, _body("plan_progress", payload), internal=True)).json() == SHOWN
    first, second = [data for event, data, _room in bridge.emitted]
    assert first == {"current": 3, "total": 3, "action": "x" * 200, "status": "COMPLETED",
                     "timestamp": 1791460000.123}
    assert (second["current"], second["total"], second["action"], second["status"]) == (1, 3, "a b", "FAILED")


@pytest.mark.parametrize("payload, field", [
    ({"current": 1, "total": 0, "action": SECRET_ACTION}, "total"),
    ({"current": 1, "total": 3, "action": SECRET_ACTION, "status": "maybe"}, "status"),
    ({"current": -2, "total": 3, "action": SECRET_ACTION}, "current"),
    ([1, 3, SECRET_ACTION], "payload"),
    (SECRET_ACTION, "payload"),
])
async def test_an_unusable_line_is_ignored_and_its_text_never_logged(bridge, payload, field):
    resp = await _post(bridge, _body("plan_progress", payload), internal=True)
    assert resp.status_code == 200 and resp.json() == {"status": "ignored", "reason": "bad_payload"}
    assert bridge.emitted == []
    assert f"plan_progress ignored: bad {field}" in bridge.log.text() and SECRET_ACTION not in bridge.log.text()


def test_plan_progress_is_in_the_others_rate_class():
    assert bridge_security.notify_class("plan_progress") == bridge_security.NOTIFY_CLASS_OTHERS


async def test_the_others_bucket_limits_it(bridge, monkeypatch):
    clock = SimpleNamespace(now=1000.0)
    limiter = bridge_security.NotifyRateLimiter(clock=lambda: clock.now)
    monkeypatch.setattr(bridge.module.app.state, "notify_limiter", limiter)
    raw = _body("plan_progress", {"current": 1, "total": 3})
    codes = [(await _post(bridge, raw, internal=True)).status_code for _ in range(21)]
    assert codes[:20] == [200] * 20 and codes[20] == 429
    assert limiter.buckets["critical"]._tokens == 50.0  # a transcript never waits behind plan lines


async def test_the_internal_listener_refuses_the_legacy_token(bridge):
    """Why the client must never send it there (K-39 v4): any notify carrying X-Vortex-Token on :5002 is 401."""
    raw = _body("plan_progress", {"current": 1, "total": 3})
    resp = await _post(bridge, raw, {"X-Vortex-Token": LEGACY_TOKEN, "X-Vortex-Signature": "x"}, internal=True)
    assert resp.status_code == 401 and resp.json() == {"error": "bad_token"} and bridge.emitted == []


# ---------------------------------------------------------------- the agent's tool into the real bridge


@pytest.fixture
async def agent(bridge, monkeypatch):
    """jarvis_bridge's HTTP client wired to the bridge app; every request the agent sends is recorded (requests)."""
    asgi = httpx.ASGITransport(app=bridge.module.app)
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        response = await asgi.handle_async_request(request)
        await response.aread()
        return response

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def get_client() -> httpx.AsyncClient:
        return client

    monkeypatch.setattr(jarvis_bridge, "get_http_client", get_client)
    monkeypatch.setattr(jarvis_bridge, "_link_logged", set())
    log = _Keep()
    old_level = planner.logger.level
    planner.logger.addHandler(log)
    planner.logger.setLevel(logging.DEBUG)
    yield SimpleNamespace(requests=requests, log=log)
    planner.logger.removeHandler(log)
    planner.logger.setLevel(old_level)
    await client.aclose()


async def test_a_three_step_plan_shows_each_step_then_completed_on_the_internal_listener(bridge, agent, monkeypatch):
    """The VPS brain: UI_BRIDGE_URL is the internal listener and the agent posts there with no credential at all when
    the v2 keys are missing (K-39 v4). 1/3, 2/3, 3/3 and COMPLETED reach room 'ui', in order."""
    monkeypatch.setattr(config, "bridge_url", f"http://jarvis-bridge:{INTERNAL_PORT}")
    monkeypatch.setattr(config, "agent_token", None)
    results = [await planner.tool_report_plan_progress(n, 3, f"{SECRET_ACTION} {n}") for n in (1, 2, 3)]
    results.append(await planner.tool_report_plan_progress(3, 3, "Mukammal", "COMPLETED"))
    assert [r["status"] for r in results] == ["success"] * 4 and all(r["data"]["delivered"] for r in results)
    assert [(c, t, s) for c, t, _a, s in _lines(bridge)] == [
        (1, 3, "IN_PROGRESS"), (2, 3, "IN_PROGRESS"), (3, 3, "IN_PROGRESS"), (3, 3, "COMPLETED")]
    for request in agent.requests:
        assert str(request.url) == f"http://jarvis-bridge:{INTERNAL_PORT}/notify"
        assert not {"x-vortex-token", "x-vortex-signature", "x-agent-token"} & set(request.headers)
    assert "UNKNOWN EVENT" not in bridge.log.text()
    for text in (bridge.log.text(), agent.log.text()):
        assert SECRET_ACTION not in text  # numbers and status only (K-29)
    assert "PLAN_PROGRESS 3/3 COMPLETED delivered" in agent.log.text()


async def test_with_the_v2_keys_the_line_goes_signed_on_either_listener(bridge, agent, monkeypatch):
    for url in (f"http://jarvis-bridge:{INTERNAL_PORT}", "http://bridge.test"):
        monkeypatch.setattr(config, "bridge_url", url)
        assert (await planner.tool_report_plan_progress(1, 2, "Scan"))["status"] == "success"
    assert len(_lines(bridge)) == 2
    assert all(request.headers["x-agent-token"] == AGENT_TOKEN and "x-vortex-token" not in request.headers
               for request in agent.requests)


async def test_the_legacy_pair_works_on_the_public_listener_while_the_flag_is_on(bridge, agent, monkeypatch):
    monkeypatch.setattr(config, "bridge_url", "http://bridge.test")
    monkeypatch.setattr(config, "agent_token", None)
    assert (await planner.tool_report_plan_progress(1, 2, "Scan"))["status"] == "success"
    assert agent.requests[0].headers["x-vortex-token"] == LEGACY_TOKEN and _lines(bridge) == [(1, 2, "Scan",
                                                                                               "IN_PROGRESS")]


async def test_a_refused_line_is_reported_as_not_delivered(bridge, agent, monkeypatch):
    """The PC brain with neither the v2 keys nor legacy auth: the bridge answers 401 and the tool never claims a
    success it did not get."""
    monkeypatch.setattr(config, "bridge_url", "http://bridge.test")
    monkeypatch.setattr(config, "agent_token", None)
    monkeypatch.setattr(config, "legacy_token_auth", False)
    result = await planner.tool_report_plan_progress(2, 3, SECRET_ACTION)
    assert result["status"] == "degraded" and result["error_code"] == "UI_NOTIFY_FAILED"
    assert result["data"]["delivered"] is False and bridge.emitted == []
    assert len(agent.requests) == 1  # sent once, never retried (not a critical type)
    assert "2/3 IN_PROGRESS not delivered" in agent.log.text() and SECRET_ACTION not in agent.log.text()


async def test_a_probe_job_never_reaches_the_bridge(bridge, agent):
    token = probe_context.bridge_suppressed.set(True)
    try:
        result = await planner.tool_report_plan_progress(1, 3, SECRET_ACTION)
    finally:
        probe_context.bridge_suppressed.reset(token)
    assert result["error_code"] == "UI_SUPPRESSED" and result["data"]["delivered"] is False
    assert agent.requests == [] and bridge.emitted == []


# ---------------------------------------------------------------- a bridge that does not show the line (C-86 review)


async def test_a_bridge_without_the_route_is_never_a_success(bridge, agent, monkeypatch):
    """The review's scenario: the PC worker runs this code while the VPS bridge is still the one deployed before C-86.
    That bridge drops plan_progress as an 'UNKNOWN EVENT' and still answers 200 {status: 'ok'}; the tool used to tell
    the model 'Plan progress dashboard ko bhej diya'. Simulated with the real app by taking the route away."""
    monkeypatch.setattr(bridge.module, "PLAN_PROGRESS_EVENT", "plan_progress_route_not_deployed")
    monkeypatch.setattr(config, "bridge_url", "http://bridge.test")  # the PC brain: the signed public path
    result = await planner.tool_report_plan_progress(2, 3, SECRET_ACTION)
    assert "UNKNOWN EVENT" in bridge.log.text() and bridge.emitted == []  # dropped, answered ok
    assert result["status"] == "degraded" and result["error_code"] == "UI_NOTIFY_FAILED"
    assert result["data"]["delivered"] is False and "bhej diya" not in result["message"]
    assert len(agent.requests) == 1 and agent.requests[0].headers["x-agent-token"] == AGENT_TOKEN
    assert "2/3 IN_PROGRESS not delivered: the bridge did not confirm it (unconfirmed answer)" in agent.log.text()
    assert SECRET_ACTION not in agent.log.text() and SECRET_ACTION not in bridge.log.text()


@pytest.mark.parametrize("status, body, content_type", [
    (200, b"<html><body>JARVIS</body></html>", "text/html"),  # a misrouted URL: a proxy's own page
    (200, b'{"status": "ignored", "reason": "bad_payload"}', "application/json"),
    (200, b'"ok"', "application/json"),
])
async def test_an_answer_that_is_not_the_bridges_emit_is_never_a_success(monkeypatch, status, body, content_type):
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status, content=body, headers={"content-type": content_type})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))

    async def get_client() -> httpx.AsyncClient:
        return client

    monkeypatch.setattr(jarvis_bridge, "get_http_client", get_client)
    monkeypatch.setattr(config, "bridge_url", "https://jarvis.example/vortex")
    monkeypatch.setattr(config, "agent_token", AGENT_TOKEN)
    monkeypatch.setattr(config, "bridge_hmac_secret", HMAC_SECRET)
    try:
        result = await planner.tool_report_plan_progress(1, 3, SECRET_ACTION)
    finally:
        await client.aclose()
    assert result["status"] == "degraded" and result["error_code"] == "UI_NOTIFY_FAILED"
    assert result["data"]["delivered"] is False and len(requests) == 1  # sent once, never retried
