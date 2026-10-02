"""C-113 (contract K-72 compat layer, K-73 pc.fetch): services/ai_core/remote_compat.py and
services/utils/jarvis_delivery.py.

In remote mode (the VPS brain) os.startfile goes to the laptop as a pc.fetch with open (DESKTOP_OFFLINE
while the laptop is offline; a fresh file once the relay serves it, K-73's 5 s), webbrowser.open
reaches pc.open_url only inside a tool flagged opens_urls, and the location never comes from ipinfo.
The self-test passes on the current tree (a fresh remote-mode interpreter imports agent_runner and runs
discovery) and fails on a rebound symbol. Local mode (the PC brain) installs nothing.

Nothing real opens or leaves the PC: before every test os.startfile and webbrowser.open are sentinels
that fail it, and uninstall() plus monkeypatch put everything back afterwards. The laptop is a fake
sender, files live in tmp_path, and requests.get is a fake that fails on ipinfo.
"""
# pylint: disable=protected-access,redefined-outer-name
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import subprocess
import sys
import textwrap
import threading
import time
import types
import webbrowser
from contextlib import suppress
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import requests

from services.ai_core import jarvis_plugin_manager, remote_compat
from services.ai_core import tool_placement as tp
from services.info import jarvis_get_weather, jarvis_search
from services.multimedia import jarvis_qr_gen
from services.utils import jarvis_delivery as delivery
from services.utils import jarvis_location
from services.utils.jarvis_config import config
from src.core import agent_runner

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ORIGINAL = {name: getattr(jarvis_search, name) for name in remote_compat.LOCATION_NAMES}
PROVIDER = {"get_current_city": jarvis_location.get_current_city,
            "get_current_city_data": jarvis_location.get_current_city_data}
YOUTUBE = "https://www.youtube.com/watch?v=c113c113c11"
SHIMMED_LOCATION = [
    (jarvis_search, "get_current_city"), (jarvis_search, "get_current_city_data"),
    (agent_runner, "get_current_city"), (agent_runner, "get_current_city_data"),
]


class FakeLaptop:
    """A DesktopSender: records every operation it is handed and the thread that handed it over."""

    def __init__(self, online: bool = True, error: BaseException | None = None) -> None:
        self.is_online = online
        self.error = error
        self.sent: list[tuple[str, dict]] = []
        self.threads: list[str] = []

    def online(self) -> bool:
        return self.is_online

    def submit(self, op: str, args: dict) -> None:
        if self.error is not None:
            raise self.error
        self.sent.append((op, dict(args)))
        self.threads.append(threading.current_thread().name)


class MuteLaptop(FakeLaptop):
    """A link that cannot even say whether it is online."""

    def online(self) -> bool:
        raise RuntimeError("link broken")


def _sentinel(name: str):
    def refuse(*_args, **_kwargs):
        raise AssertionError(f"the real {name} was called")
    return refuse


@pytest.fixture
def compat(monkeypatch):
    """remote_compat clean and in remote mode, the real openers replaced by sentinels, no laptop link,
    a mock logger. Everything is put back afterwards."""
    monkeypatch.setenv("JARVIS_DESKTOP_MODE", "remote")
    monkeypatch.setattr(os, "startfile", _sentinel("os.startfile"), raising=False)
    monkeypatch.setattr(webbrowser, "open", _sentinel("webbrowser.open"))
    for module, name in SHIMMED_LOCATION:
        monkeypatch.setattr(module, name, getattr(module, name))
    monkeypatch.setattr(remote_compat, "logger", MagicMock())
    monkeypatch.setattr(delivery, "logger", MagicMock())
    remote_compat.uninstall()
    previous = delivery.set_desktop_sender(None)
    yield remote_compat
    remote_compat.uninstall()
    delivery.set_desktop_sender(previous)


@pytest.fixture
def laptop(compat):
    """An online laptop link (C-119 registers the real one)."""
    fake = FakeLaptop()
    delivery.set_desktop_sender(fake)
    return fake


@pytest.fixture
def areas(monkeypatch, tmp_path):
    """The two media relay areas in tmp_path (/app/Jarvis_Shared and /app/Jarvis_Outputs on the VPS)."""
    shared, outputs = tmp_path / "Jarvis_Shared", tmp_path / "Jarvis_Outputs"
    shared.mkdir()
    outputs.mkdir()
    monkeypatch.setattr(config, "shared_dir", str(shared))
    monkeypatch.setattr(config, "outputs_dir", str(outputs))
    return types.SimpleNamespace(shared=shared, outputs=outputs, root=tmp_path)


def _file(path: Path, data: bytes = b"c113 artifact", *, age: float = 60.0) -> Path:
    """An artifact last modified about age seconds ago, on a whole second: the relay serves files at
    least 5 s old (K-73)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    then = float(int(time.time() - age))
    os.utime(path, (then, then))
    return path


def _wait_for(condition, timeout: float = 10.0) -> bool:
    """Polls condition() in real time, for the delivery thread."""
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.01)
    return True


class FakeClock:
    """jarvis_delivery's time module: sleep() waits until the test lets it go, then moves the clock
    (and calls on_sleep, a writer that keeps changing the file)."""

    def __init__(self, now: float, on_sleep=None) -> None:
        self.now = now
        self.sleeps: list[float] = []
        self.go = threading.Event()
        self.on_sleep = on_sleep

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        assert self.go.wait(10)
        self.now += seconds
        if self.on_sleep is not None:
            self.on_sleep(self)


def _code(info: pytest.ExceptionInfo) -> str:
    assert isinstance(info.value, OSError)  # tools already handle OSError
    return str(info.value)


@pytest.fixture
def tool_call():
    """Runs code as if inside the LLM call of a tool (the plugin manager's context variable)."""
    tokens = []

    def enter(name: str) -> None:
        tokens.append(jarvis_plugin_manager.current_tool_call.set({"id": "c113", "tool": name}))

    yield enter
    for token in reversed(tokens):
        with suppress(ValueError):  # set inside an async test's own context, which ended with it
            jarvis_plugin_manager.current_tool_call.reset(token)


# ---------------------------------------------------------------- jarvis_delivery: relay paths


@pytest.mark.parametrize(("where", "relpath", "area"), [
    ("shared", "QR_Codes/qr.png", "shared"),
    ("shared", "Generated_Images/sunset.png", "shared"),
    ("shared", "Downloads/YouTube/song.mp3", "shared"),
    ("outputs", "Downloads/report.pdf", "outputs"),
    ("outputs", "leads/lahore_leads.xlsx", "outputs"),
    ("outputs", "photos.zip", "outputs"),
])
def test_relay_paths_follow_k73(areas, where, relpath, area):
    path = _file(getattr(areas, where) / relpath)
    assert delivery.relay_path(path) == (area, relpath)
    assert delivery.relay_path(str(path)) == (area, relpath)


@pytest.mark.parametrize(("where", "relpath"), [
    ("shared", "qr.png"),  # top level of shared
    ("shared", "Other/qr.png"),
    ("outputs", "notes.txt"),  # top level of outputs: *.zip only
    ("outputs", "code/app.py"),
    ("shared", "QR_Codes/.hidden.png"),
    ("shared", "Downloads/.cache/song.mp3"),
    ("shared", "Downloads/YouTube/song.mp3.part"),
    ("shared", "Downloads/YouTube/song.ytdl"),
    ("outputs", "Downloads/report.TMP"),
    ("outputs", "leads/sim_lookup_history.jsonl"),
    ("root", "elsewhere/qr.png"),  # outside both areas
])
def test_the_relay_refuses_everything_else(areas, where, relpath):
    path = _file(getattr(areas, where) / relpath)
    with pytest.raises(delivery.DeliveryError) as info:
        delivery.relay_path(path)
    assert _code(info) == "PATH_NOT_ALLOWED"


def test_an_area_root_is_no_file(areas):
    for root in (areas.shared, areas.outputs):
        with pytest.raises(delivery.DeliveryError, match="PATH_NOT_ALLOWED"):
            delivery.relay_path(root)


# ---------------------------------------------------------------- jarvis_delivery: open_artifact


@pytest.mark.parametrize("link", [None, FakeLaptop(online=False), MuteLaptop()], ids=["none", "offline", "mute"])
def test_open_artifact_with_the_laptop_offline_raises(compat, areas, link):
    delivery.set_desktop_sender(link)
    path = _file(areas.shared / "QR_Codes" / "qr.png")
    with pytest.raises(OSError) as info:
        delivery.open_artifact(path, remote=True)
    assert _code(info) == "DESKTOP_OFFLINE"
    assert isinstance(info.value, delivery.DeliveryError) and info.value.code == "DESKTOP_OFFLINE"
    assert delivery.desktop_online() is False
    assert link is None or link.sent == []


def test_open_artifact_sends_one_pc_fetch_with_the_files_sha256(laptop, areas):
    data = b"\x89PNG c113 qr"
    path = _file(areas.shared / "QR_Codes" / "qr.png", data)
    assert delivery.open_artifact(str(path), remote=True) is None
    assert laptop.sent == [("pc.fetch", {"area": "shared", "relpath": "QR_Codes/qr.png",
                                         "sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
                                         "open": True})]
    assert set(laptop.sent[0][1]) == {name for name, _, _ in next(
        op for op in tp.PC_OPERATIONS if op.name == "pc.fetch").params}  # the K-70 operation's fields


def test_open_artifact_refuses_what_the_laptop_cannot_fetch(laptop, areas):
    folder = areas.shared / "Downloads" / "YouTube"
    folder.mkdir(parents=True)
    cases = [(folder, None, "DELIVERY_FAILED"),  # a folder: pc.fetch takes files
             (areas.shared / "QR_Codes" / "gone.png", None, "DELIVERY_FAILED"),
             (_file(areas.root / "elsewhere" / "x.png"), None, "PATH_NOT_ALLOWED"),
             (_file(areas.shared / "QR_Codes" / "qr.png"), "print", "BAD_ARGS"),
             (12345, None, "BAD_ARGS")]
    for path, operation, code in cases:
        with pytest.raises(delivery.DeliveryError) as info:
            delivery.open_artifact(path, operation, remote=True)
        assert _code(info) == code, path
    assert laptop.sent == []


@pytest.mark.parametrize(("error", "code"), [
    (RuntimeError("socket closed"), "DELIVERY_FAILED"),
    (delivery.DeliveryError("DESKTOP_OFFLINE"), "DESKTOP_OFFLINE"),  # the link's own verdict stands
])
def test_a_send_the_link_refuses_is_reported(compat, areas, error, code):
    delivery.set_desktop_sender(FakeLaptop(error=error))
    with pytest.raises(delivery.DeliveryError) as info:
        delivery.open_artifact(_file(areas.outputs / "leads" / "leads.xlsx"), remote=True)
    assert _code(info) == code


@pytest.mark.asyncio
async def test_a_big_file_opened_from_the_event_loop_is_hashed_off_the_loop(laptop, areas, monkeypatch):
    monkeypatch.setattr(delivery, "INLINE_HASH_MAX", 4)
    data = b"a video bigger than the inline limit"
    path = _file(areas.shared / "Downloads" / "YouTube" / "v.mp4", data)
    fetch = ("pc.fetch", {"area": "shared", "relpath": "Downloads/YouTube/v.mp4",
                          "sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "open": True})
    delivery.open_artifact(path, remote=True)
    assert await asyncio.to_thread(_wait_for, lambda: laptop.sent)
    assert laptop.sent == [fetch] and laptop.threads == ["jarvis-delivery"]  # not the loop's thread
    await asyncio.to_thread(delivery.open_artifact, path, remote=True)  # off the loop: hashed in place
    assert laptop.sent == [fetch] * 2
    assert laptop.threads[1] not in ("jarvis-delivery", threading.current_thread().name)


def test_a_fresh_file_is_fetched_once_the_relay_serves_it(laptop, areas, monkeypatch):
    """K-73: the relay skips files modified less than 5 s ago. open_artifact returns at once and the
    pc.fetch follows from a thread once the file is that old."""
    path = _file(areas.shared / "QR_Codes" / "qr.png")
    clock = FakeClock(path.stat().st_mtime + 1.0)  # the file is 1 s old
    monkeypatch.setattr(delivery, "time", clock)
    assert delivery.open_artifact(path, remote=True) is None
    assert _wait_for(lambda: clock.sleeps)
    wait = delivery.RELAY_MIN_AGE_S + delivery._SETTLE_MARGIN_S - 1.0
    assert clock.sleeps == [wait] and laptop.sent == []
    clock.go.set()
    assert _wait_for(lambda: laptop.sent)
    assert [(op, args["relpath"]) for op, args in laptop.sent] == [("pc.fetch", "QR_Codes/qr.png")]
    assert laptop.threads == ["jarvis-delivery"] and clock.sleeps == [wait]


def test_a_fresh_file_whose_laptop_left_meanwhile_is_only_logged(laptop, areas, monkeypatch):
    path = _file(areas.shared / "QR_Codes" / "qr.png")
    clock = FakeClock(path.stat().st_mtime)  # just written
    monkeypatch.setattr(delivery, "time", clock)
    delivery.open_artifact(path, remote=True)  # the laptop was online: the tool reports success
    assert _wait_for(lambda: clock.sleeps)
    laptop.is_online = False
    clock.go.set()
    assert _wait_for(lambda: delivery.logger.warning.called)
    assert "DESKTOP_OFFLINE" in str(delivery.logger.warning.call_args) and laptop.sent == []


@pytest.mark.parametrize("case", ["keeps-changing", "dated-ahead"])
def test_the_wait_for_the_relay_is_bounded(laptop, areas, monkeypatch, case):
    path = _file(areas.shared / "QR_Codes" / "qr.png")
    if case == "keeps-changing":  # a writer touches the file during every wait
        clock = FakeClock(path.stat().st_mtime, on_sleep=lambda fake: os.utime(path, (fake.now, fake.now)))
    else:  # an mtime an hour ahead of the clock: never a sleep of an hour
        clock = FakeClock(path.stat().st_mtime - 3600)
    clock.go.set()
    monkeypatch.setattr(delivery, "time", clock)
    delivery.open_artifact(path, remote=True)
    assert _wait_for(lambda: laptop.sent)
    full = delivery.RELAY_MIN_AGE_S + delivery._SETTLE_MARGIN_S
    assert clock.sleeps == pytest.approx([full] * delivery._SETTLE_ROUNDS) and len(laptop.sent) == 1


def test_without_the_relay_age_rule_a_fresh_file_is_sent_at_once(laptop, areas, monkeypatch):
    monkeypatch.setattr(delivery, "RELAY_MIN_AGE_S", 0)  # a relay that serves pinned fetches at once
    delivery.open_artifact(_file(areas.shared / "QR_Codes" / "qr.png", age=0), remote=True)
    assert len(laptop.sent) == 1 and laptop.threads == [threading.current_thread().name]


def test_local_mode_opens_with_os_startfile_as_before(compat, monkeypatch):
    startfile = MagicMock()
    monkeypatch.setattr(os, "startfile", startfile, raising=False)
    delivery.open_artifact("C:/Users/x/Jarvis_Shared/QR_Codes/qr.png", remote=False)
    delivery.open_artifact("C:/Users/x/report.docx", "print", remote=False)
    assert [call.args for call in startfile.call_args_list] == [
        ("C:/Users/x/Jarvis_Shared/QR_Codes/qr.png",), ("C:/Users/x/report.docx", "print")]
    monkeypatch.delenv("JARVIS_DESKTOP_MODE")  # the default: local
    delivery.open_artifact("C:/Users/x/a.png")
    assert startfile.call_count == 3
    monkeypatch.delattr(os, "startfile")
    with pytest.raises(delivery.DeliveryError, match="DESKTOP_ONLY"):
        delivery.open_artifact("/tmp/a.png", remote=False)


# ---------------------------------------------------------------- jarvis_delivery: URLs


@pytest.mark.parametrize("url", [YOUTUBE, "http://example.com", "HTTPS://Example.com/a?b=c#d"])
def test_http_and_https_urls_pass(url):
    assert delivery.check_url(url) == url


@pytest.mark.parametrize("url", [
    "file:///C:/Windows/System32/calc.exe", "javascript:alert(1)", "ftp://example.com/x", "https://",
    "example.com", " https://example.com", "https://example.com/\nx", "https://exa\u200bmple.com",
    "https://example.com/" + "a" * 4096, "", None, b"https://example.com",
])
def test_every_other_url_is_bad_args(url):
    with pytest.raises(delivery.DeliveryError, match="BAD_ARGS"):
        delivery.check_url(url)


def test_open_url_in_remote_mode_goes_to_the_laptop(compat):
    with pytest.raises(delivery.DeliveryError, match="DESKTOP_OFFLINE"):
        delivery.open_url(YOUTUBE, remote=True)
    fake = FakeLaptop()
    delivery.set_desktop_sender(fake)
    assert delivery.open_url(YOUTUBE, remote=True) is True
    with pytest.raises(delivery.DeliveryError, match="BAD_ARGS"):
        delivery.open_url("file:///etc/passwd", remote=True)
    assert fake.sent == [("pc.open_url", {"url": YOUTUBE})]


def test_open_url_in_local_mode_uses_the_local_browser(compat, monkeypatch):
    browser = MagicMock(return_value=True)
    monkeypatch.setattr(webbrowser, "open", browser)
    assert delivery.open_url(YOUTUBE, remote=False) is True
    browser.return_value = False
    with pytest.raises(delivery.DeliveryError, match="DELIVERY_FAILED"):
        delivery.open_url(YOUTUBE, remote=False)
    assert [call.args for call in browser.call_args_list] == [(YOUTUBE,), (YOUTUBE,)]


# ---------------------------------------------------------------- remote_compat: install


def test_local_mode_installs_nothing(compat, monkeypatch):
    monkeypatch.delenv("JARVIS_DESKTOP_MODE")
    before = {(id(module), name): getattr(module, name) for module, name in SHIMMED_LOCATION}
    startfile, browser = os.startfile, webbrowser.open
    assert remote_compat.install() is False
    assert remote_compat.install(mode="local") is False
    assert os.startfile is startfile and webbrowser.open is browser
    assert {(id(module), name): getattr(module, name) for module, name in SHIMMED_LOCATION} == before
    # the PC brain keeps today's location: agent_runner binds jarvis_search's own function
    assert agent_runner.get_current_city is ORIGINAL["get_current_city"]
    report = remote_compat.self_test()
    assert report.ok and not report.shimmed and not report.missing
    assert remote_compat.health() == {"ok": True, "active": False, "detail": "", "missing": []}


def test_remote_mode_rebinds_every_shimmed_symbol(compat):
    assert compat.install() is True
    assert compat.install() is True  # idempotent
    assert os.startfile is remote_compat.open_file_shim
    assert webbrowser.open is remote_compat.open_url_shim
    for module, name in SHIMMED_LOCATION:
        assert getattr(module, name) is PROVIDER[name], (module.__name__, name)
    report = compat.self_test()
    assert report.ok and report.missing == ()
    shimmed = "; ".join(report.shimmed)
    for symbol in ("os.startfile -> jarvis_delivery.open_artifact",
                   "webbrowser.open -> pc.open_url inside ", "download_youtube_media",
                   "services.info.jarvis_search.get_current_city -> jarvis_location.get_current_city",
                   "src.core.agent_runner.get_current_city_data"):
        assert symbol in shimmed
    assert compat.health() == {"ok": True, "active": True, "detail": "", "missing": []}
    passed = str(compat.logger.info.call_args_list[-1])
    assert "COMPAT self-test passed" in passed and "DESKTOP_OFFLINE" in passed  # no laptop link yet


def test_uninstall_puts_everything_back(compat):
    before = [getattr(module, name) for module, name in SHIMMED_LOCATION]
    startfile, browser = os.startfile, webbrowser.open
    compat.install()
    compat.install()
    compat.uninstall()
    assert [getattr(module, name) for module, name in SHIMMED_LOCATION] == before
    assert os.startfile is startfile and webbrowser.open is browser
    assert compat.is_installed() is False


def test_a_failing_install_step_never_stops_the_import(compat, monkeypatch):
    monkeypatch.setattr(remote_compat, "_install_startfile", MagicMock(side_effect=RuntimeError("boom")))
    assert compat.install() is True
    assert webbrowser.open is remote_compat.open_url_shim
    errors = [call.args for call in compat.logger.error.call_args_list]
    assert errors == [("%s %s (install: %s)", "COMPAT_SHIM_MISSING", "os.startfile", "RuntimeError")]
    assert compat.self_test().missing == ("os.startfile",)


def test_the_by_value_step_also_catches_a_failed_location_step(compat, monkeypatch):
    monkeypatch.setattr(remote_compat, "_install_location", MagicMock(side_effect=RuntimeError("boom")))
    assert compat.install() is True
    for module, name in SHIMMED_LOCATION:  # each held jarvis_search's own function, so the scan rebinds it
        assert getattr(module, name) is PROVIDER[name]
    assert compat.self_test().ok


# ---------------------------------------------------------------- remote_compat: os.startfile


def test_os_startfile_with_the_laptop_offline_raises_desktop_offline(compat, areas):
    compat.install()
    with pytest.raises(OSError) as info:
        os.startfile(str(_file(areas.shared / "Generated_Images" / "sunset.png")))
    assert _code(info) == "DESKTOP_OFFLINE"


def test_os_startfile_with_the_laptop_online_sends_pc_fetch(laptop, areas):
    remote_compat.install()
    path = _file(areas.outputs / "Downloads" / "report.pdf")
    os.startfile(path)
    assert [(op, args["area"], args["relpath"]) for op, args in laptop.sent] == [
        ("pc.fetch", "outputs", "Downloads/report.pdf")]


def test_os_startfile_raises_only_oserror(laptop, areas, monkeypatch):
    remote_compat.install()
    path = str(_file(areas.shared / "QR_Codes" / "qr.png"))
    for kwargs in ({"arguments": "--evil"}, {"cwd": "C:/"}, {"show_cmd": 0}):
        with pytest.raises(OSError, match="BAD_ARGS"):
            os.startfile(path, **kwargs)
    monkeypatch.setattr(delivery, "open_artifact", MagicMock(side_effect=KeyError("bug")))
    with pytest.raises(OSError, match="DELIVERY_FAILED"):
        os.startfile(path)
    assert laptop.sent == []


def test_os_startfile_keeps_the_builtins_signature(laptop, areas):
    names = list(inspect.signature(remote_compat.open_file_shim).parameters)
    assert names == ["filepath", "operation", "arguments", "cwd", "show_cmd"]
    builtin = getattr(sys.modules.get("nt"), "startfile", None)  # os.startfile is a sentinel here
    if builtin is not None:  # Windows: the builtin's own parameters
        parsed = [part.split("=")[0].strip() for part in builtin.__text_signature__.strip("()").split(",")]
        assert [name for name in parsed if name not in ("$module", "/")] == names
    remote_compat.install()
    os.startfile(filepath=str(_file(areas.shared / "QR_Codes" / "qr.png")), operation="open")
    assert [(op, args["relpath"]) for op, args in laptop.sent] == [("pc.fetch", "QR_Codes/qr.png")]


# ---------------------------------------------------------------- remote_compat: webbrowser.open


def test_webbrowser_open_outside_a_tool_returns_false(laptop):
    remote_compat.install()
    assert webbrowser.open(YOUTUBE) is False
    assert webbrowser.open_new_tab(YOUTUBE) is False
    assert laptop.sent == []
    assert "not inside a tool flagged opens_urls" in str(remote_compat.logger.warning.call_args)


@pytest.mark.asyncio
async def test_webbrowser_open_inside_an_opens_urls_tool_reaches_the_laptop(laptop, tool_call):
    remote_compat.install()
    tool_call("download_youtube_media")
    assert webbrowser.open(YOUTUBE) is True
    assert await asyncio.to_thread(webbrowser.open, YOUTUBE) is True  # to_thread copies the tool context
    assert webbrowser.open_new_tab(YOUTUBE) is True
    assert laptop.sent == [("pc.open_url", {"url": YOUTUBE})] * 3


def test_webbrowser_open_refuses_other_tools_schemes_and_an_offline_laptop(laptop, tool_call):
    remote_compat.install()
    tool_call("get_weather")  # a VPS tool without opens_urls
    assert webbrowser.open(YOUTUBE) is False
    tool_call("download_youtube_media")
    assert webbrowser.open("file:///C:/Windows/System32/calc.exe") is False
    assert webbrowser.open(None) is False  # never raises
    laptop.is_online = False
    assert webbrowser.open(YOUTUBE) is False
    assert laptop.sent == []


async def _browse() -> bool:
    return webbrowser.open(YOUTUBE)


@pytest.mark.asyncio
async def test_code_on_the_event_loop_outside_every_tool_gets_false(laptop, monkeypatch, tool_call):
    """K-72: only inside a tool flagged opens_urls, even while one runs. A tool's own tasks inherit its
    call, so loop code without one (a loop, a ui_command handler) is outside every tool."""
    remote_compat.install()
    monkeypatch.setitem(jarvis_plugin_manager._running_calls, "c113-call", "download_youtube_media")
    assert webbrowser.open(YOUTUBE) is False
    assert await asyncio.create_task(_browse()) is False
    results = []
    thread = threading.Thread(target=lambda: results.append(asyncio.run(_browse())))  # its own loop
    thread.start()
    thread.join(10)
    assert results == [False] and laptop.sent == []
    tool_call("download_youtube_media")
    assert await asyncio.create_task(_browse()) is True  # a task of the tool
    assert laptop.sent == [("pc.open_url", {"url": YOUTUBE})]


@pytest.mark.parametrize(("running", "expected"), [
    (("download_youtube_media",), True),
    (("get_weather",), False),
    (("download_youtube_media", "get_weather"), False),  # every running tool must be flagged
    ((), False),
])
def test_a_thread_without_the_tool_context_needs_every_running_tool_flagged(laptop, monkeypatch, running,
                                                                           expected):
    remote_compat.install()
    for index, name in enumerate(running):
        monkeypatch.setitem(jarvis_plugin_manager._running_calls, f"c113-call-{index}", name)
    results = []
    thread = threading.Thread(target=lambda: results.append(webbrowser.open(YOUTUBE)))
    thread.start()
    thread.join(10)
    assert results == [expected]
    assert len(laptop.sent) == (1 if expected else 0)


# ---------------------------------------------------------------- remote_compat: location


def _fake_requests(monkeypatch) -> list[str]:
    """requests.get that fails on ipinfo and answers OpenWeather; records what was asked."""
    asked: list[str] = []

    def fake_get(url, *_args, **kwargs):
        asked.append("ipinfo" if "ipinfo" in url else (kwargs.get("params") or {}).get("q", url))
        if "ipinfo" in url:
            raise AssertionError("ipinfo reached in remote mode")
        response = MagicMock(status_code=200)
        response.json.return_value = {"weather": [{"description": "clear sky"}],
                                      "main": {"temp": 31, "humidity": 40}, "wind": {"speed": 3}}
        return response

    monkeypatch.setattr(requests, "get", fake_get)
    return asked


@pytest.fixture
def location_cache(monkeypatch, tmp_path):
    path = tmp_path / "conversations" / "location.json"
    monkeypatch.setattr(jarvis_location, "location_path", lambda: str(path))
    return path


@pytest.mark.asyncio
async def test_get_weather_without_a_city_in_remote_mode_gives_user_city_and_never_calls_ipinfo(
        compat, monkeypatch, location_cache):
    monkeypatch.setenv("USER_CITY", "Multan")
    monkeypatch.setenv("OPENWEATHER_API_KEY", "c113-fake-key")
    asked = _fake_requests(monkeypatch)
    compat.install()
    result = await jarvis_get_weather.get_weather("")
    assert result["status"] == "success" and result["city"] == "Multan"
    jarvis_location.save_device_location("Islamabad", 33.6844, 73.0479)  # the laptop reported in
    assert (await jarvis_get_weather.get_weather(""))["city"] == "Islamabad"
    assert asked == ["Multan", "Islamabad"]


@pytest.mark.asyncio
async def test_jarvis_search_location_in_remote_mode_never_reaches_ipinfo(compat, monkeypatch, location_cache):
    monkeypatch.delenv("USER_CITY", raising=False)
    asked = _fake_requests(monkeypatch)
    compat.install()
    data = await jarvis_search.get_current_city_data()
    assert (data["city"], data["source"]) == ("Lahore", "user_city")
    assert await jarvis_search.get_current_city() == "Lahore"
    assert await agent_runner.get_current_city() == "Lahore"  # the session-start call (C-112 note)
    assert not asked


@pytest.mark.asyncio
async def test_generate_qr_code_with_the_laptop_offline_reports_an_error(compat, areas):
    compat.install()
    result = await jarvis_qr_gen.generate_qr_code("https://example.com/c113")
    assert result["status"] == "error" and "DESKTOP_OFFLINE" in result["message"]
    assert (areas.shared / "QR_Codes" / "my_stylish_qr.png").is_file()  # made on the VPS, opened nowhere


@pytest.mark.asyncio
async def test_generate_qr_code_with_the_laptop_online_sends_the_code(laptop, areas, monkeypatch):
    monkeypatch.setattr(jarvis_qr_gen, "_lan_qr_url", lambda path: "http://192.0.2.1:8000/qr.png")
    monkeypatch.setattr(jarvis_qr_gen, "_emit_qr_update", AsyncMock())
    monkeypatch.setattr(delivery, "RELAY_MIN_AGE_S", 0.2)  # the QR is fresh: sent once the relay serves it
    remote_compat.install()
    result = await jarvis_qr_gen.generate_qr_code("https://example.com/c113", filename="c113.png")
    assert result["status"] == "success"
    assert await asyncio.to_thread(_wait_for, lambda: laptop.sent)
    data = (areas.shared / "QR_Codes" / "c113.png").read_bytes()
    assert laptop.sent == [("pc.fetch", {"area": "shared", "relpath": "QR_Codes/c113.png",
                                         "sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
                                         "open": True})]


# ---------------------------------------------------------------- remote_compat: self-test


@pytest.mark.parametrize(("module", "name", "symbol"), [
    (agent_runner, "get_current_city_data", "src.core.agent_runner.get_current_city_data"),
    (jarvis_search, "get_current_city", "services.info.jarvis_search.get_current_city"),
])
def test_the_self_test_detects_a_rebound_location_symbol(compat, monkeypatch, module, name, symbol):
    compat.install()
    monkeypatch.setattr(module, name, ORIGINAL[name])  # back to the ipinfo function
    report = compat.self_test()
    assert not report.ok and report.missing == (symbol,)
    assert ("%s %s", "COMPAT_SHIM_MISSING", symbol) in [c.args for c in compat.logger.error.call_args_list]
    health = compat.health()
    assert health["ok"] is False and symbol in health["detail"] and health["missing"] == [symbol]


async def _elsewhere() -> str:
    return "Kuala Lumpur"


@pytest.mark.parametrize("change", ["renamed", "moved"])
@pytest.mark.parametrize("name", remote_compat.LOCATION_NAMES)
def test_the_self_test_fails_when_jarvis_search_lost_a_location_function(compat, monkeypatch, name, change):
    """A renamed or moved ipinfo function (X-64) leaves its consumers unseen by the scan: fail loudly."""
    if change == "renamed":
        monkeypatch.delattr(jarvis_search, name)
    else:  # re-exported from another module
        monkeypatch.setattr(jarvis_search, name, _elsewhere)
    compat.install()
    assert getattr(jarvis_search, name) is PROVIDER[name]
    symbol = f"services.info.jarvis_search.{name}"
    report = compat.self_test()
    assert not report.ok and report.missing == (symbol,)
    assert ("%s %s", "COMPAT_SHIM_MISSING", symbol) in [c.args for c in compat.logger.error.call_args_list]
    assert compat.health()["missing"] == [symbol]


@pytest.mark.parametrize(("module", "name", "symbol"), [
    (os, "startfile", "os.startfile"), (webbrowser, "open", "webbrowser.open"),
])
def test_the_self_test_detects_a_rebound_opener(compat, monkeypatch, module, name, symbol):
    compat.install()
    monkeypatch.setattr(module, name, _sentinel(symbol))
    report = compat.self_test()
    assert not report.ok and report.missing == (symbol,)


def test_the_self_test_finds_an_unknown_by_value_consumer(compat, monkeypatch):
    name = "services.c113_consumer"
    consumer = types.ModuleType(name)
    namespace = vars(consumer)
    namespace["where"] = ORIGINAL["get_current_city_data"]  # 'from ...jarvis_search import ... as where'
    monkeypatch.setitem(sys.modules, name, consumer)
    compat.install()  # install() rebinds it too
    assert namespace["where"] is PROVIDER["get_current_city_data"]
    assert compat.self_test().ok
    namespace["where"] = ORIGINAL["get_current_city_data"]  # imported again after install(), by value
    assert compat.self_test().missing == (f"{name}.where",)


def test_an_opener_bound_by_value_is_rebound_and_checked(compat, monkeypatch):
    name = "services.c113_opener"
    consumer = types.ModuleType(name)
    namespace = vars(consumer)
    startfile, browse = os.startfile, webbrowser.open  # the sentinels stand for the real openers
    namespace.update({"startfile": startfile, "browse": browse})  # 'from webbrowser import open as browse'
    monkeypatch.setitem(sys.modules, name, consumer)
    compat.install()
    assert namespace["startfile"] is remote_compat.open_file_shim
    assert namespace["browse"] is remote_compat.open_url_shim
    assert compat.self_test().ok
    namespace["browse"] = browse
    assert compat.self_test().missing == (f"{name}.browse",)
    compat.uninstall()
    assert namespace["startfile"] is startfile


def test_the_self_test_in_remote_mode_without_install_reports_every_symbol(compat):
    report = compat.self_test()
    assert not report.ok
    assert {"os.startfile", "webbrowser.open", "services.info.jarvis_search.get_current_city",
            "services.info.jarvis_search.get_current_city_data"} <= set(report.missing)
    assert compat.health()["ok"] is False


def test_agent_runner_installs_first_and_self_tests_after_discovery():
    source = (PROJECT_ROOT / "src" / "core" / "agent_runner.py").read_text(encoding="utf-8")
    install = source.index("remote_compat.install()")
    assert install < source.index("from services.ai_core.agent_loops import")
    assert install < source.index("from services.info.jarvis_search import")
    entry = source.index("async def entrypoint(")
    assert source.index("plugin_manager.discover_plugins(", entry) < source.index("remote_compat.self_test()", entry)


PROBE = textwrap.dedent(
    """
    import importlib, json, os, sys
    from src.core import agent_runner  # remote mode: installs the shims at import
    from services.ai_core import remote_compat
    from services.ai_core.jarvis_plugin_manager import plugin_manager
    from services.info import jarvis_search
    from services.utils import jarvis_location
    from services.utils.jarvis_config import config

    installed_at_import = remote_compat.is_installed()
    plugin_manager.discover_plugins(os.path.join(config.project_root, "services"))
    report = remote_compat.self_test()
    weather = sys.modules.get("services.info.jarvis_get_weather")
    result = {
        "installed_at_import": installed_at_import,
        "ok": report.ok, "missing": list(report.missing), "shimmed": list(report.shimmed),
        "health": remote_compat.health(),
        "runner": [agent_runner.get_current_city is jarvis_location.get_current_city,
                   agent_runner.get_current_city_data is jarvis_location.get_current_city_data],
        # X-64: weather calls jarvis_search.get_current_city() at runtime, no by-value binding
        "weather": weather is not None and not hasattr(weather, "get_current_city"),
        "startfile": getattr(os, "startfile", None) is remote_compat.open_file_shim,
    }
    importlib.reload(jarvis_search)  # a binding that changes after install() must fail loudly
    rebound = remote_compat.self_test()
    result["rebound"] = {"ok": rebound.ok, "missing": list(rebound.missing),
                         "health": remote_compat.health()["ok"]}
    print("RESULT " + json.dumps(result))
    """
)


@pytest.fixture(scope="module")
def remote_tree() -> dict:
    """The current tree in a fresh remote-mode interpreter: agent_runner imported, discovery run."""
    env = {name: value for name, value in os.environ.items() if name != "PYTEST_CURRENT_TEST"}
    env.update({
        "JARVIS_DESKTOP_MODE": "remote", "JARVIS_ROLE": "agent", "JARVIS_NO_LOCK": "1",
        "JARVIS_TEST_MODE": "true", "UI_BRIDGE_URL": "http://127.0.0.1:9", tp.OVERRIDES_ENV: "",
        "PYTHONIOENCODING": "utf-8", "PYTHONPATH": str(PROJECT_ROOT),
    })
    result = subprocess.run([sys.executable, "-c", PROBE], cwd=PROJECT_ROOT, env=env, capture_output=True,
                            encoding="utf-8", errors="replace", timeout=600, check=False)
    lines = [line for line in result.stdout.splitlines() if line.startswith("RESULT ")]
    assert result.returncode == 0 and lines, (result.stdout[-2000:], result.stderr[-4000:])
    return json.loads(lines[-1][len("RESULT "):])


def test_the_self_test_passes_on_the_current_tree(remote_tree):
    assert remote_tree["installed_at_import"] is True
    assert remote_tree["ok"] is True, remote_tree["missing"]
    assert remote_tree["missing"] == [] and remote_tree["health"]["ok"] is True
    assert remote_tree["runner"] == [True, True]  # the first get_current_city() of a session too
    assert remote_tree["weather"] is True and remote_tree["startfile"] is True
    assert len(remote_tree["shimmed"]) == 6


def test_the_self_test_fails_when_a_binding_changes_after_install(remote_tree):
    rebound = remote_tree["rebound"]
    assert rebound["ok"] is False and rebound["health"] is False
    assert rebound["missing"] == ["services.info.jarvis_search.get_current_city",
                                  "services.info.jarvis_search.get_current_city_data"]
