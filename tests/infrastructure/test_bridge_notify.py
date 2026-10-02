"""Plan task C-14: POST /notify authentication on the bridge (C-NOTIFY-1), the bridge_security helpers, the
notify signatures in jarvis_security, the agent link settings in jarvis_config (env-only origins), the logger's
role gate and the bridge's compose settings.

Every test uses throwaway credentials, a temporary bridge.db and a recorded sio.emit: nothing reaches a socket,
the live bridge or a real env file, and no real secret is read or printed.
"""
# pylint: disable=protected-access,redefined-outer-name
from __future__ import annotations

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
    monkeypatch.setattr(module.app.state, "rate_limit_window", [])
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
    # ConfirmFlow (C-28) posts confirm_request and needs status 'ok' until C-29 routes it.
    raw = _body("confirm_request", {"id": "c1"})
    assert (await _post(bridge, raw, _agent_headers(raw))).json() == {"status": "ok"}
    raw = json.dumps({"payload": 1}).encode()
    assert (await _post(bridge, raw, _agent_headers(raw))).json() == {"status": "ignored",
                                                                      "reason": "missing_type"}


async def test_the_rate_limit_answers_429_with_retry_after(bridge):
    bridge.module.app.state.rate_limit_window = [time.time()] * 501
    raw = _transcription()
    resp = await _post(bridge, raw, _agent_headers(raw))
    assert resp.status_code == 429 and 1 <= int(resp.headers["Retry-After"]) <= 60


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


async def test_todays_confirm_flow_poster_still_gets_status_ok(bridge, agent_code):
    from services.ai_core import jarvis_confirm  # pylint: disable=import-outside-toplevel

    answer = await jarvis_confirm._post_to_bridge("confirm_request", {"id": "c-1", "tool": "t", "summary": "s"})
    assert answer == {"status": "ok"}


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
