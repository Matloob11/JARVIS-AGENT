"""Plan task C-14: POST /notify authentication on the bridge (C-NOTIFY-1), the bridge_security helpers, the
notify signatures in jarvis_security, the agent link settings in jarvis_config (env-only origins), the logger's
role gate and the bridge's compose settings.

Every test uses throwaway credentials, a temporary bridge.db and a recorded sio.emit: nothing reaches a socket,
the live bridge or a real env file, and no real secret is read or printed.
"""
# pylint: disable=protected-access,redefined-outer-name
from __future__ import annotations

import asyncio
import hashlib
import hmac
import importlib
import json
import logging
import os
import secrets
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import yaml
from starlette.datastructures import Headers

from services.utils import jarvis_config, jarvis_logger, jarvis_security
from services.utils.jarvis_config import JarvisConfig, config
from src.core import bridge_security
from src.core.bridge_security import NonceCache, TokenBucket, check_origin, same_secret
from src.core.bridge_store import SEEN_NOTIFY, BridgeStore

ROOT = Path(__file__).resolve().parents[2]
AGENT_TOKEN = "c14-test-agent-token-" + "a" * 24
HMAC_SECRET = "c14-test-hmac-secret-" + "b" * 24
LEGACY_TOKEN = "c14-test-legacy-token-" + "c" * 24
OLD_FALLBACK_KEY = b"insecure_default_secret_token_change_immediately"


@pytest.fixture
async def bridge(monkeypatch, tmp_path):
    """ui_bridge with a temporary store, fresh volatile state and nonce cache, throwaway credentials and a
    recorded sio.emit (event, data, room)."""
    monkeypatch.setenv("BRIDGE_DB_PATH", str(tmp_path / "import-default.db"))
    module = importlib.import_module("src.core.ui_bridge")
    emitted: list[tuple[str, Any, Any]] = []

    async def fake_emit(event: str, data: Any = None, **kwargs: Any) -> None:
        emitted.append((event, data, kwargs.get("to") or kwargs.get("room")))

    store = BridgeStore(tmp_path / "bridge.db")
    monkeypatch.setattr(module, "store", store)
    monkeypatch.setattr(module, "bridge_manager", module.BridgeStateManager())
    monkeypatch.setattr(module, "_notify_nonces", NonceCache())
    monkeypatch.setattr(module, "VORTEX_SECURITY_TOKEN", LEGACY_TOKEN)
    monkeypatch.setattr(module.sio, "emit", fake_emit)
    monkeypatch.setattr(module.app.state, "notify_limiter", module.NotifyRateLimiter())
    monkeypatch.setattr(config, "agent_token", AGENT_TOKEN)
    monkeypatch.setattr(config, "bridge_hmac_secret", HMAC_SECRET)
    monkeypatch.setattr(config, "security_token", LEGACY_TOKEN)
    monkeypatch.setattr(config, "legacy_token_auth", True)
    monkeypatch.setattr(config, "env", "development")
    ns = SimpleNamespace(module=module, emitted=emitted, store=store)
    yield ns
    await store.close()


def _body(event_type: str, payload: Any) -> bytes:
    return json.dumps({"type": event_type, "payload": payload}, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _agent_headers(raw: bytes, *, token: str = AGENT_TOKEN, ts: Any = None, nonce: str | None = None,
                   signature: str | None = None) -> dict[str, str]:
    ts = str(int(time.time())) if ts is None else str(ts)
    nonce = secrets.token_hex(16) if nonce is None else nonce
    return {"Content-Type": "application/json", "X-Agent-Token": token, "X-Agent-Timestamp": ts,
            "X-Agent-Nonce": nonce,
            "X-Agent-Signature": jarvis_security.sign_notify(ts, nonce, raw) if signature is None else signature}


def _legacy_headers(raw: bytes, token: str = LEGACY_TOKEN) -> dict[str, str]:
    signature = jarvis_security.security_manager.generate_signature(json.dumps(json.loads(raw), sort_keys=True))
    return {"Content-Type": "application/json", "X-Vortex-Token": token, "X-Vortex-Signature": signature}


async def _post(ns: Any, raw: bytes, headers: dict[str, str]) -> httpx.Response:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ns.module.app),
                                 base_url="http://bridge.test") as client:
        return await client.post("/notify", content=raw, headers=headers)


def _contact(ns: Any) -> float:
    return ns.module.bridge_manager.state["last_runner_contact"]


def _transcription(text: str = "Salam Sir", msg_id: str = "v1") -> bytes:
    return _body("transcription", {"id": msg_id, "role": "agent", "text": text, "is_final": True})


# ---------------------------------------------------------------- POST /notify, agent HMAC v2


async def test_signed_notify_is_accepted_and_reaches_only_room_ui(bridge):
    before = _contact(bridge)
    raw = _transcription()
    resp = await _post(bridge, raw, _agent_headers(raw))

    assert resp.status_code == 200 and resp.json() == {"status": "ok"}
    assert [(event, room) for event, _data, room in bridge.emitted] == [
        ("new_message", "ui"), ("transcription_update", "ui")]
    assert bridge.emitted[0][1]["role"] == "assistant" and bridge.emitted[0][1]["text"] == "Salam Sir"
    assert _contact(bridge) > before


async def test_the_signature_covers_the_exact_raw_bytes(bridge):
    raw = ('{ "type" : "transcription", "payload": {"id": "u1", "role": "user",'
           ' "text": "سلام  Sir", "final": true} }').encode("utf-8")  # not canonical JSON: signed as sent
    assert (await _post(bridge, raw, _agent_headers(raw))).status_code == 200
    assert bridge.emitted[0][1]["text"] == "سلام  Sir"

    headers = _agent_headers(raw)
    tampered = raw.replace(b"Sir", b"Sit")
    resp = await _post(bridge, tampered, headers)
    assert resp.status_code == 403 and resp.json() == {"error": "bad_signature"}


@pytest.mark.parametrize("token", ["wrong-token", "", AGENT_TOKEN[:-1], LEGACY_TOKEN],
                         ids=["wrong", "empty", "truncated", "legacy-token"])
async def test_a_forged_agent_token_is_401_and_changes_nothing(bridge, token):
    before = _contact(bridge)
    raw = _transcription()
    resp = await _post(bridge, raw, _agent_headers(raw, token=token))

    assert resp.status_code == 401 and resp.json() == {"error": "bad_token"}
    assert not bridge.emitted and _contact(bridge) == before
    assert (await bridge.store.list_messages())[0] == []


async def test_a_bad_signature_is_403_and_changes_nothing(bridge):
    before = _contact(bridge)
    raw = _transcription()
    for signature in ("0" * 64, "zz", "", hmac.new(b"other-key", raw, hashlib.sha256).hexdigest()):
        resp = await _post(bridge, raw, _agent_headers(raw, signature=signature))
        assert resp.status_code == 403 and resp.json() == {"error": "bad_signature"}
    assert not bridge.emitted and _contact(bridge) == before


@pytest.mark.parametrize("skew", [-61, 61, -3600])
async def test_a_stale_or_future_timestamp_is_401(bridge, skew):
    raw = _transcription()
    resp = await _post(bridge, raw, _agent_headers(raw, ts=int(time.time()) + skew))
    assert resp.status_code == 401 and resp.json() == {"error": "stale"}
    assert not bridge.emitted


@pytest.mark.parametrize("ts", ["", "abc", "1.5", "-5", " 123", "1" * 13])
async def test_a_malformed_timestamp_is_401(bridge, ts):
    raw = _transcription()
    headers = _agent_headers(raw)
    headers["X-Agent-Timestamp"] = ts
    assert (await _post(bridge, raw, headers)).status_code == 401


async def test_a_timestamp_within_60_seconds_is_fine(bridge):
    for n, skew in enumerate((-55, 55)):
        raw = _transcription(msg_id=f"v{n}")
        assert (await _post(bridge, raw, _agent_headers(raw, ts=int(time.time()) + skew))).status_code == 200


@pytest.mark.parametrize("nonce", ["", "abc", "g" * 16, "a" * 15, "a" * 129])
async def test_a_malformed_nonce_is_401(bridge, nonce):
    raw = _transcription()
    resp = await _post(bridge, raw, _agent_headers(raw, nonce=nonce))
    assert resp.status_code == 401 and resp.json() == {"error": "bad_nonce"}


async def test_a_replayed_request_is_409_and_handled_once(bridge):
    raw = _transcription()
    headers = _agent_headers(raw)
    assert (await _post(bridge, raw, headers)).status_code == 200
    resp = await _post(bridge, raw, headers)

    assert resp.status_code == 409 and resp.json() == {"error": "replay"}
    assert [event for event, _data, _room in bridge.emitted].count("new_message") == 1


async def test_a_replay_after_a_bridge_restart_is_still_409(bridge, monkeypatch):
    raw = _transcription()
    headers = _agent_headers(raw)
    assert (await _post(bridge, raw, headers)).status_code == 200

    monkeypatch.setattr(bridge.module, "_notify_nonces", NonceCache())  # the in-memory cache is gone
    assert (await _post(bridge, raw, headers)).status_code == 409
    assert await bridge.store.mark_seen(SEEN_NOTIFY, headers["X-Agent-Nonce"]) is False


async def test_a_failing_store_still_blocks_replays_from_memory(bridge, monkeypatch):
    async def broken(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError("disk gone")

    monkeypatch.setattr(bridge.store, "mark_seen", broken)
    raw = _transcription()
    headers = _agent_headers(raw)
    assert (await _post(bridge, raw, headers)).status_code == 200
    assert (await _post(bridge, raw, headers)).status_code == 409


async def test_a_body_over_256_kb_is_413(bridge):
    raw = _body("vortex_log", {"text": "x" * (bridge_security.NOTIFY_MAX_BYTES + 1), "category": "SYSTEM"})
    resp = await _post(bridge, raw, _agent_headers(raw))
    assert resp.status_code == 413 and resp.json() == {"error": "too_large"}
    assert not bridge.emitted


async def test_a_body_over_the_read_limit_is_413_for_the_agent_and_401_for_anyone_else(bridge):
    raw = b"x" * (bridge_security.NOTIFY_READ_LIMIT + 10)
    resp = await _post(bridge, raw, _agent_headers(b"", signature="0" * 64))
    assert resp.status_code == 413 and resp.json() == {"error": "too_large"}
    assert (await _post(bridge, raw, _agent_headers(b"", token="nope"))).status_code == 401


async def test_a_client_that_disconnects_mid_body_gets_a_quiet_400(bridge):
    # Before C-14 FastAPI's body parsing answered this with 400. An escaping ClientDisconnect would be a 500 plus an
    # 'Exception in ASGI application' traceback in the bridge log for every attempt on the public port.
    before = _contact(bridge)
    raw = _transcription()
    headers = {**_agent_headers(raw), "Content-Length": str(len(raw))}
    incoming = [{"type": "http.request", "body": raw[:10], "more_body": True}, {"type": "http.disconnect"}]
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return incoming.pop(0) if incoming else {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "scheme": "http",
             "path": "/notify", "raw_path": b"/notify", "root_path": "", "query_string": b"",
             "headers": [(name.lower().encode(), value.encode()) for name, value in headers.items()],
             "client": ("192.0.2.7", 50000), "server": ("bridge.test", 80)}
    await bridge.module.app(scope, receive, send)  # raises when the disconnect escapes

    assert sent[0]["type"] == "http.response.start" and sent[0]["status"] == 400
    assert json.loads(b"".join(m.get("body", b"") for m in sent[1:])) == {"error": "client_disconnect"}
    assert not bridge.emitted and _contact(bridge) == before


async def test_without_agent_token_on_the_bridge_the_agent_link_is_off(bridge, monkeypatch):
    monkeypatch.setattr(config, "agent_token", None)
    raw = _transcription()
    for token in ("", "None", AGENT_TOKEN):
        assert (await _post(bridge, raw, _agent_headers(raw, token=token))).status_code == 401
    assert not bridge.emitted


async def test_production_without_hmac_secret_answers_503(bridge, monkeypatch):
    monkeypatch.setattr(config, "bridge_hmac_secret", None)
    monkeypatch.setattr(config, "env", "production")
    raw = _transcription()
    resp = await _post(bridge, raw, _agent_headers(raw, signature="0" * 64))
    assert resp.status_code == 503 and resp.json() == {"error": "agent_link_unconfigured"}
    assert not bridge.emitted


async def test_bad_json_after_auth_is_400(bridge):
    for raw in (b"not json", b"[1, 2]", b"\xff\xfe"):
        resp = await _post(bridge, raw, _agent_headers(raw))
        assert resp.status_code == 400 and resp.json() == {"error": "bad_body"}


async def test_an_unknown_type_still_answers_ok_and_a_missing_type_is_ignored(bridge):
    raw = _body("made_up_event", {"id": "c1"})
    assert (await _post(bridge, raw, _agent_headers(raw))).json() == {"status": "ok"}
    # C-29 routes confirm_request: an unusable one (no tool, no risk) is ignored, never shown as a dialog.
    raw = _body("confirm_request", {"id": "c1"})
    assert (await _post(bridge, raw, _agent_headers(raw))).json() == {"status": "ignored", "reason": "bad_payload"}
    raw = json.dumps({"payload": 1}).encode()
    assert (await _post(bridge, raw, _agent_headers(raw))).json() == {"status": "ignored",
                                                                      "reason": "missing_type"}


# ---------------------------------------------------------------- C-64: per-class rate buckets (K-39 v10)


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _limiter(bridge: Any, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, _Clock]:
    clock = _Clock()
    limiter = bridge_security.NotifyRateLimiter(clock=clock)
    monkeypatch.setattr(bridge.module.app.state, "notify_limiter", limiter)
    return limiter, clock


async def _signed(bridge: Any, event_type: str, payload: Any) -> httpx.Response:
    raw = _body(event_type, payload)
    return await _post(bridge, raw, _agent_headers(raw))


async def _drain(bridge: Any) -> None:
    """Lets the background log-batch broadcast tasks finish (C-64 LS-3: new_log emits run after the answer)."""
    while bridge.module._log_broadcasts:
        await asyncio.gather(*list(bridge.module._log_broadcasts))


def test_every_notify_type_has_its_rate_class():
    critical = {"transcription", "status", "thinking", "confirm_request", "settings_sync", "agent_heartbeat",
                "user_speaking_sync", "job_end", "turn_rejected", "voice_id_status", "task_alert"}  # C-44
    assert bridge_security.NOTIFY_CRITICAL_TYPES == critical
    assert {t: bridge_security.notify_class(t) for t in ("vortex_log", "vortex_log_batch")} == {
        "vortex_log": "logs", "vortex_log_batch": "logs"}
    for other in ("telemetry", "tool_sync", "vitals_pc", "audit_event", "memory_update", "nope", None, 7, {"a": 1}):
        assert bridge_security.notify_class(other) == "others"
    # C-64 review SP-3: the logs class is an item budget (25 items/s, burst 100), not a notify budget.
    assert bridge_security.NOTIFY_CLASS_RATES == {"critical": (50.0, 50.0), "logs": (25.0, 100.0),
                                                  "others": (20.0, 20.0)}


def test_a_log_batch_costs_one_token_per_item_and_everything_else_one():
    """C-64 review SP-3: 10 posts/s x 50 items was a 500 stored-rows/s amplifier; items are charged now."""
    cost = bridge_security.notify_cost
    assert cost("vortex_log_batch", {"items": [{"text": "x"}] * 7}) == 7.0
    assert cost("vortex_log_batch", {"items": [{"text": "x"}] * 500}) == 50.0  # the stored-item cap
    assert cost("vortex_log_batch", {"items": []}) == 1.0
    assert cost("vortex_log_batch", {"items": "nope"}) == 1.0
    assert cost("vortex_log_batch", "nope") == 1.0
    assert cost("vortex_log", {"text": "x"}) == 1.0
    assert cost("transcription", {"items": [1] * 99}) == 1.0


async def test_a_full_class_answers_429_with_its_class_and_retry_after(bridge, monkeypatch):
    _limiter(bridge, monkeypatch)
    for _ in range(2):  # 2 x 50 items: the whole 100-token burst
        assert (await _signed(bridge, "vortex_log_batch",
                              {"items": [{"text": "l"}] * 50})).status_code == 200
    resp = await _signed(bridge, "vortex_log", {"text": "one too many", "category": "SYSTEM"})
    assert resp.status_code == 429
    assert resp.json() == {"error": "rate_limited", "class": "logs"}
    assert int(resp.headers["Retry-After"]) == 1  # whole seconds, at least 1
    await _drain(bridge)
    assert not [event for event, _data, _room in bridge.emitted if event == "new_log" and _data.get("text") ==
                "one too many"]


async def test_a_log_flood_never_throttles_a_transcript_or_other_events(bridge, monkeypatch):
    _limiter(bridge, monkeypatch)
    statuses = [(await _signed(bridge, "vortex_log_batch", {"items": [{"text": "x"}] * 50})).status_code
                for _ in range(15)]
    assert statuses.count(429) == 13  # 2 x 50 items pass, the flood is refused per item (SP-3)
    for n in range(3):
        assert (await _signed(bridge, "transcription", {"id": f"t{n}", "role": "user", "text": "hi"})).status_code \
            == 200
    assert (await _signed(bridge, "memory_update", {"id": "m", "content": "x"})).status_code == 200


async def test_the_buckets_refill_and_the_critical_class_takes_fifty_a_second(bridge, monkeypatch):
    _, clock = _limiter(bridge, monkeypatch)
    codes = [(await _signed(bridge, "status", "START")).status_code for _ in range(51)]
    assert codes[:50] == [200] * 50 and codes[50] == 429
    clock.now += 0.1  # 5 tokens back
    assert [(await _signed(bridge, "thinking", "STOP")).status_code for _ in range(6)] == [200] * 5 + [429]


async def test_a_missing_type_counts_against_the_others_bucket(bridge, monkeypatch):
    limiter, _clock = _limiter(bridge, monkeypatch)
    raw = json.dumps({"payload": 1}).encode()
    for _ in range(20):
        assert (await _post(bridge, raw, _agent_headers(raw))).json() == {"status": "ignored",
                                                                          "reason": "missing_type"}
    resp = await _post(bridge, raw, _agent_headers(raw))
    assert resp.status_code == 429 and resp.json()["class"] == "others"
    assert limiter.buckets["critical"]._tokens == 50.0  # untouched


async def test_refusals_are_logged_once_a_minute_per_class_with_their_count(bridge, monkeypatch):
    _, clock = _limiter(bridge, monkeypatch)
    warnings: list[str] = []
    monkeypatch.setattr(bridge.module.logger, "warning", lambda msg, *args: warnings.append(msg % args))
    full = {"items": [{"text": "x"}] * 50}
    for _ in range(5):
        await _signed(bridge, "vortex_log_batch", full)  # 2 pass, 3 refused: the first one is reported at once
    assert warnings == ["⚠️ /notify rate limit: 1 'logs' notify(s) answered 429 since the last report"]
    clock.now += 30.0
    await _signed(bridge, "vortex_log", {"text": "x"})
    assert len(warnings) == 1  # the other 2 wait for the minute
    clock.now += 31.0
    assert (await _signed(bridge, "vortex_log", {"text": "x"})).status_code == 200  # any notify reports them
    assert warnings[1:] == ["⚠️ /notify rate limit: 2 'logs' notify(s) answered 429 since the last report"]
    await _drain(bridge)


async def test_the_vitals_loop_reports_a_floods_last_window_without_another_notify(bridge, monkeypatch):
    """C-64 review COR-2: the refusals of a burst's last minute are logged even when the sender dies."""
    limiter, clock = _limiter(bridge, monkeypatch)
    warnings: list[str] = []
    monkeypatch.setattr(bridge.module.logger, "warning", lambda msg, *args: warnings.append(msg % args))
    for _ in range(4):
        await _signed(bridge, "vortex_log_batch", {"items": [{"text": "x"}] * 50})  # 2 pass, 2 refused
    assert len(warnings) == 1  # the first refusal is reported at once; the second waits for the minute
    clock.now += 61.0
    bridge.module._report_notify_refusals(limiter)  # what vitals_heartbeat now does every 12 s
    assert warnings[1:] == ["⚠️ /notify rate limit: 1 'logs' notify(s) answered 429 since the last report"]
    source = (ROOT / "src" / "core" / "ui_bridge.py").read_text(encoding="utf-8")
    heartbeat = source.partition("async def vitals_heartbeat")[2].partition("async def telemetry_heartbeat")[0]
    assert "_report_notify_refusals(app.state.notify_limiter)" in heartbeat
    await _drain(bridge)


def test_the_limiter_reports_each_class_on_its_own():
    clock = _Clock()
    limiter = bridge_security.NotifyRateLimiter(clock=clock)
    for _ in range(25):
        limiter.check("telemetry")
    for _ in range(4):
        limiter.check("vortex_log_batch", cost=50.0)  # 2 x 50 items pass, 2 batches refused
    assert sorted(limiter.refusal_report()) == [("logs", 2), ("others", 5)]
    assert limiter.refusal_report() == []
    assert limiter.check("transcription") == ("critical", None)
    name, retry_after = limiter.check("vortex_log")
    assert name == "logs" and retry_after == pytest.approx(1.0 / 25.0)


# ---------------------------------------------------------------- C-64: vortex_log_batch and audit_event (K-40 v10)


async def test_a_log_batch_is_normalized_stored_and_emitted_line_by_line(bridge):
    items = [
        {"text": "Worker started", "category": "AGENT", "level": "INFO", "ts": 1759550000.5, "extra": "dropped"},
        {"text": "x" * 2500, "category": "C" * 60, "level": "warning", "ts": 1759550001000},  # ms, long fields
        {"text": 42, "level": "LOUD", "ts": "soon"},  # odd fields: defaults
        "not an object",
    ]
    payload = {"items": items, "dropped": 3, "source": "desktop"}
    resp = await _signed(bridge, "vortex_log_batch", payload)
    assert resp.status_code == 200 and resp.json() == {"status": "ok"}
    await _drain(bridge)

    logs = [data for event, data, room in bridge.emitted if event == "new_log" and room == "ui"]
    assert [log["text"] for log in logs] == ["Worker started", "x" * 2000, "42"]
    first, second, third = logs
    assert first == {"text": "Worker started", "category": "AGENT", "level": "INFO", "ts": 1759550000.5,
                     "timestamp": 1759550000.5, "source": "desktop"}
    assert second["category"] == "C" * 40 and second["level"] == "WARNING" and second["ts"] == 1759550001.0
    assert "level" not in third and third["category"] == "SYSTEM" and third["ts"] == third["timestamp"]
    dropped = [data for event, data, _room in bridge.emitted if event == "new_logs"]
    assert len(dropped) == 1 and dropped[0]["items"] == [] and dropped[0]["dropped"] == 4  # 3 + the string
    assert dropped[0]["source"] == "desktop"
    stored = await bridge.store.recent_events("log")
    assert [row["text"] for row in stored] == ["Worker started", "x" * 2000, "42"]


async def test_items_past_fifty_count_as_dropped_and_init_state_keeps_the_last_30(bridge):
    items = [{"text": f"line {n}", "level": "ERROR"} for n in range(52)]
    assert (await _signed(bridge, "vortex_log_batch", {"items": items})).json() == {"status": "ok"}
    await _drain(bridge)
    logs = [data for event, data, _room in bridge.emitted if event == "new_log"]
    assert len(logs) == 50 and logs[-1]["text"] == "line 49" and all(log["source"] == "agent" for log in logs)
    [dropped] = [data for event, data, _room in bridge.emitted if event == "new_logs"]
    assert dropped["dropped"] == 2
    state = await bridge.module.build_init_state()
    assert [log["text"] for log in state["logs"]] == [f"line {n}" for n in range(20, 50)]
    assert state["vortex_logs"] == state["logs"]


async def test_a_batch_without_lines_or_drops_emits_nothing_and_a_bad_payload_is_ignored(bridge):
    assert (await _signed(bridge, "vortex_log_batch", {"items": [], "dropped": 0})).json() == {"status": "ok"}
    assert (await _signed(bridge, "vortex_log_batch", {"items": "nope", "dropped": -5})).json() == {"status": "ok"}
    await _drain(bridge)
    assert not [event for event, _data, _room in bridge.emitted if event in ("new_log", "new_logs")]
    for bad in (["a list"], "text", None, 7):
        assert (await _signed(bridge, "vortex_log_batch", bad)).json() == {"status": "ignored",
                                                                           "reason": "bad_payload"}
    assert await bridge.store.recent_events("log") == []


async def test_numbers_no_float_can_hold_never_break_a_batch_or_an_audit_event(bridge):
    huge = 10 ** 400  # JSON numbers have no limit
    payload = {"items": [{"text": "big ts", "ts": huge}], "dropped": huge}
    assert (await _signed(bridge, "vortex_log_batch", payload)).json() == {"status": "ok"}
    await _drain(bridge)
    [log] = [data for event, data, _room in bridge.emitted if event == "new_log"]
    [dropped] = [data for event, data, _room in bridge.emitted if event == "new_logs"]
    assert log["ts"] == log["timestamp"] > 1.7e9 and dropped["dropped"] == 1_000_000_000
    bridge.emitted.clear()
    assert (await _signed(bridge, "audit_event", {"action": "A", "ts": huge})).json() == {"status": "ok"}
    [(_event, data, _room)] = bridge.emitted
    assert data["ts"] > 1.7e9
    raw = _body("transcription", {"id": "t-huge", "role": "user", "text": "hi", "ts": huge})
    assert (await _post(bridge, raw, _agent_headers(raw))).status_code == 200  # the message path too


async def test_a_dropped_count_alone_feeds_the_ui_counter(bridge):
    assert (await _signed(bridge, "vortex_log_batch", {"items": [], "dropped": 7, "source": "nobody"})).json() == {
        "status": "ok"}
    await _drain(bridge)
    [(event, data, room)] = bridge.emitted
    assert (event, room) == ("new_logs", "ui")
    assert data["items"] == [] and data["dropped"] == 7 and data["source"] == "agent" and data["ts"] > 0


async def test_control_characters_in_a_log_item_collapse_to_spaces(bridge):
    """C-64 review SP-2: a crafted multi-line or ANSI-escape message cannot render as extra, fake entries in
    the UI log panel or sit in bridge.db with raw control bytes."""
    sneaky = "x\n2026-10-06 00:00:00 - SECURITY - CRITICAL - threat cleared\x1b[2Jy\x00\x7fz"
    payload = {"items": [{"text": sneaky, "category": "A\nB"}]}
    assert (await _signed(bridge, "vortex_log_batch", payload)).json() == {"status": "ok"}
    await _drain(bridge)
    [log] = [data for event, data, _room in bridge.emitted if event == "new_log"]
    assert log["text"] == "x 2026-10-06 00:00:00 - SECURITY - CRITICAL - threat cleared [2Jy z"
    assert log["category"] == "A B"
    [row] = await bridge.store.recent_events("log")
    assert row["text"] == log["text"]


async def test_the_log_feed_is_capped_at_bridge_log_max_rows(bridge, monkeypatch):
    """C-64 review LS-2: the EVENT_LOG table stays bounded even with BRIDGE_EVENT_RETENTION_DAYS=0; other
    kinds keep the user's retention decision."""
    from src.core import bridge_store  # pylint: disable=import-outside-toplevel
    monkeypatch.delenv("BRIDGE_LOG_MAX_ROWS", raising=False)
    assert bridge_store.log_max_rows() == bridge_store.DEFAULT_LOG_MAX_ROWS == 20_000
    monkeypatch.setenv("BRIDGE_LOG_MAX_ROWS", "0")
    assert bridge_store.log_max_rows() == 0  # the cap can be switched off, like the day retention
    monkeypatch.setenv("BRIDGE_LOG_MAX_ROWS", "nope")
    assert bridge_store.log_max_rows() == bridge_store.DEFAULT_LOG_MAX_ROWS  # invalid: the bounded default
    store = bridge.store
    await store.append_events("log", [{"text": f"l{n}"} for n in range(12)])
    await store.append_event("memory", {"id": "m"})
    assert await store.cap_events("log", 0) == 0  # no cap: nothing deleted
    assert await store.cap_events("log", 5) == 7
    assert [row["text"] for row in await store.recent_events("log", 100)] == [f"l{n}" for n in range(7, 12)]
    assert await store.cap_events("log", 5) == 0  # already at the cap
    assert len(await store.recent_events("memory", 100)) == 1  # other kinds untouched
    source = (ROOT / "src" / "core" / "ui_bridge.py").read_text(encoding="utf-8")
    loop = source.partition("async def store_retention_loop")[2].partition("async def _expire_ui_sessions")[0]
    assert "cap_events(EVENT_LOG, log_cap)" in loop  # the hourly loop applies it


async def test_the_batch_broadcast_runs_off_the_notify_request(bridge, monkeypatch):
    """C-64 review LS-3: the HTTP answer never waits for up to 50 serialized emits per UI socket; the per-item
    new_log emits run as one background task per batch."""
    module = bridge.module
    gate = asyncio.Event()
    slow: list[tuple[str, Any]] = []

    async def slow_emit(event: str, data: Any = None, **_kwargs: Any) -> None:
        await gate.wait()
        slow.append((event, data))

    monkeypatch.setattr(module, "emit_ui", slow_emit)
    answer = await module._handle_log_batch({"items": [{"text": f"l{n}"} for n in range(3)], "dropped": 1})
    assert answer == {"status": "ok"}  # answered while every emit still waits on the gate
    assert slow == [] and len(module._log_broadcasts) == 1
    assert [row["text"] for row in await bridge.store.recent_events("log")] == ["l0", "l1", "l2"]  # stored first
    gate.set()
    await _drain(bridge)
    assert [event for event, _data in slow] == ["new_log"] * 3 + ["new_logs"]
    assert slow[-1][1]["dropped"] == 1 and module._log_broadcasts == set()


async def test_the_single_vortex_log_is_kept_as_before(bridge):
    payload = {"text": "worker ready", "category": "SYSTEM", "timestamp": 1759550000.0}
    assert (await _signed(bridge, "vortex_log", payload)).json() == {"status": "ok"}
    assert [(event, data) for event, data, _room in bridge.emitted] == [("new_log", payload)]
    assert await bridge.store.recent_events("log") == [payload]


async def test_an_audit_event_becomes_audit_update_and_is_not_stored(bridge):
    payload = {"action": "THREAT_DETECTED" + "!" * 80, "detail": "SOURCE=TypedChat:ui | DESC=" + "d" * 400,
               "severity": "critical", "ts": 1759550000.25, "extra": "dropped"}
    assert (await _signed(bridge, "audit_event", payload)).json() == {"status": "ok"}
    [(event, data, room)] = bridge.emitted
    assert (event, room) == ("audit_update", "ui")
    assert data == {"action": ("THREAT_DETECTED" + "!" * 80)[:64], "detail": payload["detail"][:300],
                    "severity": "CRITICAL", "ts": 1759550000.25}
    bridge.emitted.clear()
    assert (await _signed(bridge, "audit_event", {"action": "X\u0000Y", "severity": "LOUD"})).json() == {
        "status": "ok"}
    [(_event, data, _room)] = bridge.emitted
    assert data["action"] == "XY" and data["detail"] == "" and data["severity"] == "INFO" and data["ts"] > 0
    bridge.emitted.clear()
    for bad in ({"detail": "no action"}, "text", None):
        assert (await _signed(bridge, "audit_event", bad)).json() == {"status": "ok"}
    assert not bridge.emitted
    assert await bridge.store.recent_events("log") == []
    assert await bridge.store.recent_audit() == []


async def test_the_agent_batch_helper_caps_items_and_counts_the_rest(bridge, agent_code):
    await agent_code.notify_log_batch([{"text": f"n{n}", "level": "INFO", "category": "AGENT", "ts": 1.0e9}
                                       for n in range(55)], dropped=2, source="svc")
    await _drain(bridge)
    logs = [data for event, data, _room in bridge.emitted if event == "new_log"]
    assert len(logs) == 50 and {log["source"] for log in logs} == {"svc"}
    [dropped] = [data for event, data, _room in bridge.emitted if event == "new_logs"]
    assert dropped["dropped"] == 7


# ---------------------------------------------------------------- legacy headers (LEGACY_TOKEN_AUTH)


async def test_the_legacy_headers_work_while_the_flag_is_on(bridge):
    raw = _transcription()
    resp = await _post(bridge, raw, _legacy_headers(raw))
    assert resp.status_code == 200 and resp.json() == {"status": "ok"}
    assert [(event, room) for event, _data, room in bridge.emitted][0] == ("new_message", "ui")


async def test_the_legacy_headers_are_refused_once_the_flag_is_off(bridge, monkeypatch):
    monkeypatch.setattr(config, "legacy_token_auth", False)
    before = _contact(bridge)
    raw = _transcription()
    resp = await _post(bridge, raw, _legacy_headers(raw))
    assert resp.status_code == 401 and resp.json() == {"error": "bad_token"}
    assert not bridge.emitted and _contact(bridge) == before
    assert (await _post(bridge, raw, _agent_headers(raw))).status_code == 200  # v2 is unaffected


async def test_legacy_forgeries_get_the_same_codes_as_before(bridge):
    before = _contact(bridge)
    raw = _transcription()
    assert (await _post(bridge, raw, _legacy_headers(raw, token="wrong"))).status_code == 401
    headers = _legacy_headers(raw)
    del headers["X-Vortex-Signature"]
    resp = await _post(bridge, raw, headers)
    assert resp.status_code == 401 and resp.json() == {"error": "missing_signature"}
    headers = _legacy_headers(raw)
    headers["X-Vortex-Signature"] = "0" * 64
    assert (await _post(bridge, raw, headers)).status_code == 403
    assert not bridge.emitted and _contact(bridge) == before


@pytest.fixture
async def agent_code(bridge, monkeypatch):
    """Today's PC agent code (services/utils/jarvis_bridge.py, unchanged by C-14) posting into this bridge app."""
    from services.utils import jarvis_bridge  # pylint: disable=import-outside-toplevel

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=bridge.module.app), base_url="http://bridge.test")

    async def shared_client() -> httpx.AsyncClient:
        return client

    monkeypatch.setattr(jarvis_bridge, "get_http_client", shared_client)
    monkeypatch.setattr(config, "bridge_url", "http://bridge.test")
    yield jarvis_bridge
    await client.aclose()


async def test_todays_pc_agent_notify_code_still_works(bridge, agent_code):
    await agent_code.notify_transcription("agent", "Ji Sir", msg_id="pc-1")
    await agent_code.notify_log("worker ready", "SYSTEM")
    await agent_code.notify_event("persona_change", "anna")
    await agent_code.notify_ui("START")

    events = [(event, room) for event, _data, room in bridge.emitted]
    for expected in ("new_message", "transcription_update", "new_log", "persona_update", "status_change"):
        assert (expected, "ui") in events, expected
    assert (await bridge.store.list_messages())[0][0]["text"] == "Ji Sir"


async def test_the_confirm_flow_poster_gets_ok_and_the_owner_ui_count(bridge, agent_code):
    """C-29 (K-14): the broker's signed confirm_request is stored and answered with ui_clients; one without a risk
    is ignored, so the broker's tries fail and it refuses the action instead of waiting for a dialog that never
    shows."""
    from services.ai_core import jarvis_confirm  # pylint: disable=import-outside-toplevel

    request = {"id": "c" * 32, "tool": "t", "summary": "s", "risk": "high", "expires_at": time.time() + 60}
    assert await jarvis_confirm._post_to_bridge("confirm_request", request) == {"status": "ok", "ui_clients": 0}
    answer = await jarvis_confirm._post_to_bridge("confirm_request", {"id": "c-1", "tool": "t", "summary": "s"})
    assert answer == {"status": "ignored", "reason": "bad_payload"}


async def test_no_headers_at_all_is_401(bridge):
    raw = _transcription()
    assert (await _post(bridge, raw, {"Content-Type": "application/json"})).status_code == 401


async def test_legacy_header_endpoints_follow_the_flag(bridge, monkeypatch):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=bridge.module.app),
                                 base_url="http://bridge.test") as client:
        assert (await client.get("/messages", headers={"X-Vortex-Token": LEGACY_TOKEN})).status_code == 200
        assert (await client.get("/messages", headers={"X-Vortex-Token": "None"})).status_code == 401
        monkeypatch.setattr(bridge.module, "VORTEX_SECURITY_TOKEN", "")  # unset: nothing matches it
        for token in ("", "None"):
            assert (await client.get("/messages", headers={"X-Vortex-Token": token})).status_code == 401
        monkeypatch.setattr(bridge.module, "VORTEX_SECURITY_TOKEN", LEGACY_TOKEN)
        monkeypatch.setattr(config, "legacy_token_auth", False)
        assert (await client.get("/messages", headers={"X-Vortex-Token": LEGACY_TOKEN})).status_code == 401
        assert (await client.get("/livekit/token", headers={"X-Vortex-Token": LEGACY_TOKEN})).status_code == 401
        assert (await client.post("/feedback", json={}, headers={"X-Vortex-Token": LEGACY_TOKEN})).status_code == 401


# ---------------------------------------------------------------- bridge_security helpers


def test_same_secret_never_matches_an_unset_credential():
    assert same_secret("abc", "abc") and not same_secret("abc", "abd")
    assert not same_secret("", "") and not same_secret("None", None) and not same_secret(None, "abc")
    assert not same_secret(5, "5") and not same_secret("ü-token", "u-token")


def test_nonce_cache_forgets_after_its_ttl_and_stays_bounded():
    now = [0.0]
    cache = NonceCache(ttl_s=300, max_items=3, clock=lambda: now[0])
    assert cache.add("a") is True and cache.add("a") is False
    now[0] = 299.9
    assert cache.add("a") is False
    now[0] = 300.1
    assert cache.add("a") is True  # expired, so new again
    for nonce in "bcd":
        assert cache.add(nonce)
    assert len(cache) == 3  # the oldest went first


def test_token_bucket_rate_burst_and_retry_after():
    now = [0.0]
    bucket = TokenBucket(10, clock=lambda: now[0])
    assert all(bucket.take() for _ in range(10)) and bucket.take() is False
    assert bucket.retry_after() == pytest.approx(0.1)
    now[0] = 0.1
    assert bucket.take() is True and bucket.take() is False
    frames = TokenBucket(1.0, clock=lambda: now[0])
    assert frames.take() and not frames.take()
    now[0] = 1.1
    assert frames.take()
    with pytest.raises(ValueError):
        TokenBucket(0)


def test_check_origin_for_cookie_posts():
    allowed = ["https://jarvis.example", "http://localhost:5173"]
    assert check_origin({"origin": "https://jarvis.example"}, allowed)
    assert check_origin(Headers({"Origin": "https://jarvis.example/"}), allowed)
    assert not check_origin({"origin": "https://evil.example"}, allowed)
    assert not check_origin({"origin": "null"}, allowed)
    assert not check_origin({"origin": "https://evil.example", "referer": "https://jarvis.example/"}, allowed)
    assert check_origin({"referer": "http://localhost:5173/settings?tab=1"}, allowed)
    assert not check_origin({"referer": "https://evil.example/https://jarvis.example"}, allowed)
    assert not check_origin({"referer": "not a url"}, allowed)
    assert not check_origin({}, allowed)


# ---------------------------------------------------------------- notify signatures (jarvis_security)


def test_sign_and_verify_notify_round_trip(monkeypatch):
    monkeypatch.setattr(config, "bridge_hmac_secret", HMAC_SECRET)
    raw = b'{"type":"qa_ping","payload":{"n":1}}'
    signature = jarvis_security.sign_notify(1_790_000_000, "a" * 16, raw)
    expected = hmac.new(HMAC_SECRET.encode(), b"1790000000." + b"a" * 16 + b"." + raw, hashlib.sha256)
    assert signature == expected.hexdigest()  # the C-NOTIFY-1 formula
    assert jarvis_security.verify_notify("1790000000", "a" * 16, raw, signature.upper())
    for bad in (None, "", "0" * 64, signature[:-1], signature + "0", "é" * 64):
        assert jarvis_security.verify_notify("1790000000", "a" * 16, raw, bad) is False
    assert not jarvis_security.verify_notify("1790000001", "a" * 16, raw, signature)


def test_dev_without_hmac_secret_uses_one_random_key_per_process(monkeypatch, caplog):
    monkeypatch.setattr(config, "bridge_hmac_secret", None)
    monkeypatch.setattr(config, "env", "development")
    monkeypatch.setattr(jarvis_security, "_dev_notify_key", [])
    monkeypatch.setattr(jarvis_security, "_key_warnings", set())
    caplog.set_level(logging.CRITICAL, logger="JARVIS-SECURITY")

    signature = jarvis_security.sign_notify("1", "a" * 16, b"{}")
    assert jarvis_security.verify_notify("1", "a" * 16, b"{}", signature)
    assert jarvis_security.sign_notify("1", "a" * 16, b"{}") == signature
    criticals = [r.getMessage() for r in caplog.records if r.levelno == logging.CRITICAL]
    assert len(criticals) == 1 and "BRIDGE_HMAC_SECRET" in criticals[0]

    monkeypatch.setattr(jarvis_security, "_dev_notify_key", [])  # another process: another key
    assert not jarvis_security.verify_notify("1", "a" * 16, b"{}", signature)


def test_production_without_hmac_secret_refuses_to_sign(monkeypatch):
    monkeypatch.setattr(config, "bridge_hmac_secret", None)
    monkeypatch.setattr(config, "env", "production")
    with pytest.raises(RuntimeError, match="BRIDGE_HMAC_SECRET"):
        jarvis_security.sign_notify("1", "a" * 16, b"{}")
    with pytest.raises(RuntimeError):
        jarvis_security.verify_notify("1", "a" * 16, b"{}", "0" * 64)


def test_the_legacy_signature_has_no_fallback_key(monkeypatch):
    monkeypatch.setattr(config, "security_token", None)
    monkeypatch.setattr(jarvis_security, "_key_warnings", set())
    manager = jarvis_security.security_manager
    with pytest.raises(RuntimeError, match="VORTEX_SECURITY_TOKEN"):
        manager.generate_signature("{}")
    old_default = hmac.new(OLD_FALLBACK_KEY, b"{}", hashlib.sha256).hexdigest()
    assert manager.verify_signature("{}", old_default) is False
    assert b"insecure_default" not in (ROOT / "services" / "utils" / "jarvis_security.py").read_bytes()

    monkeypatch.setattr(config, "security_token", LEGACY_TOKEN)
    good = manager.generate_signature("{}")
    assert manager.verify_signature("{}", good) and not manager.verify_signature("{}", None)


# ---------------------------------------------------------------- config, logger, deploy settings


@pytest.fixture
def make_config(monkeypatch, tmp_path):
    """make_config(**env) -> a fresh JarvisConfig that sees only these variables of interest (no .env read)."""
    for name in ("AGENT_TOKEN", "BRIDGE_HMAC_SECRET", "LEGACY_TOKEN_AUTH", "ALLOWED_ORIGINS", "BRIDGE_DB_PATH",
                 "VORTEX_ENV", "GOOGLE_API_KEY", "VORTEX_SECURITY_TOKEN", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(jarvis_config, "load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.setenv("JARVIS_SHARED_DIR", str(tmp_path / "shared"))

    def build(**env: str) -> JarvisConfig:
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        return JarvisConfig()

    return build


def test_agent_link_settings_come_from_the_environment(make_config):
    cfg = make_config()
    assert (cfg.agent_token, cfg.bridge_hmac_secret, cfg.legacy_token_auth) == (None, None, True)
    assert cfg.bridge_db_path == os.path.join(cfg.project_root, "data", "bridge.db")
    cfg = make_config(AGENT_TOKEN=" c14-agent ", BRIDGE_HMAC_SECRET="c14-hmac", LEGACY_TOKEN_AUTH="0",
                      BRIDGE_DB_PATH="/app/data/bridge.db")
    assert (cfg.agent_token, cfg.bridge_hmac_secret, cfg.legacy_token_auth) == ("c14-agent", "c14-hmac", False)
    assert cfg.bridge_db_path == "/app/data/bridge.db"


def test_allowed_origins_come_only_from_the_environment(make_config):
    dev = ["http://localhost:5173", "http://127.0.0.1:5173"]
    assert make_config().allowed_origins == dev
    assert make_config(ALLOWED_ORIGINS=" , ").allowed_origins == dev
    cfg = make_config(ALLOWED_ORIGINS=" https://jarvis.example/ ,http://127.0.0.1:5173,,https://jarvis.example")
    assert cfg.allowed_origins == ["https://jarvis.example", "http://127.0.0.1:5173"]
    source = (ROOT / "src" / "core" / "ui_bridge.py").read_text(encoding="utf-8")
    assert "vercel.app" not in source and "print(" not in source


def test_the_bridge_needs_nothing_new_with_todays_vps_env(make_config):
    # Today's VPS env: production, the LiveKit keys and the legacy token; no agent link keys yet.
    cfg = make_config(VORTEX_ENV="production", LIVEKIT_API_KEY="k", LIVEKIT_API_SECRET="s",
                      VORTEX_SECURITY_TOKEN="t")
    assert cfg.validate("bridge") is True
    source = (ROOT / "src" / "core" / "ui_bridge.py").read_text(encoding="utf-8")
    assert "config.validate(ROLE_BRIDGE)" in source


@pytest.mark.parametrize(("role", "attached"), [(None, True), ("agent", True), ("bridge", False),
                                                ("svc", False), ("desktop", False)])
def test_only_the_agent_forwards_its_logs_to_the_ui(monkeypatch, tmp_path, role, attached):
    if role is None:
        monkeypatch.delenv("JARVIS_ROLE", raising=False)
    else:
        monkeypatch.setenv("JARVIS_ROLE", role)
    logger = jarvis_logger.setup_logger(f"JARVIS-TEST-C14-ROLE-{role}", tmp_path / "role.log")
    try:
        assert any(isinstance(h, jarvis_logger.UIBridgeHandler) for h in logger.handlers) is attached
    finally:
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)


def test_compose_runs_the_bridge_role_with_env_only_origins():
    compose = yaml.safe_load((ROOT / "deploy" / "hostinger" / "docker-compose.yml").read_text(encoding="utf-8"))
    bridge = compose["services"]["ui-bridge"]
    assert bridge["env_file"] == [".env", {"path": "bridge.env", "required": False}]
    env = bridge["environment"]
    assert env["JARVIS_ROLE"] == "bridge"
    assert env["ALLOWED_ORIGINS"] == ("https://jarvis.srv1802669.hstgr.cloud,http://127.0.0.1:5173,"
                                      "http://localhost:5173")
    assert (env["COOKIE_PATH"], str(env["COOKIE_SECURE"]), env["BRIDGE_DB_PATH"]) == (
        "/vortex", "1", "/app/data/bridge.db")
    assert "UI_BRIDGE_URL" not in env
    assert "187.127.123.235" not in json.dumps(env)
