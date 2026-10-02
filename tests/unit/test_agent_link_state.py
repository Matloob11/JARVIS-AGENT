"""C-136: the PC worker's LiveKit link state (run/agent_link.json), agent_ctl status/wait, and recovery.

Nothing here starts, signals or kills a real process or opens a network connection beyond 127.0.0.1:
livekit-agents' real connection task runs against a fake websocket session, the LiveKit probe (outside its
own tests, which ask a local HTTP server), the job count and the restart helper are fakes, time is a fake
clock, and every file lives under tmp_path (JARVIS_RUN_DIR, agent_ctl.LOG_DIR).
"""
# pylint: disable=protected-access,redefined-outer-name,no-member,too-many-lines  # no-member: protobuf classes
from __future__ import annotations

import asyncio
import http.server
import importlib
import importlib.util
import inspect
import json
import logging
import os
import socket
import ssl
import subprocess
import sys
import threading
import time
import types
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import aiohttp
import pytest
from aiohttp.client_reqrep import ConnectionKey
from livekit.agents import worker as lk_worker
from livekit.agents.cli import cli as livekit_cli
from livekit.protocol import agent as lk_agent
from multidict import CIMultiDict, CIMultiDictProxy
from yarl import URL

from services.utils.jarvis_security import redact_secrets
from src.core import agent_lock, agent_runner, livekit_link

ROOT = Path(__file__).resolve().parents[2]
OWNER_STARTED = "2026-10-02T14:00:00+05:00"
OWNER = {"pid": 31500, "started_at": OWNER_STARTED}
FAKE_SECRET = "c136-throwaway-secret-0123456789"


def _load_agent_ctl() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("agent_ctl_c136", ROOT / "scripts" / "agent_ctl.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


agent_ctl = _load_agent_ctl()


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now
        self.slept = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds
        self.slept += seconds


@pytest.fixture
def run_dir(monkeypatch, tmp_path) -> Path:
    path = tmp_path / "run"
    monkeypatch.setenv("JARVIS_RUN_DIR", str(path))
    return path


@pytest.fixture
def lk_logger():
    """livekit.agents as in the worker (propagate off, no handler of its own); restored afterwards."""
    log = logging.getLogger(livekit_link.LK_LOGGER)
    saved = (log.level, log.propagate, list(log.handlers))
    log.propagate = False
    log.handlers.clear()
    log.setLevel(logging.WARNING)
    yield log
    log.setLevel(saved[0])
    log.propagate = saved[1]
    log.handlers[:] = saved[2]


def make_monitor(path: Path, **kwargs) -> tuple[livekit_link.LinkMonitor, dict]:
    """A monitor with fakes; seen['lines'], seen['probes'], seen['restarts'] and seen['envs'] (the helper's
    environment of each restart) record what it did."""
    seen: dict = {"lines": [], "probes": 0, "restarts": [], "envs": [], "reachable": True, "jobs": 0,
                  "restart_error": None}
    clock = kwargs.pop("clock", FakeClock())
    wall = kwargs.pop("wall", FakeClock(time.time()))

    def probe() -> bool:
        seen["probes"] += 1
        return seen["reachable"]

    def restart(reason: str, env: dict) -> int:
        error = seen["restart_error"]
        if isinstance(error, BaseException):
            raise error
        seen["restarts"].append(reason)
        seen["envs"].append(env)
        return 4242

    monitor = livekit_link.LinkMonitor(path, dict(OWNER), clock=clock, wall=wall, probe=probe,
                                       active_jobs=lambda: seen["jobs"], restart=restart,
                                       base_env=lambda: {"C136_BASE": "1"}, log=seen["lines"].append, **kwargs)
    seen["clock"], seen["wall"] = clock, wall
    return monitor, seen


def read_doc(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- livekit-agents' connection lines


def test_installed_livekit_agents_still_logs_the_lines_the_monitor_reads():
    """The monitor reads log lines, not private state: an upgrade that changes them must fail here."""
    assert lk_worker.logger.name == livekit_link.LK_LOGGER
    register = inspect.getsource(lk_worker.AgentServer._handle_register)
    assert f'"{livekit_link.LK_REGISTERED}"' in register and '"id": reg.worker_id' in register
    source = inspect.getsource(lk_worker)
    connection = source[source.index("async def _connection_task"):source.index("async def _run_ws")]
    assert f'f"{livekit_link.LK_RETRYING}{{retry_delay}}s"' in connection
    assert f'f"{livekit_link.LK_GAVE_UP}{{retry_count}} attempts"' in connection
    assert "@utils.log_exceptions(logger=logger)\n    async def _connection_task" in source
    # Why /health is not enough: it answers 503 only once the retries are used up.
    health = source[source.index("async def health_check"):source.index("async def worker(")]
    assert "if self._connection_failed:" in health and "503" in health


class FakeWebSocket:
    """One /agent connection: answers the register request, then closes ('worker connection closed')."""

    def __init__(self, worker_id: str, on_close=None) -> None:
        self.worker_id = worker_id
        self.on_close = on_close
        self.sent: list[bytes] = []

    async def send_bytes(self, data: bytes) -> None:
        self.sent.append(data)

    async def receive_bytes(self) -> bytes:
        msg = lk_agent.ServerMessage()
        msg.register.worker_id = self.worker_id
        msg.register.server_info.region = "test"
        return msg.SerializeToString()

    async def receive(self):
        if self.on_close is not None:
            self.on_close()
        return types.SimpleNamespace(type=aiohttp.WSMsgType.CLOSED, data=None)

    async def close(self) -> None:
        return None


class FakeSession:
    """ws_connect answers from a script: a FakeWebSocket, or an exception for an unreachable host."""

    def __init__(self, *script) -> None:
        self.script = list(script)
        self.urls: list[str] = []

    async def ws_connect(self, url, **_kwargs):
        self.urls.append(url)
        step = self.script.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


def fake_server(monkeypatch, session: FakeSession, *, max_retry: int) -> lk_worker.AgentServer:
    """A real AgentServer, set up the way run() leaves it, with no process pool, HTTP server or network."""
    monkeypatch.delenv("LIVEKIT_WORKER_TOKEN", raising=False)
    server = lk_worker.AgentServer(ws_url="ws://livekit.example.test:7880", api_key="c136-test-key",
                                   api_secret=FAKE_SECRET, max_retry=max_retry)
    server._agent_name = "jarvis-pc"
    server._closed = False
    server._loop = asyncio.get_running_loop()
    server._devmode = False
    server._msg_chan = lk_worker.utils.aio.Chan(128, loop=server._loop)
    server._proc_pool = types.SimpleNamespace(processes=[])
    server._worker_load = 0.0
    server._reserved_slots = 0
    server._previous_status = lk_agent.WorkerStatus.WS_AVAILABLE
    server._job_lifecycle_tasks = set()
    server._http_session = session

    async def no_wait(_delay, result=None):
        await asyncio.sleep(0)
        return result

    fast = types.SimpleNamespace(**{name: getattr(asyncio, name) for name in dir(asyncio) if not name.startswith("__")})
    fast.sleep = no_wait  # the retry delays (0 s, 2 s, ...) without waiting
    monkeypatch.setattr(lk_worker, "asyncio", fast)
    return server


async def test_real_connection_task_connect_disconnect_retry_and_give_up(monkeypatch, lk_logger, run_dir, capsys):
    monitor, seen = make_monitor(run_dir / livekit_link.LINK_FILE_NAME, redact=redact_secrets)
    monitor.attach()
    unreachable = OSError("[WinError 1232] The network location cannot be reached")
    session = FakeSession(FakeWebSocket("AW_c136first"), unreachable, unreachable)
    server = fake_server(monkeypatch, session, max_retry=2)

    states = []
    note = monitor.note

    def record(state, detail="", worker_id="", cause=""):
        states.append((state, cause))
        note(state, detail, worker_id, cause)

    monkeypatch.setattr(monitor, "note", record)
    with pytest.raises(RuntimeError, match="failed to connect to livekit after 2 attempts"):
        await server._connection_task()
    monitor.tick()

    assert session.urls == ["ws://livekit.example.test:7880/agent"] * 3
    # registered, the disconnect (retrying in 0s), one more retry (2s: no change), then livekit-agents gives up
    assert states == [("connected", ""), ("retrying", "rejected"), ("retrying", "network"), ("failed", "")]
    assert server._connection_failed  # from now on /health answers 503
    doc = read_doc(run_dir / livekit_link.LINK_FILE_NAME)
    assert (doc["state"], doc["worker_id"], doc["connected_once"]) == ("failed", "AW_c136first", True)
    assert doc["cause"] == "network"  # of the last attempt; the disconnect line's own cause is not kept
    assert "failed to connect to livekit after 2 attempts" in doc["detail"]
    assert (doc["lock_pid"], doc["lock_started_at"], doc["pid"]) == (31500, OWNER_STARTED, os.getpid())
    assert seen["lines"] == [
        "LIVEKIT LINK connected: worker AW_c136first registered with LiveKit",
        "LIVEKIT LINK disconnected, livekit-agents retries: retrying in 0s "
        "(Exception: worker connection closed unexpectedly)",
        "LIVEKIT LINK failed: failed to connect to livekit after 2 attempts (livekit-agents stopped retrying)",
    ]
    # livekit-agents' own WARNING/ERROR lines reach stderr as before (logging.lastResort); its INFO lines do not.
    err = capsys.readouterr().err
    assert "failed to connect to livekit, retrying in 0s" in err and "retrying in 2s" in err
    assert "Error in _connection_task" in err and "The network location cannot be reached" in err
    assert "registered worker" not in err
    assert FAKE_SECRET not in err + json.dumps(doc)


async def test_real_connection_task_reconnects_by_itself(monkeypatch, lk_logger, run_dir):
    monitor, seen = make_monitor(run_dir / livekit_link.LINK_FILE_NAME)
    monitor.attach()
    server_box: list = []
    last = FakeWebSocket("AW_c136second", on_close=lambda: setattr(server_box[0], "_closed", True))
    session = FakeSession(FakeWebSocket("AW_c136first"), OSError("host unreachable"), last)
    server_box.append(fake_server(monkeypatch, session, max_retry=16))

    await server_box[0]._connection_task()  # ends cleanly once the worker closes
    monitor.tick()

    doc = read_doc(run_dir / livekit_link.LINK_FILE_NAME)
    assert (doc["state"], doc["worker_id"]) == ("connected", "AW_c136second")
    assert seen["lines"][-1] == "LIVEKIT LINK connected after 0 s down: worker AW_c136second registered with LiveKit"
    assert seen["probes"] == 0 and not seen["restarts"]


def test_other_livekit_agents_lines_are_not_connection_lines():
    def record(msg, *, level=logging.INFO, exc=None, **extra):
        return logging.makeLogRecord({"name": "livekit.agents", "msg": msg, "levelno": level,
                                      "exc_info": (type(exc), exc, None) if exc else None, **extra})

    assert livekit_link.classify_record(record("registered worker", id="AW_x")) == ("connected", "", "AW_x", "")
    assert livekit_link.classify_record(record("starting worker")) is None
    assert livekit_link.classify_record(record("worker failed", level=logging.ERROR, exc=ValueError("x"))) is None
    assert livekit_link.classify_record(record("draining worker")) is None
    retry = record("failed to connect to livekit, retrying in 4s", level=logging.WARNING, exc=TimeoutError("t"))
    assert livekit_link.classify_record(retry) == ("retrying", "retrying in 4s (TimeoutError: t)", "", "network")
    assert livekit_link.classify_record(record("failed to connect to livekit, retrying in 2s")) == \
        ("retrying", "retrying in 2s", "", "")


def connection_errors(url: str, secret: str) -> dict[str, BaseException]:
    """What aiohttp 3 raises in livekit-agents' ws_connect, built the way aiohttp builds them."""
    target = URL(url)
    key = ConnectionKey(host=target.host, port=target.port, is_ssl=False, ssl=True, proxy=None, proxy_auth=None,
                        proxy_headers_hash=None)
    agent_url = target.with_path("/agent").with_query(worker_token=secret)
    info = aiohttp.RequestInfo(agent_url, "GET", CIMultiDictProxy(CIMultiDict()), agent_url)

    def handshake(status: int) -> aiohttp.WSServerHandshakeError:
        return aiohttp.WSServerHandshakeError(info, (), status=status, message="Invalid response status",
                                              headers=None)

    return {
        "refused": aiohttp.ClientConnectorError(key, ConnectionRefusedError(10061, "The remote computer refused "
                                                                                   "the network connection")),
        "dns": aiohttp.ClientConnectorDNSError(key, OSError(11001, "getaddrinfo failed")),
        "timeout": aiohttp.ConnectionTimeoutError("Connection timeout to host " + url),
        "dropped": aiohttp.ServerDisconnectedError(),
        "proxy 502": handshake(502),
        "unauthorized 401": handshake(401),
        "not found 404": handshake(404),
        "certificate": aiohttp.ClientConnectorCertificateError(
            key, ssl.SSLCertVerificationError(1, "certificate verify failed")),
        "unknown": Exception("expected register response as first message"),
    }


def test_failures_on_the_way_to_livekit_differ_from_being_turned_away():
    errors = connection_errors("ws://livekit.example.test:7880", FAKE_SECRET)
    causes = {name: livekit_link.failure_cause(error) for name, error in errors.items()}
    assert causes == {"refused": "network", "dns": "network", "timeout": "network", "dropped": "network",
                      "proxy 502": "network", "unauthorized 401": "rejected", "not found 404": "rejected",
                      "certificate": "rejected", "unknown": "rejected"}


# --------------------------------------------------------------------------- the link file


def test_start_writes_connecting_before_anything_else_and_the_thread_follows(lk_logger, run_dir):
    path = run_dir / livekit_link.LINK_FILE_NAME
    monitor = livekit_link.LinkMonitor(path, dict(OWNER), probe=lambda: False, active_jobs=lambda: 0,
                                       restart=lambda *_args: pytest.fail("no restart"), log=lambda _line: None)
    monitor.start()
    try:
        doc = read_doc(path)
        assert (doc["state"], doc["worker_id"], doc["connected_once"]) == ("connecting", "", False)
        assert lk_logger.level == logging.INFO and any(isinstance(h, livekit_link.LinkLogHandler)
                                                       for h in lk_logger.handlers)
        lk_logger.info("registered worker", extra={"id": "AW_thread", "agent_name": "jarvis-pc"})
        deadline = time.monotonic() + 5
        while read_doc(path)["state"] != "connected" and time.monotonic() < deadline:
            time.sleep(0.05)
        assert read_doc(path)["worker_id"] == "AW_thread"
    finally:
        monitor.stop()
    assert not any(isinstance(h, livekit_link.LinkLogHandler) for h in lk_logger.handlers)
    assert sorted(p.name for p in run_dir.iterdir()) == [livekit_link.LINK_FILE_NAME]  # no temporary file left


def test_each_change_is_written_once_and_retries_are_not(run_dir, monkeypatch):
    writes: list[str] = []
    real = livekit_link.write_json_atomic
    monkeypatch.setattr(livekit_link, "write_json_atomic", lambda path, doc: writes.append(doc["state"]) or real(path, doc))
    monitor = make_monitor(run_dir / livekit_link.LINK_FILE_NAME)[0]
    monitor.tick()
    monitor.note("connected", worker_id="AW_1")
    monitor.tick()
    for delay in (0, 2, 4, 6):
        monitor.note("retrying", f"retrying in {delay}s")
    monitor.tick()
    monitor.tick()
    assert writes == ["connecting", "connected", "retrying"]
    doc = read_doc(run_dir / livekit_link.LINK_FILE_NAME)
    assert doc["detail"] == "retrying in 6s"  # the latest retry, written with the change


async def test_the_link_file_and_lines_hold_no_secret_and_no_livekit_address(monkeypatch, lk_logger, run_dir):
    """aiohttp's real errors name LiveKit's host, port and URL (with its query): none of it is written."""
    url = "ws://livekit.example.test:7880"
    monkeypatch.setenv("LIVEKIT_URL", url)
    monkeypatch.setenv("LIVEKIT_API_SECRET", FAKE_SECRET)
    path = run_dir / livekit_link.LINK_FILE_NAME
    monitor, seen = make_monitor(path, redact=redact_secrets)
    monitor.attach()
    errors = connection_errors(url, FAKE_SECRET)
    session = FakeSession(FakeWebSocket("AW_c136first"), errors["unauthorized 401"], errors["refused"],
                          errors["refused"])
    server = fake_server(monkeypatch, session, max_retry=3)
    docs: list[str] = []
    note = monitor.note

    def note_and_write(*args):
        note(*args)
        monitor.tick()  # each line written at once, as the monitor thread would
        docs.append(path.read_text(encoding="utf-8"))

    monkeypatch.setattr(monitor, "note", note_and_write)
    with pytest.raises(RuntimeError, match="after 3 attempts"):
        await server._connection_task()

    assert str(errors["refused"]).startswith("Cannot connect to host livekit.example.test:7880")
    assert FAKE_SECRET in str(errors["unauthorized 401"])  # what the masking must catch
    written = "\n".join(docs + seen["lines"])
    assert "livekit.example.test" not in written and ":7880" not in written and FAKE_SECRET not in written
    assert "(ClientConnectorError: Cannot connect to host <livekit> ssl:default" in written
    assert "(WSServerHandshakeError: 401, message='Invalid response status', url='<livekit>')" in written
    # connected, the disconnect, 401, refused, then livekit-agents gives up (its last error is not logged)
    assert [json.loads(doc)["cause"] for doc in docs] == ["", "", "rejected", "network", "network"]
    assert sorted(json.loads(docs[-1])) == ["cause", "connected_once", "detail", "lock_pid", "lock_started_at", "pid",
                                            "recoveries", "recovery", "since", "state", "worker_id"]


@pytest.mark.parametrize(("text", "url", "expected"), [
    ("Cannot connect to host livekit.example.test:443 ssl:default [x]", "wss://LiveKit.Example.Test",
     "Cannot connect to host <livekit> ssl:default [x]"),
    ("url='wss://livekit.example.test/agent?worker_token=abc'", "wss://livekit.example.test", "url='<livekit>'"),
    ("Cannot connect to host 192.0.2.10:7880 ssl:default [refused]", "ws://192.0.2.10:7880",
     "Cannot connect to host <livekit> ssl:default [refused]"),
    ("host 192.0.2.100:7880 and xlivekit.example.test", "ws://192.0.2.10:7880",
     "host 192.0.2.100:7880 and xlivekit.example.test"),  # other hosts stay
    ("retrying in 2s (OSError: unreachable)", "", "retrying in 2s (OSError: unreachable)"),
])
def test_livekits_address_is_hidden(text, url, expected):
    assert livekit_link.hide_livekit_address(text, url) == expected


def test_a_failed_write_is_written_again_by_the_next_tick(run_dir, monkeypatch):
    """A write that fails (a reader held the file longer than the retries, a full disk) is not lost: agent_ctl
    would otherwise read 'connecting' for hours and its start would skip ensure_room.py."""
    path = run_dir / livekit_link.LINK_FILE_NAME
    monitor = make_monitor(path)[0]
    monitor.tick()
    real = livekit_link.write_json_atomic
    fails = {"left": 1}

    def flaky(target, doc):
        if fails["left"]:
            fails["left"] -= 1
            raise PermissionError(5, "Access is denied")
        real(target, doc)

    monkeypatch.setattr(livekit_link, "write_json_atomic", flaky)
    monitor.note("connected", worker_id="AW_1")
    monitor.tick()
    assert read_doc(path)["state"] == "connecting" and fails["left"] == 0  # that write failed
    monitor.tick()
    assert (read_doc(path)["state"], read_doc(path)["worker_id"]) == ("connected", "AW_1")


def test_a_failed_first_write_is_not_tried_again(run_dir, monkeypatch, lk_logger):
    """Dev mode's reloader parent writes 'connecting' at its start and nothing else: a late copy of that
    write would overwrite what its child, the worker, wrote since. Any later change is written anyway."""
    writes: list[str] = []

    def failing(_target, doc):
        writes.append(doc["state"])
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(livekit_link, "write_json_atomic", failing)
    monitor = make_monitor(run_dir / livekit_link.LINK_FILE_NAME)[0]
    monitor.start()
    monitor.stop()
    monitor.tick()
    assert writes == ["connecting"]


def test_a_write_that_fails_leaves_no_temporary_file(tmp_path, monkeypatch):
    real_write_text = Path.write_text

    def disk_full(self, data, *args, **kwargs):
        real_write_text(self, data[:10], *args, **kwargs)
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "write_text", disk_full)
    with pytest.raises(OSError, match="No space left"):
        livekit_link.write_json_atomic(tmp_path / "agent_link.json", {"state": "connected"})
    assert not list(tmp_path.iterdir())


def test_atomic_write_retries_while_a_reader_holds_the_file(tmp_path, monkeypatch):
    path = tmp_path / "agent_link.json"
    real_replace = os.replace
    failures = {"left": 2}

    def busy_replace(src, dst):
        if Path(dst) == path and failures["left"]:  # a reader holds our file; any other call goes through
            failures["left"] -= 1
            raise PermissionError(13, "The process cannot access the file")
        real_replace(src, dst)

    monkeypatch.setattr(livekit_link.os, "replace", busy_replace)
    livekit_link.write_json_atomic(path, {"state": "connected"})
    assert read_doc(path) == {"state": "connected"} and failures["left"] == 0

    failures["left"] = 99
    with pytest.raises(PermissionError):
        livekit_link.write_json_atomic(path, {"state": "failed"}, attempts=3)
    assert read_doc(path) == {"state": "connected"}  # the old file, whole
    assert [p.name for p in tmp_path.iterdir()] == ["agent_link.json"]


# --------------------------------------------------------------------------- recovery decision (fake clock and probe)


def run_for(monitor, seen, seconds: float, step: float = livekit_link.CHECK_S) -> None:
    end = seen["clock"].now + seconds
    while seen["clock"].now < end:
        seen["clock"].now += step
        monitor.tick()


def connected_then_lost(monitor, seen, state: str = "retrying") -> None:
    monitor.note("connected", worker_id="AW_1")
    monitor.tick()
    monitor.note("retrying", "retrying in 0s (Exception: worker connection closed unexpectedly)")
    if state == "failed":
        monitor.note("failed", "failed to connect to livekit after 16 attempts")
    monitor._lines.clear()  # the setup's own lines, not yet flushed
    seen["lines"].clear()


def test_no_probe_and_no_recovery_while_connected(run_dir):
    monitor, seen = make_monitor(run_dir / livekit_link.LINK_FILE_NAME)
    monitor.note("connected", worker_id="AW_1")
    run_for(monitor, seen, 8 * 3600)
    assert seen["probes"] == 0 and not seen["restarts"]


def test_retrying_asks_for_a_fresh_worker_after_ten_minutes_once_livekit_answers(run_dir):
    monitor, seen = make_monitor(run_dir / livekit_link.LINK_FILE_NAME)
    connected_then_lost(monitor, seen)
    seen["reachable"] = False  # the host is unreachable (laptop offline)
    run_for(monitor, seen, 20 * 60)
    assert not seen["restarts"] and seen["probes"] == 80

    seen["reachable"] = True  # the network is back, livekit-agents is still not connected
    run_for(monitor, seen, livekit_link.CHECK_S)
    assert not seen["restarts"]  # one answer is not enough
    run_for(monitor, seen, livekit_link.CHECK_S)
    assert seen["restarts"] == ["C-136 link recovery: retrying for 1230 s, the LiveKit host answers again"]
    assert seen["lines"] == ["LIVEKIT LINK recovery: C-136 link recovery: retrying for 1230 s, the LiveKit host "
                             "answers again; 'agent_ctl restart --by worker' (pid 4242) replaces this worker under "
                             "run/restart.lock and run/agent.lock"]
    assert read_doc(run_dir / livekit_link.LINK_FILE_NAME)["detail"].startswith("recovery requested: C-136")
    # the helper gets this worker's environment; it registered, so the count of failed restarts starts again
    assert seen["envs"] == [{"C136_BASE": "1", livekit_link.RECOVERIES_ENV: "1"}]

    run_for(monitor, seen, livekit_link.REQUEST_EVERY_S - livekit_link.CHECK_S)
    assert len(seen["restarts"]) == 1  # one request per 5 min while agent_ctl replaces the worker
    run_for(monitor, seen, livekit_link.CHECK_S)
    assert len(seen["restarts"]) == 2


def test_retrying_less_than_ten_minutes_is_left_to_livekit_agents(run_dir):
    monitor, seen = make_monitor(run_dir / livekit_link.LINK_FILE_NAME)
    connected_then_lost(monitor, seen)
    run_for(monitor, seen, livekit_link.RECOVER_AFTER_S - livekit_link.CHECK_S)  # LiveKit answers all along
    assert not seen["restarts"]
    run_for(monitor, seen, livekit_link.CHECK_S)
    assert seen["restarts"] == ["C-136 link recovery: retrying for 600 s, the LiveKit host answers again"]


def test_failed_link_is_replaced_without_waiting_ten_minutes(run_dir):
    # A worker that a recovery started (2 failed restarts before it) and that has registered since.
    monitor, seen = make_monitor(run_dir / livekit_link.LINK_FILE_NAME, recoveries=2)
    connected_then_lost(monitor, seen, state="failed")
    run_for(monitor, seen, 2 * livekit_link.CHECK_S)
    assert seen["restarts"] == ["C-136 link recovery: livekit-agents stopped retrying, the LiveKit host answers again"]
    assert seen["envs"][0][livekit_link.RECOVERIES_ENV] == "1"  # it registered: the count starts again


def test_one_failed_probe_starts_the_count_again(run_dir):
    monitor, seen = make_monitor(run_dir / livekit_link.LINK_FILE_NAME)
    connected_then_lost(monitor, seen, state="failed")
    answers = iter([True, False, True])
    monitor._probe = lambda: next(answers)
    run_for(monitor, seen, 3 * livekit_link.CHECK_S)
    assert not seen["restarts"]


def test_a_worker_that_livekit_turned_away_never_restarts_itself(run_dir):
    """Never registered and turned away (a wrong key, URL or certificate): a restart would only loop."""
    monitor, seen = make_monitor(run_dir / livekit_link.LINK_FILE_NAME)
    monitor.note("retrying", "retrying in 0s (ClientConnectorError: refused)", cause="network")
    monitor.note("retrying", "retrying in 2s (WSServerHandshakeError: 401)", cause="rejected")  # the latest counts
    monitor.note("failed", "failed to connect to livekit after 16 attempts")
    run_for(monitor, seen, 3 * 3600)
    assert not seen["restarts"] and seen["probes"] == 0
    assert seen["lines"] == [
        "LIVEKIT LINK retrying (never connected yet): retrying in 0s (ClientConnectorError: refused)",
        "LIVEKIT LINK failed (never connected yet): failed to connect to livekit after 16 attempts",
    ]
    doc = read_doc(run_dir / livekit_link.LINK_FILE_NAME)
    assert (doc["cause"], doc["recoveries"], livekit_link.self_recovers(doc)) == ("rejected", 0, False)


def test_a_worker_that_never_reached_livekit_asks_once_it_answers_after_five_minutes(run_dir):
    """Started while the laptop was offline: livekit-agents gave up before the network came back."""
    monitor, seen = make_monitor(run_dir / livekit_link.LINK_FILE_NAME)
    monitor.note("retrying", "retrying in 0s (ClientConnectorError: Cannot connect to host <livekit>)",
                 cause="network")
    monitor.note("failed", "failed to connect to livekit after 16 attempts")
    run_for(monitor, seen, livekit_link.REQUEST_EVERY_S - livekit_link.CHECK_S)
    assert not seen["restarts"]  # LiveKit answers, but 5 min have not passed since the start
    run_for(monitor, seen, livekit_link.CHECK_S)
    assert seen["restarts"] == ["C-136 link recovery: not registered 300 s after its start (automatic restart 1 "
                                "in a row), the LiveKit host answers again"]
    assert seen["envs"][0][livekit_link.RECOVERIES_ENV] == "1"


def test_a_worker_that_a_recovery_started_asks_again_with_a_growing_pause(run_dir):
    """The replacement never registered (the network dropped again, or LiveKit was still down behind a
    proxy): it asks too, whatever the cause, after a pause that doubles with each restart in a row."""
    monitor, seen = make_monitor(run_dir / livekit_link.LINK_FILE_NAME, recoveries=1)
    monitor.note("retrying", "retrying in 0s (WSServerHandshakeError: 401)", cause="rejected")
    monitor.note("failed", "failed to connect to livekit after 16 attempts")
    run_for(monitor, seen, livekit_link.pause_s(1) - livekit_link.CHECK_S)
    assert not seen["restarts"]
    run_for(monitor, seen, livekit_link.CHECK_S)
    assert seen["restarts"] == ["C-136 link recovery: not registered 600 s after its start (automatic restart 2 "
                                "in a row), the LiveKit host answers again"]
    assert seen["envs"][0][livekit_link.RECOVERIES_ENV] == "2"
    assert [livekit_link.pause_s(n) for n in (0, 1, 2, 3, 4, 50)] == [300, 600, 1200, 2400, 3600, 3600]


def test_a_never_registered_worker_that_still_retries_waits_ten_minutes(run_dir):
    monitor, seen = make_monitor(run_dir / livekit_link.LINK_FILE_NAME)
    monitor.note("retrying", "retrying in 10s (ConnectionTimeoutError: timeout)", cause="network")
    run_for(monitor, seen, livekit_link.RECOVER_AFTER_S - livekit_link.CHECK_S)
    assert not seen["restarts"]
    run_for(monitor, seen, livekit_link.CHECK_S)
    assert len(seen["restarts"]) == 1


def test_the_count_of_failed_restarts_comes_only_from_a_recovery_start(monkeypatch):
    monkeypatch.setenv(livekit_link.RECOVERIES_ENV, "2")
    monkeypatch.setenv("JARVIS_STARTED_BY", "worker")
    assert livekit_link.inherited_recoveries() == 2
    for who in ("user", "claude", ""):
        monkeypatch.setenv("JARVIS_STARTED_BY", who)
        assert livekit_link.inherited_recoveries() == 0
    monkeypatch.setenv("JARVIS_STARTED_BY", "worker")
    for bad in ("", "two", "-3"):
        monkeypatch.setenv(livekit_link.RECOVERIES_ENV, bad)
        assert livekit_link.inherited_recoveries() == 0


def test_reconnecting_resets_the_ten_minutes(run_dir):
    monitor, seen = make_monitor(run_dir / livekit_link.LINK_FILE_NAME)
    connected_then_lost(monitor, seen)
    run_for(monitor, seen, 585)  # ticks every 15 s: 585 s down, just under the 10 min
    monitor.note("connected", worker_id="AW_2")
    run_for(monitor, seen, 30)
    monitor.note("retrying", "retrying in 0s")
    run_for(monitor, seen, 400)
    assert not seen["restarts"]


def test_a_running_job_postpones_the_recovery(run_dir):
    monitor, seen = make_monitor(run_dir / livekit_link.LINK_FILE_NAME)
    connected_then_lost(monitor, seen, state="failed")
    seen["jobs"] = 1
    run_for(monitor, seen, 30 * 60)
    assert not seen["restarts"]
    assert seen["lines"] == ["LIVEKIT LINK recovery postponed: 1 job(s) still running"]  # once
    seen["jobs"] = 0
    run_for(monitor, seen, livekit_link.CHECK_S)
    assert len(seen["restarts"]) == 1


def test_recovery_can_be_switched_off(run_dir):
    monitor, seen = make_monitor(run_dir / livekit_link.LINK_FILE_NAME, recovery=False)
    connected_then_lost(monitor, seen, state="failed")
    run_for(monitor, seen, 3600)
    assert not seen["restarts"] and seen["probes"] == 0
    assert read_doc(run_dir / livekit_link.LINK_FILE_NAME)["recovery"] is False


def test_a_helper_that_cannot_start_is_tried_again_later(run_dir):
    monitor, seen = make_monitor(run_dir / livekit_link.LINK_FILE_NAME)
    connected_then_lost(monitor, seen, state="failed")
    seen["restart_error"] = FileNotFoundError("agent_ctl.py is missing")
    run_for(monitor, seen, 2 * livekit_link.CHECK_S)
    assert seen["lines"] == ["LIVEKIT LINK recovery not started (C-136 link recovery: livekit-agents stopped "
                             "retrying, the LiveKit host answers again): agent_ctl.py is missing; next try in 300 s"]
    seen["restart_error"] = None
    run_for(monitor, seen, livekit_link.REQUEST_EVERY_S - livekit_link.CHECK_S)
    assert not seen["restarts"]
    run_for(monitor, seen, livekit_link.CHECK_S)
    assert len(seen["restarts"]) == 1


@pytest.mark.parametrize(("url", "address"), [
    ("wss://livekit.example.test", ("livekit.example.test", 443)),
    ("ws://192.0.2.10:7880", ("192.0.2.10", 7880)),
    ("https://livekit.example.test:8443/", ("livekit.example.test", 8443)),
    ("http://livekit.example.test", ("livekit.example.test", 80)),
    ("", None), ("not a url", None), ("ws://livekit.example.test:99999", None),
    ("wss://[livekit.example.test", None), ("wss://[livekit]:7880", None),  # urlsplit raises for both
])
def test_the_livekit_address_comes_from_livekit_url(url, address):
    assert livekit_link.livekit_address(url) == address


@pytest.mark.parametrize(("url", "target"), [
    ("wss://livekit.example.test", "https://livekit.example.test/rtc/validate"),
    ("ws://192.0.2.10:7880", "http://192.0.2.10:7880/rtc/validate"),
    ("wss://livekit.example.test/lk/", "https://livekit.example.test/lk/rtc/validate"),
    ("https://livekit.example.test:8443", "https://livekit.example.test:8443/rtc/validate"),
    ("", None), ("not a url", None), ("wss://[livekit.example.test", None), ("ftp://livekit.example.test", None),
])
def test_the_probe_asks_rtc_validate_under_livekit_url(url, target):
    assert livekit_link.livekit_validate_url(url) == target


@pytest.fixture
def livekit_stub():
    """A local HTTP server on 127.0.0.1 in the place of LiveKit, or of nginx in front of it: every GET gets
    stub.status, and stub.paths records what was asked. LIVEKIT_URL for it: stub.url."""
    stub = types.SimpleNamespace(status=401, paths=[])

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            stub.paths.append(self.path)
            self.send_response(stub.status)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    stub.url = f"ws://127.0.0.1:{server.server_address[1]}"
    yield stub
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


@pytest.mark.parametrize(("status", "answers"), [(401, True), (404, True), (502, False), (503, False), (504, False)])
def test_the_probe_counts_only_an_answer_of_livekit_itself(livekit_stub, monkeypatch, status, answers):
    """LiveKit answers /rtc/validate without a token with 401. nginx on the domain accepts the connection and
    answers 502 while LiveKit behind it is down (C-16): that is no answer."""
    livekit_stub.status = status
    monkeypatch.setenv("LIVEKIT_URL", livekit_stub.url)
    monkeypatch.delenv("NO_PROXY", raising=False)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):  # nothing listens there: the probe uses no proxy
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    assert livekit_link.probe_livekit(timeout=2.0) is answers
    assert livekit_stub.paths == ["/rtc/validate"]


def test_the_probe_without_an_answer_or_a_usable_livekit_url(monkeypatch):
    with socket.socket() as sock:  # a local port that nothing listens on
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    monkeypatch.setenv("LIVEKIT_URL", f"ws://127.0.0.1:{port}")
    assert livekit_link.probe_livekit(timeout=0.5) is False  # refused, or Windows' slow refusal: a timeout
    monkeypatch.setattr(livekit_link.urllib.request, "build_opener", lambda *_a: pytest.fail("a request was sent"))
    for unusable in ("", "not a url", "wss://[livekit.example.test", "ftp://livekit.example.test"):
        monkeypatch.setenv("LIVEKIT_URL", unusable)
        assert livekit_link.probe_livekit() is False


def test_a_failed_link_waits_while_nginx_answers_for_a_livekit_that_is_down(livekit_stub, monkeypatch, run_dir):
    """C-16: LIVEKIT_URL on the domain and LiveKit down behind nginx (502). A fresh worker now would not
    register either and only start the growing pauses; it is asked for once LiveKit itself answers."""
    monkeypatch.setenv("LIVEKIT_URL", livekit_stub.url)
    monitor, seen = make_monitor(run_dir / livekit_link.LINK_FILE_NAME)
    monitor._probe = lambda: livekit_link.probe_livekit(timeout=2.0)
    connected_then_lost(monitor, seen, state="failed")
    livekit_stub.status = 502
    run_for(monitor, seen, 4 * livekit_link.CHECK_S)
    assert not seen["restarts"] and len(livekit_stub.paths) == 4
    livekit_stub.status = 401
    run_for(monitor, seen, 2 * livekit_link.CHECK_S)
    assert seen["restarts"] == ["C-136 link recovery: livekit-agents stopped retrying, the LiveKit host answers again"]


# --------------------------------------------------------------------------- the restart request (no real process)


def test_recovery_runs_agent_ctl_restart_by_worker_detached(monkeypatch, tmp_path):
    spawned = []

    def fake_popen(cmd, **kwargs):
        spawned.append((cmd, kwargs))
        flags = kwargs.get("creationflags", 0)
        if sys.platform == "win32" and flags & subprocess.CREATE_BREAKAWAY_FROM_JOB:
            raise PermissionError(5, "Access is denied")  # inside a job that forbids breakaway
        return types.SimpleNamespace(pid=5150)

    monkeypatch.setattr(livekit_link, "_popen", fake_popen)
    monkeypatch.setattr(livekit_link, "RECOVERY_LOG", tmp_path / "logs" / "agent_recovery.log")
    reason = "C-136 link recovery: retrying for 612 s, the LiveKit host answers again"

    assert livekit_link.request_worker_restart(reason, {"C136_KEEP": "1"}, dict(OWNER)) == 5150

    cmd, kwargs = spawned[-1]
    assert cmd[1:] == [str(livekit_link.AGENT_CTL), "restart", "--by", "worker", "--reason", reason,
                       "--expect-pid", "31500", "--expect-started", OWNER_STARTED]
    assert Path(cmd[0]).name.lower().startswith("python")
    assert kwargs["env"] == {"C136_KEEP": "1"} and kwargs["cwd"] == agent_lock.PROJECT_ROOT
    assert kwargs["stdin"] is subprocess.DEVNULL and kwargs["close_fds"] is True
    assert Path(kwargs["stdout"].name) == tmp_path / "logs" / "agent_recovery.log" == Path(kwargs["stderr"].name)
    if sys.platform == "win32":
        assert len(spawned) == 2  # retried without breakaway
        flags = kwargs["creationflags"]
        assert flags & subprocess.CREATE_NO_WINDOW and flags & subprocess.CREATE_NEW_PROCESS_GROUP
    assert reason in (tmp_path / "logs" / "agent_recovery.log").read_text(encoding="utf-8")
    # agent_ctl accepts it, and its stop never takes the helper for an agent process.
    args = agent_ctl.parse_args(cmd[2:])
    assert (args.command, args.by, args.reason, args.force) == ("restart", "worker", reason, False)
    assert (args.expect_pid, args.expect_started) == (31500, OWNER_STARTED)
    assert not agent_ctl.is_agent_cmdline(cmd)


def test_the_default_restart_names_this_worker_and_hands_on_the_environment(run_dir, monkeypatch):
    requests = []
    monkeypatch.setattr(livekit_link, "request_worker_restart",
                        lambda reason, env, owner=None: requests.append((reason, env, owner)) or 5150)
    monitor = livekit_link.LinkMonitor(run_dir / livekit_link.LINK_FILE_NAME, dict(OWNER), recoveries=1,
                                       base_env=lambda: {"C136_BASE": "1"}, log=lambda _line: None)
    monitor._request("C-136 link recovery: test")
    assert requests == [("C-136 link recovery: test", {"C136_BASE": "1", livekit_link.RECOVERIES_ENV: "2"},
                         OWNER)]


def test_the_fresh_worker_reads_the_env_files_again(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("LIVEKIT_URL=wss://livekit.example.test\nC136_FROM_FILE=old\n", encoding="utf-8")
    monkeypatch.setattr(agent_runner, "_ENV_FILES", (env_file, tmp_path / "missing.env"))
    monkeypatch.setattr(agent_runner, "_START_ENV", {"PATH": "C:/bin", "C136_FROM_FILE": "old",
                                                     "LIVEKIT_URL": "wss://stale.example.test", "C136_SHELL": "1"})
    assert agent_runner._restart_env() == {"PATH": "C:/bin", "C136_SHELL": "1"}


# --------------------------------------------------------------------------- worker wiring (agent_runner)


def test_the_cli_hook_is_installed_once_next_to_the_k29_mask():
    hooked = livekit_cli._configure_logger
    assert getattr(hooked, "starts_link_monitor", False) and getattr(hooked, "masks_secrets", False)
    agent_runner._hook_link_monitor()
    agent_runner._mask_cli_log_handler()
    assert livekit_cli._configure_logger is hooked


@pytest.fixture
def no_monitor(monkeypatch, lk_logger):
    monkeypatch.setattr(agent_runner, "_link_monitor", None)
    yield
    if agent_runner._link_monitor is not None:
        agent_runner._link_monitor.stop()


def test_the_monitor_starts_only_in_the_pc_worker_that_holds_the_lock(monkeypatch, run_dir, no_monitor):
    monkeypatch.setenv("LIVEKIT_URL", "ws://127.0.0.1:9")  # its default probes never reach a real server
    monkeypatch.setenv("JARVIS_WORKER_PORT", "9")
    monkeypatch.setenv("JARVIS_DESKTOP_MODE", "local")
    assert agent_runner.start_link_monitor() is None  # pytest: the lock is disabled, nothing is written
    assert not run_dir.exists()

    monkeypatch.setattr(livekit_link, "lock_owner", lambda: dict(OWNER))
    monkeypatch.setenv("JARVIS_DESKTOP_MODE", "remote")
    assert agent_runner.start_link_monitor() is None  # the VPS brain is not this task's
    monkeypatch.setenv("JARVIS_DESKTOP_MODE", "local")
    monkeypatch.setattr(agent_runner, "in_container", lambda: True)
    assert agent_runner.start_link_monitor() is None
    monkeypatch.setattr(agent_runner, "in_container", lambda: False)
    monkeypatch.setenv("JARVIS_LINK_RECOVERY", "0")
    monkeypatch.setenv("JARVIS_STARTED_BY", "worker")  # a worker that a recovery started
    monkeypatch.setenv(livekit_link.RECOVERIES_ENV, "2")

    monitor = agent_runner.start_link_monitor()

    assert monitor is not None and agent_runner.start_link_monitor() is monitor  # once per process
    doc = read_doc(run_dir / livekit_link.LINK_FILE_NAME)
    assert (doc["state"], doc["lock_pid"], doc["recovery"], doc["recoveries"]) == ("connecting", 31500, False, 2)
    assert monitor._base_env is agent_runner._restart_env  # the helper re-reads the .env files
    agent_runner._quiet_livekit_agents_logger()  # what a job does: INFO stays for 'registered worker'
    assert logging.getLogger(livekit_link.LK_LOGGER).level == logging.INFO


def test_without_the_monitor_a_job_keeps_the_old_warning_level(no_monitor, lk_logger):
    lk_logger.setLevel(logging.INFO)
    agent_runner._quiet_livekit_agents_logger()
    assert lk_logger.level == logging.WARNING


def test_lock_owner_is_this_process_or_its_parent(monkeypatch):
    monkeypatch.setattr(agent_lock, "lock_disabled", lambda: False)
    monkeypatch.setattr(agent_lock, "is_locked", lambda: True)
    for pid, owner in ((os.getpid(), True), (os.getppid(), True), (987654, False)):
        monkeypatch.setattr(agent_lock, "read_pid_info", lambda pid=pid: {"pid": pid, "started_at": OWNER_STARTED})
        assert (livekit_link.lock_owner() is not None) is owner
    monkeypatch.setattr(agent_lock, "is_locked", lambda: False)
    assert livekit_link.lock_owner() is None


# --------------------------------------------------------------------------- agent_ctl: status, wait, start, restart


class FakePsutil:
    """agent_ctl's psutil with no process at all (status then names the pid as not running)."""

    class Error(Exception):
        pass

    class NoSuchProcess(Error):
        pass

    class AccessDenied(Error):
        pass

    CONN_LISTEN = "LISTEN"

    def Process(self, pid: int):  # noqa: N802  (psutil's name)
        raise self.NoSuchProcess(pid)

    def net_connections(self, kind: str = "tcp") -> list:
        return []


@pytest.fixture
def ctl(monkeypatch, run_dir, tmp_path):
    """agent_ctl with its files in tmp_path, a fake clock and every process call refused."""
    logs = tmp_path / "logs"
    for name in ("JARVIS_WORKER_HOST", "JARVIS_WORKER_PORT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(agent_ctl, "LOG_DIR", logs)
    monkeypatch.setattr(agent_ctl, "ENV_FILES", ())
    clock = FakeClock()
    monkeypatch.setattr(agent_ctl, "_monotonic", clock)
    monkeypatch.setattr(agent_ctl, "_sleep", clock.sleep)

    def refuse(cmd, **_kwargs):
        raise AssertionError(f"unexpected process call: {cmd[:3]}")

    monkeypatch.setattr(agent_ctl, "_run", refuse)
    monkeypatch.setattr(agent_ctl, "_popen", refuse)
    monkeypatch.setattr(agent_ctl, "probe_health", lambda timeout=2.0: 200)
    monkeypatch.setattr(agent_ctl, "worker_info", lambda: None)
    # No test reaches the VPS (C-16): the bridge's health answers 200 and LiveKit's /rtc/validate 401.
    monkeypatch.setattr(agent_ctl, "_http_status",
                        lambda url, timeout: (401 if url.endswith("/rtc/validate") else 200, ""))
    monkeypatch.setattr(agent_ctl, "psutil", FakePsutil())
    state = {"locked": True}
    monkeypatch.setattr(agent_lock, "is_locked", lambda: state["locked"])
    return types.SimpleNamespace(run=run_dir, logs=logs, clock=clock, state=state)


def write_pid_json(run_dir: Path, pid: int = 31500, started: str = OWNER_STARTED, by: str = "claude") -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "agent.pid.json").write_text(json.dumps({
        "pid": pid, "started_at": started, "by": by, "reason": "C-136", "argv": ["src/core/agent.py", "start"],
    }), encoding="utf-8")


def write_link(run_dir: Path, state: str, *, age_s: float = 100, worker_id: str = "AW_c136",
               detail: str = "", connected_once: bool = True, lock_pid: int = 31500,
               lock_started_at: str = OWNER_STARTED, cause: str = "", recoveries: int = 0) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    livekit_link.write_json_atomic(run_dir / livekit_link.LINK_FILE_NAME, {
        "state": state, "since": time.time() - age_s, "worker_id": worker_id, "detail": detail, "cause": cause,
        "connected_once": connected_once, "recovery": True, "recoveries": recoveries, "pid": lock_pid,
        "lock_pid": lock_pid, "lock_started_at": lock_started_at})


def audit_lines(logs: Path) -> list[dict]:
    path = logs / "agent_ctl.log"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.is_file() else []


def test_status_prints_the_link_state_and_its_age(ctl, capsys):
    write_pid_json(ctl.run)
    write_link(ctl.run, "connected", age_s=3725, worker_id="AW_c136ok")
    assert agent_ctl.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "  link     connected for 1:02:0" in out and "(LiveKit worker AW_c136ok)" in out
    assert "not connected to LiveKit" not in out

    write_link(ctl.run, "retrying", age_s=190, detail="retrying in 10s (ClientConnectorError: host down)")
    assert agent_ctl.main(["status"]) == 0  # still exit 0: a worker runs (K-53)
    out = capsys.readouterr().out
    assert "  link     retrying for 0:03:1" in out and "(retrying in 10s (ClientConnectorError: host down))" in out
    assert "WARNING  not connected to LiveKit, so no room gets this worker: it asks agent_ctl for a fresh " \
           "worker once LiveKit answers again" in out

    write_link(ctl.run, "failed", age_s=5, detail="failed to connect to livekit after 16 attempts",
               connected_once=False, cause="rejected")
    agent_ctl.main(["status"])
    out = capsys.readouterr().out
    assert "  link     failed for 0:00:0" in out and "16 attempts, never connected)" in out
    assert "LiveKit or TLS turned it away and it never restarts itself for that: check LIVEKIT_URL and its keys" \
        in out

    write_link(ctl.run, "failed", detail="failed to connect to livekit after 16 attempts", connected_once=False,
               cause="network")  # started while the laptop was offline
    agent_ctl.main(["status"])
    assert "it has not registered since it started; once LiveKit answers again it asks agent_ctl for a fresh " \
           "worker, at the earliest 5 min after its start" in capsys.readouterr().out
    write_link(ctl.run, "failed", connected_once=False, cause="rejected", recoveries=2)  # a recovery started it
    agent_ctl.main(["status"])
    assert "at the earliest 20 min after its start" in capsys.readouterr().out

    write_link(ctl.run, "connecting", worker_id="", connected_once=False)
    agent_ctl.main(["status"])
    assert "it registers within seconds once LiveKit answers" in capsys.readouterr().out


def test_status_says_unknown_for_a_worker_without_its_own_link_file(ctl, capsys):
    write_pid_json(ctl.run)
    agent_ctl.main(["status"])
    assert "  link     unknown (no run/agent_link.json from this worker: it started before C-136)" in \
        capsys.readouterr().out
    write_link(ctl.run, "connected", lock_started_at="2026-10-01T09:00:00+05:00")  # an earlier worker's file
    agent_ctl.main(["status"])
    out = capsys.readouterr().out
    assert "link     unknown" in out and "AW_c136" not in out


def test_wait_is_ready_only_once_the_link_is_connected(ctl, monkeypatch, capsys):
    write_pid_json(ctl.run)
    write_link(ctl.run, "connecting", worker_id="")
    sleeps = {"n": 0}
    real_sleep = ctl.clock.sleep

    def sleep(seconds):
        real_sleep(seconds)
        sleeps["n"] += 1
        if sleeps["n"] == 3:  # registered while health was 200 all along
            write_link(ctl.run, "connected", worker_id="AW_c136new")

    monkeypatch.setattr(agent_ctl, "_sleep", sleep)
    assert agent_ctl.main(["wait", "--timeout", "60"]) == 0
    assert ctl.clock.slept == 3  # three polls, no register settle: the link says it registered
    assert "Worker pid 31500 is healthy (200 on http://127.0.0.1:8081/), LiveKit link connected," in \
        capsys.readouterr().out
    entry = audit_lines(ctl.logs)[-1]
    assert (entry["result"], entry["link"]) == ("healthy", "connected")


def test_wait_fails_at_once_when_the_link_failed(ctl, monkeypatch, capsys):
    write_pid_json(ctl.run)
    write_link(ctl.run, "failed", detail="failed to connect to livekit after 16 attempts")
    monkeypatch.setattr(agent_ctl, "probe_health", lambda timeout=2.0: 503)
    assert agent_ctl.main(["wait", "--timeout", "60"]) == 1
    assert "the worker's LiveKit link failed (failed to connect to livekit after 16 attempts)" in \
        capsys.readouterr().err
    assert ctl.clock.slept == 0


def test_wait_times_out_while_the_link_retries_even_with_health_200(ctl, capsys):
    write_pid_json(ctl.run)
    write_link(ctl.run, "retrying", detail="retrying in 10s (OSError: unreachable)")
    assert agent_ctl.main(["wait", "--timeout", "30"]) == 1
    err = capsys.readouterr().err
    assert "no LiveKit link within 30 s (health 200; link retrying for" in err and "unreachable" in err


def test_start_runs_ensure_room_only_after_the_link_connected(ctl, monkeypatch, capsys):
    ctl.state["locked"] = False
    polls = {"n": 0}

    class FakePopen:
        pid = 777
        returncode = None

        def poll(self):
            return None

    def fake_popen(cmd, **_kwargs):
        ctl.state["locked"] = True
        write_pid_json(ctl.run)
        write_link(ctl.run, "connecting", worker_id="")
        return FakePopen()

    def fake_probe(timeout=2.0):
        if not ctl.state["locked"]:
            return None
        polls["n"] += 1
        if polls["n"] == 4:
            write_link(ctl.run, "connected", worker_id="AW_c136start")
        return 200

    runs: list = []
    monkeypatch.setattr(agent_ctl, "_popen", fake_popen)
    monkeypatch.setattr(agent_ctl, "probe_health", fake_probe)
    monkeypatch.setattr(agent_ctl, "_run", lambda cmd, **kw: runs.append(polls["n"]) or subprocess.CompletedProcess(cmd, 0))

    assert agent_ctl.main(["start", "--by", "claude", "--reason", "C-136 test"]) == 0
    assert runs == [4]  # ensure_room.py once, after the poll that saw the link connected
    assert "Worker pid 31500 healthy (200) and connected to LiveKit after" in capsys.readouterr().out
    entry = audit_lines(ctl.logs)[-1]
    assert (entry["result"], entry["link"], entry["ensure_room"]) == ("started", "connected", 0)


def test_restart_by_the_worker_uses_the_normal_path_and_prints_no_highlight(ctl, monkeypatch, capsys):
    write_pid_json(ctl.run)
    calls: list = []

    def stop(timeout, force=False):
        calls.append(("stop", timeout, force))
        return 0, {"result": "stopped", "old_pid": 31500, "graceful": True, "killed": [], "stop_s": 1.0}

    def start(args, mode, *, foreground, timeout=60):
        calls.append(("start", mode, foreground, args.by))
        return 0, {"result": "started", "new_pid": 31600, "health": 200, "start_s": 9.0, "ensure_room": 0}

    monkeypatch.setattr(agent_ctl, "stop_worker", stop)
    monkeypatch.setattr(agent_ctl, "start_worker", start)
    reason = "C-136 link recovery: retrying for 612 s, the LiveKit host answers again"

    assert agent_ctl.main(["restart", "--by", "worker", "--reason", reason]) == 0

    assert calls == [("stop", 20, False), ("start", "start", False, "worker")]
    assert "done_highlight.md entry" not in capsys.readouterr().out
    entry = audit_lines(ctl.logs)[-1]
    assert (entry["action"], entry["by"], entry["reason"], entry["result"]) == ("restart", "worker", reason, "restarted")
    assert not (ctl.run / "restart.lock").exists()


def test_a_recovery_never_runs_beside_another_restart(ctl, monkeypatch, capsys):
    """run/restart.lock held by a running restart: the worker's request is refused and nothing stops."""
    write_pid_json(ctl.run)
    stamp = time.time() - 5
    (ctl.run / "restart.lock").write_text(json.dumps({"pid": os.getpid(), "ts": stamp, "by": "claude"}),
                                          encoding="utf-8")
    monkeypatch.setattr(agent_ctl, "_pid_running_since", lambda pid, since: True)
    monkeypatch.setattr(agent_ctl, "stop_worker", lambda *a, **k: pytest.fail("stopped beside another restart"))
    assert agent_ctl.main(["restart", "--by", "worker", "--reason", "C-136 link recovery: test"]) == 4
    assert "run/restart.lock is held by a restart by claude" in capsys.readouterr().err


RECOVERY_RESTART = ["restart", "--by", "worker", "--reason", "C-136 link recovery: test",
                    "--expect-pid", "31500", "--expect-started", OWNER_STARTED]


@pytest.mark.parametrize("case", ["stopped", "replaced", "pid reused", "reconnected"])
def test_a_recovery_request_that_no_longer_applies_is_refused(ctl, monkeypatch, capsys, case):
    """Between the worker's request and its helper: someone stopped it (for a pip install), another worker
    took over, or livekit-agents reconnected by itself. Nothing is stopped or started then."""
    write_pid_json(ctl.run)
    write_link(ctl.run, "failed", detail="failed to connect to livekit after 16 attempts")
    if case == "stopped":
        ctl.state["locked"] = False
    elif case == "replaced":
        write_pid_json(ctl.run, pid=31600, started="2026-10-02T15:00:00+05:00")
    elif case == "pid reused":
        write_pid_json(ctl.run, started="2026-10-02T15:00:00+05:00")
    else:
        write_link(ctl.run, "connected", worker_id="AW_c136again")
    monkeypatch.setattr(agent_ctl, "stop_worker", lambda *a, **k: pytest.fail("stopped"))
    monkeypatch.setattr(agent_ctl, "start_worker", lambda *a, **k: pytest.fail("started"))

    assert agent_ctl.main(RECOVERY_RESTART) == agent_ctl.EXIT_REFUSED

    expected = {"stopped": "no worker runs any more (stopped in the meantime)",
                "replaced": "another worker holds run/agent.lock now (pid 31600, started 2026-10-02T15:00:00+05:00)",
                "pid reused": "another worker holds run/agent.lock now (pid 31500, started 2026-10-02T15:00:00+05:00)",
                "reconnected": "its LiveKit link is connected again"}[case]
    captured = capsys.readouterr()
    assert f"Restart refused: worker pid 31500 asked for it, but {expected}" in captured.err
    assert "done_highlight.md entry" not in captured.out
    entry = audit_lines(ctl.logs)[-1]
    assert (entry["result"], entry["by"], entry["expect_pid"]) == ("refused", "worker", 31500)
    assert not (ctl.run / "restart.lock").exists() and not (ctl.run / "last_restart.json").exists()


def test_a_recovery_request_still_valid_replaces_the_worker(ctl, monkeypatch):
    write_pid_json(ctl.run)
    write_link(ctl.run, "failed", detail="failed to connect to livekit after 16 attempts")
    calls: list = []
    monkeypatch.setattr(agent_ctl, "stop_worker", lambda timeout, force=False: calls.append("stop") or (
        0, {"result": "stopped", "old_pid": 31500, "graceful": True, "killed": [], "stop_s": 1.0}))
    monkeypatch.setattr(agent_ctl, "start_worker", lambda args, mode, *, foreground, timeout=60: calls.append(
        "start") or (0, {"result": "started", "new_pid": 31600, "health": 200, "start_s": 9.0, "ensure_room": 0}))
    assert agent_ctl.main(RECOVERY_RESTART) == 0
    assert calls == ["stop", "start"] and audit_lines(ctl.logs)[-1]["result"] == "restarted"


def test_a_recovery_never_starts_a_worker_that_left_before_its_stop(ctl, monkeypatch, capsys):
    """The worker was there at the check but gone at the stop (someone else stopped it): no start."""
    write_pid_json(ctl.run)
    write_link(ctl.run, "failed", detail="failed to connect to livekit after 16 attempts")
    monkeypatch.setattr(agent_ctl, "stop_worker", lambda timeout, force=False: (0, {"result": "not_running"}))
    monkeypatch.setattr(agent_ctl, "start_worker", lambda *a, **k: pytest.fail("started"))
    assert agent_ctl.main(RECOVERY_RESTART) == agent_ctl.EXIT_REFUSED
    assert "worker pid 31500 exited before this restart stopped it; no new worker is started" in \
        capsys.readouterr().err
    assert audit_lines(ctl.logs)[-1]["result"] == "refused"


def test_only_the_workers_own_restart_hands_its_count_on(monkeypatch):
    monkeypatch.setenv(livekit_link.RECOVERIES_ENV, "2")
    monkeypatch.setattr(agent_ctl, "_registry_path_values", lambda: [])
    env = agent_ctl.agent_env("worker", "C-136 link recovery: test", background=True)
    assert (env[livekit_link.RECOVERIES_ENV], env["JARVIS_STARTED_BY"]) == ("2", "worker")
    for who in ("claude", "cursor", "user"):
        assert livekit_link.RECOVERIES_ENV not in agent_ctl.agent_env(who, "test", background=True)


@pytest.mark.parametrize("by", ["worker", "claude"])
def test_a_foreground_window_says_when_the_workers_own_recovery_replaces_it(ctl, monkeypatch, capsys, by):
    """start-agent.bat's window: the recovery's fresh worker runs hidden, so closing the window no longer
    stops JARVIS. A restart by anyone else shows no such note."""
    ctl.state["locked"] = False

    class FakeWorker:
        pid = 888

        def wait(self, timeout=None):
            # the helper holds run/restart.lock while it replaces this worker with Ctrl+Break
            (ctl.run / "restart.lock").write_text(json.dumps({"pid": 5150, "ts": time.time(), "by": by}),
                                                  encoding="utf-8")
            return agent_ctl.STATUS_CONTROL_C_EXIT

    def fake_popen(cmd, **_kwargs):
        ctl.state["locked"] = True
        write_pid_json(ctl.run)
        return FakeWorker()

    monkeypatch.setattr(agent_ctl, "_popen", fake_popen)
    monkeypatch.setattr(agent_ctl, "probe_health", lambda timeout=2.0: 200 if ctl.state["locked"] else None)
    monkeypatch.setattr(agent_ctl, "_pid_running_since", lambda pid, since: pid == 5150)

    assert agent_ctl.main(["start", "--by", "user", "--reason", "start-agent.bat", "--foreground"]) == 0

    out = capsys.readouterr().out
    assert "Worker stopped by Ctrl+C / Ctrl+Break" in out
    note = "the new worker runs hidden in the background, so closing this window no longer stops JARVIS"
    assert (note in out) is (by == "worker")
    assert audit_lines(ctl.logs)[-1].get("replaced_by") == ("worker" if by == "worker" else None)


def test_a_worker_that_its_recovery_started_plays_no_startup_voice(monkeypatch, capsys):
    """The automatic restart can come at 3 AM: no startup voice then; every other start plays it as before."""
    fake_runner = types.ModuleType("src.core.agent_runner")
    fake_runner.entrypoint = MagicMock(name="entrypoint")
    fake_runner.build_worker_options = MagicMock(name="build_worker_options")
    with patch.dict(sys.modules, {"src.core.agent_runner": fake_runner}):
        sys.modules.pop("src.core.agent", None)
        agent = importlib.import_module("src.core.agent")
    threads: list[dict] = []
    monkeypatch.setattr(agent, "running_under_pytest", False)
    monkeypatch.setattr(threading, "Thread", lambda **kwargs: threads.append(kwargs) or MagicMock())
    monkeypatch.setenv("JARVIS_DESKTOP_MODE", "local")

    monkeypatch.setenv("JARVIS_STARTED_BY", "worker")
    assert agent.play_startup_voice() is None and not threads
    assert "Startup voice skipped: automatic restart (C-136 LiveKit link recovery)" in capsys.readouterr().out
    monkeypatch.setenv("JARVIS_STARTED_BY", "user")
    assert agent.play_startup_voice() is not None and len(threads) == 1


def test_a_monitor_file_reads_back_through_agent_ctl(ctl, run_dir):
    """Both sides of run/agent_link.json: what the worker writes is what status and wait read."""
    write_pid_json(ctl.run)
    monitor = make_monitor(run_dir / livekit_link.LINK_FILE_NAME)[0]
    monitor.note("connected", worker_id="AW_c136both")
    monitor.tick()
    link = agent_ctl.worker_link()
    assert (link["state"], link["worker_id"]) == ("connected", "AW_c136both")
    write_pid_json(ctl.run, pid=31501)  # another worker holds the lock now
    assert agent_ctl.worker_link() is None


def test_a_read_that_meets_the_writers_move_is_tried_again(ctl, monkeypatch):
    write_pid_json(ctl.run)
    write_link(ctl.run, "connected")
    real_read_text = Path.read_text
    busy = {"left": 1}

    def read_text(self, *args, **kwargs):
        if self.name == livekit_link.LINK_FILE_NAME and busy["left"]:
            busy["left"] -= 1  # Windows: the writer is replacing the file at this moment
            raise PermissionError(32, "The process cannot access the file because it is being used")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    assert agent_ctl.worker_link()["state"] == "connected" and busy["left"] == 0
    (ctl.run / livekit_link.LINK_FILE_NAME).unlink()
    assert agent_ctl.worker_link() is None  # absent: a worker from before C-136


def test_status_line_age_uses_the_wall_clock(ctl):
    link = {"state": "retrying", "since": time.time() - timedelta(minutes=11).total_seconds(),
            "detail": "retrying in 10s", "connected_once": True}
    assert agent_ctl.describe_link(link).startswith("retrying for 0:11:0")
    assert agent_ctl.describe_link({"state": "connecting", "since": "?"}) == \
        "connecting for ? (not registered with LiveKit yet)"
