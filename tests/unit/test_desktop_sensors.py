"""Desktop Node sensors and pc.* capabilities (plan task C-122 steps 1, 2, 5 and 6; K-19, K-21, K-26, K-70, K-73,
K-74, K-76).

Every reader (foreground window, clipboard monitor, vitals, location), every device (screen, webcam, browser, speaker)
and the bridge are fakes: nothing is captured, opened, spoken or played on this machine and nothing connects to the
live bridge. Blobs are sealed with a test session key and opened again here to check them.
"""
# pylint: disable=redefined-outer-name,protected-access
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import types
from pathlib import Path
from typing import Any

import httpx
import pytest
from PIL import Image

from services.ai_core import tool_placement
from services.utils import desktop_protocol as dp
from src.core.desktop import capabilities, sensors
from src.core.desktop import settings as ns
from src.core.desktop.mirror import NodeHttp
from src.core.desktop.node import DesktopState, MemoryState, NodeKeys, NodeOptions, NodeRuntime

SECRET_TITLE = "Payroll-Q4 Budget.xlsx - Excel"
SECRET_CLIP = "Traceback (most recent call last):\n  File \"secret_module.py\"\nValueError: hunter2-password-value"
SECRET_CITY = "Secretabad"
SECRET_PATH_WORD = "secret-folder-name"


# --- fakes ---------------------------------------------------------------------------------------------------

class FakeLink:
    """Records what the sensors hand to the link: events (sealed to one agent peer) and notices (to the bridge),
    checking every notice with the library exactly as the link would before sending it."""

    def __init__(self, *, connected: bool = True) -> None:
        self.connected = connected
        self.current: dict[str, dict[str, Any]] = {}
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.notices: list[dict[str, Any]] = []
        self.alerts: list[tuple[str, dict[str, Any]]] = []
        self.signer = dp.LinkSigner("pc:laptop-test", dp.generate_signing_key())
        self.peer_key = dp.SessionKey(os.urandom(32), sess="a" * 32, peer_id="job1")
        self.peers = {"agent:job1": types.SimpleNamespace(key=self.peer_key)}

    async def set_current(self, kind: str, body: dict[str, Any]) -> bool:
        self.current[kind] = dict(body)
        return await self.send_event(kind, body)

    async def send_event(self, kind: str, body: dict[str, Any]) -> bool:
        # as link.send_event: the event is built (and sealed) by the library, which refuses a bad kind
        self.signer.build("event", {**body, "kind": kind}, to="agent:job1", sess="a" * 32, room="jarvis-hybrid",
                          session_key=self.peer_key)
        self.events.append((kind, dict(body)))
        return True

    async def send_notice(self, kind: str, **fields: Any) -> bool:
        self.signer.build("notice", {"kind": kind, **fields}, to=dp.BRIDGE_IDENT, sess="a" * 32)
        self.notices.append({"kind": kind, **fields})
        return True

    def raise_alert(self, code: str, **fields: Any) -> None:
        self.alerts.append((code, fields))

    def peer(self, ident: str) -> Any:
        return self.peers.get(ident)


class FakeNode:
    def __init__(self, tmp: Path, *, state: DesktopState | None = None) -> None:
        self.node_id = "laptop-test"
        self.data_root = tmp / "repo"
        self.run_dir = self.data_root / "run"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.outputs = self.data_root / "Jarvis_Outputs"
        self.docs = tmp / "home" / "Documents"
        self.outputs.mkdir(parents=True, exist_ok=True)
        self.docs.mkdir(parents=True, exist_ok=True)
        self.config = types.SimpleNamespace(outputs_dir=str(self.outputs), shared_dir=str(tmp / "home" / "Shared"),
                                            open_roots=[str(self.docs), str(self.outputs)])
        self.link = FakeLink()
        self.state = MemoryState("full")
        if state is not None:
            self.state.set(state)
        self.audit_lines: list[tuple[str, dict[str, Any]]] = []
        self.audit = types.SimpleNamespace(record=lambda event, **fields: self.audit_lines.append((event, fields)))
        self.bytes_noted: list[tuple[str, int]] = []
        self.byte_cap: str | None = None

        def note_bytes(bucket: str, size: int, **_kw: Any) -> str | None:
            self.bytes_noted.append((bucket, size))
            return self.byte_cap

        self.policy = types.SimpleNamespace(note_bytes=note_bytes)
        self.ctx: Any = types.SimpleNamespace(call_id="call-0001", peer="agent:job1", origin="llm",
                                              room="jarvis-hybrid", cls="capture", risk="low", approval=None)

    def call_context(self, call_id: str | None = None) -> Any:
        return self.ctx


class FakeBlobInbox:
    """POST /vortex/desktop/blob as the bridge: checks the PC signature and stores the sealed body."""

    def __init__(self, key: Any) -> None:
        self.key = key
        self.blobs: dict[str, bytes] = {}

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = request.read()
        dp.verify_http_headers(request.headers.raw, method=request.method, path=request.url.raw_path,
                               query=request.url.query, public_key=self.key.public_key(), seen=None,
                               content_length=len(body))
        assert request.url.path == "/vortex/desktop/blob"
        blob_id = os.urandom(16).hex()
        self.blobs[blob_id] = body
        return httpx.Response(200, json={"blob_id": blob_id, "size": len(body)})


@pytest.fixture
def node(tmp_path: Path) -> FakeNode:
    return FakeNode(tmp_path)


def make_caps(node: FakeNode, **kwargs: Any) -> tuple[capabilities.Capabilities, FakeBlobInbox]:
    key = dp.generate_signing_key()
    inbox = FakeBlobInbox(key)
    http = NodeHttp("https://bridge.test", key, transport=httpx.MockTransport(inbox.handle))
    return capabilities.Capabilities(node, http=lambda: http, **kwargs), inbox


def open_blob(node: FakeNode, inbox: FakeBlobInbox, entry: dict[str, Any]) -> bytes:
    """What the agent does with a result's blobs entry: fetch by blob_id, decrypt with seal_id, check sha256."""
    return dp.decrypt_blob(node.link.peer_key, entry["seal_id"], inbox.blobs[entry["blob_id"]], size=entry["size"],
                           sha256=entry["sha256"])


def run(coro: Any) -> Any:
    return asyncio.run(coro)


# --- field allow-lists (BAD_ARGS) ------------------------------------------------------------------------------

VITALS = {"cpu": 12.5, "ram": 40.0, "disk": 70.1, "disk_label": "Disk C", "host": "laptop-test", "cores": 8,
          "ram_total_gb": 15.8}


def test_vitals_notice_has_exactly_the_k74_fields() -> None:
    assert sensors.VITALS_FIELDS == ("cpu", "ram", "disk", "disk_label", "host", "cores", "ram_total_gb")
    assert sensors.vitals_notice(**VITALS) == VITALS
    assert sensors.vitals_notice(cpu=1, ram=2, disk=3) == {"cpu": 1, "ram": 2, "disk": 3}


@pytest.mark.parametrize("change", [
    {"battery": 80}, {"plugged": True}, {"secs_left": 100}, {"title": "x"}, {"kind": "vitals_pc"},
    {"cpu": None}, {"cpu": 101}, {"cpu": True}, {"cpu": float("nan")}, {"cores": 0}, {"cores": 2.0},
    {"host": "C:\\Users\\me"}, {"host": "a/b"}, {"host": SECRET_TITLE}, {"host": ""},
    {"disk_label": "C:\\"}, {"disk_label": "C:"}, {"disk_label": "/home/me"}, {"disk_label": "x" * 33},
    {"disk_label": None},
])
def test_vitals_notice_refuses_anything_else(change: dict[str, Any]) -> None:
    with pytest.raises(dp.DesktopLinkError) as info:
        sensors.vitals_notice(**{**VITALS, **change})
    assert info.value.code == "BAD_ARGS"


def test_vitals_notice_refuses_a_missing_required_field() -> None:
    fields = dict(VITALS)
    fields.pop("disk")
    with pytest.raises(dp.DesktopLinkError) as info:
        sensors.vitals_notice(**fields)
    assert info.value.code == "BAD_ARGS"


@pytest.mark.parametrize(("kind", "fields"), [
    ("location", {"city": "Lahore", "lat": 31.5, "lng": 74.3, "ts": 1.0e9, "extra": 1}),
    ("location", {"city": "Lahore", "lat": 91.0, "lng": 74.3, "ts": 1.0e9}),
    ("location", {"city": "Lahore", "lat": 31.5, "ts": 1.0e9}),
    ("location", {"city": "Lahore", "lat": 31.5, "lng": 74.3, "ts": 1.0e9, "source": "ipinfo"}),
    ("location", {"city": "", "lat": 31.5, "lng": 74.3, "ts": 1.0e9}),
    ("window_context", {"title": None, "ts": 1.0e9}),
    ("window_context", {"title": "a\u200bb", "ts": 1.0e9}),
    ("window_context", {"title": "x" * 201, "ts": 1.0e9}),
    ("window_context", {"title": "ok", "ts": 1.0e9, "process": "excel.exe"}),
    ("window_context", {"title": "ok"}),
    ("clipboard_suggestion", {"id": "1", "error_excerpt": "e" * 201, "query": "q", "solution": "", "ts": 1.0e9}),
    ("clipboard_suggestion", {"id": "1", "error_excerpt": "e", "query": "q", "solution": "", "ts": 1.0e9,
                              "full_text": SECRET_CLIP}),
    ("clipboard_suggestion", {"id": "1", "error_excerpt": "e", "query": "q", "solution": "", "ts": 1.0e9,
                              "source_url": None}),
    ("clipboard_suggestion", {"id": "1", "error_excerpt": "e", "query": "q", "solution": "", "ts": 1.0e9,
                              "source_url": "javascript:alert(1)"}),
    ("clipboard_suggestion", {"id": "bad id!", "error_excerpt": "e", "query": "q", "solution": "", "ts": 1.0e9}),
    ("log_batch", {"items": []}),
    ("vitals_pc", dict(VITALS)),
])
def test_event_fields_outside_the_allow_list_are_refused(kind: str, fields: dict[str, Any]) -> None:
    with pytest.raises(dp.DesktopLinkError) as info:
        sensors.build_event(kind, **fields)
    assert info.value.code == "BAD_ARGS"


def test_event_bodies_that_pass_are_accepted_by_the_library() -> None:
    link = FakeLink()
    for kind, fields in (("location", {"city": "Lahore", "lat": 31.5, "lng": 74.3, "ts": 1.7e9, "source": "device"}),
                         ("window_context", {"title": "Notepad", "ts": 1.7e9}), ("window_context", {"ts": 1.7e9}),
                         ("clipboard_suggestion", {"id": "abc-1", "error_excerpt": "line1\nline2", "query": "q",
                                                   "solution": "", "source_url": "https://example.com/a", "ts": 1.7e9})):
        run(link.send_event(kind, sensors.build_event(kind, **fields)))
    assert [kind for kind, _ in link.events] == ["location", "window_context", "window_context",
                                                 "clipboard_suggestion"]


# --- vitals ----------------------------------------------------------------------------------------------------

def test_vitals_send_only_the_allowed_fields(node: FakeNode) -> None:
    raw = {"cpu": 33.33, "ram": 51.0, "disk": 120.0, "battery": 77, "plugged": True, "secs_left": 999, "cores": 12,
           "ram_total_gb": 31.7, "title": SECRET_TITLE}
    sens = sensors.Sensors(node, vitals=lambda: raw)
    assert run(sens.vitals_once()) is True
    notice = node.link.notices[-1]
    assert set(notice) <= {"kind", *sensors.VITALS_FIELDS}
    assert notice["kind"] == "vitals_pc" and notice["cpu"] == 33.3 and notice["disk"] == 100.0
    assert notice["host"] == "laptop-test" and notice["disk_label"].startswith("Disk ")
    assert "battery" not in notice and SECRET_TITLE not in json.dumps(notice)


def test_vitals_wait_for_the_link(node: FakeNode) -> None:
    node.link.connected = False
    sens = sensors.Sensors(node, vitals=lambda: {"cpu": 1, "ram": 1, "disk": 1})
    assert run(sens.vitals_once()) is False and node.link.notices == []


# --- active window ---------------------------------------------------------------------------------------------

def test_window_context_is_pushed_only_on_change(node: FakeNode) -> None:
    titles = iter([("Notepad", "notepad.exe"), ("Notepad", "notepad.exe"), ("Chrome - News", "chrome.exe"),
                   ("", "")])
    sens = sensors.Sensors(node, foreground=lambda: next(titles), wall=lambda: 1_760_000_000.0)
    assert run(sens.window_once()) is True
    assert run(sens.window_once()) is False  # unchanged: nothing sent
    assert run(sens.window_once()) is True
    assert run(sens.window_once()) is True   # no window: an event without a title clears the agent's cache
    assert [body.get("title") for _kind, body in node.link.events] == ["Notepad", "Chrome - News", None]
    assert node.link.current["window_context"] == {"ts": 1_760_000_000.0}


def test_window_titles_are_masked_and_private_windows_hidden(node: FakeNode) -> None:
    home = str(Path.home())
    titles = iter([("Database.kdbx - KeePassXC", "keepassxc.exe"), ("Vault", "bitwarden.exe"),
                   (f"{home}\\{SECRET_PATH_WORD}\\notes.txt - Notepad", "notepad.exe"),
                   ("Mail to someone@example.com 03001234567", "outlook.exe"), ("id_rsa - Notepad", "notepad.exe")])
    sens = sensors.Sensors(node, foreground=lambda: next(titles))
    sent = []
    for _ in range(5):
        sens._last_title = None
        run(sens.window_once())
        sent.append(node.link.events[-1][1]["title"])
    assert sent[0] == sensors.PRIVATE_TITLE and sent[1] == sensors.PRIVATE_TITLE
    assert SECRET_PATH_WORD not in sent[2] and "[user_path]" in sent[2]
    assert "someone@example.com" not in sent[3] and "03001234567" not in sent[3]
    assert sent[4] == sensors.PRIVATE_TITLE


@pytest.mark.parametrize("state", [DesktopState(mode="full", locked=True), DesktopState(mode="off"),
                                   DesktopState(mode="read", panic=True)])
def test_window_and_clipboard_pause_while_locked_off_or_panic(tmp_path: Path, state: DesktopState) -> None:
    node = FakeNode(tmp_path, state=state)
    sens = sensors.Sensors(node, foreground=lambda: (SECRET_TITLE, "excel.exe"))
    assert run(sens.window_once()) is False
    run(sens.on_clipboard({"id": "x1", "error_excerpt": SECRET_CLIP, "query": "q", "solution": "s", "ts": 1.0}))
    assert node.link.events == []


# --- clipboard -------------------------------------------------------------------------------------------------

def test_clipboard_sends_only_excerpt_query_and_solution(node: FakeNode) -> None:
    sens = sensors.Sensors(node, wall=lambda: 1_760_000_000.0)
    payload = {"id": "5f0c-uuid", "error_excerpt": SECRET_CLIP * 10, "query": "ValueError solution",
               "solution": "Use the right type." * 50, "source_url": "https://docs.python.org/3/", "ts": 1.0,
               "full_text": SECRET_CLIP, "clipboard": SECRET_CLIP}
    run(sens.on_clipboard(payload))
    kind, body = node.link.events[-1]
    assert kind == "clipboard_suggestion"
    assert set(body) == {"id", "error_excerpt", "query", "solution", "source_url", "ts"}
    assert len(body["error_excerpt"]) <= 200 and len(body["solution"]) <= 500


class FakeMonitor:
    def __init__(self, payloads: list[Any]) -> None:
        self.payloads = payloads
        self.stopped = False

    async def start(self, callback: Any) -> None:
        for payload in self.payloads:
            await callback(payload)

    def stop(self) -> None:
        self.stopped = True


def test_clipboard_loop_runs_the_k19_monitor(node: FakeNode) -> None:
    monitor = FakeMonitor([{"id": "a1", "error_excerpt": "KeyError: 'x'", "query": "KeyError solution",
                            "solution": "check the key", "source_url": None, "ts": 1.0}])
    sens = sensors.Sensors(node, clipboard_monitor=lambda: monitor)
    run(sens.clipboard_loop())
    assert monitor.stopped and [kind for kind, _ in node.link.events] == ["clipboard_suggestion"]
    assert "source_url" not in node.link.events[0][1]


class LoopingMonitor:
    """As the K-19 ClipboardMonitor: reads the clipboard every tick while is_running holds."""

    def __init__(self, reads: list[str]) -> None:
        self.reads = reads
        self.is_running = False
        self.last_content = ""
        self.started_with: str | None = None
        self.stopped = False

    async def start(self, callback: Any) -> None:
        self.started_with = self.last_content
        self.is_running = True
        while self.is_running:
            self.reads.append("read")
            await asyncio.sleep(0.005)

    def stop(self) -> None:
        self.stopped = True


def test_the_clipboard_is_not_read_during_a_pause_and_is_primed_after_it(node: FakeNode) -> None:
    reads: list[str] = []
    monitors: list[LoopingMonitor] = []

    def make() -> LoopingMonitor:
        monitors.append(LoopingMonitor(reads))
        return monitors[-1]

    sens = sensors.Sensors(node, clipboard_monitor=make, clipboard_text=lambda: SECRET_CLIP, window_poll_s=0.01)

    async def until(check: Any) -> None:
        for _ in range(400):
            if check():
                return
            await asyncio.sleep(0.005)
        raise AssertionError("condition not reached")

    async def scenario() -> None:
        task = asyncio.ensure_future(sens.clipboard_loop())
        await until(lambda: len(reads) >= 3)
        assert len(monitors) == 1
        node.state.set(DesktopState(mode="full", locked=True))  # Windows locked: the monitor stops
        await until(lambda: monitors[0].stopped)
        assert monitors[0].is_running is False
        await asyncio.sleep(0.02)  # a read already in its tick may finish
        paused_reads = len(reads)
        await asyncio.sleep(0.08)
        assert len(reads) == paused_reads and len(monitors) == 1  # no clipboard read, no search, while paused
        node.state.set(DesktopState(mode="full"))
        await until(lambda: len(monitors) == 2 and len(reads) > paused_reads)
        # what was copied during the pause counts as seen: it is never searched or sent
        assert monitors[1].started_with == SECRET_CLIP
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert monitors[1].stopped

    run(scenario())
    assert node.link.events == []


def test_a_pause_clears_the_current_window_title(node: FakeNode) -> None:
    sens = sensors.Sensors(node, foreground=lambda: ("Report.docx - Word", "winword.exe"), wall=lambda: 1_760_000_000.0)
    assert run(sens.window_once()) is True
    assert node.link.current["window_context"]["title"] == "Report.docx - Word"
    node.state.set(DesktopState(mode="full", locked=True))
    assert run(sens.window_once()) is False
    # the link's current window_context (re-sent after an owner-room agent_hello) holds no title any more,
    # and the agent got a title-less event that clears its cache
    assert node.link.current["window_context"] == {"ts": 1_760_000_000.0}
    assert node.link.events[-1] == ("window_context", {"ts": 1_760_000_000.0})
    events = len(node.link.events)
    assert run(sens.window_once()) is False and len(node.link.events) == events  # cleared once per pause
    node.state.set(DesktopState(mode="full"))
    assert run(sens.window_once()) is True
    assert node.link.current["window_context"]["title"] == "Report.docx - Word"


def test_clipboard_sensor_off_when_the_monitor_cannot_load(node: FakeNode) -> None:
    def broken() -> Any:
        raise ImportError("pyperclip")

    run(sensors.Sensors(node, clipboard_monitor=broken).clipboard_loop())
    assert node.link.events == []


# --- location --------------------------------------------------------------------------------------------------

def test_location_is_an_event_for_the_svc_peer(node: FakeNode) -> None:
    sens = sensors.Sensors(node, locate=lambda: {"city": SECRET_CITY, "lat": 31.52, "lng": 74.35},
                           wall=lambda: 1_760_000_000.0)
    assert run(sens.location_once()) is True
    assert node.link.current["location"] == {"city": SECRET_CITY, "lat": 31.52, "lng": 74.35,
                                             "ts": 1_760_000_000.0, "source": "device"}
    assert node.link.notices == []  # never a notice
    assert run(sensors.Sensors(node, locate=lambda: None).location_once()) is False


def test_location_with_bad_coordinates_is_refused(node: FakeNode) -> None:
    sens = sensors.Sensors(node, locate=lambda: {"city": "X", "lat": 200.0, "lng": 0.0})
    with pytest.raises(dp.DesktopLinkError) as info:
        run(sens.location_once())
    assert info.value.code == "BAD_ARGS" and "location" not in node.link.current


# --- no title, clipboard text or location in alerts, audit or logs (step 6, K-74 node duty) -------------------

def test_no_private_text_reaches_alerts_audit_or_logs(node: FakeNode, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    sens = sensors.Sensors(node, foreground=lambda: (SECRET_TITLE, "excel.exe"),
                           locate=lambda: {"city": SECRET_CITY, "lat": 1.0, "lng": 2.0},
                           vitals=lambda: {"cpu": 1, "ram": 2, "disk": 3, "title": SECRET_TITLE},
                           clipboard_monitor=lambda: FakeMonitor([{"id": "c1", "error_excerpt": SECRET_CLIP,
                                                                   "query": "ValueError hunter2-password-value",
                                                                   "solution": "x", "ts": 1.0}, "not a mapping"]))

    async def all_sensors() -> None:
        await sens.window_once()
        await sens.location_once()
        await sens.vitals_once()
        await sens.clipboard_loop()
        try:  # a refused event is logged by its reason only
            sens.clipboard_event({"id": "bad id!", "error_excerpt": SECRET_CLIP, "query": "q", "solution": ""})
        except dp.DesktopLinkError:
            pass

    run(all_sensors())
    assert {kind for kind, _ in node.link.events} == {"window_context", "location", "clipboard_suggestion"}
    outside = json.dumps([node.link.notices, node.link.alerts, node.audit_lines], default=str)
    outside += "\n".join(record.getMessage() + str(record.args) for record in caplog.records)
    for secret in (SECRET_TITLE, "Payroll", SECRET_CITY, "hunter2", "secret_module", "Traceback"):
        assert secret not in outside, secret


def test_capability_logs_and_audit_hold_no_paths_or_urls(node: FakeNode, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    secret_file = node.docs / f"{SECRET_PATH_WORD}.txt"
    secret_file.write_bytes(b"private notes")
    node.ctx = types.SimpleNamespace(call_id="call-0002", peer="agent:job1", origin="llm", room="jarvis-hybrid",
                                     cls="exposure", risk="medium",
                                     approval={"confirm_id": "c" * 32, "channel": "ui", "risk": "medium"})
    caps, _inbox = make_caps(node, open_browser=lambda url: True, speak=lambda text: True, notice=lambda: None)
    assert run(caps.read_file({"path": str(secret_file)}))["status"] == "success"
    assert run(caps.open_url({"url": f"https://example.com/{SECRET_PATH_WORD}"}))["status"] == "success"
    assert run(caps.speak_local({"text": f"Reminder {SECRET_PATH_WORD}"}))["status"] == "success"
    run(caps.read_file({"path": str(secret_file), "extra": SECRET_PATH_WORD}))
    outside = json.dumps(node.audit_lines, default=str)
    outside += "\n".join(record.getMessage() + str(record.args) for record in caplog.records)
    assert SECRET_PATH_WORD not in outside and "private notes" not in outside


# --- pc.capture_screen and pc.capture_webcam --------------------------------------------------------------------

def test_capture_screen_is_a_1600px_jpeg_blob_sealed_to_the_agent(node: FakeNode) -> None:
    shot = Image.new("RGB", (3200, 1800), (10, 120, 200))
    caps, inbox = make_caps(node, grab_screen=lambda: shot, foreground=lambda: ("Notepad", "notepad.exe"))
    result = run(caps.capture_screen({}))
    assert result["status"] == "success", result
    (entry,) = result["blobs"]
    assert set(entry) == {"blob_id", "seal_id", "sha256", "size", "mime"} and entry["mime"] == "image/jpeg"
    assert entry["blob_id"] in inbox.blobs and entry["seal_id"] != entry["blob_id"]
    plain = open_blob(node, inbox, entry)
    image = Image.open(io.BytesIO(plain))
    assert image.format == "JPEG" and max(image.size) == 1600 and image.size == (1600, 900)
    assert result["data"]["width"] == 1600 and result["data"]["height"] == 900
    assert node.bytes_noted == [("uploads", len(inbox.blobs[entry["blob_id"]]))]
    # the bridge holds only ciphertext
    assert plain not in inbox.blobs[entry["blob_id"]]


def test_a_small_screen_is_never_scaled_up() -> None:
    data, width, height = capabilities.encode_jpeg(Image.new("RGBA", (800, 600)))
    assert (width, height) == (800, 600) and data[:2] == b"\xff\xd8"


def test_capture_refused_on_a_sensitive_foreground_at_run_time(node: FakeNode) -> None:
    grabbed: list[int] = []
    caps, inbox = make_caps(node, grab_screen=lambda: grabbed.append(1),
                            foreground=lambda: ("C:\\Users\\me\\.ssh\\config - Notepad", "notepad.exe"))
    result = run(caps.capture_screen({}))
    assert result["error_code"] == "SENSITIVE_SCREEN" and grabbed == [] and inbox.blobs == {}
    caps, inbox = make_caps(node, grab_screen=lambda: grabbed.append(1), foreground=lambda: ("Vault", "keepass.exe"))
    assert run(caps.capture_webcam({}))["error_code"] == "SENSITIVE_SCREEN" and grabbed == []


def test_capture_webcam_frame(node: FakeNode) -> None:
    caps, inbox = make_caps(node, grab_webcam=lambda: Image.new("RGB", (640, 480)), foreground=lambda: ("", ""))
    result = run(caps.capture_webcam({}))
    assert result["status"] == "success"
    assert Image.open(io.BytesIO(open_blob(node, inbox, result["blobs"][0]))).size == (640, 480)


def test_capture_upload_cap_and_lost_session(node: FakeNode) -> None:
    caps, inbox = make_caps(node, grab_screen=lambda: Image.new("RGB", (10, 10)), foreground=lambda: ("", ""))
    node.byte_cap = "DESKTOP_RATE_LIMITED"
    assert run(caps.capture_screen({}))["error_code"] == "DESKTOP_RATE_LIMITED" and inbox.blobs == {}
    node.byte_cap = None
    node.link.peers.clear()  # the agent peer's session is gone: nothing can be sealed for it
    assert run(caps.capture_screen({}))["error_code"] == "UPLOAD_FAILED"


def test_capture_refuses_arguments(node: FakeNode) -> None:
    caps, _inbox = make_caps(node, grab_screen=lambda: Image.new("RGB", (10, 10)), foreground=lambda: ("", ""))
    assert run(caps.capture_screen({"monitor": 2}))["error_code"] == "BAD_ARGS"


# --- pc.read_file ----------------------------------------------------------------------------------------------

def approved(node: FakeNode) -> None:
    node.ctx = types.SimpleNamespace(call_id="call-0003", peer="agent:job1", origin="llm", room="jarvis-hybrid",
                                     cls="exposure", risk="medium",
                                     approval={"confirm_id": "f" * 32, "channel": "ui", "risk": "medium"})


def test_read_file_uploads_with_the_approval_reference(node: FakeNode) -> None:
    approved(node)
    report = node.docs / "report.pdf"
    report.write_bytes(b"%PDF-1.7 content")
    caps, inbox = make_caps(node)
    result = run(caps.read_file({"path": str(report)}))
    assert result["status"] == "success", result
    assert open_blob(node, inbox, result["blobs"][0]) == b"%PDF-1.7 content"
    assert result["data"]["confirm_id"] == "f" * 32 and result["blobs"][0]["mime"] == "application/pdf"
    assert node.audit_lines == [("read_file_exposed", {"call_id": "call-0003", "tool": "pc.read_file",
                                                       "confirm_id": "f" * 32, "approval_channel": "ui",
                                                       "count": len(b"%PDF-1.7 content")})]


def test_read_file_without_an_approval_reference_is_refused(node: FakeNode) -> None:
    (node.docs / "a.txt").write_text("x", encoding="utf-8")
    caps, inbox = make_caps(node)
    assert run(caps.read_file({"path": str(node.docs / "a.txt")}))["error_code"] == "APPROVAL_REQUIRED"
    node.ctx = None
    assert run(caps.read_file({"path": str(node.docs / "a.txt")}))["error_code"] == "APPROVAL_REQUIRED"
    assert inbox.blobs == {}


@pytest.mark.parametrize("where", ["outside", "repo", "ssh", "env", "pth", "traversal", "folder"])
def test_read_file_path_policy_at_run_time(node: FakeNode, tmp_path: Path, where: str) -> None:
    approved(node)
    target = {"outside": tmp_path / "elsewhere" / "a.txt", "repo": node.data_root / "secret.txt",
              "ssh": node.docs / ".ssh" / "id_ed25519", "env": node.docs / "prod.env", "pth": node.docs / "x.pth",
              "traversal": node.docs / ".." / ".." / "repo" / "secret.txt", "folder": node.docs / "sub"}[where]
    if where == "folder":
        target.mkdir(parents=True)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"k")
    caps, inbox = make_caps(node)
    assert run(caps.read_file({"path": str(target)}))["error_code"] == "PATH_NOT_ALLOWED"
    assert inbox.blobs == {}


def test_read_file_over_25_mb_is_refused(node: FakeNode, monkeypatch: pytest.MonkeyPatch) -> None:
    approved(node)
    monkeypatch.setattr(capabilities, "READ_FILE_MAX_BYTES", 10)
    (node.docs / "big.bin").write_bytes(b"x" * 11)
    caps, inbox = make_caps(node)
    assert run(caps.read_file({"path": str(node.docs / "big.bin")}))["error_code"] == "FILE_TOO_LARGE"
    assert inbox.blobs == {}


# --- pc.open_url, pc.speak_local, pc.play_sound ---------------------------------------------------------------

@pytest.mark.parametrize("url", ["https://example.com/a?b=c", "http://localhost:8080/x", "HTTPS://Example.com"])
def test_open_url_http_and_https(node: FakeNode, url: str) -> None:
    opened: list[str] = []
    caps, _ = make_caps(node, open_browser=lambda value: opened.append(value) or True)
    assert run(caps.open_url({"url": url}))["status"] == "success" and opened == [url]


@pytest.mark.parametrize("url", ["file:///C:/Windows/system32/calc.exe", "javascript:alert(1)", "ftp://x.com/a",
                                 "ms-settings:privacy", "https://user:pw@example.com/", "https://", "https://a b.com",
                                 "https://example.com/\u202e", "C:\\Windows\\notepad.exe", "", "x" * 3000,
                                 "https://example.com:99999/"])
def test_open_url_refuses_everything_else(node: FakeNode, url: str) -> None:
    opened: list[str] = []
    caps, _ = make_caps(node, open_browser=lambda value: opened.append(value) or True)
    assert run(caps.open_url({"url": url}))["error_code"] == "BAD_ARGS" and opened == []


def test_speak_local(node: FakeNode) -> None:
    spoken: list[str] = []
    notices: list[int] = []
    caps, _ = make_caps(node, speak=lambda text: spoken.append(text) or True, notice=lambda: notices.append(1))
    assert run(caps.speak_local({"text": "Sir,\n  meeting   ka waqt"}))["status"] == "success"
    assert spoken == ["Sir, meeting ka waqt"] and notices == [1]
    for bad in ({"text": ""}, {"text": "x" * 1001}, {"text": 5}, {}):
        assert run(caps.speak_local(bad))["error_code"] == "BAD_ARGS"
    caps, _ = make_caps(node, speak=lambda text: False, notice=lambda: None)
    assert run(caps.speak_local({"text": "hi"}))["error_code"] == "SPEAK_FAILED"


def test_play_sound_names(node: FakeNode, tmp_path: Path) -> None:
    played: list[Path] = []
    beeps: list[int] = []
    caps, _ = make_caps(node, play_file=played.append, beep=beeps.append)
    result = run(caps.play_sound({"name": "chime"}))
    assert result["status"] == "success" and played and played[0].name == "jarvis_startup_chime.wav"
    assert run(caps.play_sound({"name": "startup"}))["status"] == "success"
    assert run(caps.play_sound({"name": "../../evil"}))["error_code"] == "BAD_ARGS"
    assert run(caps.play_sound({"name": "chime", "path": "x.wav"}))["error_code"] == "BAD_ARGS"
    if os.name == "nt":
        assert run(caps.play_sound({"name": "alert"}))["status"] == "success" and beeps


def test_startup_sound_plays_once_per_supervisor(node: FakeNode) -> None:
    caps, _ = make_caps(node)
    assert caps.startup_sound_due(parent_pid=111, boot_time=1000.0) is True
    assert caps.startup_sound_due(parent_pid=111, boot_time=1000.0) is False  # the node child restarted
    assert caps.startup_sound_due(parent_pid=222, boot_time=1000.0) is True   # a new supervisor
    assert caps.startup_sound_due(parent_pid=222, boot_time=2000.0) is True   # a reboot


def test_operation_schemas_match_the_k71_table() -> None:
    assert set(capabilities.OPERATION_SCHEMAS) == {op.name for op in tool_placement.PC_OPERATIONS}
    assert capabilities.check_args("pc.ping", {}) is None
    assert capabilities.check_args("pc.ping", {"x": 1}) == "unknown_field"
    assert capabilities.check_args("pc.nope", {}) == "unknown_operation"


# --- plugin wiring into the C-120/C-121 runtime -----------------------------------------------------------------

class _NoTransport:
    connected = False

    def set_receiver(self, receive: Any) -> None:  # pragma: no cover - never connected in these tests
        pass


def make_runtime(tmp_path: Path, run_mode: str = "active") -> NodeRuntime:
    data_root = tmp_path / "repo"
    (data_root / "run").mkdir(parents=True, exist_ok=True)
    (data_root / ".env").write_text("", encoding="utf-8")
    return NodeRuntime(node_id="laptop-test", keys=NodeKeys(dp.generate_signing_key(),
                                                            dp.generate_signing_key().public_key()),
                       data_root=data_root, run_dir=data_root / "run",
                       settings_source=ns.SettingsSource(data_root / ".env", data_root),
                       transport_factory=_NoTransport, tool_provider=types.SimpleNamespace(describe=None),
                       options=NodeOptions(run_mode=run_mode),
                       config=types.SimpleNamespace(outputs_dir=str(data_root / "Jarvis_Outputs"),
                                                    shared_dir=str(tmp_path / "Shared"), open_roots=[]))


@pytest.mark.parametrize("run_mode", ["active", "canary"])
def test_plugins_install_into_the_node(tmp_path: Path, run_mode: str) -> None:
    runtime = make_runtime(tmp_path, run_mode)
    try:
        status = runtime.install_plugins(("capabilities", "sensors", "mirror", "backup_pull"))
        assert status == {"capabilities": "installed", "sensors": "installed", "mirror": "installed",
                          "backup_pull": "installed"}
        assert runtime.operations.names() == sorted(op.name for op in tool_placement.PC_OPERATIONS)
        spec = run(runtime.operations.describe("pc.fetch"))
        assert spec.cls == tool_placement.READ and spec.param_schema_sha256 == \
            tool_placement.PC_OPERATIONS[4].manifest_entry()["param_schema_sha256"]
        tasks = {name: active_only for _factory, active_only, name in runtime._extra_tasks}
        assert set(tasks) == {"startup-sound", "sensor-window", "sensor-vitals", "sensor-location",
                              "sensor-clipboard", "mirror", "backup-pull"}
        assert all(tasks.values())  # sensors, mirror, backup pull and the startup sound: active mode only
        assert runtime.media_mirror is not None and runtime.capabilities is not None
    finally:
        runtime.close()


def test_call_context_is_only_for_calls_on_the_executor(tmp_path: Path) -> None:
    runtime = make_runtime(tmp_path)
    try:
        assert runtime.call_context("call-unknown") is None
        assert runtime.call_context() is None  # no call runs in this thread
    finally:
        runtime.close()


def test_a_25_mb_file_fits_the_k73_blob_cap_once_sealed() -> None:
    assert dp.encrypted_blob_size(capabilities.READ_FILE_MAX_BYTES) <= dp.HTTP_MAX_BODY_BYTES
