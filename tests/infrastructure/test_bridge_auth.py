"""Plan task C-19: owner session auth of the UI bridge (C-AUTH-1 / K-36, C-LK-1 / K-37).

The HTTP tests drive the real bridge app through httpx.ASGITransport; the Socket.IO tests call the connect handler
directly. Every credential is a throwaway value made here (bcrypt cost 4 to keep the tests fast), bridge.db is
temporary, the clock is fake and LiveKitAPI is replaced, so nothing reaches the live bridge, LiveKit, a real env file
or the project's data folder.
"""
# pylint: disable=protected-access,redefined-outer-name,too-many-lines
from __future__ import annotations

import asyncio
import importlib
import json
import os
import secrets
import threading
import time
from collections import OrderedDict
from types import SimpleNamespace
from typing import Any

import httpx
import jwt
import pyotp
import pytest
from fastapi import Request

from services.utils import jarvis_security
from services.utils.jarvis_config import config
from src.core import bridge_auth
from src.core.bridge_desktop import INTERNAL_PORT
from src.core.bridge_security import NonceCache, TokenBucket
from src.core.bridge_store import BridgeStore

ORIGIN = "https://jarvis.srv1802669.hstgr.cloud"
PASSWORD = "c19 test owner passphrase"
JWT_SECRET = "c19-test-session-jwt-secret-" + "a" * 40
LEGACY_TOKEN = "c19-test-legacy-token-" + "b" * 24
AGENT_TOKEN = "c19-test-agent-token-" + "c" * 24
HMAC_SECRET = "c19-test-hmac-secret-" + "d" * 24
LK_KEY, LK_SECRET = "c19-test-lk-key", "c19-test-lk-secret-" + "e" * 32
START = 1_790_000_000.0  # a fixed 'now' (Sept 2026); TOTP codes are made for it
FAST = 4                 # bcrypt cost for test hashes


class FakeClock:
    def __init__(self, now: float = START) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture(scope="module")
def creds() -> SimpleNamespace:
    """One enrolled owner for the whole module: hashing is the slow part."""
    codes = bridge_auth.new_recovery_codes(3)
    return SimpleNamespace(
        password_hash=bridge_auth.hash_password(PASSWORD, cost=FAST),
        totp_secret=pyotp.random_base32(),
        codes=codes,
        recovery_hashes=[bridge_auth.hash_recovery_code(code, cost=FAST) for code in codes],
    )


@pytest.fixture
async def bridge(monkeypatch, tmp_path, creds):
    """ui_bridge with a temporary store, a fake clock, throwaway owner credentials and a fresh login limiter."""
    monkeypatch.setenv("BRIDGE_DB_PATH", str(tmp_path / "import-default.db"))
    # The bridge validates its settings at import. The test conftest removes every real secret while the root .env
    # may still say VORTEX_ENV=production, so the first import runs in development mode with throwaway keys.
    monkeypatch.setattr(config, "env", "development")
    monkeypatch.setenv("LIVEKIT_API_KEY", LK_KEY)
    monkeypatch.setenv("LIVEKIT_API_SECRET", LK_SECRET)
    monkeypatch.setenv("LIVEKIT_URL", "")  # no room ensure unless a test mocks LiveKitAPI
    module = importlib.import_module("src.core.ui_bridge")
    clock = FakeClock()
    store = BridgeStore(tmp_path / "bridge.db", clock=clock)
    monkeypatch.setattr(module, "store", store)
    monkeypatch.setattr(module, "bridge_manager", module.BridgeStateManager())
    monkeypatch.setattr(module, "_clients", {})
    monkeypatch.setattr(module, "_notify_nonces", NonceCache())
    monkeypatch.setattr(module, "_refusal_audit", TokenBucket(1.0, 30.0))
    monkeypatch.setattr(module, "VORTEX_SECURITY_TOKEN", LEGACY_TOKEN)
    monkeypatch.setattr(module, "ALLOWED_ORIGINS", [ORIGIN, "http://127.0.0.1:5173"])
    monkeypatch.setattr(config, "agent_token", AGENT_TOKEN)
    monkeypatch.setattr(config, "bridge_hmac_secret", HMAC_SECRET)
    monkeypatch.setattr(config, "security_token", LEGACY_TOKEN)
    monkeypatch.setattr(config, "legacy_token_auth", True)
    monkeypatch.setattr(config, "env", "development")
    monkeypatch.setattr(config, "session_jwt_secret", JWT_SECRET)
    monkeypatch.setattr(config, "owner_password_hash", creds.password_hash)
    monkeypatch.setattr(config, "owner_totp_secret", creds.totp_secret)
    monkeypatch.setattr(config, "owner_recovery_hashes", list(creds.recovery_hashes))
    monkeypatch.setattr(config, "cookie_path", "/vortex")
    monkeypatch.setattr(config, "cookie_secure", "1")
    auth = module.owner_auth
    monkeypatch.setattr(auth, "clock", clock)
    monkeypatch.setattr(auth, "limiter", bridge_auth.LoginLimiter(clock=clock))
    monkeypatch.setattr(auth, "_factor_lock", asyncio.Lock())
    monkeypatch.setattr(auth, "max_verifying", bridge_auth.MAX_VERIFYING)
    monkeypatch.setattr(auth, "_verifying", 0)
    monkeypatch.setattr(auth, "refusal_audit_daily_cap", bridge_auth.REFUSAL_AUDIT_DAILY_CAP)
    monkeypatch.setattr(auth, "_refusal_day", -1)
    monkeypatch.setattr(auth, "_refusal_rows", 0)
    monkeypatch.setattr(auth, "_refusals_skipped", 0)
    monkeypatch.setattr(auth, "_touched", OrderedDict())
    yield SimpleNamespace(module=module, store=store, clock=clock, auth=auth, creds=creds)
    await store.close()


def http(ns: Any, *, internal: bool = False, scheme: str = "https") -> httpx.AsyncClient:
    base = f"{scheme}://bridge.test:{INTERNAL_PORT}" if internal else f"{scheme}://bridge.test"
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=ns.module.app), base_url=base)


def code_at(ns: Any, offset_steps: int = 0) -> str:
    return pyotp.TOTP(ns.creds.totp_secret).at(ns.clock() + offset_steps * bridge_auth.TOTP_INTERVAL_S)


async def login(client: httpx.AsyncClient, *, password: str = PASSWORD, totp: str | None = None,
                recovery: str | None = None, ip: str = "203.0.113.7", origin: str | None = ORIGIN,
                cookie: str | None = None) -> httpx.Response:
    body: dict[str, Any] = {"password": password}
    if totp is not None:
        body["totp"] = totp
    if recovery is not None:
        body["recovery_code"] = recovery
    headers = {"X-Real-IP": ip}
    if origin is not None:
        headers["Origin"] = origin
    if cookie:
        headers["Cookie"] = f"jarvis_session={cookie}"
    return await client.post("/auth/login", json=body, headers=headers)


def session_cookie(resp: httpx.Response) -> str:
    value = resp.cookies.get("jarvis_session")
    assert value, resp.headers.get("set-cookie")
    return value


def cookie_header(token: str) -> dict[str, str]:
    return {"Cookie": f"jarvis_session={token}"}


def set_cookie_attrs(resp: httpx.Response) -> dict[str, str]:
    raw = resp.headers["set-cookie"]
    parts = [p.strip() for p in raw.split(";")]
    attrs = {}
    for part in parts[1:]:
        name, _, value = part.partition("=")
        attrs[name.lower()] = value
    return attrs


async def audit_rows(ns: Any, prefix: str = "auth") -> list[dict[str, Any]]:
    return await ns.store.recent_audit(limit=500, action_prefix=prefix)


# ---------------------------------------------------------------- login: password, TOTP, cookie


async def test_login_ok_sets_a_strict_httponly_secure_cookie_on_vortex(bridge):
    async with http(bridge) as client:
        resp = await login(client, totp=code_at(bridge))
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True and body["user"] == "owner"
    assert body["expires_at"] == int(START) + bridge_auth.SESSION_TTL_S
    attrs = set_cookie_attrs(resp)
    assert "httponly" in attrs and "secure" in attrs
    assert attrs["samesite"].lower() == "strict"
    assert attrs["path"] == "/vortex"
    assert attrs["max-age"] == "43200"
    token = session_cookie(resp)
    claims = jwt.decode(token, JWT_SECRET, algorithms=["HS256"], options={"verify_exp": False})
    assert claims["sub"] == "owner" and claims["sid"] == body["session_id"] and claims["v"] == 1
    assert claims["exp"] - claims["iat"] == 43200
    row = await bridge.store.get_session(body["session_id"])
    assert row is not None and row["revoked_ts"] is None
    actions = [r["action"] for r in await audit_rows(bridge)]
    assert "auth_login_ok" in actions


async def test_cookie_secure_follows_cookie_secure_setting(bridge, monkeypatch):
    monkeypatch.setattr(config, "cookie_secure", "0")
    async with http(bridge) as client:
        resp = await login(client, totp=code_at(bridge))
    assert resp.status_code == 200
    assert "secure" not in set_cookie_attrs(resp)
    assert set_cookie_attrs(resp)["samesite"].lower() == "strict"


@pytest.mark.parametrize("scheme, secure", [("https", True), ("http", False)])
async def test_cookie_secure_auto_follows_the_request_scheme(bridge, monkeypatch, scheme, secure):
    monkeypatch.setattr(config, "cookie_secure", "auto")
    async with http(bridge, scheme=scheme) as client:
        resp = await login(client, totp=code_at(bridge))
        assert resp.status_code == 200
        attrs = set_cookie_attrs(resp)
        assert ("secure" in attrs) is secure
        assert "httponly" in attrs and attrs["samesite"].lower() == "strict"
        client.cookies.clear()
        out = await client.post("/auth/logout", headers={**cookie_header(session_cookie(resp)), "Origin": ORIGIN})
    assert out.status_code == 200
    assert ("secure" in set_cookie_attrs(out)) is secure


@pytest.mark.parametrize("field", ["password", "totp"])
async def test_a_wrong_password_or_code_gets_the_same_401(bridge, field):
    async with http(bridge) as client:
        if field == "password":
            resp = await login(client, password="not the owner password", totp=code_at(bridge))
        else:
            good = code_at(bridge)
            wrong = f"{(int(good) + 1) % 1_000_000:06d}"
            resp = await login(client, totp=wrong)
    assert resp.status_code == 401
    assert resp.json() == {"ok": False, "error": "invalid_credentials"}
    assert "set-cookie" not in resp.headers
    rows = await audit_rows(bridge, "auth_login_failed")
    assert len(rows) == 1
    assert json.loads(rows[0]["detail"])["reason"] == ("bad_password" if field == "password" else "bad_totp")


async def test_a_wrong_password_does_not_spend_the_totp_code(bridge):
    code = code_at(bridge)
    async with http(bridge) as client:
        assert (await login(client, password="not the owner password", totp=code)).status_code == 401
        assert (await login(client, totp=code)).status_code == 200


@pytest.mark.parametrize("body", [{}, {"password": PASSWORD}, {"totp": "123456"}, ["x"]])
async def test_a_malformed_body_is_invalid_credentials(bridge, body):
    async with http(bridge) as client:
        resp = await client.post("/auth/login", json=body, headers={"Origin": ORIGIN})
    assert resp.status_code == 401
    assert resp.json()["error"] == "invalid_credentials"


async def test_totp_accepts_one_step_of_drift_and_refuses_two(bridge):
    async with http(bridge) as client:
        assert (await login(client, totp=code_at(bridge, -2))).status_code == 401
        assert (await login(client, totp=code_at(bridge, +2))).status_code == 401
        assert (await login(client, totp=code_at(bridge, -1))).status_code == 200
        bridge.clock.now += 120
        assert (await login(client, totp=code_at(bridge, +1))).status_code == 200


async def test_a_totp_code_is_never_accepted_twice(bridge):
    code = code_at(bridge)
    async with http(bridge) as client:
        assert (await login(client, totp=code)).status_code == 200
        replay = await login(client, totp=code)
        assert replay.status_code == 401
        assert replay.json()["error"] == "invalid_credentials"
        # an older step's code is refused after a newer one was accepted, even inside the window
        bridge.clock.now += 30
        assert (await login(client, totp=code_at(bridge, -1))).status_code == 401
        assert (await login(client, totp=code_at(bridge))).status_code == 200
    reasons = [json.loads(r["detail"])["reason"] for r in await audit_rows(bridge, "auth_login_failed")]
    assert reasons.count("totp_replay") == 2
    assert await bridge.store.kv_get(bridge_auth.KV_TOTP_LAST) == bridge_auth.totp_timecode(bridge.clock())


async def test_totp_match_is_pure_and_strict():
    secret = pyotp.random_base32()
    now = START
    code = pyotp.TOTP(secret).at(now)
    assert bridge_auth.totp_match(secret, code, now) == bridge_auth.totp_timecode(now)
    assert bridge_auth.totp_match(secret, f" {code[:3]} {code[3:]} ", now) == bridge_auth.totp_timecode(now)
    for bad in (None, 123456, "12345", "1234567", "abcdef", ""):
        assert bridge_auth.totp_match(secret, bad, now) is None


# Arabic-Indic (U+0660..) and Extended Arabic-Indic (U+06F0.., what an Urdu keyboard types) digits.
ARABIC_INDIC = str.maketrans("0123456789", "".join(chr(0x0660 + i) for i in range(10)))
EXTENDED_ARABIC_INDIC = str.maketrans("0123456789", "".join(chr(0x06F0 + i) for i in range(10)))
FULL_WIDTH = str.maketrans("0123456789", "".join(chr(0xFF10 + i) for i in range(10)))


def test_ascii_digits_turns_any_scripts_digits_into_ascii():
    assert bridge_auth.ascii_digits("0123456789".translate(ARABIC_INDIC)) == "0123456789"
    assert bridge_auth.ascii_digits("0123456789".translate(EXTENDED_ARABIC_INDIC)) == "0123456789"
    assert bridge_auth.ascii_digits("0123456789".translate(FULL_WIDTH)) == "0123456789"
    assert bridge_auth.ascii_digits("ab-12") == "ab-12"
    # not decimal digits: kept, so the code check refuses them
    assert bridge_auth.ascii_digits("x²³ح") == "x²³ح"


@pytest.mark.parametrize("digits", [ARABIC_INDIC, EXTENDED_ARABIC_INDIC, FULL_WIDTH],
                         ids=["arabic-indic", "extended-arabic-indic", "full-width"])
def test_totp_match_reads_urdu_and_arabic_digits(digits):
    secret = pyotp.random_base32()
    now = START
    code = pyotp.TOTP(secret).at(now)
    expected = bridge_auth.totp_timecode(now)
    assert bridge_auth.totp_match(secret, code.translate(digits), now) == expected
    # mixed scripts, spaces of any kind and the direction marks an RTL keyboard adds
    mixed = code[:3].translate(digits) + "\u00a0" + code[3:]
    assert bridge_auth.totp_match(secret, mixed, now) == expected
    assert bridge_auth.totp_match(secret, "\u200f" + code.translate(digits) + "\u061c", now) == expected
    wrong = f"{(int(code) + 1) % 1_000_000:06d}".translate(digits)
    assert bridge_auth.totp_match(secret, wrong, now) is None


@pytest.mark.parametrize("bad", [
    "١٢٣٤٥", "١٢٣٤٥٦٧", "۱۲۳abc", "١٢٣-٤٥٦", "²³⁴⁵⁶⁷", "١٢٣٤٥\ud800", "\u0000" * 6, "٫٫٫٫٫٫", "ⅠⅡⅢⅣⅤⅥ"])
def test_totp_match_refuses_other_unicode_cleanly(bad):
    assert bridge_auth.totp_match(pyotp.random_base32(), bad, START) is None


@pytest.mark.parametrize("digits", [ARABIC_INDIC, EXTENDED_ARABIC_INDIC], ids=["arabic-indic", "extended-arabic-indic"])
async def test_login_takes_a_totp_code_typed_with_urdu_or_arabic_digits(bridge, digits):
    async with http(bridge) as client:
        resp = await login(client, totp=code_at(bridge).translate(digits))
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True
    assert await bridge.store.kv_get(bridge_auth.KV_TOTP_LAST) == bridge_auth.totp_timecode(bridge.clock())


def _raw_login_body(field: str, code: str) -> bytes:
    """A login body with ensure_ascii escapes, so a lone surrogate ('\\ud800') reaches the bridge as JSON allows."""
    return json.dumps({"password": PASSWORD, field: code}).encode("ascii")


@pytest.mark.parametrize("bad", ["١٢٣٤٥", "١٢٣٤٥٦٧", "۱۲۳abc", "²³⁴⁵⁶⁷", "١٢٣٤٥\ud800", "ⅠⅡⅢⅣⅤⅥ"])
async def test_a_login_with_other_unicode_in_the_code_is_a_wrong_code_not_a_500(bridge, bad):
    headers = {"Origin": ORIGIN, "Content-Type": "application/json"}
    async with http(bridge) as client:
        resp = await client.post("/auth/login", content=_raw_login_body("totp", bad),
                                 headers={**headers, "X-Real-IP": "198.51.100.10"})
        recovery = await client.post("/auth/login", content=_raw_login_body("recovery_code", bad),
                                     headers={**headers, "X-Real-IP": "198.51.100.20"})
    for answer in (resp, recovery):
        assert answer.status_code == 401, answer.text
        assert answer.json() == {"ok": False, "error": "invalid_credentials"}
    reasons = sorted(json.loads(r["detail"])["reason"] for r in await audit_rows(bridge, "auth_login_failed"))
    assert reasons == ["bad_recovery", "bad_totp"]


# ---------------------------------------------------------------- recovery codes


async def test_a_recovery_code_works_exactly_once(bridge):
    first, second = bridge.creds.codes[0], bridge.creds.codes[1]
    async with http(bridge) as client:
        resp = await login(client, recovery=first.upper().replace("-", " "))
        assert resp.status_code == 200
        again = await login(client, recovery=first)
        assert again.status_code == 401
        assert again.json()["error"] == "invalid_credentials"
        assert (await login(client, recovery=second)).status_code == 200
        assert (await login(client, password="not the owner password", recovery=bridge.creds.codes[2])
                ).status_code == 401
    used = await bridge.store.kv_get(bridge_auth.KV_RECOVERY_USED)
    assert len(used) == 2
    assert all(code not in json.dumps(used) for code in bridge.creds.codes)
    rows = await audit_rows(bridge, "auth_recovery_used")
    assert [json.loads(r["detail"])["left"] for r in rows] == [1, 2]


@pytest.mark.parametrize("digits", [ARABIC_INDIC, EXTENDED_ARABIC_INDIC], ids=["arabic-indic", "extended-arabic-indic"])
def test_a_recovery_code_typed_with_urdu_or_arabic_digits_is_the_same_code(digits):
    code = "ab23-cd45-ef67"
    stored = bridge_auth.hash_recovery_code(code, cost=FAST)
    typed = code.translate(digits)
    assert typed != code and not typed.isascii()
    assert bridge_auth.normalize_recovery_code(typed) == "ab23cd45ef67"
    assert bridge_auth.verify_recovery_code(typed, stored)
    assert bridge_auth.verify_recovery_code(typed.upper().replace("-", "\u2010 "), stored)  # a phone's dash
    assert bridge_auth.recovery_match(typed, ["not-a-hash", stored]) == 1
    for bad in ("ab²³-cd45-ef67", "ab23-cd45-ef6٧٧", "اب23-cd45-ef67", "ab23-cd45-ef67\ud800", ""):
        assert not bridge_auth.verify_recovery_code(bad, stored)
    assert bridge_auth.recovery_match("ab²³-cd45-ef67", [stored]) is None


@pytest.mark.parametrize("digits", [ARABIC_INDIC, EXTENDED_ARABIC_INDIC], ids=["arabic-indic", "extended-arabic-indic"])
async def test_login_takes_a_recovery_code_typed_with_urdu_or_arabic_digits(bridge, monkeypatch, digits):
    code = "ab23-cd45-ef67"
    monkeypatch.setattr(config, "owner_recovery_hashes",
                        [*bridge.creds.recovery_hashes, bridge_auth.hash_recovery_code(code, cost=FAST)])
    async with http(bridge) as client:
        resp = await login(client, recovery=code.translate(digits))
        assert resp.status_code == 200, resp.text
        # spent: the ASCII form of the same code is refused now
        assert (await login(client, recovery=code)).status_code == 401
    assert len(await bridge.store.kv_get(bridge_auth.KV_RECOVERY_USED)) == 1


@pytest.fixture
def bcrypt_calls(monkeypatch) -> list[int]:
    """Counts bcrypt.checkpw calls (the expensive step of a login) made through bridge_auth."""
    calls = [0]
    real = bridge_auth.bcrypt.checkpw

    def counting(password: bytes, hashed: bytes) -> bool:
        calls[0] += 1
        return real(password, hashed)

    monkeypatch.setattr(bridge_auth.bcrypt, "checkpw", counting)
    return calls


async def test_the_recovery_path_costs_the_same_whether_the_password_is_right(bridge, bcrypt_calls):
    """A right password with a bad recovery code must not take longer than a wrong password: every attempt does one
    password check plus one check per configured recovery hash."""
    expected = 1 + len(bridge.creds.recovery_hashes)
    valid = bridge.creds.codes[0]
    cases = [("not the owner password", "aaaa-aaaa-aaaa", 401), (PASSWORD, "aaaa-aaaa-aaaa", 401),
             ("not the owner password", valid, 401), (PASSWORD, valid, 200), (PASSWORD, valid, 401)]
    async with http(bridge) as client:
        for i, (password, code, status) in enumerate(cases):
            bcrypt_calls[0] = 0
            resp = await login(client, password=password, recovery=code, ip=f"198.51.100.{i + 1}")
            assert resp.status_code == status, (i, resp.text)
            assert bcrypt_calls[0] == expected, (i, bcrypt_calls[0])
            if status == 401:
                assert resp.json() == {"ok": False, "error": "invalid_credentials"}
    # the wrong-password attempt with a valid code did not spend it; the right one did, once
    assert len(await bridge.store.kv_get(bridge_auth.KV_RECOVERY_USED)) == 1


async def test_a_totp_login_does_one_bcrypt_check_whether_the_password_is_right(bridge, bcrypt_calls):
    good = code_at(bridge)
    wrong = f"{(int(good) + 1) % 1_000_000:06d}"
    async with http(bridge) as client:
        for i, password in enumerate(("not the owner password", PASSWORD)):
            bcrypt_calls[0] = 0
            assert (await login(client, password=password, totp=wrong, ip=f"198.51.100.{i + 1}")).status_code == 401
            assert bcrypt_calls[0] == 1


def test_recovery_match_checks_every_hash(creds, monkeypatch):
    calls = []
    monkeypatch.setattr(bridge_auth, "verify_recovery_code", lambda code, stored: calls.append(stored) or
                        stored == creds.recovery_hashes[0])
    assert bridge_auth.recovery_match("x", creds.recovery_hashes) == 0
    assert calls == list(creds.recovery_hashes)
    assert bridge_auth.recovery_match(None, creds.recovery_hashes) is None


# ---------------------------------------------------------------- brute-force limits


async def test_five_failures_per_ip_lock_that_ip_for_fifteen_minutes(bridge):
    async with http(bridge) as client:
        for _ in range(5):
            assert (await login(client, password="wrong password here", totp="000000")).status_code == 401
        locked = await login(client, totp=code_at(bridge))
        assert locked.status_code == 429
        body = locked.json()
        assert body["ok"] is False and body["error"] == "rate_limited"
        assert 0 < body["retry_after"] <= 900
        assert locked.headers["retry-after"] == str(body["retry_after"])
        # another address is not locked by this one
        assert (await login(client, totp=code_at(bridge), ip="198.51.100.9")).status_code == 200
        bridge.clock.now += 901
        assert (await login(client, totp=code_at(bridge))).status_code == 200
    assert await audit_rows(bridge, "auth_login_rate_limited")


async def test_twenty_failures_an_hour_lock_every_address(bridge):
    async with http(bridge) as client:
        for i in range(20):
            resp = await login(client, password="wrong password here", totp="000000", ip=f"192.0.2.{i + 1}")
            assert resp.status_code == 401
        resp = await login(client, totp=code_at(bridge), ip="198.51.100.77")
        assert resp.status_code == 429
        assert resp.json()["retry_after"] <= 3600
        bridge.clock.now += 3601
        assert (await login(client, totp=code_at(bridge), ip="198.51.100.77")).status_code == 200


async def _burst(client: httpx.AsyncClient, ips: list[str]) -> list[httpx.Response]:
    return list(await asyncio.gather(*(login(client, password="wrong password here", totp="000000", ip=ip)
                                       for ip in ips)))


async def test_concurrent_bad_logins_from_one_ip_get_at_most_five_checks(bridge, monkeypatch):
    # no cap on parallel checks here, so only the limiter's reservations stand between the burst and bcrypt
    monkeypatch.setattr(bridge.auth, "max_verifying", 1000)
    async with http(bridge) as client:
        responses = await _burst(client, ["203.0.113.7"] * 50)
    statuses = [r.status_code for r in responses]
    assert statuses.count(401) == 5 and statuses.count(429) == 45, statuses
    assert len(await audit_rows(bridge, "auth_login_failed")) == 5
    assert all(r.json()["error"] == "rate_limited" for r in responses if r.status_code == 429)


async def test_concurrent_bad_logins_from_many_ips_stop_at_the_global_limit(bridge, monkeypatch):
    monkeypatch.setattr(bridge.auth, "max_verifying", 1000)
    async with http(bridge) as client:
        responses = await _burst(client, [f"192.0.2.{i + 1}" for i in range(30)])
        late = await login(client, totp=code_at(bridge), ip="198.51.100.77")
    statuses = [r.status_code for r in responses]
    assert statuses.count(401) == 20 and statuses.count(429) == 10, statuses
    assert late.status_code == 429


async def test_a_concurrent_burst_with_the_default_cap_gets_at_most_five_checks(bridge):
    async with http(bridge) as client:
        responses = await _burst(client, ["203.0.113.7"] * 50)
    statuses = [r.status_code for r in responses]
    assert statuses.count(401) <= 5 and statuses.count(401) + statuses.count(429) == 50, statuses
    assert len(await audit_rows(bridge, "auth_login_failed")) == statuses.count(401)


async def test_at_most_two_credential_checks_run_at_once(bridge, monkeypatch):
    lock = threading.Lock()
    state = {"now": 0, "peak": 0, "calls": 0}
    real = bridge_auth.verify_password

    def slow_verify(password: Any, stored: Any) -> bool:
        with lock:
            state["now"] += 1
            state["calls"] += 1
            state["peak"] = max(state["peak"], state["now"])
        try:
            time.sleep(0.05)
            return real(password, stored)
        finally:
            with lock:
                state["now"] -= 1

    monkeypatch.setattr(bridge_auth, "verify_password", slow_verify)
    async with http(bridge) as client:
        responses = await _burst(client, [f"192.0.2.{i + 1}" for i in range(10)])
    statuses = [r.status_code for r in responses]
    assert state["peak"] <= bridge_auth.MAX_VERIFYING
    assert statuses.count(401) == state["calls"] >= 1
    assert statuses.count(401) + statuses.count(429) == 10, statuses
    busy = [r for r in responses if r.status_code == 429]
    assert busy and all(r.json()["retry_after"] == bridge_auth.BUSY_RETRY_S for r in busy)
    # a busy refusal checked nothing, so it does not count against the limits
    assert len(bridge.auth.limiter._all) == statuses.count(401)
    reasons = [json.loads(r["detail"])["reason"] for r in await audit_rows(bridge, "auth_login_rate_limited")]
    assert reasons.count("busy") == len(busy)


def test_login_limiter_reserve_counts_at_once_and_release_takes_it_back():
    clock = FakeClock()
    limiter = bridge_auth.LoginLimiter(clock=clock)
    stamps = [limiter.reserve("a") for _ in range(5)]
    assert all(s is not None for s in stamps)
    assert limiter.reserve("a") is None and limiter.retry_after("a") > 0
    limiter.release("a", stamps[0])
    assert limiter.retry_after("a") == 0.0
    assert limiter.reserve("a") is not None
    # releasing a stamp that has expired out of the window is harmless
    clock.now += 901
    limiter.release("a", stamps[1])
    assert limiter.retry_after("a") == 0.0


def test_login_limiter_success_clears_only_that_ip():
    clock = FakeClock()
    limiter = bridge_auth.LoginLimiter(clock=clock)
    for _ in range(4):
        limiter.fail("a")
    limiter.succeed("a")
    assert limiter.retry_after("a") == 0.0
    for _ in range(5):
        limiter.fail("b")
    assert 899 < limiter.retry_after("b") <= 900
    clock.now += 900
    assert limiter.retry_after("b") == 0.0


# ---------------------------------------------------------------- origin, enrolment, internal listener


@pytest.mark.parametrize("origin", [None, "https://evil.example", "null", "https://jarvis-agent-three.vercel.app"])
async def test_login_needs_an_allowed_origin(bridge, origin):
    async with http(bridge) as client:
        resp = await login(client, totp=code_at(bridge), origin=origin)
    assert resp.status_code == 403
    assert resp.json()["error"] == "bad_origin"
    assert "set-cookie" not in resp.headers


async def test_every_cheap_refusal_is_audited_up_to_a_daily_cap(bridge, monkeypatch):
    monkeypatch.setattr(bridge.auth, "refusal_audit_daily_cap", 3)
    async with http(bridge) as client:
        for _ in range(6):
            assert (await login(client, totp="123456", origin="https://evil.example")).status_code == 403
        assert len(await audit_rows(bridge, "auth_login_refused")) == 3
        assert len(await audit_rows(bridge, "auth_audit_capped")) == 1
        bridge.clock.now += 86400
        assert (await login(client, totp="123456", origin="https://evil.example")).status_code == 403
    rows = await audit_rows(bridge, "auth_login_refused")
    assert len(rows) == 4
    assert json.loads(rows[0]["detail"]) == {"reason": "bad_origin", "skipped_before": 3}


@pytest.mark.parametrize("missing", ["session_jwt_secret", "owner_password_hash", "owner_totp_secret"])
async def test_not_enrolled_answers_503_and_me_401(bridge, monkeypatch, missing):
    monkeypatch.setattr(config, missing, None)
    async with http(bridge) as client:
        resp = await login(client, totp=code_at(bridge))
        me = await client.get("/auth/me")
    assert resp.status_code == 503
    assert resp.json()["error"] == "owner_not_enrolled"
    assert me.status_code == 401 and me.json()["authenticated"] is False
    assert await audit_rows(bridge, "auth_login_refused")


async def test_a_short_jwt_secret_is_not_enrolled(bridge, monkeypatch):
    monkeypatch.setattr(config, "session_jwt_secret", "too-short")
    async with http(bridge) as client:
        assert (await login(client, totp=code_at(bridge))).status_code == 503


async def test_behind_the_proxy_a_client_sent_forwarded_for_cannot_pick_the_limited_address(bridge, monkeypatch):
    """main() runs uvicorn with proxy_headers and forwarded_allow_ips=TRUSTED_PROXIES. nginx appends the real client
    address to X-Forwarded-For, so with a spoofed left-most entry the bridge must still count the right-most one."""
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware  # pylint: disable=import-outside-toplevel

    monkeypatch.setattr(config, "cookie_secure", "auto")
    app = ProxyHeadersMiddleware(bridge.module.app_asgi, trusted_hosts=bridge.module.TRUSTED_PROXIES)
    real = "203.0.113.50"
    # the peer is nginx on the Docker network; the client is plain http to the bridge, https to nginx
    transport = httpx.ASGITransport(app=app, client=("172.18.0.1", 40000))
    async with httpx.AsyncClient(transport=transport, base_url="http://bridge.test") as client:
        for i in range(5):
            resp = await client.post("/auth/login", json={"password": "wrong password here", "totp": "000000"},
                                     headers={"Origin": ORIGIN, "X-Forwarded-For": f"10.9.9.{i}, {real}",
                                              "X-Forwarded-Proto": "https", "X-Real-IP": f"198.51.100.{i}"})
            assert resp.status_code == 401
        locked = await client.post("/auth/login", json={"password": PASSWORD, "totp": code_at(bridge)},
                                   headers={"Origin": ORIGIN, "X-Forwarded-For": f"1.2.3.4, {real}",
                                            "X-Forwarded-Proto": "https"})
        assert locked.status_code == 429
        other = await client.post("/auth/login", json={"password": PASSWORD, "totp": code_at(bridge)},
                                  headers={"Origin": ORIGIN, "X-Forwarded-For": f"{real}, 198.51.100.200",
                                           "X-Forwarded-Proto": "https"})
    assert other.status_code == 200
    # X-Forwarded-Proto from the trusted proxy makes the request https, so COOKIE_SECURE=auto sets Secure
    assert "secure" in set_cookie_attrs(other)
    assert {r["ip"] for r in await audit_rows(bridge, "auth_login_failed")} == {real}
    assert bridge.auth.limiter.retry_after(real) > 0


async def test_an_untrusted_peer_cannot_set_its_address_with_forwarded_for(bridge):
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware  # pylint: disable=import-outside-toplevel

    app = ProxyHeadersMiddleware(bridge.module.app_asgi, trusted_hosts=bridge.module.TRUSTED_PROXIES)
    transport = httpx.ASGITransport(app=app, client=("198.51.100.66", 40000))
    async with httpx.AsyncClient(transport=transport, base_url="https://bridge.test") as client:
        resp = await client.post("/auth/login", json={"password": "wrong password here", "totp": "000000"},
                                 headers={"Origin": ORIGIN, "X-Forwarded-For": "1.2.3.4", "X-Real-IP": "1.2.3.4"})
    assert resp.status_code == 401
    assert [r["ip"] for r in await audit_rows(bridge, "auth_login_failed")] == ["198.51.100.66"]


@pytest.mark.parametrize("peer, expected", [
    ("127.0.0.1", "203.0.113.9"), ("172.18.0.1", "203.0.113.9"), ("::ffff:172.18.0.1", "203.0.113.9"),
    ("::1", "203.0.113.9"), ("198.51.100.66", "198.51.100.66"), ("169.254.1.1", "169.254.1.1"), ("", "unknown")])
async def test_x_real_ip_counts_only_from_a_trusted_proxy(bridge, peer, expected):
    assert bridge.module._peer_ip(peer, "203.0.113.9") == expected


async def test_the_internal_listener_has_no_owner_login(bridge):
    async with http(bridge, internal=True) as client:
        resp = await login(client, totp=code_at(bridge))
    assert resp.status_code == 404


# The owner's routes. GET /status and GET /settings (C-60, K-46 v51) are left out: the internal listener itself is
# their agent credential (K-43); tests/infrastructure/test_bridge_settings.py checks GET /settings there.
INTERNAL_IGNORES_COOKIE = [("GET", "/messages"), ("GET", "/livekit/token"), ("GET", "/telemetry/production"),
                           ("POST", "/feedback"), ("GET", "/media/index?area=shared")]


async def test_a_valid_session_cookie_is_ignored_on_the_internal_listener(bridge, monkeypatch, tmp_path):
    """K-38 v4: owner sessions exist only on the public listener (the _public() guard). The same valid cookie that
    opens /auth/me and the protected routes there opens nothing on :5002."""
    monkeypatch.setenv("JARVIS_SHARED_DIR", str(tmp_path / "shared"))
    async with http(bridge) as public:
        token, sid = await _logged_in(bridge, public)
        public.cookies.clear()
        assert (await public.get("/auth/me", headers=cookie_header(token))).json()["session_id"] == sid
        assert (await public.get("/messages", headers=cookie_header(token))).status_code == 200
    request_scope = {"type": "http", "server": ("bridge.test", INTERNAL_PORT), "client": ("172.18.0.5", 4000),
                     "headers": [(b"cookie", f"jarvis_session={token}".encode())]}
    assert await bridge.auth.session_from_request(Request(request_scope)) is None
    async with http(bridge, internal=True) as internal:
        me = await internal.get("/auth/me", headers=cookie_header(token))
        assert me.status_code == 401
        assert me.json() == {"ok": False, "authenticated": False}
        for method, path in INTERNAL_IGNORES_COOKIE:
            resp = await internal.request(method, path, headers=cookie_header(token),
                                          json={} if method == "POST" else None)
            assert resp.status_code in (401, 404), (path, resp.status_code, resp.text)
            assert sid not in resp.text and "owner-" not in resp.text, path
        out = await internal.post("/auth/logout", headers={**cookie_header(token), "Origin": ORIGIN})
        assert out.status_code == 404
    # nothing on :5002 touched the session: it is still valid on the public listener
    assert (await bridge.store.get_session(sid))["revoked_ts"] is None
    async with http(bridge) as public:
        assert (await public.get("/auth/me", headers=cookie_header(token))).status_code == 200


async def test_a_valid_session_cookie_does_not_open_an_internal_socket(sockets):
    token, record = await sockets.auth.mint_session(label="test")
    internal = _environ(token)
    internal["asgi.scope"]["server"] = ("bridge.test", INTERNAL_PORT)
    assert await sockets.auth.session_from_environ(internal) is None
    assert await sockets.auth.session_from_environ(_environ(token)) is not None  # the public listener takes it
    assert await sockets.module.handle_connect("sid-internal-ui", internal, None) is False
    assert "sid-internal-ui" not in sockets.module._clients
    rows = await sockets.store.recent_audit(action_prefix="socket_auth_failed")
    assert json.loads(rows[0]["detail"])["reason"] == "internal_agent_only"
    # the internal agent role still connects there, cookie or not, and never as 'ui'
    assert await sockets.module.handle_connect("sid-internal-agent", internal, {"role": "agent"}) is True
    assert sockets.module._clients["sid-internal-agent"].role == "agent"
    assert not sockets.module._clients["sid-internal-agent"].session_id
    assert (await sockets.store.get_session(record["sid"]))["revoked_ts"] is None


# ---------------------------------------------------------------- sessions: me, logout, expiry, revocation, rotation


async def _logged_in(ns: Any, client: httpx.AsyncClient) -> tuple[str, str]:
    resp = await login(client, totp=code_at(ns))
    assert resp.status_code == 200
    return session_cookie(resp), resp.json()["session_id"]


async def test_me_reports_the_session_and_401_without_one(bridge):
    async with http(bridge) as client:
        assert (await client.get("/auth/me")).json() == {"ok": False, "authenticated": False}
        token, sid = await _logged_in(bridge, client)
        client.cookies.clear()
        me = await client.get("/auth/me", headers=cookie_header(token))
    assert me.status_code == 200
    assert me.json() == {"ok": True, "authenticated": True, "user": "owner", "session_id": sid,
                         "expires_at": int(START) + 43200}


async def test_logout_revokes_the_session_and_clears_the_cookie(bridge):
    async with http(bridge) as client:
        token, sid = await _logged_in(bridge, client)
        client.cookies.clear()
        out = await client.post("/auth/logout", headers={**cookie_header(token), "Origin": ORIGIN})
        assert out.status_code == 200 and out.json() == {"ok": True}
        attrs = set_cookie_attrs(out)
        assert attrs["max-age"] == "0" and attrs["path"] == "/vortex"
        client.cookies.clear()
        me = await client.get("/auth/me", headers=cookie_header(token))
        protected = await client.get("/messages", headers=cookie_header(token))
    assert me.status_code == 401
    assert protected.status_code == 401
    row = await bridge.store.get_session(sid)
    assert row["revoked_ts"] is not None
    assert await audit_rows(bridge, "auth_logout")


async def test_logout_needs_an_allowed_origin(bridge):
    async with http(bridge) as client:
        token, sid = await _logged_in(bridge, client)
        client.cookies.clear()
        resp = await client.post("/auth/logout", headers=cookie_header(token))
    assert resp.status_code == 403
    assert (await bridge.store.get_session(sid))["revoked_ts"] is None


async def test_an_expired_session_is_refused(bridge):
    async with http(bridge) as client:
        token = (await _logged_in(bridge, client))[0]
        client.cookies.clear()
        bridge.clock.now += 43200 - 1
        assert (await client.get("/auth/me", headers=cookie_header(token))).status_code == 200
        bridge.clock.now += 2
        assert (await client.get("/auth/me", headers=cookie_header(token))).status_code == 401
        resp = await client.get("/messages", headers=cookie_header(token))
    assert resp.status_code == 401 and resp.json() == {"error": "auth_required"}


def _jwt_for(sid: str, *, iat: float, exp: float) -> str:
    return jwt.encode({"sub": "owner", "sid": sid, "iat": int(iat), "exp": int(exp), "v": 1}, JWT_SECRET,
                      algorithm="HS256")


async def test_the_session_rows_expiry_counts_even_when_the_jwt_lives_longer(bridge):
    """Only the sessions-table check can refuse this cookie: its JWT exp is 12 h away, its row ends after 60 s."""
    record = (await bridge.auth.mint_session(ttl_s=60, label="test"))[1]
    long_jwt = _jwt_for(record["sid"], iat=bridge.clock(), exp=bridge.clock() + 43200)
    async with http(bridge) as client:
        assert (await client.get("/auth/me", headers=cookie_header(long_jwt))).status_code == 200
        bridge.clock.now += 61
        assert (await client.get("/auth/me", headers=cookie_header(long_jwt))).status_code == 401
        assert (await client.get("/messages", headers=cookie_header(long_jwt))).status_code == 401
    assert (await bridge.store.get_session(record["sid"]))["revoked_ts"] is None


async def test_the_jwts_exp_counts_even_when_the_session_row_lives_longer(bridge):
    """Only the JWT exp check can refuse this cookie: its row is valid for 12 h, its JWT ends after 60 s."""
    token, record = await bridge.auth.mint_session(label="test")
    short_jwt = _jwt_for(record["sid"], iat=bridge.clock(), exp=bridge.clock() + 60)
    async with http(bridge) as client:
        assert (await client.get("/auth/me", headers=cookie_header(short_jwt))).status_code == 200
        bridge.clock.now += 61
        assert (await client.get("/auth/me", headers=cookie_header(short_jwt))).status_code == 401
        assert (await client.get("/messages", headers=cookie_header(short_jwt))).status_code == 401
        # the row itself is still good: the cookie the bridge issued for it still works
        assert (await client.get("/auth/me", headers=cookie_header(token))).status_code == 200


async def test_revoke_all_ends_every_session(bridge):
    async with http(bridge) as client:
        token = (await _logged_in(bridge, client))[0]
        client.cookies.clear()
        assert await bridge.store.revoke_all_sessions() == 1
        assert (await client.get("/auth/me", headers=cookie_header(token))).status_code == 401


async def test_a_jwt_without_a_session_row_or_with_another_key_is_refused(bridge):
    now = int(START)
    unknown = jwt.encode({"sub": "owner", "sid": secrets.token_hex(16), "iat": now, "exp": now + 600, "v": 1},
                         JWT_SECRET, algorithm="HS256")
    async with http(bridge) as client:
        sid = (await _logged_in(bridge, client))[1]
        client.cookies.clear()
        forged = jwt.encode({"sub": "owner", "sid": sid, "iat": now, "exp": now + 600, "v": 1},
                            "another-key-" + "z" * 40, algorithm="HS256")
        none_alg = jwt.encode({"sub": "owner", "sid": sid, "iat": now, "exp": now + 600, "v": 1}, key=None,
                              algorithm="none")
        for token in (unknown, forged, none_alg, "garbage", "a.b.c"):
            assert (await client.get("/auth/me", headers=cookie_header(token))).status_code == 401


async def test_a_new_login_with_a_cookie_rotates_the_old_session(bridge):
    async with http(bridge) as client:
        old_token, old_sid = await _logged_in(bridge, client)
        client.cookies.clear()
        bridge.clock.now += 30
        resp = await login(client, totp=code_at(bridge), cookie=old_token)
        assert resp.status_code == 200
        new_sid = resp.json()["session_id"]
        client.cookies.clear()
        assert (await client.get("/auth/me", headers=cookie_header(old_token))).status_code == 401
    assert new_sid != old_sid
    assert (await bridge.store.get_session(old_sid))["revoked_ts"] is not None


# ---------------------------------------------------------------- protected endpoints


PROTECTED = [("GET", "/messages"), ("GET", "/livekit/token"), ("GET", "/telemetry/production"),
             ("POST", "/feedback"), ("GET", "/status"), ("GET", "/media/index?area=shared")]


@pytest.mark.parametrize("method, path", PROTECTED)
async def test_protected_endpoints_answer_401_without_credentials(bridge, method, path):
    async with http(bridge) as client:
        resp = await client.request(method, path, json={} if method == "POST" else None)
    assert resp.status_code == 401
    assert resp.json() == {"error": "auth_required"}


@pytest.mark.parametrize("method, path", [p for p in PROTECTED if p[1] != "/livekit/token"])
async def test_protected_endpoints_take_the_session(bridge, method, path, monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_SHARED_DIR", str(tmp_path / "shared"))
    async with http(bridge) as client:
        token = (await _logged_in(bridge, client))[0]
        client.cookies.clear()
        resp = await client.request(method, path, headers=cookie_header(token),
                                    json={} if method == "POST" else None)
    assert resp.status_code != 401, resp.text
    if path in ("/messages", "/telemetry/production", "/feedback", "/status"):
        assert resp.status_code == 200, resp.text


async def test_telemetry_production_is_awaited_not_a_500(bridge):
    async with http(bridge) as client:
        resp = await client.get("/telemetry/production", headers={"X-Vortex-Token": LEGACY_TOKEN})
    assert resp.status_code == 200
    assert isinstance(resp.json(), dict)


async def test_status_takes_the_agents_signed_headers(bridge):
    ts = str(int(time.time()))
    nonce = secrets.token_hex(16)
    headers = {"X-Agent-Token": AGENT_TOKEN, "X-Agent-Timestamp": ts, "X-Agent-Nonce": nonce,
               "X-Agent-Signature": jarvis_security.sign_notify(ts, nonce, b"")}
    async with http(bridge) as client:
        ok = await client.get("/status", headers=headers)
        replay = await client.get("/status", headers=headers)
        bad = await client.get("/status", headers={**headers, "X-Agent-Nonce": secrets.token_hex(16)})
    assert ok.status_code == 200
    assert replay.status_code == 401
    assert bad.status_code == 401


# ---------------------------------------------------------------- LiveKit token (C-LK-1)


class FakeLiveKitAPI:
    """Stands in for livekit.api.LiveKitAPI behind the room owner (C-23): an empty server, so ensure('token') creates
    the room and dispatches. Nothing may delete a room or a dispatch. fail / delay make every call raise / wait."""

    created: list[Any] = []
    dispatched: list[Any] = []
    fail: Exception | None = None
    delay: float = 0.0

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        self.room = self
        self.agent_dispatch = self
        self.rooms: list[Any] = []

    async def _gate(self) -> None:
        if FakeLiveKitAPI.delay:
            await asyncio.sleep(FakeLiveKitAPI.delay)
        if FakeLiveKitAPI.fail is not None:
            raise FakeLiveKitAPI.fail

    async def list_rooms(self, _req: Any) -> Any:
        await self._gate()
        return SimpleNamespace(rooms=list(self.rooms))

    async def list_participants(self, _req: Any) -> Any:
        await self._gate()
        return SimpleNamespace(participants=[])

    async def list_dispatch(self, room_name: str) -> list[Any]:
        await self._gate()
        return []

    async def delete_room(self, _req: Any) -> None:
        raise AssertionError("no bridge path deletes a room (K-37 v9)")

    async def delete_dispatch(self, *_args: Any) -> None:
        raise AssertionError("no bridge path deletes a dispatch (K-44 v9)")

    async def create_room(self, req: Any) -> Any:
        await self._gate()
        assert not list(req.agents), "create_room never carries an agents list (K-44 v9)"
        FakeLiveKitAPI.created.append(req)
        self.rooms = [SimpleNamespace(name=req.name, sid="RM_c23_token")]
        return self.rooms[0]

    async def create_dispatch(self, req: Any) -> Any:
        await self._gate()
        FakeLiveKitAPI.dispatched.append(req)
        return SimpleNamespace(id="AD_c23_token")

    async def aclose(self) -> None:
        return None


@pytest.fixture
def livekit(bridge, monkeypatch):
    """LiveKit keys and an enabled room owner (as in the VPS bridge, pc mode) on FakeLiveKitAPI."""
    from livekit import api as lk_api  # pylint: disable=import-outside-toplevel

    from src.core import bridge_rooms  # pylint: disable=import-outside-toplevel

    FakeLiveKitAPI.created, FakeLiveKitAPI.dispatched = [], []
    FakeLiveKitAPI.fail, FakeLiveKitAPI.delay = None, 0.0
    monkeypatch.setattr(lk_api, "LiveKitAPI", FakeLiveKitAPI)
    monkeypatch.setenv("LIVEKIT_API_KEY", LK_KEY)
    monkeypatch.setenv("LIVEKIT_API_SECRET", LK_SECRET)
    monkeypatch.setenv("LIVEKIT_URL", "ws://livekit.test:7880")
    monkeypatch.setenv("LIVEKIT_PUBLIC_URL", "wss://jarvis.srv1802669.hstgr.cloud")
    monkeypatch.setenv("JARVIS_ROOM_NAME", "jarvis-hybrid")
    settings = bridge_rooms.RoomSettings(api_url="http://livekit.test:7880", key=LK_KEY, secret=LK_SECRET,
                                         room="jarvis-hybrid", role_ok=True)
    monkeypatch.setattr(bridge.module, "rooms", bridge_rooms.RoomManager(settings, store=lambda: bridge.store,
                                                                         api_factory=FakeLiveKitAPI))
    return FakeLiveKitAPI


async def test_livekit_token_with_a_session_has_a_stable_owner_identity(bridge, livekit):
    async with http(bridge) as client:
        token, sid = await _logged_in(bridge, client)
        client.cookies.clear()
        first = await client.get("/livekit/token?identity=attacker", headers=cookie_header(token))
        second = await client.get("/livekit/token", headers=cookie_header(token))
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["identity"] == f"owner-{sid[:8]}" == second.json()["identity"]
    assert body["room"] == "jarvis-hybrid" and body["url"] == "wss://jarvis.srv1802669.hstgr.cloud"
    claims = jwt.decode(body["token"], LK_SECRET, algorithms=["HS256"], options={"verify_aud": False})
    assert claims["sub"] == f"owner-{sid[:8]}"
    assert claims["name"] == "Owner"
    assert claims["attributes"] == {"role": "owner"}
    assert claims["exp"] - claims["nbf"] <= 3600 + 1
    assert body["expires_at"] - int(time.time()) in range(3595, 3601)
    video = claims["video"]
    assert video["roomJoin"] and video["room"] == "jarvis-hybrid"
    assert video["canPublish"] and video["canSubscribe"] and video["canPublishData"]
    # K-37 v9: the room owner's ensure('token') ran before the mint: the missing room was created (never an agents
    # list). In pc mode with nothing reported yet the target is '' (the PC worker's name until C-23 phase 3), so the
    # new room's implicit '' dispatch is the dispatch and nothing is added beside it.
    assert [req.name for req in livekit.created] == ["jarvis-hybrid"]
    assert livekit.dispatched == []


async def test_livekit_token_is_401_without_a_session(bridge, livekit):
    async with http(bridge) as client:
        resp = await client.get("/livekit/token", headers=cookie_header("not-a-jwt"))
    assert resp.status_code == 401
    assert not livekit.created


async def test_livekit_token_is_minted_when_the_room_ensure_fails(bridge, livekit):
    livekit.fail = RuntimeError("livekit unreachable")
    async with http(bridge) as client:
        resp = await client.get("/livekit/token", headers={"X-Vortex-Token": LEGACY_TOKEN})
    assert resp.status_code == 200 and resp.json()["token"]
    assert not livekit.created and not livekit.dispatched
    assert bridge.module.rooms.snapshot()["ok"] is False  # the status says LiveKit is not readable


async def test_livekit_token_waits_at_most_three_seconds_for_the_room_ensure(bridge, livekit, monkeypatch):
    monkeypatch.setattr(bridge.module, "ROOM_TOKEN_WAIT_S", 0.05)
    livekit.delay = 0.3  # every LiveKit call is slow: the whole ensure takes about 2 s
    async with http(bridge) as client:
        started = time.monotonic()
        resp = await client.get("/livekit/token", headers={"X-Vortex-Token": LEGACY_TOKEN})
        waited = time.monotonic() - started
    assert resp.status_code == 200 and resp.json()["token"]
    assert waited < 0.3, "the token waited for the whole ensure"
    for _ in range(200):  # the shielded ensure finishes in the background
        if bridge.module.rooms._last_result is not None:
            break
        await asyncio.sleep(0.05)
    assert bridge.module.rooms._last_result.result == "in_flight"  # the new room's implicit '' dispatch
    assert [req.name for req in livekit.created] == ["jarvis-hybrid"]
    assert livekit.dispatched == []


@pytest.mark.usefixtures("livekit")
async def test_livekit_token_legacy_header_still_works(bridge):
    async with http(bridge) as client:
        resp = await client.get("/livekit/token?identity=x", headers={"X-Vortex-Token": LEGACY_TOKEN})
    assert resp.status_code == 200
    identity = resp.json()["identity"]
    assert identity.startswith("owner-") and identity != "owner-x"


@pytest.mark.usefixtures("livekit")
async def test_livekit_token_without_keys_is_livekit_unavailable(bridge, monkeypatch):
    monkeypatch.delenv("LIVEKIT_API_SECRET")
    async with http(bridge) as client:
        resp = await client.get("/livekit/token", headers={"X-Vortex-Token": LEGACY_TOKEN})
    assert resp.status_code == 503 and resp.json() == {"error": "livekit_unavailable"}


# ---------------------------------------------------------------- legacy paths keep working until C-22


@pytest.mark.parametrize("method, path", [("GET", "/messages"), ("GET", "/telemetry/production"),
                                          ("POST", "/feedback"), ("GET", "/status")])
async def test_the_legacy_header_still_opens_the_ui_endpoints(bridge, method, path):
    async with http(bridge) as client:
        resp = await client.request(method, path, headers={"X-Vortex-Token": LEGACY_TOKEN},
                                    json={} if method == "POST" else None)
    assert resp.status_code == 200, resp.text


async def test_the_legacy_header_stops_when_legacy_auth_is_off(bridge, monkeypatch):
    monkeypatch.setattr(config, "legacy_token_auth", False)
    async with http(bridge) as client:
        legacy = await client.get("/messages", headers={"X-Vortex-Token": LEGACY_TOKEN})
        token = (await _logged_in(bridge, client))[0]
        client.cookies.clear()
        session = await client.get("/messages", headers=cookie_header(token))
    assert legacy.status_code == 401
    assert session.status_code == 200


async def test_signed_notify_from_the_agent_is_unchanged(bridge):
    raw = json.dumps({"type": "log", "payload": {"message": "c19"}}, separators=(",", ":")).encode()
    ts, nonce = str(int(time.time())), secrets.token_hex(16)
    headers = {"Content-Type": "application/json", "X-Agent-Token": AGENT_TOKEN, "X-Agent-Timestamp": ts,
               "X-Agent-Nonce": nonce, "X-Agent-Signature": jarvis_security.sign_notify(ts, nonce, raw)}
    async with http(bridge) as client:
        resp = await client.post("/notify", content=raw, headers=headers)
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------- Socket.IO: cookie role 'ui', legacy and agent


@pytest.fixture
def sockets(bridge, monkeypatch):
    emitted: list[tuple[str, Any, Any]] = []
    disconnected: list[str] = []

    async def fake_emit(event: str, data: Any = None, **kwargs: Any) -> None:
        emitted.append((event, data, kwargs.get("to") or kwargs.get("room")))

    async def fake_disconnect(sid: str, **_kwargs: Any) -> None:
        disconnected.append(sid)

    async def fake_enter_room(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(bridge.module.sio, "emit", fake_emit)
    monkeypatch.setattr(bridge.module.sio, "disconnect", fake_disconnect)
    monkeypatch.setattr(bridge.module.sio, "enter_room", fake_enter_room)
    bridge.emitted, bridge.disconnected = emitted, disconnected
    return bridge


def _environ(cookie: str | None = None) -> dict[str, Any]:
    environ: dict[str, Any] = {"asgi.scope": {"type": "http", "server": ("bridge.test", 443),
                                              "client": ("127.0.0.1", 5555)}}
    if cookie is not None:
        environ["HTTP_COOKIE"] = f"theme=dark; jarvis_session={cookie}"
    return environ


async def test_socket_with_a_session_cookie_gets_role_ui(sockets):
    token = (await sockets.auth.mint_session(label="test"))[0]
    assert await sockets.module.handle_connect("sid-ui", _environ(token), None) is True
    client = sockets.module._clients["sid-ui"]
    assert client.role == "ui" and client.session_id
    assert any(event == "init_state" for event, _data, _to in sockets.emitted)


async def test_socket_with_a_bad_cookie_is_refused_and_audited(sockets):
    assert await sockets.module.handle_connect("sid-bad", _environ("x.y.z"), None) is False
    rows = await sockets.store.recent_audit(action_prefix="socket_auth_failed")
    assert json.loads(rows[0]["detail"])["reason"] == "bad_session"


async def test_legacy_and_agent_sockets_still_connect(sockets):
    assert await sockets.module.handle_connect("sid-legacy", {}, {"token": LEGACY_TOKEN}) is True
    assert sockets.module._clients["sid-legacy"].role == "legacy"
    auth = {"agent_token": AGENT_TOKEN, "agent_id": "pc-host", "version": "abc"}
    # an agent with a stray cookie stays an agent
    assert await sockets.module.handle_connect("sid-agent", _environ("x.y.z"), auth) is True
    assert sockets.module._clients["sid-agent"].role == "agent"


async def test_a_revoked_session_ends_its_ui_sockets(sockets):
    token, record = await sockets.auth.mint_session(label="test")
    assert await sockets.module.handle_connect("sid-ui", _environ(token), None) is True
    assert await sockets.module.handle_connect("sid-legacy", {}, {"token": LEGACY_TOKEN}) is True
    await sockets.store.revoke_all_sessions()
    await sockets.module._expire_ui_sessions()
    assert ("session_expired", {"reason": "expired"}, "sid-ui") in sockets.emitted
    assert sockets.disconnected == ["sid-ui"]
    # logout ends them at once with reason 'logout'
    token2, _ = await sockets.auth.mint_session(label="test")
    assert await sockets.module.handle_connect("sid-ui2", _environ(token2), None) is True
    async with http(sockets) as client:
        out = await client.post("/auth/logout", headers={**cookie_header(token2), "Origin": ORIGIN})
    assert out.status_code == 200
    assert ("session_expired", {"reason": "logout"}, "sid-ui2") in sockets.emitted
    assert record["sid"]


# ---------------------------------------------------------------- audit holds no secrets


async def test_audit_rows_never_hold_a_password_code_hash_or_token(bridge):
    async with http(bridge) as client:
        await login(client, password="wrong password here", totp="123456")
        resp = await login(client, totp=code_at(bridge))
        token = session_cookie(resp)
        await login(client, recovery=bridge.creds.codes[0])
    rows = await bridge.store.recent_audit(limit=500)
    text = json.dumps(rows)
    secrets_seen = [PASSWORD, "wrong password here", token, JWT_SECRET, bridge.creds.totp_secret,
                    bridge.creds.password_hash, *bridge.creds.codes, *bridge.creds.recovery_hashes]
    assert not [s for s in secrets_seen if s in text]
    assert resp.json()["session_id"] not in text  # only its first 8 characters


def test_redact_secrets_masks_every_owner_credential(monkeypatch, creds):
    """K-29: the C-19 owner credentials are on the redaction list; OWNER_RECOVERY_HASHES is masked whole and item by
    item (a log line may hold a single hash)."""
    for name in bridge_auth.ENV_KEYS:
        assert name in jarvis_security.SECRET_ENV_NAMES, name
    for name in ("AGENT_TOKEN", "BRIDGE_HMAC_SECRET", "VORTEX_SECURITY_TOKEN", "LIVEKIT_API_SECRET"):
        assert name in jarvis_security.SECRET_ENV_NAMES, name
    monkeypatch.setattr(jarvis_security, "_runtime_secrets", set())
    for name in jarvis_security.SECRET_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    joined = ",".join(creds.recovery_hashes)
    monkeypatch.setenv("SESSION_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("OWNER_PASSWORD_HASH", creds.password_hash)
    monkeypatch.setenv("OWNER_TOTP_SECRET", creds.totp_secret)
    monkeypatch.setenv("OWNER_RECOVERY_HASHES", " , ".join(creds.recovery_hashes) + ", short")
    line = (f"jwt={JWT_SECRET} pw {creds.password_hash} totp {creds.totp_secret} all {joined} "
            f"one {creds.recovery_hashes[1]} end")
    redacted = jarvis_security.redact_secrets(line)
    masked_list = ",".join(["***"] * len(creds.recovery_hashes))
    assert redacted == f"jwt=*** pw *** totp *** all {masked_list} one *** end"
    whole = jarvis_security.redact_secrets(f"env {' , '.join(creds.recovery_hashes)}, short")
    assert not [h for h in creds.recovery_hashes if h in whole]
    assert "short" in jarvis_security.redact_secrets("a short word")  # items under 8 characters stay
    monkeypatch.setenv("DESKTOP_AGENT_SIGNING_KEY", "ab" * 32)
    assert jarvis_security.redact_secrets("key " + "ab" * 32) == "key ***"


# ---------------------------------------------------------------- hashes and env encoding


def test_hashes_are_base64url_without_dollar_signs(creds):
    for value in (creds.password_hash, *creds.recovery_hashes):
        assert "$" not in value and "=" not in value
        assert bridge_auth.decode_bcrypt_hash(value) is not None
    assert bridge_auth.verify_password(PASSWORD, bridge_auth.decode_bcrypt_hash(creds.password_hash))
    assert not bridge_auth.verify_password(PASSWORD + "x", bridge_auth.decode_bcrypt_hash(creds.password_hash))
    assert not bridge_auth.verify_password("x" * 100, bridge_auth.decode_bcrypt_hash(creds.password_hash))
    assert bridge_auth.decode_bcrypt_hash("not-a-hash") is None


@pytest.mark.parametrize("password", ["short", "password1234", "aaaaaaaaaaaaaaaa", "é" * 40])
def test_weak_or_unhashable_passwords_are_refused(password):
    assert bridge_auth.password_problem(password) is not None
    with pytest.raises(ValueError):
        bridge_auth.hash_password(password, cost=FAST)


def test_write_env_values_replaces_and_appends_and_keeps_other_lines(tmp_path):
    path = tmp_path / "bridge.env"
    path.write_text("# comment\nAGENT_TOKEN=keep\nOWNER_TOTP_SECRET=old\n", encoding="utf-8")
    bridge_auth.write_env_values(str(path), {"OWNER_TOTP_SECRET": "NEW", "SESSION_JWT_SECRET": "s"})
    assert path.read_text(encoding="utf-8").splitlines() == [
        "# comment", "AGENT_TOKEN=keep", "OWNER_TOTP_SECRET=NEW", "SESSION_JWT_SECRET=s"]
    assert bridge_auth.env_file_has(str(path), "SESSION_JWT_SECRET")
    assert not bridge_auth.env_file_has(str(path), "OWNER_PASSWORD_HASH")
    if os.name != "nt":
        assert (path.stat().st_mode & 0o777) == 0o600


# ---------------------------------------------------------------- CLI


def _cli(argv: list[str], db: str) -> int:
    return bridge_auth.main(["--db", db, *argv])


def test_cli_mint_session_prints_a_cookie_that_the_bridge_accepts(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "session_jwt_secret", JWT_SECRET)
    db = str(tmp_path / "cli.db")
    assert _cli(["mint-session", "--ttl", "300", "--label", "probe"], db) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1 and out[0].startswith("jarvis_session=")
    claims = jwt.decode(out[0].split("=", 1)[1], JWT_SECRET, algorithms=["HS256"])
    assert claims["exp"] - claims["iat"] == 300

    async def check() -> None:
        store = BridgeStore(db)
        try:
            row = await store.get_session(claims["sid"])
            assert row is not None and row["label"] == "cli:probe"
            actions = [r["action"] for r in await store.recent_audit(action_prefix="auth")]
            assert actions == ["auth_session_minted"]
        finally:
            await store.close()

    asyncio.run(check())
    assert _cli(["mint-session", "--ttl", "0"], db) == 2
    assert _cli(["mint-session", "--ttl", "43201"], db) == 2


def test_cli_mint_session_without_a_secret_mints_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "session_jwt_secret", None)
    assert _cli(["mint-session"], str(tmp_path / "cli.db")) == 1
    captured = capsys.readouterr()
    assert "jarvis_session=" not in captured.out


def test_cli_enroll_check_names_settings_never_values(tmp_path, monkeypatch, capsys, creds):
    db = str(tmp_path / "cli.db")
    monkeypatch.setattr(config, "session_jwt_secret", None)
    monkeypatch.setattr(config, "owner_password_hash", None)
    monkeypatch.setattr(config, "owner_totp_secret", None)
    monkeypatch.setattr(config, "owner_recovery_hashes", [])
    assert _cli(["enroll-check"], db) == 1
    assert "owner_not_enrolled" in capsys.readouterr().out
    monkeypatch.setattr(config, "session_jwt_secret", JWT_SECRET)
    monkeypatch.setattr(config, "owner_password_hash", creds.password_hash)
    monkeypatch.setattr(config, "owner_totp_secret", creds.totp_secret)
    monkeypatch.setattr(config, "owner_recovery_hashes", list(creds.recovery_hashes))
    assert _cli(["enroll-check"], db) == 0
    out = capsys.readouterr().out
    assert "enrolled" in out and "3 usable of 3" in out
    for value in (JWT_SECRET, creds.password_hash, creds.totp_secret, *creds.recovery_hashes):
        assert value not in out


def test_cli_revoke_all_and_audit_summary(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "session_jwt_secret", JWT_SECRET)
    db = str(tmp_path / "cli.db")
    assert _cli(["mint-session", "--ttl", "60"], db) == 0
    assert _cli(["revoke-all"], db) == 0
    assert "revoked 1 session(s)" in capsys.readouterr().out
    assert _cli(["audit-summary", "--prefix", "auth"], db) == 0
    out = capsys.readouterr().out
    assert "auth_session_minted: 1" in out and "auth_revoke_all: 1" in out


async def test_cli_hash_password_prints_only_a_hash(tmp_path, capsys):
    store = BridgeStore(tmp_path / "cli.db")
    try:
        answers = iter([PASSWORD, PASSWORD])
        assert await bridge_auth._cmd_hash_password(store, lambda _prompt: next(answers)) == 0
        printed = capsys.readouterr().out.strip()
        assert PASSWORD not in printed
        assert bridge_auth.verify_password(PASSWORD, bridge_auth.decode_bcrypt_hash(printed))
        answers = iter([PASSWORD, PASSWORD + "!"])
        assert await bridge_auth._cmd_hash_password(store, lambda _prompt: next(answers)) == 1
        assert [r["action"] for r in await store.recent_audit(action_prefix="auth")] == [
            "auth_cli_hash_password", "auth_cli_hash_password"]
    finally:
        await store.close()


async def test_cli_enroll_writes_the_env_file_and_shows_codes_once(tmp_path, capsys):
    store = BridgeStore(tmp_path / "cli.db")
    env_file = tmp_path / "bridge.env"
    env_file.write_text("AGENT_TOKEN=keep\n", encoding="utf-8")
    seen: dict[str, str] = {}

    def ask_visible(_prompt: str) -> str:
        uri = [line for line in capsys.readouterr().out.splitlines() if line.startswith("otpauth://")][0]
        seen["secret"] = uri.split("secret=")[1].split("&")[0]
        return pyotp.TOTP(seen["secret"]).at(START)

    passwords = iter([PASSWORD, PASSWORD])
    try:
        code = await bridge_auth._cmd_enroll(store, str(env_file), ask=lambda _p: next(passwords),
                                             ask_visible=ask_visible, interactive=True, force=False,
                                             clock=lambda: START)
        assert code == 0
        out = capsys.readouterr().out
        lines = env_file.read_text(encoding="utf-8").splitlines()
        values = dict(line.split("=", 1) for line in lines)
        assert values["AGENT_TOKEN"] == "keep"
        assert values["OWNER_TOTP_SECRET"] == seen["secret"]
        assert len(values["SESSION_JWT_SECRET"]) >= 64
        assert bridge_auth.verify_password(PASSWORD, bridge_auth.decode_bcrypt_hash(values["OWNER_PASSWORD_HASH"]))
        hashes = values["OWNER_RECOVERY_HASHES"].split(",")
        assert len(hashes) == 10 and all("$" not in h for h in hashes)
        printed_codes = [line.strip() for line in out.splitlines() if line.startswith("  ")]
        assert len(printed_codes) == 10
        assert bridge_auth.verify_recovery_code(printed_codes[0], hashes[0])
        assert PASSWORD not in out
        # a second run without --force refuses to replace the owner
        again = await bridge_auth._cmd_enroll(store, str(env_file), ask=lambda _p: "", ask_visible=lambda _p: "",
                                              interactive=True, force=False)
        assert again == 1
        # and a non-interactive run never shows anything
        assert await bridge_auth._cmd_enroll(store, str(tmp_path / "x.env"), ask=lambda _p: "",
                                             ask_visible=lambda _p: "", interactive=False, force=False) == 2
        assert not (tmp_path / "x.env").exists()
        actions = [r["action"] for r in await store.recent_audit(action_prefix="auth_enroll")]
        assert actions == ["auth_enroll"]
    finally:
        await store.close()


# ---------------------------------------------------------------- config


def test_config_reads_the_owner_settings(monkeypatch):
    from services.utils.jarvis_config import JarvisConfig  # pylint: disable=import-outside-toplevel

    monkeypatch.setenv("OWNER_RECOVERY_HASHES", "aaa, bbb ,ccc")
    monkeypatch.setenv("COOKIE_SECURE", "auto")
    monkeypatch.delenv("COOKIE_PATH", raising=False)
    fresh = JarvisConfig()
    assert fresh.owner_recovery_hashes == ["aaa", "bbb", "ccc"]
    assert fresh.cookie_secure == "auto"
    assert fresh.cookie_path == "/vortex"
    monkeypatch.setenv("COOKIE_SECURE", "no")
    assert JarvisConfig().cookie_secure == "0"
