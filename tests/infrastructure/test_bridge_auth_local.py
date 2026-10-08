"""Plan task C-36: local/desktop mode auth parity (K-09 DASH-LOCAL-AUTH, on top of C-AUTH-1 / K-36).

- COOKIE_SECURE=auto is the default: the session cookie is Secure only for an HTTPS request. Both bridges are served by
  ui_bridge.main(), uvicorn with proxy_headers and forwarded_allow_ips=TRUSTED_PROXIES: the X-Forwarded-Proto of a
  TRUSTED_PROXIES peer sets the ASGI scheme (lower-case http/https/ws/wss; of several lines the last one) and the
  scheme decides. is_https_request's own rule (exactly one line saying https, from a trusted scope['client'], which
  uvicorn may have set from X-Forwarded-For) covers only what uvicorn left alone. A header from any other peer is
  ignored, and COOKIE_SECURE=1/0 force the flag. The VPS (compose COOKIE_SECURE=1 behind nginx) keeps Secure.
- A bridge without owner credentials answers 503 owner_not_enrolled and logs one WARNING naming what is missing.
- scripts/ops/set_owner_credentials.py --target local writes the local login into the root .env and nowhere else.

The HTTP tests drive the real bridge app through httpx.ASGITransport as a TCP peer of the test's choosing, through
uvicorn's ProxyHeadersMiddleware as ui_bridge.main() serves both bridges (vortex.py runs it on the PC). The 'bare'
variants call the app without it, which is what is_https_request's own rule sees of a request uvicorn left alone.
Every credential is a throwaway value made here (bcrypt cost 4), bridge.db and every env file are temporary, and
getpass, input, HTTP and ssh are replaced, so nothing reaches the live bridge, the VPS or the PC's real .env.
"""
# pylint: disable=protected-access,redefined-outer-name,too-many-positional-arguments
from __future__ import annotations

import asyncio
import contextlib
import email.message
import importlib
import importlib.util
import io
import json
import logging
import os
import re
import stat
import sys
import urllib.error
from collections import OrderedDict
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pyotp
import pytest
import yaml
from dotenv import dotenv_values
from starlette.requests import Request
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from services.utils.jarvis_config import JarvisConfig, config
from src.core import bridge_auth
from src.core.bridge_security import NonceCache, TokenBucket
from src.core.bridge_store import BridgeStore

ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location("jarvis_ops_test_c36_set_owner_credentials",
                                               ROOT / "scripts" / "ops" / "set_owner_credentials.py")
assert _SPEC is not None and _SPEC.loader is not None
soc = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = soc  # dataclasses look the module up
_SPEC.loader.exec_module(soc)

VPS_ORIGIN = "https://jarvis.srv1802669.hstgr.cloud"
DEV_ORIGIN = "http://localhost:5173"              # the Vite dev page (npm run vite-dev)
DEV_ORIGIN_IP = "http://127.0.0.1:5173"
LOCAL_BRIDGE = "http://127.0.0.1:5001"            # vortex.py's bridge; VITE_DEV_BRIDGE_STRIP=1 drops the /vortex prefix
PASSWORD = "c36 fake owner passphrase"            # throwaway test value
JWT_SECRET = "c36-fake-session-jwt-secret-" + "j" * 40
OLD_JWT = "c36-fake-older-jwt-secret-" + "o" * 40  # usable: 32+ characters
SECRET = "CTHIRTYSIXFAKETESTSECRETAAAAAAAA"       # throwaway base32 test secret
FAKE_COOKIE = "c36.fake.cookie-value-never-printed"
LK_KEY, LK_SECRET = "c36-fake-lk-key", "c36-fake-lk-secret-" + "k" * 32
START = 1_790_000_000.0                           # a fixed fake 'now'; TOTP codes are made for it
FAST = 4                                          # bcrypt cost for test hashes
# Peers in TRUSTED_PROXIES (loopback, Docker's networks, a LAN, an IPv4-mapped one) and peers outside it
# (internet addresses from the documentation ranges, IPv4 and IPv6, and link-local).
TRUSTED_PEERS = ["127.0.0.1", "::1", "172.18.0.1", "10.0.0.5", "192.168.1.20", "::ffff:172.18.0.1"]
UNTRUSTED_PEERS = ["198.51.100.66", "203.0.113.9", "192.0.2.10", "169.254.1.1", "2001:db8::1"]
# A test on both ways to reach the app: as ui_bridge.main() serves it, and the bare app (client(proxy=False)).
STACKS = pytest.mark.parametrize("proxy", [True, False], ids=["served", "bare"])
CODE_RE = re.compile(r"[a-z2-9]{4}-[a-z2-9]{4}-[a-z2-9]{4}")
# A PC root .env (fake values) with CRLF endings, an 'export' line, a commented key and a stale recovery list.
ROOT_ENV = ("# JARVIS PC env (fake)\r\n"
            "GOOGLE_API_KEY=c36-fake-google-key\r\n"
            "OWNER_TOTP_SECRET=OLDFAKETOTPSECRETAAAAAAA\r\n"
            "export OWNER_PASSWORD_HASH = c36-old-fake-hash  \r\n"
            "OWNER_RECOVERY_HASHES=c36-old-fake-hash-a,c36-old-fake-hash-b\r\n"
            "ALLOWED_ORIGINS=http://localhost:5173,http://127.0.0.1:5173\r\n"
            "# SESSION_JWT_SECRET=commented-out-stays\r\n"
            "AGENT_TOKEN=c36-fake-agent-token\r\n")


class FakeClock:
    def __init__(self, now: float = START) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class Records(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@contextlib.contextmanager
def captured(name: str) -> Iterator[list[logging.LogRecord]]:
    """Every record of one logger at DEBUG and up (the bridge's logger does not propagate, so caplog misses it)."""
    logger = logging.getLogger(name)
    handler, level = Records(), logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        yield handler.records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(level)


@pytest.fixture
def bridge_log() -> Iterator[list[logging.LogRecord]]:
    with captured("UI-BRIDGE") as records:
        yield records


@pytest.fixture(scope="module")
def password_hash() -> str:
    return bridge_auth.hash_password(PASSWORD, cost=FAST)


@pytest.fixture
async def bridge(monkeypatch, tmp_path, password_hash):
    """ui_bridge as vortex.py runs it on the PC: COOKIE_SECURE unset (auto), the dev origins (plus the VPS one), a
    temporary store, a fake clock, throwaway owner credentials and fresh login state."""
    monkeypatch.setenv("BRIDGE_DB_PATH", str(tmp_path / "import-default.db"))
    monkeypatch.setattr(config, "env", "development")
    monkeypatch.setenv("LIVEKIT_API_KEY", LK_KEY)
    monkeypatch.setenv("LIVEKIT_API_SECRET", LK_SECRET)
    monkeypatch.setenv("LIVEKIT_URL", "")  # no room ensure
    module = importlib.import_module("src.core.ui_bridge")
    clock = FakeClock()
    store = BridgeStore(tmp_path / "bridge.db", clock=clock)
    monkeypatch.setattr(module, "store", store)
    monkeypatch.setattr(module, "bridge_manager", module.BridgeStateManager())
    monkeypatch.setattr(module, "_clients", {})
    monkeypatch.setattr(module, "_notify_nonces", NonceCache())
    monkeypatch.setattr(module, "_refusal_audit", TokenBucket(1.0, 30.0))
    monkeypatch.setattr(module, "ALLOWED_ORIGINS", [VPS_ORIGIN, DEV_ORIGIN, DEV_ORIGIN_IP])
    monkeypatch.setattr(config, "legacy_token_auth", True)
    monkeypatch.setattr(config, "session_jwt_secret", JWT_SECRET)
    monkeypatch.setattr(config, "owner_password_hash", password_hash)
    monkeypatch.setattr(config, "owner_totp_secret", SECRET)
    monkeypatch.setattr(config, "owner_recovery_hashes", [])
    monkeypatch.setattr(config, "cookie_path", "/vortex")
    monkeypatch.setattr(config, "cookie_secure", "auto")  # what an unset COOKIE_SECURE gives (tested below)
    auth = module.owner_auth
    monkeypatch.setattr(auth, "clock", clock)
    monkeypatch.setattr(auth, "limiter", bridge_auth.LoginLimiter(clock=clock))
    monkeypatch.setattr(auth, "_factor_lock", asyncio.Lock())
    monkeypatch.setattr(auth, "max_verifying", bridge_auth.MAX_VERIFYING)
    monkeypatch.setattr(auth, "_verifying", 0)
    monkeypatch.setattr(auth, "_refusal_day", -1)
    monkeypatch.setattr(auth, "_refusal_rows", 0)
    monkeypatch.setattr(auth, "_refusals_skipped", 0)
    monkeypatch.setattr(auth, "_not_enrolled_warned", ())
    monkeypatch.setattr(auth, "_touched", OrderedDict())
    yield SimpleNamespace(module=module, store=store, clock=clock, auth=auth)
    await store.close()


def client(ns: Any, *, peer: str = "127.0.0.1", base: str = LOCAL_BRIDGE, proxy: bool = True) -> httpx.AsyncClient:
    """A client of the bridge app as one TCP peer, through uvicorn's ProxyHeadersMiddleware with the bridge's
    TRUSTED_PROXIES as ui_bridge.main() serves both bridges; proxy=False calls the bare app instead."""
    app: Any = ns.module.app_asgi
    if proxy:
        app = ProxyHeadersMiddleware(app, trusted_hosts=ns.module.TRUSTED_PROXIES)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(peer, 40000)), base_url=base)


def code_at(ns: Any) -> str:
    return pyotp.TOTP(SECRET).at(ns.clock())


async def login(http: httpx.AsyncClient, ns: Any, *, origin: str = DEV_ORIGIN,
                extra: list[tuple[str, str]] | None = None) -> httpx.Response:
    headers = [("Origin", origin), *(extra or [])]
    return await http.post("/auth/login", json={"password": PASSWORD, "totp": code_at(ns)}, headers=headers)


def cookie_attrs(resp: httpx.Response) -> dict[str, str]:
    first, *parts = [part.strip() for part in resp.headers["set-cookie"].split(";")]
    assert first.startswith(f"{bridge_auth.COOKIE_NAME}=")
    return {name.lower(): value for name, _, value in (part.partition("=") for part in parts)}


def token_of(resp: httpx.Response) -> str:
    return resp.headers["set-cookie"].split(";", 1)[0].split("=", 1)[1]


def http_request(*, scheme: str = "http", peer: str = "127.0.0.1",
                 headers: list[tuple[str, str]] | None = None) -> Request:
    """A bare Starlette request as the bridge sees it (scope 'client' is the TCP peer)."""
    return Request({"type": "http", "method": "POST", "path": "/auth/login", "query_string": b"", "scheme": scheme,
                    "server": ("127.0.0.1", 5001), "client": (peer, 40000) if peer else None,
                    "headers": [(name.lower().encode("latin-1"), value.encode("latin-1"))
                                for name, value in headers or []]})


# ---------------------------------------------------------------- COOKIE_SECURE: the setting (step 1)


@pytest.mark.parametrize("raw, mode", [
    (None, "auto"), ("", "auto"), ("   ", "auto"), ("auto", "auto"), (" AUTO ", "auto"),
    ("1", "1"), ("true", "1"), ("On", "1"), ("0", "0"), ("false", "0"), ("off", "0")])
def test_cookie_secure_is_auto_unless_set(monkeypatch, raw, mode):
    if raw is None:
        monkeypatch.delenv("COOKIE_SECURE", raising=False)
    else:
        monkeypatch.setenv("COOKIE_SECURE", raw)
    assert JarvisConfig().cookie_secure == mode


def test_an_unknown_cookie_secure_value_keeps_secure_and_warns_by_name_only(monkeypatch):
    value = "c36-not-a-mode"
    monkeypatch.setenv("COOKIE_SECURE", value)
    with captured("JARVIS-CONFIG") as records:
        assert JarvisConfig().cookie_secure == "1"
    warnings = [r.getMessage() for r in records if r.levelno == logging.WARNING and "COOKIE_SECURE" in r.getMessage()]
    assert len(warnings) == 1
    assert value not in warnings[0]


@pytest.mark.parametrize("mode, scheme, peer, forwarded, secure", [
    ("1", "http", "198.51.100.66", None, True),       # forced on: even plain http from anyone
    ("0", "https", "127.0.0.1", "https", False),      # forced off: even https behind a trusted proxy
    ("auto", "http", "127.0.0.1", None, False),       # the local bridge behind the Vite dev proxy
    ("auto", "https", "198.51.100.66", None, True),   # an https scheme needs no proxy
    ("auto", "http", "127.0.0.1", "https", True),     # the trusted proxy says https
    ("auto", "http", "198.51.100.66", "https", False),  # anyone else saying so is ignored
    ("", "http", "127.0.0.1", "https", True),         # empty is the default, auto
    ("c36-bogus", "http", "198.51.100.66", None, True),  # anything unknown keeps Secure
])
def test_the_cookie_secure_modes(monkeypatch, mode, scheme, peer, forwarded, secure):
    monkeypatch.setattr(config, "cookie_secure", mode)
    auth = bridge_auth.OwnerAuth(lambda: None, allowed_origins=lambda: (), client_ip=lambda _r: "x",
                                 legacy_ok=lambda _r: False, trusted_proxy=lambda p: p == "127.0.0.1")
    headers = [("X-Forwarded-Proto", forwarded)] if forwarded else []
    assert auth._secure(http_request(scheme=scheme, peer=peer, headers=headers)) is secure


def test_is_https_request_takes_the_scheme_or_one_header_of_a_trusted_peer():
    asked: list[str] = []

    def trusted(peer: str) -> bool:
        asked.append(peer)
        return peer == "10.0.0.2"

    forwarded = [("X-Forwarded-Proto", "https")]
    assert bridge_auth.is_https_request(http_request(scheme="https", peer="198.51.100.66"), trusted)
    assert not asked  # an https scheme is decided without any proxy
    assert bridge_auth.is_https_request(http_request(peer="10.0.0.2", headers=forwarded), trusted)
    assert not bridge_auth.is_https_request(http_request(peer="10.0.0.3", headers=forwarded), trusted)
    assert not bridge_auth.is_https_request(http_request(peer="10.0.0.2"), trusted)
    assert not bridge_auth.is_https_request(http_request(peer="", headers=forwarded), trusted)
    # the function's own rule takes one line only; uvicorn's proxy_headers take the last of several before it runs
    assert not bridge_auth.is_https_request(http_request(peer="10.0.0.2", headers=forwarded * 2), trusted)


def test_an_owner_auth_without_a_trusted_proxy_ignores_the_header(monkeypatch):
    monkeypatch.setattr(config, "cookie_secure", "auto")
    auth = bridge_auth.OwnerAuth(lambda: None, allowed_origins=lambda: (), client_ip=lambda _r: "x",
                                 legacy_ok=lambda _r: False)
    assert not auth._secure(http_request(peer="127.0.0.1", headers=[("X-Forwarded-Proto", "https")]))


# ---------------------------------------------------------------- COOKIE_SECURE=auto on the wire (steps 1 and 5)


async def test_an_http_login_in_auto_mode_sets_no_secure_flag(bridge):
    """The local bridge as the Vite dev proxy reaches it: plain http from 127.0.0.1, no X-Forwarded-Proto."""
    async with client(bridge) as http:
        resp = await login(http, bridge)
    assert resp.status_code == 200, resp.text
    attrs = cookie_attrs(resp)
    assert "secure" not in attrs
    assert "httponly" in attrs and attrs["samesite"].lower() == "strict" and attrs["path"] == "/vortex"
    assert attrs["max-age"] == "43200"


@STACKS
@pytest.mark.parametrize("peer", TRUSTED_PEERS)
async def test_forwarded_proto_https_from_a_trusted_peer_sets_secure(bridge, peer, proxy):
    async with client(bridge, peer=peer, proxy=proxy) as http:
        resp = await login(http, bridge, extra=[("X-Forwarded-Proto", "https")])
    assert resp.status_code == 200, resp.text
    assert "secure" in cookie_attrs(resp)


@STACKS
@pytest.mark.parametrize("peer", UNTRUSTED_PEERS)
async def test_the_same_header_from_an_untrusted_peer_is_ignored(bridge, peer, proxy):
    async with client(bridge, peer=peer, proxy=proxy) as http:
        resp = await login(http, bridge, extra=[("X-Forwarded-Proto", "https")])
    assert resp.status_code == 200, resp.text
    assert "secure" not in cookie_attrs(resp)


# X-Forwarded-Proto lines from the trusted peer 172.18.0.1: (lines, Secure as served, Secure on the bare app).
# Served, uvicorn's proxy_headers set the scheme from lower-case http/https/ws/wss and of several lines the last one
# wins; a value uvicorn does not take ('HTTPS ') leaves the scheme alone and reaches is_https_request's own rule.
# The bare app shows that rule: exactly one line saying https in any case; two lines are ambiguous, so ignored.
@STACKS
@pytest.mark.parametrize("values, served, bare", [
    (["https"], True, True), (["HTTPS "], True, True), (["wss"], True, True), (["http"], False, False),
    (["https, http"], False, False), ([""], False, False), (["ftp"], False, False),
    (["https", "https"], True, False), (["http", "https"], True, False), (["https", "http"], False, False)])
async def test_which_forwarded_proto_lines_make_it_https(bridge, values, served, bare, proxy):
    async with client(bridge, peer="172.18.0.1", proxy=proxy) as http:
        resp = await login(http, bridge, extra=[("X-Forwarded-Proto", value) for value in values])
    assert resp.status_code == 200, resp.text
    assert ("secure" in cookie_attrs(resp)) is (served if proxy else bare)


@pytest.mark.parametrize("proto, forwarded_for, secure", [
    ("https", "203.0.113.50", True),   # nginx's lines: uvicorn sets the scheme, whoever the client is
    ("HTTPS", None, True),             # uvicorn leaves the scheme; the rule checks the TCP peer, a trusted proxy
    ("HTTPS", "203.0.113.50", False),  # uvicorn put the internet client into scope['client']: the rule refuses it
    ("HTTPS", "10.0.0.9", True)])      # a LAN client there is in TRUSTED_PROXIES
async def test_behind_uvicorn_the_header_rule_checks_the_forwarded_client(bridge, proto, forwarded_for, secure):
    """As served, is_https_request's own rule asks about scope['client'], which uvicorn's proxy_headers have already
    replaced with the X-Forwarded-For host when the TCP peer is a trusted proxy; it never sees that TCP peer then."""
    extra = [("X-Forwarded-Proto", proto), *([("X-Forwarded-For", forwarded_for)] if forwarded_for else [])]
    async with client(bridge, peer="172.18.0.1", base="http://bridge.test") as http:
        resp = await login(http, bridge, origin=VPS_ORIGIN, extra=extra)
    assert resp.status_code == 200, resp.text
    assert ("secure" in cookie_attrs(resp)) is secure


@pytest.mark.parametrize("peer, secure", [("172.18.0.1", True), ("198.51.100.66", False)])
async def test_logout_clears_the_cookie_by_the_same_rule(bridge, peer, secure):
    async with client(bridge, peer=peer) as http:
        resp = await login(http, bridge, extra=[("X-Forwarded-Proto", "https")])
        assert resp.status_code == 200, resp.text
        http.cookies.clear()
        out = await http.post("/auth/logout", headers=[("Origin", DEV_ORIGIN), ("X-Forwarded-Proto", "https"),
                                                       ("Cookie", f"{bridge_auth.COOKIE_NAME}={token_of(resp)}")])
    assert out.status_code == 200
    attrs = cookie_attrs(out)
    assert attrs["max-age"] == "0"
    assert ("secure" in attrs) is secure


@pytest.mark.parametrize("peer, secure", [("172.18.0.1", True), ("127.0.0.1", True), ("10.1.2.3", True),
                                          ("198.51.100.66", False), ("192.0.2.10", False)])
async def test_behind_uvicorn_proxy_headers_only_a_trusted_peer_makes_it_https(bridge, peer, secure):
    """ui_bridge.main() runs uvicorn with proxy_headers and forwarded_allow_ips=TRUSTED_PROXIES: nginx's
    X-Forwarded-Proto makes the request https only when nginx is the peer; from anyone else it changes nothing."""
    async with client(bridge, peer=peer, base="http://bridge.test") as http:
        resp = await login(http, bridge, origin=VPS_ORIGIN,
                           extra=[("X-Forwarded-Proto", "https"), ("X-Forwarded-For", "203.0.113.50")])
    assert resp.status_code == 200, resp.text
    assert ("secure" in cookie_attrs(resp)) is secure


async def test_the_bridge_reuses_its_trusted_proxies_for_the_header(bridge):
    """owner_auth asks ui_bridge's TRUSTED_PROXIES check (the X-Real-IP rule's), not a list of its own."""
    for peer in TRUSTED_PEERS:
        assert bridge.auth._trusted_proxy(peer) is True, peer
        assert bridge.module._trusted_proxy_peer(peer) is True, peer
    for peer in UNTRUSTED_PEERS:
        assert bridge.auth._trusted_proxy(peer) is False, peer


async def test_the_vps_cookie_still_carries_secure(bridge, monkeypatch):
    """The VPS compose forces COOKIE_SECURE=1 and nginx sends X-Forwarded-Proto $scheme on /vortex/, so the cookie
    stays Secure there whether the setting is 1 or auto."""
    compose = yaml.safe_load((ROOT / "deploy" / "hostinger" / "docker-compose.yml").read_text(encoding="utf-8"))
    forced = str(compose["services"]["ui-bridge"]["environment"]["COOKIE_SECURE"])
    assert forced == "1"
    nginx = (ROOT / "deploy" / "hostinger" / "jarvis.conf").read_text(encoding="utf-8")
    vortex = nginx.split("location /vortex/ {", 1)[1].split("}", 1)[0]
    assert "proxy_set_header X-Forwarded-Proto $scheme;" in vortex
    monkeypatch.setenv("COOKIE_SECURE", forced)
    for mode in (JarvisConfig().cookie_secure, "auto"):
        monkeypatch.setattr(config, "cookie_secure", mode)
        bridge.clock.now += bridge_auth.TOTP_INTERVAL_S  # a fresh code for each login
        # nginx reaches the container through Docker's port proxy: plain http from the gateway address
        async with client(bridge, peer="172.18.0.1", base="http://bridge.test") as http:
            resp = await login(http, bridge, origin=VPS_ORIGIN,
                               extra=[("X-Forwarded-Proto", "https"), ("X-Forwarded-For", "203.0.113.50"),
                                      ("X-Real-IP", "203.0.113.50")])
        assert resp.status_code == 200, resp.text
        assert "secure" in cookie_attrs(resp), mode
    monkeypatch.setattr(config, "cookie_secure", forced)
    bridge.clock.now += bridge_auth.TOTP_INTERVAL_S
    async with client(bridge, peer="198.51.100.66", base="http://bridge.test") as http:
        resp = await login(http, bridge, origin=VPS_ORIGIN)
    assert resp.status_code == 200, resp.text
    assert "secure" in cookie_attrs(resp)


async def test_the_vite_dev_page_logs_in_on_the_local_bridge(bridge):
    """K-09 in miniature: npm run vite-dev with VITE_DEV_BRIDGE=http://127.0.0.1:5001 and VITE_DEV_BRIDGE_STRIP=1
    sends /vortex/auth/* as /auth/* over plain http from 127.0.0.1 with the page's Origin (changeOrigin rewrites only
    Host, and no X-Forwarded-* header is added, so uvicorn's proxy_headers change nothing), and the browser sends the
    cookie it keeps for /vortex back. A simulation only: C-36's acceptance is the owner's login through the real
    Vite proxy on vortex.py's local bridge after set_owner_credentials.py --target local."""
    async with client(bridge) as http:
        resp = await login(http, bridge, origin=DEV_ORIGIN)
        assert resp.status_code == 200, resp.text
        assert "secure" not in cookie_attrs(resp)
        cookie = {"Cookie": f"{bridge_auth.COOKIE_NAME}={token_of(resp)}"}
        http.cookies.clear()
        me = await http.get("/auth/me", headers=cookie)
        assert me.status_code == 200 and me.json()["authenticated"] is True
        out = await http.post("/auth/logout", headers={**cookie, "Origin": DEV_ORIGIN})
        assert out.status_code == 200 and "secure" not in cookie_attrs(out)
        assert (await http.get("/auth/me", headers=cookie)).status_code == 401


# ---------------------------------------------------------------- owner_not_enrolled (step 3)


async def test_without_owner_credentials_503_and_one_warning(bridge, monkeypatch, bridge_log):
    for name in ("session_jwt_secret", "owner_password_hash", "owner_totp_secret"):
        monkeypatch.setattr(config, name, None)
    async with client(bridge) as http:
        answers = [await login(http, bridge) for _ in range(3)]
        me = await http.get("/auth/me")
    assert [(r.status_code, r.json()) for r in answers] == [(503, {"ok": False, "error": "owner_not_enrolled"})] * 3
    assert me.status_code == 401
    refused = [r for r in bridge_log if "owner_not_enrolled" in r.getMessage()]
    warnings = [r.getMessage() for r in refused if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "(SESSION_JWT_SECRET, OWNER_PASSWORD_HASH, OWNER_TOTP_SECRET missing or unusable)" in warnings[0]
    assert "set_owner_credentials.py" in warnings[0] and "--target local" in warnings[0]
    assert [r.levelno for r in refused].count(logging.DEBUG) == 2
    rows = await bridge.store.recent_audit(limit=50, action_prefix="auth_login_refused")
    assert [json.loads(row["detail"]) for row in rows] == [{"reason": "owner_not_enrolled"}] * 3


async def test_an_unusable_setting_is_named_and_its_value_never_logged(bridge, monkeypatch, bridge_log,
                                                                       password_hash):
    bad = "c36-not-base32-at-all!!"
    monkeypatch.setattr(config, "owner_totp_secret", bad)
    async with client(bridge) as http:
        assert (await login(http, bridge)).status_code == 503
        # another setting goes missing: a new WARNING names the new set
        monkeypatch.setattr(config, "session_jwt_secret", "c36-short")
        assert (await login(http, bridge)).status_code == 503
    warnings = [r.getMessage() for r in bridge_log
                if r.levelno == logging.WARNING and "owner_not_enrolled" in r.getMessage()]
    assert len(warnings) == 2
    assert "(OWNER_TOTP_SECRET missing or unusable)" in warnings[0]
    assert "(SESSION_JWT_SECRET, OWNER_TOTP_SECRET missing or unusable)" in warnings[1]
    text = "\n".join(r.getMessage() for r in bridge_log)
    for value in (bad, "c36-short", JWT_SECRET, password_hash, PASSWORD):
        assert value not in text


# ---------------------------------------------------------------- set_owner_credentials.py --target local (step 2)


class Script:
    """Answers for getpass / input in order; an exception instance in the list is raised (Ctrl+C)."""

    def __init__(self, answers: list[Any]) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    def __call__(self, prompt: str = "") -> str:
        self.prompts.append(prompt)
        if not self.answers:
            raise AssertionError(f"unexpected prompt {prompt!r}")
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


def no_ssh(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError("--target local never runs ssh")


def no_http(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError("no HTTP here")


def local_deps(env_file: Path, secrets_: list[Any], inputs: list[Any], *, http: Any = no_http) -> Any:
    return soc.Deps(ask_secret=Script(secrets_), ask=Script(inputs), out=io.StringIO(), clock=lambda: START,
                    interactive=lambda: True, private_output=lambda: True, ssh_run=no_ssh, http_open=http, env={},
                    local_env=env_file)


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(soc, "BCRYPT_COST", FAST)
    monkeypatch.setattr(soc.bridge_auth, "new_totp_secret", lambda: SECRET)


def enrol_local(env_file: Path) -> tuple[int, Any]:
    deps = local_deps(env_file, [PASSWORD, PASSWORD], [pyotp.TOTP(SECRET).at(START)])
    return soc.main(["--target", "local"], deps), deps


def test_target_local_writes_the_login_into_the_root_env_only(tmp_path, fast):
    env_file = tmp_path / ".env"
    env_file.write_bytes(ROOT_ENV.encode("utf-8"))
    overlay = tmp_path / "deploy" / "windows-local" / ".env"  # the hybrid overlay stays as it is
    overlay.parent.mkdir(parents=True)
    overlay.write_bytes(b"ALLOWED_ORIGINS=http://localhost:5173\n")
    others = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file() and path != env_file}

    code, deps = enrol_local(env_file)

    out = deps.out.getvalue()
    assert code == 0, out
    assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file() and path != env_file} == others
    assert not list(tmp_path.rglob("*.tmp"))
    raw = env_file.read_bytes()
    assert raw.count(b"\r\n") == raw.count(b"\n")  # the file's CRLF endings, the appended line included
    values = dotenv_values(env_file)
    assert raw.decode("utf-8").split("\r\n") == [
        "# JARVIS PC env (fake)",
        "GOOGLE_API_KEY=c36-fake-google-key",
        f"OWNER_TOTP_SECRET={SECRET}",
        f"export OWNER_PASSWORD_HASH ={values['OWNER_PASSWORD_HASH']}",
        "OWNER_RECOVERY_HASHES=",
        "ALLOWED_ORIGINS=http://localhost:5173,http://127.0.0.1:5173",
        "# SESSION_JWT_SECRET=commented-out-stays",
        "AGENT_TOKEN=c36-fake-agent-token",
        f"SESSION_JWT_SECRET={values['SESSION_JWT_SECRET']}",
        "",
    ]
    assert bridge_auth.verify_password(PASSWORD, bridge_auth.decode_bcrypt_hash(values["OWNER_PASSWORD_HASH"]))
    assert re.fullmatch(r"[A-Za-z0-9_-]{86}", values["SESSION_JWT_SECRET"] or "")  # token_urlsafe(64)
    # what was done, by name only; the authenticator entry is the local one; no recovery codes, no Enter prompt
    assert ("Saved: OWNER_PASSWORD_HASH replaced, OWNER_TOTP_SECRET replaced, SESSION_JWT_SECRET added, "
            "OWNER_RECOVERY_HASHES cleared.") in out
    assert f"otpauth://totp/JARVIS%20local:owner?secret={SECRET}" in out
    assert not CODE_RE.search(out) and "recovery codes" not in out.lower()
    assert len(deps.ask.prompts) == 1
    assert soc.LOCAL_DONE_LINE in out.splitlines()[-1]
    assert "does not list" not in out  # both dev origins are allowed
    for value in (PASSWORD, values["OWNER_PASSWORD_HASH"], values["SESSION_JWT_SECRET"], "c36-old-fake-hash",
                  "c36-fake-google-key", "c36-fake-agent-token"):
        assert value not in out


@pytest.mark.parametrize("existing, kept", [(OLD_JWT, True), ("c36-short", False), ("", False)])
def test_target_local_keeps_only_a_usable_jwt_secret(tmp_path, fast, existing, kept):
    env_file = tmp_path / ".env"
    env_file.write_text(f"SESSION_JWT_SECRET={existing}\nA=1\n", encoding="utf-8")
    code, deps = enrol_local(env_file)
    assert code == 0, deps.out.getvalue()
    value = dotenv_values(env_file)["SESSION_JWT_SECRET"] or ""
    assert (value == existing) is kept
    assert len(value) >= (len(existing) if kept else 86)
    assert f"SESSION_JWT_SECRET {'kept' if kept else 'replaced'}" in deps.out.getvalue()
    assert value not in deps.out.getvalue()
    assert env_file.read_text(encoding="utf-8").splitlines()[1] == "A=1"


async def test_what_target_local_writes_opens_the_local_bridge_over_http(bridge, tmp_path, monkeypatch, fast,
                                                                        password_hash):
    """Round trip: the script's values, read the way the bridge reads the root .env, log the Vite dev page in."""
    env_file = tmp_path / ".env"
    env_file.write_text("A=1\n", encoding="utf-8")
    code, deps = enrol_local(env_file)
    assert code == 0, deps.out.getvalue()
    parsed = dotenv_values(env_file)
    for name in ("SESSION_JWT_SECRET", "OWNER_PASSWORD_HASH", "OWNER_TOTP_SECRET"):
        monkeypatch.setenv(name, parsed[name] or "")
    monkeypatch.delenv("OWNER_RECOVERY_HASHES", raising=False)
    fresh = JarvisConfig()
    for attr in ("session_jwt_secret", "owner_password_hash", "owner_totp_secret", "owner_recovery_hashes"):
        monkeypatch.setattr(config, attr, getattr(fresh, attr))
    assert config.owner_password_hash != password_hash  # the script's own hash is in use now
    creds = bridge_auth.OwnerCredentials.from_config()
    assert creds.enrolled and not creds.problems()
    async with client(bridge) as http:
        resp = await login(http, bridge, origin=DEV_ORIGIN)
    assert resp.status_code == 200, resp.text
    assert "secure" not in cookie_attrs(resp)


def test_target_local_stops_before_any_question_without_a_root_env(tmp_path, fast):
    deps = local_deps(tmp_path / ".env", [], [])
    assert soc.main(["--target", "local"], deps) == 1
    out = deps.out.getvalue()
    assert "root .env cannot be used" in out and "nothing was written" in out
    assert deps.ask_secret.prompts == [] and deps.ask.prompts == []
    assert not (tmp_path / ".env").exists()


@pytest.mark.skipif(getattr(os, "geteuid", lambda: 1)() == 0, reason="root may write a read-only file")
def test_target_local_refuses_a_read_only_root_env(tmp_path, fast):
    env_file = tmp_path / ".env"
    env_file.write_text("A=1\n", encoding="utf-8")
    os.chmod(env_file, stat.S_IREAD)
    try:
        deps = local_deps(env_file, [], [])
        assert soc.main(["--target", "local"], deps) == 1
        assert "read-only" in deps.out.getvalue() and deps.ask_secret.prompts == []
    finally:
        os.chmod(env_file, stat.S_IREAD | stat.S_IWRITE)
    assert env_file.read_text(encoding="utf-8") == "A=1\n"


def test_target_local_write_failure_changes_nothing_and_leaves_no_temp(tmp_path, fast, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_bytes(ROOT_ENV.encode("utf-8"))

    def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(soc.gen_secrets.os, "replace", refuse)
    code, deps = enrol_local(env_file)
    monkeypatch.undo()
    out = deps.out.getvalue()
    assert code == 1
    assert "root .env could not be updated" in out and "nothing was written" in out and "NOT active" in out
    assert soc.LOCAL_DONE_LINE not in out
    assert env_file.read_bytes() == ROOT_ENV.encode("utf-8")
    assert not list(tmp_path.glob("*.tmp"))


def test_target_local_ctrl_c_in_the_write_leaves_no_copy_of_the_env(tmp_path, fast, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_bytes(ROOT_ENV.encode("utf-8"))

    def interrupt(_fd: int) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(soc.gen_secrets.os, "fsync", interrupt)  # after the temp copy is written, before the rename
    code, deps = enrol_local(env_file)
    monkeypatch.undo()
    assert code == 1
    assert "cancelled during the .env write" in deps.out.getvalue()
    assert env_file.read_bytes() == ROOT_ENV.encode("utf-8")
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("line, missing", [
    (None, []),                                   # unset: the dev origins are the bridge's default
    ("ALLOWED_ORIGINS=", []),
    ("ALLOWED_ORIGINS=http://localhost:5173/, http://127.0.0.1:5173", []),
    ("ALLOWED_ORIGINS=https://jarvis.example.invalid,http://192.0.2.10", [DEV_ORIGIN, DEV_ORIGIN_IP]),
    ("ALLOWED_ORIGINS=https://jarvis.example.invalid, http://127.0.0.1:5173", [DEV_ORIGIN]),
])
def test_the_local_enrolment_names_dev_origins_the_root_env_leaves_out(tmp_path, line, missing):
    env_file = tmp_path / ".env"
    env_file.write_text("A=1\n" + (f"{line}\n" if line else ""), encoding="utf-8")
    note = soc.local_origin_note(env_file)
    if not missing:
        assert note is None
        return
    assert note is not None and f"does not list {' and '.join(missing)}," in note
    assert "jarvis.example.invalid" not in note and "192.0.2.10" not in note  # the file's value is never shown


def test_target_local_prints_the_origin_note_after_saving(tmp_path, fast):
    env_file = tmp_path / ".env"
    env_file.write_text("ALLOWED_ORIGINS=https://jarvis.example.invalid\n", encoding="utf-8")
    code, deps = enrol_local(env_file)
    out = deps.out.getvalue()
    assert code == 0, out
    assert f"does not list {DEV_ORIGIN} and {DEV_ORIGIN_IP}" in out
    assert dotenv_values(env_file)["ALLOWED_ORIGINS"] == "https://jarvis.example.invalid"  # never changed here


class FakeResponse:
    def __init__(self, status: int, body: bytes, cookies: list[str] | None = None) -> None:
        self.status = status
        self._body = body
        self.headers = email.message.Message()
        for cookie in cookies or []:
            self.headers["Set-Cookie"] = cookie

    def read(self) -> bytes:
        return self._body

    def getcode(self) -> int:
        return self.status

    def close(self) -> None:
        pass


class FakeHTTP:
    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.requests: list[Any] = []

    def __call__(self, request: Any, timeout: float = 0) -> Any:
        self.requests.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


def http_error(status: int, body: dict[str, Any]) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://127.0.0.1:5001/auth/login", status, "err", email.message.Message(),
                                  io.BytesIO(json.dumps(body).encode()))


def test_target_local_test_login_tries_the_local_bridge_as_the_dev_page(tmp_path):
    ok = FakeResponse(200, json.dumps({"ok": True, "user": "owner", "session_id": "c36fakesid",
                                       "expires_at": 1_790_043_200}).encode(),
                      cookies=[f"jarvis_session={FAKE_COOKIE}; Path=/vortex; HttpOnly; SameSite=strict"])
    http = FakeHTTP(ok, FakeResponse(200, b'{"ok": true}'))
    deps = local_deps(tmp_path / ".env", [PASSWORD], ["123456"], http=http)
    assert soc.main(["--target", "local", "--test-login"], deps) == 0
    out = deps.out.getvalue()
    assert "LOGIN OK" in out and FAKE_COOKIE not in out and PASSWORD not in out
    assert len(http.requests) == 2
    sent_login, sent_logout = http.requests[0], http.requests[1]
    assert sent_login.full_url == f"{LOCAL_BRIDGE}/auth/login"
    assert sent_login.get_header("Origin") == DEV_ORIGIN
    assert json.loads(sent_login.data) == {"password": PASSWORD, "totp": "123456"}
    assert sent_logout.full_url == f"{LOCAL_BRIDGE}/auth/logout"
    assert sent_logout.get_header("Cookie") == f"jarvis_session={FAKE_COOKIE}"


@pytest.mark.parametrize("error, status, hint", [
    ("bad_origin", 403, f"does not accept {DEV_ORIGIN}: add it to ALLOWED_ORIGINS"),
    ("owner_not_enrolled", 503, "Run this with --target local first")])
def test_target_local_test_login_explains_the_local_refusals(tmp_path, error, status, hint):
    deps = local_deps(tmp_path / ".env", [PASSWORD], ["123456"],
                      http=FakeHTTP(http_error(status, {"ok": False, "error": error})))
    assert soc.main(["--target", "local", "--test-login"], deps) == 1
    out = deps.out.getvalue()
    assert f"LOGIN FAILED: {error} (HTTP {status})" in out and hint in out


def test_the_vps_stays_the_default_target_and_the_help_names_local(capsys):
    assert soc.parse_args([]).target == "vps"
    assert soc.parse_args(["--target", "local"]).target == "local"
    with pytest.raises(SystemExit):
        soc.parse_args(["--target", "elsewhere"])
    capsys.readouterr()
    with pytest.raises(SystemExit) as stop:
        soc.main(["--help"])
    assert stop.value.code == 0
    text = capsys.readouterr().out
    assert "--target" in text and "JARVIS local" in text and "--test-login" in text


def test_the_local_authenticator_entry_has_its_own_issuer():
    assert bridge_auth.provisioning_uri(SECRET).startswith(f"otpauth://totp/JARVIS:owner?secret={SECRET}")
    local = bridge_auth.provisioning_uri(SECRET, issuer=soc.LOCAL_ISSUER)
    assert local.startswith(f"otpauth://totp/JARVIS%20local:owner?secret={SECRET}")
    assert pyotp.parse_uri(local).at(START) == pyotp.TOTP(SECRET).at(START)  # the same codes, another label
