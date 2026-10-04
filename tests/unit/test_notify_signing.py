"""Plan task C-15: the agent side of C-NOTIFY-1 (K-39) in services/utils/jarvis_bridge.py.

notify_event() signs exactly the bytes it sends (agent link v2), retries the critical types from a bounded queue
with backoff, in the order they were sent, and keeps the legacy headers when the v2 keys are missing. The interop
tests post into the real bridge app (src/core/ui_bridge.py, in process through httpx.ASGITransport) with a
temporary bridge.db; the event-loop tests run jobs on their own loops in threads, as LiveKit does on Windows.

Every credential is a throwaway value, every HTTP client is a mock or the in-process bridge, and the backoff wait
is recorded instead of slept where a test would otherwise wait: nothing reaches the network, the live bridge or a
real env file.
"""
# pylint: disable=protected-access,redefined-outer-name
from __future__ import annotations

import asyncio
import gc
import hashlib
import hmac
import importlib
import json
import logging
import re
import threading
import time
import weakref
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from starlette.requests import Request

from services.utils import jarvis_bridge, jarvis_security
from services.utils.jarvis_config import config
from src.core import bridge_security
from src.core.bridge_security import NonceCache, NotifyAuthError
from src.core.bridge_store import BridgeStore

AGENT_TOKEN = "c15-test-agent-token-" + "a" * 24
HMAC_SECRET = "c15-test-hmac-secret-" + "b" * 24
LEGACY_TOKEN = "c15-test-legacy-token-" + "c" * 24
SECRETS = (AGENT_TOKEN, HMAC_SECRET, LEGACY_TOKEN)
REAL_WAIT = jarvis_bridge._wait_before_retry


@pytest.fixture
def link(monkeypatch):
    """Agent link v2 on with throwaway keys, fresh warning and queue state, and a backoff wait that is recorded and
    returns at once (so no test sleeps through 1..30 s)."""
    monkeypatch.setattr(config, "agent_token", AGENT_TOKEN)
    monkeypatch.setattr(config, "bridge_hmac_secret", HMAC_SECRET)
    monkeypatch.setattr(config, "security_token", LEGACY_TOKEN)
    monkeypatch.setattr(config, "env", "development")
    monkeypatch.setattr(config, "bridge_url", "http://bridge.test/vortex")
    monkeypatch.setattr(jarvis_bridge, "_link_logged", set())
    monkeypatch.setattr(jarvis_bridge, "_warn_state", {"last": float("-inf"), "dropped": 0})
    monkeypatch.setattr(jarvis_bridge, "_queues", type(jarvis_bridge._queues)())
    waits: list[float] = []

    async def no_wait(queue: Any) -> None:
        waits.append(queue.wait_s)
        await asyncio.sleep(0)

    monkeypatch.setattr(jarvis_bridge, "_wait_before_retry", no_wait)
    return SimpleNamespace(waits=waits)


@pytest.fixture
def bridge_log(caplog):
    """Records of the JARVIS-BRIDGE logger, DEBUG included (setup_logger loggers do not propagate to caplog)."""
    logger = jarvis_bridge.logger
    old_level = logger.level
    logger.addHandler(caplog.handler)
    logger.setLevel(logging.DEBUG)
    yield caplog
    logger.removeHandler(caplog.handler)
    logger.setLevel(old_level)


def _hold(monkeypatch: pytest.MonkeyPatch) -> asyncio.Event:
    """The flusher waits before every retry until the returned event is set."""
    gate = asyncio.Event()

    async def held(_queue: Any) -> None:
        await gate.wait()

    monkeypatch.setattr(jarvis_bridge, "_wait_before_retry", held)
    return gate


class Bridge:
    """A stand-in bridge behind a real httpx client: answers each POST from a script (an int status, an exception,
    or an async callable taking the request) and records every request it got."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, script: list[Any] | None = None,
                 default: Any = 200) -> None:
        self.script = list(script or [])
        self.default = default
        self.requests: list[httpx.Request] = []
        client = httpx.AsyncClient(transport=httpx.MockTransport(self._handle))
        self.client = client

        async def get_client() -> httpx.AsyncClient:
            return client

        monkeypatch.setattr(jarvis_bridge, "get_http_client", get_client)

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        step = self.script.pop(0) if self.script else self.default
        if isinstance(step, BaseException):
            raise step
        if callable(step):
            return await step(request)
        if 200 <= step < 300:
            return httpx.Response(step, json={"status": "ok"})
        return httpx.Response(step, json={"error": {401: "bad_token", 403: "bad_signature", 409: "replay",
                                                    413: "too_large"}.get(step, "oops")})

    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(request.content) for request in self.requests]


def _down() -> httpx.ConnectError:
    return httpx.ConnectError("bridge down")


async def _refused(_request: httpx.Request) -> httpx.Response:
    """A new error for every request: one raised again and again keeps the frames (and loop) of each raise."""
    raise _down()


async def _settle(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        assert loop.time() < deadline, "condition not reached in time"
        await asyncio.sleep(0.005)


def _queue() -> Any:
    return jarvis_bridge._loop_queue(create=False)


async def _drained() -> None:
    def drained() -> bool:
        queue = _queue()
        return queue is None or (not queue.items and (queue.task is None or queue.task.done()))

    await _settle(drained)


def _in_thread(name: str, target: Callable[[], Any]) -> Any:
    """Runs target in a new thread, as LiveKit runs each job on Windows, and returns its result."""
    result: dict[str, Any] = {}

    def run() -> None:
        try:
            result["value"] = target()
        except BaseException as exc:  # pylint: disable=broad-exception-caught  # re-raised below
            result["error"] = exc

    thread = threading.Thread(target=run, name=name)
    thread.start()
    thread.join(timeout=30)
    assert not thread.is_alive(), f"{name} did not finish"
    if "error" in result:
        raise result["error"]
    return result.get("value")


def _close_left_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Cancels what a left-behind job loop still had pending, then closes it."""
    async def cancel_all() -> None:
        pending = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    _in_thread("cleanup", lambda: loop.run_until_complete(cancel_all()))
    loop.close()


def _ids(queue: Any) -> list[str]:
    return [json.loads(item.raw)["payload"]["id"] for item in queue.items]


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "JARVIS-BRIDGE" and r.levelno == logging.WARNING]


def _no_secret_in(caplog: pytest.LogCaptureFixture) -> None:
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert not any(secret in text for secret in SECRETS)


# ---------------------------------------------------------------- the body and its headers


def test_the_body_is_compact_utf8_json_of_type_and_payload():
    raw = jarvis_bridge.encode_event("transcription", {"id": "u1", "text": "سلام  Sir", "n": [1, 2]})
    assert raw == '{"type":"transcription","payload":{"id":"u1","text":"سلام  Sir","n":[1,2]}}'.encode()
    assert jarvis_bridge.encode_event("status", "START") == b'{"type":"status","payload":"START"}'
    assert jarvis_bridge.encode_event("x", "\ud800") == b'{"type":"x","payload":"?"}'  # a lone surrogate
    with pytest.raises(TypeError):
        jarvis_bridge.encode_event("x", {"bad": object()})
    for not_json in (float("nan"), float("inf"), float("-inf")):  # the UI's JSON.parse fails on them
        with pytest.raises(ValueError):
            jarvis_bridge.encode_event("voice_id_sync", {"confidence": not_json})


def test_signed_headers_follow_the_c_notify_1_formula(link):
    raw = jarvis_bridge.encode_event("qa_ping", {"n": 1})
    before = int(time.time())
    headers = jarvis_bridge.signed_headers(raw)

    assert set(headers) == {"Content-Type", "X-Agent-Token", "X-Agent-Timestamp", "X-Agent-Nonce",
                            "X-Agent-Signature"}
    assert headers["Content-Type"] == "application/json" and headers["X-Agent-Token"] == AGENT_TOKEN
    ts, nonce = headers["X-Agent-Timestamp"], headers["X-Agent-Nonce"]
    assert before <= int(ts) <= int(time.time()) and re.fullmatch(r"[0-9a-f]{32}", nonce)
    expected = hmac.new(HMAC_SECRET.encode(), f"{ts}.{nonce}.".encode() + raw, hashlib.sha256).hexdigest()
    assert headers["X-Agent-Signature"] == expected
    assert jarvis_security.verify_notify(ts, nonce, raw, headers["X-Agent-Signature"])
    assert jarvis_bridge.signed_headers(raw)["X-Agent-Nonce"] != nonce  # every request gets its own nonce


@pytest.mark.parametrize("missing", ["agent_token", "bridge_hmac_secret"])
def test_signed_headers_need_both_keys(link, monkeypatch, missing):
    monkeypatch.setattr(config, missing, None)
    monkeypatch.setattr(jarvis_security, "_dev_notify_key", [])
    with pytest.raises(RuntimeError, match="AGENT_TOKEN and BRIDGE_HMAC_SECRET"):
        jarvis_bridge.signed_headers(b"{}")
    assert not jarvis_security._dev_notify_key  # no random development key was made for it


def _request(headers: dict[str, str]) -> Request:
    scope = {"type": "http", "method": "POST", "path": "/notify", "query_string": b"",
             "headers": [(name.lower().encode(), value.encode()) for name, value in headers.items()]}
    return Request(scope)


async def test_the_bridge_checks_accept_the_headers_once_and_a_resigned_retry_again(link):
    raw = jarvis_bridge.encode_event("transcription", {"id": "u1", "role": "user", "text": "salam"})
    nonces = NonceCache()

    async def verify(headers: dict[str, str]) -> Any:
        return await bridge_security.verify_notify_request(
            _request(headers), raw, agent_token=AGENT_TOKEN, legacy_token=LEGACY_TOKEN, legacy_enabled=False,
            nonces=nonces)

    headers = jarvis_bridge.signed_headers(raw)
    auth = await verify(headers)
    assert auth.path == bridge_security.AUTH_AGENT and auth.data["payload"]["text"] == "salam"
    with pytest.raises(NotifyAuthError) as replay:
        await verify(headers)
    assert (replay.value.status, replay.value.error) == (409, "replay")
    assert (await verify(jarvis_bridge.signed_headers(raw))).data == auth.data  # the queue re-signs a retry


# ---------------------------------------------------------------- what notify_event sends


async def test_notify_event_posts_exactly_the_signed_bytes(link, monkeypatch):
    bridge = Bridge(monkeypatch)
    await jarvis_bridge.notify_transcription("user", "salam", msg_id="u-1", source="text", turn_id="t-0")

    assert len(bridge.requests) == 1
    request = bridge.requests[0]
    assert str(request.url) == "http://bridge.test/vortex/notify"
    raw = request.content
    assert json.loads(raw)["payload"] | {"timestamp": 0, "ts": 0} == {
        "id": "u-1", "role": "user", "text": "salam", "final": True, "is_final": True, "source": "text",
        "turn_id": "t-0", "ts": 0, "timestamp": 0}
    headers = request.headers
    assert headers["x-agent-token"] == AGENT_TOKEN and "x-vortex-token" not in headers
    assert jarvis_security.verify_notify(headers["x-agent-timestamp"], headers["x-agent-nonce"], raw,
                                         headers["x-agent-signature"])
    await bridge.client.aclose()


async def test_old_transcription_callers_send_what_they_sent_before(link, monkeypatch):
    """K-58 (C-42 step 1): an old call keeps every field it sent, and only gains the K-58 ones."""
    bridge = Bridge(monkeypatch)
    await jarvis_bridge.notify_transcription("agent", "Ji Sir", is_final=False)
    [body] = bridge.bodies()
    payload = body["payload"]
    assert {"id", "role", "text", "is_final", "timestamp"} <= set(payload)  # the legacy fields stay
    assert set(payload) == {"id", "role", "text", "is_final", "timestamp", "final", "ts", "source"}
    assert payload["is_final"] is False and payload["final"] is False
    assert payload["role"] == "assistant" and payload["source"] == "voice"  # 'agent' -> 'assistant'
    assert payload["ts"] == payload["timestamp"]
    await bridge.client.aclose()


async def test_transcription_extra_fields_are_added_and_never_replace_one(link, monkeypatch):
    bridge = Bridge(monkeypatch)
    await jarvis_bridge.notify_transcription("user", "salam", msg_id="u-1", source=None,
                                             extra={"score": 0.9, "id": "other", "persona": "jarvis"})
    [body] = bridge.bodies()
    payload = body["payload"]
    assert payload["id"] == "u-1" and payload["score"] == 0.9 and payload["persona"] == "jarvis"
    assert "source" not in payload  # source=None: the bridge decides, as before
    await bridge.client.aclose()


# ---------------------------------------------------------------- the retry queue


async def test_a_failed_critical_event_is_retried_until_it_arrives_once(link, monkeypatch, bridge_log):
    bridge = Bridge(monkeypatch, [_down(), _down(), _down()])
    await jarvis_bridge.notify_transcription("user", "salam", msg_id="u-1")
    assert jarvis_bridge.retry_queue_size() == 1
    await _drained()

    bodies = bridge.bodies()
    assert len(bodies) == 4 and all(body == bodies[0] for body in bodies)  # 1 send + 3 retries, same bytes
    nonces = [request.headers["x-agent-nonce"] for request in bridge.requests]
    assert len(set(nonces)) == 4  # every retry is signed anew, so the bridge never sees a replay
    assert link.waits == [1.0, 2.0, 4.0]
    assert len(_warnings(bridge_log)) == 1  # one WARNING a minute while it fails
    assert any("delivered after a retry" in r.getMessage() for r in bridge_log.records)
    _no_secret_in(bridge_log)
    await bridge.client.aclose()


async def test_each_event_gets_at_most_5_retries_with_backoff_from_1_to_30_s(link, monkeypatch, bridge_log):
    bridge = Bridge(monkeypatch, default=_down())
    await jarvis_bridge.notify_transcription("user", "one", msg_id="u-1")
    await jarvis_bridge.notify_event("tool_sync", {"tool": "t", "details": "two"})  # waits behind one, unsent
    await _drained()

    assert len(bridge.requests) == 2 * (1 + jarvis_bridge.RETRY_MAX)  # a first send and at most 5 retries each
    assert link.waits == [1.0, 2.0, 4.0, 8.0, 16.0] + [30.0] * 6
    assert [body["payload"].get("text", body["payload"].get("details")) for body in bridge.bodies()] == (
        ["one"] * 6 + ["two"] * 6)  # oldest first: two is never sent before one
    assert len(_warnings(bridge_log)) == 1
    await bridge.client.aclose()


async def test_after_a_delivery_the_backoff_starts_again_and_waiting_events_go_at_once(link, monkeypatch):
    bridge = Bridge(monkeypatch, [_down(), _down(), 200, 200, _down(), 200])
    await jarvis_bridge.notify_transcription("user", "a", msg_id="a")
    await jarvis_bridge.notify_transcription("user", "b", msg_id="b")  # queued behind a
    await _drained()
    assert [body["payload"]["id"] for body in bridge.bodies()] == ["a", "a", "a", "b"]
    assert link.waits == [1.0, 2.0]  # b went straight after a arrived

    await jarvis_bridge.notify_transcription("user", "c", msg_id="c")
    await _drained()
    assert link.waits == [1.0, 2.0, 1.0]
    await bridge.client.aclose()


async def test_the_queue_never_holds_more_than_500_and_drops_the_oldest(link, monkeypatch):
    bridge = Bridge(monkeypatch, default=_down())
    _hold(monkeypatch)
    sizes = []
    for n in range(520):
        await jarvis_bridge.notify_transcription("user", f"m{n}", msg_id=f"m{n}")
        sizes.append(jarvis_bridge.retry_queue_size())
    assert max(sizes) == jarvis_bridge.RETRY_QUEUE_MAX == 500
    queue = _queue()
    assert _ids(queue) == [f"m{n}" for n in range(20, 520)]  # the 20 oldest made room
    assert jarvis_bridge._warn_state["dropped"] == 20  # counted for the next WARNING
    queue.task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queue.task
    await bridge.client.aclose()


async def test_a_full_queue_keeps_the_event_in_flight(link, monkeypatch):
    monkeypatch.setattr(jarvis_bridge, "RETRY_QUEUE_MAX", 2)
    release = asyncio.Event()
    seen: dict[str, int] = {}

    async def answer(request: httpx.Request) -> httpx.Response:
        msg_id = json.loads(request.content)["payload"]["id"]
        seen[msg_id] = seen.get(msg_id, 0) + 1
        if msg_id == "a" and seen["a"] == 2:  # the first retry of a hangs until released
            await release.wait()
        raise _down()

    bridge = Bridge(monkeypatch, default=answer)
    await jarvis_bridge.notify_transcription("user", "a", msg_id="a")
    await _settle(lambda: _queue().in_flight is not None)
    for msg_id in ("b", "c", "d"):  # each one queues behind a, unsent
        await jarvis_bridge.notify_transcription("user", msg_id, msg_id=msg_id)
    queue = _queue()
    assert _ids(queue) == ["a", "d"]  # b and c made room; a, being sent, stays
    assert queue.items[0] is queue.in_flight and seen == {"a": 2}
    assert jarvis_bridge._warn_state["dropped"] == 2  # counted for the next WARNING
    release.set()
    queue.task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queue.task
    await bridge.client.aclose()


async def test_a_new_critical_event_waits_behind_the_queued_ones(link, monkeypatch):
    """K-13: the UI keeps the last tool_sync per id. A start refused while the bridge restarted must reach the
    bridge before its end, or the row stays 'running'."""
    bridge = Bridge(monkeypatch, [_down()])
    gate = _hold(monkeypatch)
    start = {"id": "call-1", "tool": "open_app", "phase": "start", "status": "running"}
    await jarvis_bridge.notify_event("tool_sync", start)  # refused: queued
    await jarvis_bridge.notify_event("tool_sync", start | {"phase": "end", "status": "success"})  # bridge is back
    assert len(bridge.requests) == 1 and jarvis_bridge.retry_queue_size() == 2  # the end did not overtake it

    gate.set()
    await _drained()
    assert [(body["payload"]["phase"], body["payload"]["status"]) for body in bridge.bodies()] == [
        ("start", "running"), ("start", "running"), ("end", "success")]
    await bridge.client.aclose()


async def test_updates_of_one_message_sent_together_arrive_in_the_order_they_were_sent(link, monkeypatch):
    """An interim transcript and its final update are posted at the same time and the proxy answers the interim
    with a 503. The bridge upserts by id, so the interim's retry must not land after the final text."""
    refused: list[int] = []

    async def answer(request: httpx.Request) -> httpx.Response:
        if not json.loads(request.content)["payload"]["is_final"] and not refused:
            refused.append(503)
            await asyncio.sleep(0.05)  # still on its way when the final update is ready
            return httpx.Response(503, json={"error": "unavailable"})
        return httpx.Response(200, json={"status": "ok"})

    bridge = Bridge(monkeypatch, default=answer)
    await asyncio.gather(
        jarvis_bridge.notify_transcription("assistant", "Ji Sir, main", msg_id="m-1", is_final=False),
        jarvis_bridge.notify_transcription("assistant", "Ji Sir, main dekh raha hoon.", msg_id="m-1"))
    await _drained()
    assert [body["payload"]["is_final"] for body in bridge.bodies()] == [False, False, True]
    await bridge.client.aclose()


@pytest.mark.parametrize(("failure", "event_type", "queued"), [
    (httpx.ReadTimeout("slow"), "transcription", True),          # the bridge keeps one copy per id
    (httpx.RemoteProtocolError("cut"), "settings_sync", True),   # a snapshot
    (500, "confirm_request", True),
    (504, "agent_heartbeat", True),
    (httpx.ReadTimeout("slow"), "tool_sync", False),             # would be logged twice
    (500, "tool_sync", False),
    (httpx.ConnectError("refused"), "tool_sync", True),          # never reached the bridge
    (httpx.ConnectTimeout("syn"), "tool_sync", True),
    (502, "tool_sync", True),
    (503, "tool_sync", True),
    (429, "tool_sync", True),
])
async def test_uncertain_failures_are_retried_only_for_events_the_bridge_keeps_once(
        link, monkeypatch, failure, event_type, queued):
    bridge = Bridge(monkeypatch, [failure])
    gate = _hold(monkeypatch)
    await jarvis_bridge.notify_event(event_type, {"id": "x-1", "tool": "t", "text": "x"})
    assert jarvis_bridge.retry_queue_size() == (1 if queued else 0)
    if queued:
        gate.set()
        await _drained()
        assert len(bridge.requests) == 2
    await bridge.client.aclose()


async def test_a_transcription_without_an_id_is_not_sent_twice_after_an_uncertain_failure(link, monkeypatch):
    bridge = Bridge(monkeypatch, [httpx.ReadTimeout("slow")])
    await jarvis_bridge.notify_event("transcription", {"role": "user", "text": "no id"})  # the bridge picks one
    assert jarvis_bridge.retry_queue_size() == 0 and len(bridge.requests) == 1
    await bridge.client.aclose()


@pytest.mark.parametrize("status", [400, 401, 403, 413, 404])
async def test_a_refusal_is_never_retried_and_names_only_the_code(link, monkeypatch, bridge_log, status):
    bridge = Bridge(monkeypatch, [status])
    await jarvis_bridge.notify_transcription("user", "salam", msg_id="u-1")
    assert jarvis_bridge.retry_queue_size() == 0 and len(bridge.requests) == 1
    [warning] = _warnings(bridge_log)
    assert f"HTTP {status}" in warning and "1 dropped" in warning
    _no_secret_in(bridge_log)
    await bridge.client.aclose()


@pytest.mark.parametrize("status", [200, 409])
async def test_ok_and_replay_answers_count_as_delivered(link, monkeypatch, bridge_log, status):
    bridge = Bridge(monkeypatch, [status])
    await jarvis_bridge.notify_transcription("user", "salam", msg_id="u-1")
    assert jarvis_bridge.retry_queue_size() == 0 and not _warnings(bridge_log)
    await bridge.client.aclose()


async def test_other_types_are_sent_once_and_fail_quietly(link, monkeypatch, bridge_log):
    bridge = Bridge(monkeypatch, default=_down())
    for event_type, payload in (("vortex_log", {"text": "x"}), ("status", "START"), ("vitals", {"cpu": 1}),
                                ("memory_update", {"id": "m"}), ("thinking", "STOP")):
        await jarvis_bridge.notify_event(event_type, payload)
    assert jarvis_bridge.retry_queue_size() == 0 and len(bridge.requests) == 5
    assert not _warnings(bridge_log)
    debug = [r for r in bridge_log.records if r.levelno == logging.DEBUG]
    assert sum("Bridge Notification failed" in r.getMessage() for r in debug) == 5
    await bridge.client.aclose()


async def test_a_body_over_the_bridge_limit_is_not_queued(link, monkeypatch):
    bridge = Bridge(monkeypatch, [_down()])
    await jarvis_bridge.notify_transcription("user", "x" * jarvis_bridge.NOTIFY_MAX_BYTES, msg_id="big")
    assert jarvis_bridge.retry_queue_size() == 0 and len(bridge.requests) == 1
    await bridge.client.aclose()


@pytest.mark.parametrize("event_type", ["voice_id_sync", "tool_sync"])
async def test_a_payload_that_is_not_json_is_never_sent(link, monkeypatch, bridge_log, event_type):
    """NaN is not JSON: the bridge would store it and every init_state after it would break the UI's parser.
    The legacy path's json= refused it too."""
    bridge = Bridge(monkeypatch)
    await jarvis_bridge.notify_event(event_type, {"id": "x-1", "confidence": float("nan")})
    assert not bridge.requests and jarvis_bridge.retry_queue_size() == 0
    assert any(r.levelno == logging.DEBUG and f"Bridge Notification failed for {event_type}" in r.getMessage()
               for r in bridge_log.records)
    await bridge.client.aclose()


async def test_wake_retry_queue_skips_the_backoff(link, monkeypatch):
    monkeypatch.setattr(jarvis_bridge, "_wait_before_retry", REAL_WAIT)
    monkeypatch.setattr(jarvis_bridge, "RETRY_FIRST_S", 30.0)  # a real 30 s wait unless woken
    bridge = Bridge(monkeypatch, [_down()])
    await jarvis_bridge.notify_transcription("user", "salam", msg_id="u-1")
    await asyncio.sleep(0.05)
    assert jarvis_bridge.retry_queue_size() == 1 and len(bridge.requests) == 1

    started = time.monotonic()
    jarvis_bridge.wake_retry_queue()  # as the UI socket's on_connect does
    await _drained()
    assert len(bridge.requests) == 2 and time.monotonic() - started < 5
    await bridge.client.aclose()


async def test_a_delivered_notify_wakes_the_waiting_queue(link, monkeypatch):
    woken: list[bool] = []

    async def recorded(queue: Any) -> None:
        await REAL_WAIT(queue)
        woken.append(queue.wake.is_set())

    monkeypatch.setattr(jarvis_bridge, "_wait_before_retry", recorded)
    monkeypatch.setattr(jarvis_bridge, "RETRY_FIRST_S", 30.0)
    bridge = Bridge(monkeypatch, [_down()])
    await jarvis_bridge.notify_transcription("user", "a", msg_id="a")
    await asyncio.sleep(0.05)
    await jarvis_bridge.notify_event("status", "START")  # goes through: the bridge is back
    await _drained()
    assert woken == [True] and bridge.bodies()[-1]["payload"]["id"] == "a"
    await bridge.client.aclose()


def test_outside_an_event_loop_the_queue_helpers_do_nothing():
    assert jarvis_bridge.retry_queue_size() == 0
    jarvis_bridge.wake_retry_queue()


async def test_each_event_loop_has_its_own_queue(link, monkeypatch):
    bridge = Bridge(monkeypatch, default=_down())
    _hold(monkeypatch)
    await jarvis_bridge.notify_transcription("user", "here", msg_id="here")
    mine = _queue()

    def other_loop() -> int:
        async def run() -> int:
            return jarvis_bridge.retry_queue_size()
        return asyncio.run(run())

    assert await asyncio.to_thread(other_loop) == 0 and _ids(mine) == ["here"]
    mine.task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await mine.task
    await bridge.client.aclose()


async def test_a_loop_queue_is_forgotten_once_nothing_uses_it(link, monkeypatch):
    bridge = Bridge(monkeypatch, [_down()])
    await jarvis_bridge.notify_transcription("user", "a", msg_id="a")
    assert jarvis_bridge.retry_queue_size() == 1
    await _drained()
    await jarvis_bridge.notify_event("tool_sync", {"id": "t-1", "tool": "t"})  # delivered at once
    assert len(bridge.requests) == 3 and not jarvis_bridge._queues  # the registry keeps no loop alive
    await bridge.client.aclose()


def test_a_loop_that_shuts_down_with_events_queued_leaves_nothing_behind(link, monkeypatch, bridge_log):
    bridge = Bridge(monkeypatch, default=_refused)
    monkeypatch.setattr(jarvis_bridge, "_wait_before_retry", lambda _queue: asyncio.sleep(3600))
    loops: list[weakref.ref[asyncio.AbstractEventLoop]] = []

    async def job(event_type: str) -> int:
        loops.append(weakref.ref(asyncio.get_running_loop()))
        await jarvis_bridge.notify_event(event_type, {"id": "u-1", "role": "user", "text": "lost"})
        return jarvis_bridge.retry_queue_size()

    # asyncio.run cancels the waiting flusher, then closes the loop
    assert _in_thread("job-1", lambda: asyncio.run(job("transcription"))) == 1
    assert not jarvis_bridge._queues
    assert any("1 queued critical event(s) dropped: their event loop is shutting down" in message
               for message in _warnings(bridge_log))
    _in_thread("job-2", lambda: asyncio.run(job("status")))  # the next loop takes over the shared client
    gc.collect()
    assert loops[0]() is None  # nothing keeps the finished loop alive
    _in_thread("close", lambda: asyncio.run(bridge.client.aclose()))


def test_a_job_loop_left_without_closing_is_forgotten_on_the_next_access(link, monkeypatch, bridge_log):
    """LiveKit's proc_client leaves a finished job's loop as it is: not closed, its tasks pending. Its queue goes
    as soon as another loop uses the registry, so neither the loop nor its events are kept for good."""
    bridge = Bridge(monkeypatch, default=_refused)
    monkeypatch.setattr(jarvis_bridge, "_wait_before_retry", lambda _queue: asyncio.sleep(3600))
    job_loop = asyncio.new_event_loop()

    async def job() -> None:
        await jarvis_bridge.notify_transcription("user", "lost", msg_id="u-1")

    async def next_job() -> int:
        return jarvis_bridge.retry_queue_size()

    try:
        _in_thread("job-1", lambda: job_loop.run_until_complete(job()))
        assert job_loop in jarvis_bridge._queues  # its flusher waits in a loop that never runs again
        assert _in_thread("job-2", lambda: asyncio.run(next_job())) == 0
        assert job_loop not in jarvis_bridge._queues
        assert any("1 queued critical event(s) dropped: their event loop ended" in message
                   for message in _warnings(bridge_log))
    finally:
        _close_left_loop(job_loop)
        _in_thread("close", lambda: asyncio.run(bridge.client.aclose()))


@pytest.mark.parametrize("client_first", [False, True], ids=["notify-first", "client-first"])
@pytest.mark.parametrize("mode", ["v2", "legacy"])
def test_a_new_job_loop_still_delivers_after_the_last_one_ended_with_notifies_pending(
        link, monkeypatch, mode, client_first):
    """On Windows LiveKit runs each job on a new event loop and leaves the old one with its tasks pending (log
    lines still being posted), so their semaphore permits never come back. The next loop must get a new
    semaphore, also when something asked for the HTTP client first (as ConfirmFlow does)."""
    if mode == "legacy":
        monkeypatch.setattr(config, "agent_token", None)
    for name in ("HTTP_CLIENT", "_LAST_LOOP", "_TELEMETRY_SEMAPHORE", "_HTTP_LOCK"):
        monkeypatch.setattr(jarvis_bridge, name, None)
    reached: list[str] = []

    async def bridge(_request: httpx.Request) -> httpx.Response:
        if threading.current_thread().name == "job-1":
            await asyncio.sleep(3600)  # still on its way when job 1 ends
        reached.append(threading.current_thread().name)
        return httpx.Response(200, json={"status": "ok"})

    # The real get_http_client(): only the clients it builds talk to the stand-in.
    monkeypatch.setattr(jarvis_bridge, "httpx", SimpleNamespace(
        AsyncClient=lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(bridge), **kwargs)))
    job_loop = asyncio.new_event_loop()

    async def first_job() -> None:
        for n in range(25):  # more than the 20 permits
            asyncio.get_running_loop().create_task(jarvis_bridge.notify_event("vortex_log", {"text": f"bye {n}"}))
        await asyncio.sleep(0.1)

    async def next_job() -> None:
        if client_first:
            await jarvis_bridge.get_http_client()
        await asyncio.wait_for(jarvis_bridge.notify_event("status", "START"), 5)

    try:
        _in_thread("job-1", lambda: job_loop.run_until_complete(first_job()))
        _in_thread("job-2", lambda: asyncio.run(next_job()))
    finally:
        _close_left_loop(job_loop)
    assert reached == ["job-2"]


# ---------------------------------------------------------------- without the v2 keys: today's legacy path


@pytest.mark.parametrize("missing", ["agent_token", "bridge_hmac_secret"])
async def test_without_the_v2_keys_the_legacy_headers_go_out_once_with_one_warning(
        link, monkeypatch, bridge_log, missing):
    monkeypatch.setattr(config, missing, None)
    bridge = Bridge(monkeypatch, [_down()])
    await jarvis_bridge.notify_transcription("user", "salam", msg_id="u-1")
    await jarvis_bridge.notify_event("persona_change", "anna")
    await jarvis_bridge.notify_ui("START")

    first = bridge.requests[0]
    body = json.loads(first.content)
    assert first.headers["x-vortex-token"] == LEGACY_TOKEN and "x-agent-token" not in first.headers
    assert jarvis_security.security_manager.verify_signature(json.dumps(body, sort_keys=True),
                                                             first.headers["x-vortex-signature"])
    assert str(first.url) == "http://bridge.test/vortex/notify" and len(bridge.requests) == 3
    assert jarvis_bridge.retry_queue_size() == 0  # the legacy path never retries, as before
    [warning] = _warnings(bridge_log)
    assert "Agent link v2 is off" in warning and missing.upper() in warning
    _no_secret_in(bridge_log)
    await bridge.client.aclose()


# ---------------------------------------------------------------- interop with the real bridge app


@pytest.fixture
async def real_bridge(monkeypatch, tmp_path, link):
    """src/core/ui_bridge.py in process with a temporary store and a recorded sio.emit; jarvis_bridge's HTTP client
    reaches it through a transport that can be switched off (connection refused) like a restarting bridge."""
    monkeypatch.setenv("BRIDGE_DB_PATH", str(tmp_path / "import-default.db"))
    module = importlib.import_module("src.core.ui_bridge")
    store = BridgeStore(tmp_path / "bridge.db")
    emitted: list[tuple[str, Any, Any]] = []

    async def fake_emit(event: str, data: Any = None, **kwargs: Any) -> None:
        emitted.append((event, data, kwargs.get("to") or kwargs.get("room")))

    monkeypatch.setattr(module, "store", store)
    monkeypatch.setattr(module, "bridge_manager", module.BridgeStateManager())
    monkeypatch.setattr(module, "_notify_nonces", NonceCache())
    monkeypatch.setattr(module, "VORTEX_SECURITY_TOKEN", LEGACY_TOKEN)
    monkeypatch.setattr(module.sio, "emit", fake_emit)
    monkeypatch.setattr(module.app.state, "rate_limit_window", [])
    monkeypatch.setattr(config, "legacy_token_auth", True)
    monkeypatch.setattr(config, "bridge_url", "http://bridge.test")  # the app itself; nginx strips /vortex
    asgi = httpx.ASGITransport(app=module.app)
    state = SimpleNamespace(up=True, responses=[], proxy=[])

    async def handler(request: httpx.Request) -> httpx.Response:
        if not state.up:
            raise httpx.ConnectError("connection refused")
        if state.proxy:  # nginx answers this one itself: no bridge behind it for a moment
            status = state.proxy.pop(0)
            state.responses.append((request, status))
            return httpx.Response(status, json={"error": "unavailable"})
        response = await asgi.handle_async_request(request)
        await response.aread()
        state.responses.append((request, response.status_code))
        return response

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def get_client() -> httpx.AsyncClient:
        return client

    monkeypatch.setattr(jarvis_bridge, "get_http_client", get_client)
    yield SimpleNamespace(module=module, store=store, emitted=emitted, state=state)
    await client.aclose()
    await store.close()


async def test_confirm_request_and_resolved_reach_the_real_bridge_and_return_its_answer(real_bridge, monkeypatch):
    """C-29 (K-14): the broker's helpers sign the request, and the answer carries ui_clients (owner UI sockets)."""
    from services.ai_core import jarvis_confirm  # pylint: disable=import-outside-toplevel

    module = real_bridge.module
    monkeypatch.setattr(module, "_clients", {"sid-ui": module.SocketClient(role="ui", ip="203.0.113.7")})
    request = {"id": "a1" * 16, "tool": "run_cmd_command", "summary": "Get-Process", "risk": "high",
               "voice_allowed": False, "expires_at": time.time() + 60, "ttl_s": 60}

    answer = await jarvis_confirm._post_to_bridge("confirm_request", request)  # the broker's own path

    assert answer == {"status": "ok", "ui_clients": 1}
    assert [e for e, _d, room in real_bridge.emitted if room == "ui"] == ["confirm_request"]
    assert (await real_bridge.store.get_confirmation(request["id"]))["status"] == "pending"
    resolved = await jarvis_bridge.notify_confirm_resolved({"id": request["id"], "allow": False, "reason": "denied"})
    assert resolved == {"status": "ok"}
    assert ("confirm_resolved", {"id": request["id"], "allow": False, "reason": "denied", "by": "owner"}, "ui") \
        in real_bridge.emitted

    real_bridge.state.up = False
    with pytest.raises(httpx.ConnectError):  # the broker's tries see the failure and refuse the action
        await jarvis_bridge.notify_confirm_request({**request, "id": "b2" * 16})


async def test_the_real_bridge_accepts_the_signed_notifies(real_bridge):
    await jarvis_bridge.notify_transcription("user", "سلام Sir", msg_id="u-1", source="text")
    await jarvis_bridge.notify_transcription("assistant", "Ji Sir", msg_id="a-1", source="text", turn_id="u-1")
    await jarvis_bridge.notify_event("persona_change", "anna")
    await jarvis_bridge.notify_ui("START")

    assert [status for _request, status in real_bridge.state.responses] == [200, 200, 200, 200]
    messages, _ = await real_bridge.store.list_messages()
    assert [(m["id"], m["role"], m["text"], m["source"]) for m in messages] == [
        ("u-1", "user", "سلام Sir", "text"), ("a-1", "assistant", "Ji Sir", "text")]
    assert messages[1]["turn_id"] == "u-1"
    events = [(event, room) for event, _data, room in real_bridge.emitted]
    for expected in ("new_message", "persona_update", "status_change"):
        assert (expected, "ui") in events


async def test_the_real_bridge_refuses_a_replay_and_a_stale_clock_and_nothing_is_retried(
        real_bridge, monkeypatch, bridge_log):
    await jarvis_bridge.notify_transcription("user", "salam", msg_id="u-1")
    [(request, status)] = real_bridge.state.responses
    assert status == 200
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=real_bridge.module.app),
                                 base_url="http://bridge.test") as attacker:  # the same request, sent again
        replay = await attacker.post("/notify", content=request.content, headers=dict(request.headers))
    assert (replay.status_code, replay.json()) == (409, {"error": "replay"})

    skewed = SimpleNamespace(time=lambda: time.time() - 120, monotonic=time.monotonic)  # the PC clock is off
    monkeypatch.setattr(jarvis_bridge, "time", skewed)
    await jarvis_bridge.notify_transcription("user", "late", msg_id="u-2")
    assert real_bridge.state.responses[-1][1] == 401
    assert jarvis_bridge.retry_queue_size() == 0  # a retry would be just as stale
    assert any("HTTP 401 stale" in message for message in _warnings(bridge_log))
    messages, _ = await real_bridge.store.list_messages()
    assert [m["id"] for m in messages] == ["u-1"]


async def test_transcripts_sent_while_the_bridge_restarts_arrive_once_and_in_order(real_bridge, monkeypatch):
    monkeypatch.setattr(jarvis_bridge, "_wait_before_retry", REAL_WAIT)
    monkeypatch.setattr(jarvis_bridge, "RETRY_FIRST_S", 30.0)
    real_bridge.state.up = False
    for n in range(5):
        await jarvis_bridge.notify_transcription("user" if n % 2 == 0 else "assistant", f"t{n}", msg_id=f"t{n}")
    await jarvis_bridge.notify_event("vortex_log", {"text": "lost while down", "category": "SYSTEM"})
    assert jarvis_bridge.retry_queue_size() == 5

    real_bridge.state.up = True
    jarvis_bridge.wake_retry_queue()  # the UI socket is back
    await _drained()

    messages, _ = await real_bridge.store.list_messages()
    assert [m["id"] for m in messages] == [f"t{n}" for n in range(5)]  # none lost, none twice
    assert [status for _request, status in real_bridge.state.responses] == [200] * 5  # no replay, no stale
    new = [data["id"] for event, data, _room in real_bridge.emitted if event == "new_message"]
    assert new == [f"t{n}" for n in range(5)]


async def test_the_real_bridge_keeps_the_final_text_when_an_earlier_update_needed_a_retry(real_bridge):
    real_bridge.state.proxy.append(503)
    await jarvis_bridge.notify_transcription("assistant", "Ji Sir, main", msg_id="m-1", is_final=False)
    await jarvis_bridge.notify_transcription("assistant", "Ji Sir, main dekh raha hoon.", msg_id="m-1")
    await _drained()

    assert [status for _request, status in real_bridge.state.responses] == [503, 200, 200]
    messages, _ = await real_bridge.store.list_messages()
    assert [(m["id"], m["text"], m["final"]) for m in messages] == [("m-1", "Ji Sir, main dekh raha hoon.", True)]
