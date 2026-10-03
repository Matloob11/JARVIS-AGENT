"""Desktop Node file mirror, pc.fetch and the encrypted backup pull (plan task C-122 steps 1, 3, 4 and 6; K-70,
K-73, K-76).

The bridge is the real C-117 MediaRelay (src/core/bridge_media.py) over temp folders, served through an httpx
MockTransport that verifies every K-70 PC-signed request with the library. Every key is generated for the test,
every file lives in tmp_path, nothing connects to the live bridge, and the file opener and Explorer are fakes:
nothing is opened or run on this machine.
"""
# pylint: disable=redefined-outer-name,protected-access
from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import os
import time
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from services.utils import desktop_protocol as dp
from src.core import bridge_media
from src.core.desktop import backup_pull, capabilities, mirror
from src.core.desktop import settings as ns
from src.core.desktop.mirror import MediaMirror, MirrorError, MirrorNameError, NodeHttp
from src.core.desktop.node import NodeKeys, NodeOptions, NodeRuntime

BASE = "https://bridge.test"


# --- a fake bridge: the real media relay behind a signature-checking mock transport ---------------------------

class FakeBridge:
    """Serves /vortex/media/index, /vortex/media/file and /vortex/desktop/backup/latest like ui_bridge.py, after
    verifying the PC signature (K-70 signed HTTP) of every request."""

    def __init__(self, tmp: Path) -> None:
        self.shared = tmp / "vps_shared"
        self.outputs = tmp / "vps_outputs"
        self.snapshots = tmp / "vps_snapshots"
        for folder in (self.shared, self.outputs, self.snapshots):
            folder.mkdir(parents=True, exist_ok=True)
        self.now = time.time()
        self.relay = bridge_media.MediaRelay(shared_dir=lambda: str(self.shared), outputs_dir=lambda: str(self.outputs),
                                             clock=lambda: self.now)
        self.backups = bridge_media.BackupRelay(snapshots_dir=lambda: str(self.snapshots))
        self.key = dp.generate_signing_key()
        self.fail_file: dict[str, int] = {}     # relpath -> failures left (503)
        self.tamper: dict[str, bytes] = {}      # relpath -> bytes served instead
        self.requests: list[tuple[str, str]] = []
        self.bad_signatures = 0

    def http(self) -> NodeHttp:
        return NodeHttp(BASE, self.key, transport=httpx.MockTransport(self.handle))

    def put(self, area: str, relpath: str, data: bytes, *, age_s: float = 60.0) -> Path:
        root = self.shared if area == "shared" else self.outputs
        path = root.joinpath(*relpath.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        mtime_ns = int((self.now - age_s) * 1_000_000_000)
        os.utime(path, ns=(mtime_ns, mtime_ns))
        return path

    def handle(self, request: httpx.Request) -> httpx.Response:
        try:
            dp.verify_http_headers(request.headers.raw, method=request.method, path=request.url.raw_path,
                                   query=request.url.query, public_key=self.key.public_key(), seen=None,
                                   content_length=int(request.headers["content-length"])
                                   if "content-length" in request.headers else None)
        except dp.DesktopLinkError:
            self.bad_signatures += 1
            return httpx.Response(403, json={"error": "signature"})
        path = request.url.path
        params = dict(request.url.params)
        self.requests.append((path, params.get("path", "")))
        try:
            if path == "/vortex/media/index":
                cursor = params.get("cursor")
                return httpx.Response(200, json=self.relay.build_index(params["area"],
                                                                       None if cursor is None else float(cursor)))
            if path == "/vortex/media/file":
                relpath = params["path"]
                if self.fail_file.get(relpath, 0) > 0:
                    self.fail_file[relpath] -= 1
                    return httpx.Response(503, json={"error": "unavailable"})
                if relpath in self.tamper:
                    return httpx.Response(200, content=self.tamper[relpath])
                fd, st = self.relay.open_file(params["area"], relpath)
                with os.fdopen(fd, "rb") as handle:
                    data = handle.read()
                assert len(data) == st.st_size
                return httpx.Response(200, content=data)
            if path == "/vortex/desktop/backup/latest":
                fd, _st, name = self.backups.open_latest()
                with os.fdopen(fd, "rb") as handle:
                    data = handle.read()
                return httpx.Response(200, content=data,
                                      headers={"Content-Disposition": f'attachment; filename="{name}"'})
        except bridge_media.MediaError as exc:
            return httpx.Response(exc.status, json={"error": exc.reason})
        return httpx.Response(404, json={"detail": "Not Found"})


@pytest.fixture
def bridge(tmp_path: Path) -> FakeBridge:
    return FakeBridge(tmp_path)


@pytest.fixture
def local(tmp_path: Path) -> dict[str, Path]:
    roots = {"shared": tmp_path / "home" / "Jarvis_Shared", "outputs": tmp_path / "repo" / "Jarvis_Outputs"}
    for root in roots.values():
        root.mkdir(parents=True, exist_ok=True)
    return roots


def make_mirror(bridge: FakeBridge, local: dict[str, Path], tmp_path: Path, *, voice: Callable[[], bool] = lambda: False,
                large_bytes: int = mirror.LARGE_FILE_BYTES, note_bytes: Callable[[int], str | None] | None = None,
                audit: list[tuple[str, dict[str, Any]]] | None = None,
                mono: Callable[[], float] = time.monotonic,
                delivery_cap: int = mirror.DELIVERIES_CAP_BYTES) -> MediaMirror:
    http = bridge.http()
    sink = audit if audit is not None else []
    return MediaMirror(http=lambda: http, roots=lambda: {k: str(v) for k, v in local.items()},
                       state_path=tmp_path / "run" / mirror.STATE_FILE, voice_active=voice, note_bytes=note_bytes,
                       audit=lambda event, **fields: sink.append((event, fields)), large_bytes=large_bytes, mono=mono,
                       delivery_cap=delivery_cap)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- name validation (step 6: the table) ---------------------------------------------------------------------

@pytest.mark.parametrize("name", ["code.png", "QR code 2026-10-03.png", "report_v2.pdf", "a.b.c.txt", "naam-1",
                                  "CONSOLE.txt", "comet.png", "LPT10.txt", "x" * 255, "\u0645\u0631\u062d\u0628\u0627.png"])
def test_component_names_that_pass(name: str) -> None:
    assert mirror.validate_component(name) is None


@pytest.mark.parametrize(("name", "reason"), [
    ("", "empty"), (".", "dot"), ("..", "dot"), ("..\\evil.exe", "backslash"), ("a\\b.png", "backslash"),
    ("a/b", "separator"), ("C:evil", "colon"), ("a.png:Zone.Identifier", "colon"), ("a..b.png", "dotdot"),
    ("bad\x00name", "control"), ("bad\nname", "control"), ("tab\tname", "control"), ("del\x7f", "control"),
    ("zero\u200bwidth.png", "control"), ("bidi\u202egnp.exe", "control"), ("name.", "trailing"),
    ("name ", "trailing"), ("name. ", "trailing"), (" lead.png", "leading_space"), (".hidden", "hidden"),
    (".env", "hidden"), ("a<b.png", "invalid_char"), ("a|b", "invalid_char"), ('a"b', "invalid_char"),
    ("what?.png", "invalid_char"), ("star*.png", "invalid_char"), ("CON", "device"), ("con", "device"),
    ("con.txt", "device"), ("CON .txt", "device"), ("PRN.png", "device"), ("AUX", "device"), ("NUL.tar.gz", "device"),
    ("COM1", "device"), ("com9.log", "device"), ("LPT1.txt", "device"), ("LPT0", "device"), ("COM\u00b9", "device"),
    ("CONIN$", "device"), ("x" * 256, "too_long"), ("\u00e9" * 128, "too_long"),
])
def test_component_names_refused(name: str, reason: str) -> None:
    assert mirror.validate_component(name) == reason


@pytest.mark.parametrize(("area", "relpath", "reason"), [
    ("shared", "QR_Codes/..\\..\\evil.exe", "backslash"), ("shared", "QR_Codes/../../x.png", "dot"),
    ("shared", "/QR_Codes/x.png", "absolute"), ("shared", "QR_Codes//x.png", "empty"),
    ("shared", "Other/x.png", "subtree"), ("shared", "x.png", "subtree"), ("outputs", "x.txt", "subtree"),
    ("outputs", "Generated_Images/x.png", "subtree"), ("shared", "QR_Codes/x.png.part", "excluded"),
    ("shared", "Downloads/sim_lookup_history.jsonl", "excluded"), ("shared", "Downloads/.cache/x.bin", "hidden"),
    ("shared", "Downloads/" + "/".join(["d"] * 17) + "/x.bin", "too_deep"), ("vps", "QR_Codes/x.png", "area"),
    ("shared", "", "empty"), ("shared", "QR_Codes/C:/x.png", "colon"),
    # files Explorer parses while the user only browses the folder (NTLM leak through a UNC icon or location)
    ("shared", "QR_Codes/x.url", "excluded"), ("shared", "Downloads/x.LNK", "excluded"),
    ("shared", "QR_Codes/x.library-ms", "excluded"), ("shared", "Downloads/x.searchConnector-ms", "excluded"),
    ("shared", "Downloads/x.search-ms", "excluded"), ("shared", "QR_Codes/x.scf", "excluded"),
    ("shared", "QR_Codes/desktop.ini", "excluded"), ("shared", "Downloads/Desktop.INI", "excluded"),
    ("outputs", "leads/x.website", "excluded"), ("outputs", "Downloads/x.theme", "excluded"),
    ("outputs", "Downloads/x.themepack", "excluded"), ("shared", "Downloads/sub/autorun.inf", "excluded"),
])
def test_relpaths_refused(area: str, relpath: str, reason: str) -> None:
    with pytest.raises(MirrorNameError) as info:
        mirror.validate_relpath(area, relpath)
    assert info.value.reason == reason


@pytest.mark.parametrize(("area", "relpath"), [("shared", "QR_Codes/code.png"), ("shared", "Downloads/YouTube/v.mp4"),
                                               ("shared", "Generated_Images/a b.png"), ("outputs", "leads/x.csv"),
                                               ("outputs", "Downloads/report.pdf"), ("outputs", "bundle.zip")])
def test_relpaths_that_pass(area: str, relpath: str) -> None:
    assert mirror.validate_relpath(area, relpath) == relpath.split("/")


def test_clash_names() -> None:
    assert mirror.clash_name("report.pdf", 1) == "report (1).pdf"
    assert mirror.clash_name("archive.tar.gz", 2) == "archive.tar (2).gz"
    assert mirror.clash_name("README", 3) == "README (3)"


def test_a_name_with_dotdot_backslash_never_leaves_the_mirror_root(bridge: FakeBridge, local: dict[str, Path],
                                                                    tmp_path: Path) -> None:
    mirror_ = make_mirror(bridge, local, tmp_path)
    for relpath in ("QR_Codes/..\\..\\evil.png", "QR_Codes/../../evil.png", "..\\evil.png", "QR_Codes/..%5Cevil.png"):
        with pytest.raises(MirrorError) as info:
            mirror_.fetch_one("shared", relpath, size=3, sha256=sha(b"abc"))
        assert info.value.code in (mirror.BAD_NAME, mirror.DELIVERY_FAILED)
    assert not any(path.name.startswith("evil") for path in tmp_path.rglob("*"))
    assert not [request for request in bridge.requests if request[0] == "/vortex/media/file"]


# --- the mirror: write order, dedupe, clashes ------------------------------------------------------------------

def test_mirror_copies_with_mark_of_the_web_and_dedupes(bridge: FakeBridge, local: dict[str, Path],
                                                        tmp_path: Path) -> None:
    data = b"QR-PNG-BYTES" * 100
    bridge.put("shared", "QR_Codes/code.png", data)
    bridge.put("outputs", "leads/list.csv", b"a,b\n1,2\n")
    bridge.put("outputs", "bundle.zip", b"PK\x03\x04zip")
    audit: list[tuple[str, dict[str, Any]]] = []
    mirror_ = make_mirror(bridge, local, tmp_path, audit=audit)
    reports = mirror_.sync_once()
    assert reports["shared"].copied == 1 and reports["outputs"].copied == 2
    copy = local["shared"] / "QR_Codes" / "code.png"
    assert copy.read_bytes() == data
    assert (local["outputs"] / "leads" / "list.csv").read_bytes() == b"a,b\n1,2\n"
    assert (local["outputs"] / "bundle.zip").exists()
    assert not list(local["shared"].rglob("*.part"))
    if os.name == "nt":  # Mark-of-the-Web on the copy (ZoneId=3)
        with open(f"{copy}:Zone.Identifier", encoding="ascii") as handle:
            assert "ZoneId=3" in handle.read()
    # the remote mtime is kept, and a second pass copies nothing (relpath + size + mtime)
    assert copy.stat().st_mtime_ns == (bridge.shared / "QR_Codes" / "code.png").stat().st_mtime_ns
    again = make_mirror(bridge, local, tmp_path).sync_once()  # a new process: the state file dedupes
    assert sum(report.copied for report in again.values()) == 0
    assert sum(report.skipped for report in again.values()) == 3
    assert bridge.bad_signatures == 0
    # the audit lines carry the area and the size only, never a name
    assert all("QR_Codes" not in str(fields) and "code.png" not in str(fields) for _event, fields in audit)


def test_mirror_logs_hold_no_names_or_paths(bridge: FakeBridge, local: dict[str, Path], tmp_path: Path,
                                            caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    bridge.put("shared", "QR_Codes/secret-client-name.png", b"x" * 20)
    bridge.put("shared", "QR_Codes/flaky-secret-client.png", b"y" * 20)
    bridge.put("shared", "QR_Codes/..bad-secret-client.png", b"z" * 20)
    bridge.fail_file["QR_Codes/flaky-secret-client.png"] = 1
    make_mirror(bridge, local, tmp_path).sync_once()
    text = "\n".join(record.getMessage() + str(record.args) for record in caplog.records)
    assert "secret-client" not in text and "QR_Codes" not in text
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING


def test_a_clash_gets_a_number_and_never_overwrites(bridge: FakeBridge, local: dict[str, Path], tmp_path: Path) -> None:
    (local["shared"] / "QR_Codes").mkdir(parents=True)
    mine = local["shared"] / "QR_Codes" / "code.png"
    mine.write_bytes(b"the user's own file")
    bridge.put("shared", "QR_Codes/code.png", b"vps file")
    make_mirror(bridge, local, tmp_path).sync_once()
    assert mine.read_bytes() == b"the user's own file"
    assert (local["shared"] / "QR_Codes" / "code (1).png").read_bytes() == b"vps file"


def test_an_identical_local_file_is_kept_once(bridge: FakeBridge, local: dict[str, Path], tmp_path: Path) -> None:
    (local["shared"] / "QR_Codes").mkdir(parents=True)
    (local["shared"] / "QR_Codes" / "code.png").write_bytes(b"same bytes")
    bridge.put("shared", "QR_Codes/code.png", b"same bytes")
    make_mirror(bridge, local, tmp_path).sync_once()
    assert sorted(p.name for p in (local["shared"] / "QR_Codes").iterdir()) == ["code.png"]


def test_a_sha_mismatch_is_refused_and_leaves_nothing(bridge: FakeBridge, local: dict[str, Path],
                                                      tmp_path: Path) -> None:
    good = b"the real file"
    bridge.put("shared", "QR_Codes/code.png", good)
    mirror_ = make_mirror(bridge, local, tmp_path)
    with pytest.raises(MirrorError) as info:
        mirror_.fetch_one("shared", "QR_Codes/code.png", size=len(good), sha256=sha(b"something else"), explicit=True)
    assert (info.value.code, info.value.reason) == (mirror.DELIVERY_FAILED, "sha256_mismatch")
    assert not list(local["shared"].rglob("*")) or not [p for p in local["shared"].rglob("*") if p.is_file()]
    # a body changed on the way (the index sha256 is the check) is refused too
    bridge.tamper["QR_Codes/code.png"] = b"the evil file"
    report = mirror_.sync_area("shared", full=True)
    assert report.failed == 1 and report.copied == 0
    assert not [p for p in local["shared"].rglob("*") if p.is_file()]


def test_a_wrong_size_is_refused(bridge: FakeBridge, local: dict[str, Path], tmp_path: Path) -> None:
    bridge.put("shared", "QR_Codes/code.png", b"12345")
    mirror_ = make_mirror(bridge, local, tmp_path)
    for size in (4, 6):
        with pytest.raises(MirrorError):
            mirror_.fetch_one("shared", "QR_Codes/code.png", size=size, explicit=True)
    assert not [p for p in local["shared"].rglob("*") if p.is_file()]


def make_dir_link(link: Path, target: Path) -> str:
    """A directory reparse point at link: a symlink, or on Windows a junction (no privilege needed)."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return "symlink"
    except (OSError, NotImplementedError):
        pass
    if os.name == "nt":
        import _winapi  # pylint: disable=import-outside-toplevel,import-error

        _winapi.CreateJunction(str(target), str(link))
        return "junction"
    pytest.skip("this account cannot create a directory link")
    return ""


def test_a_reparse_point_in_the_target_is_refused(bridge: FakeBridge, local: dict[str, Path], tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    make_dir_link(local["shared"] / "QR_Codes", outside)
    assert mirror._is_link_or_reparse(local["shared"] / "QR_Codes")
    bridge.put("shared", "QR_Codes/code.png", b"x")
    with pytest.raises(MirrorError) as info:
        make_mirror(bridge, local, tmp_path).fetch_one("shared", "QR_Codes/code.png", size=1, explicit=True)
    assert info.value.reason == "reparse_point"
    assert not list(outside.iterdir())
    # a deeper folder on the path that is a link is refused the same way
    (local["shared"] / "Downloads").mkdir()
    make_dir_link(local["shared"] / "Downloads" / "YouTube", outside)
    bridge.put("shared", "Downloads/YouTube/v.mp4", b"v")
    with pytest.raises(MirrorError) as info:
        make_mirror(bridge, local, tmp_path).fetch_one("shared", "Downloads/YouTube/v.mp4", size=1, explicit=True)
    assert info.value.reason == "reparse_point"
    assert not list(outside.iterdir())


def test_a_reparse_point_at_the_final_name_is_never_written_through(bridge: FakeBridge, local: dict[str, Path],
                                                                     tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (local["shared"] / "QR_Codes").mkdir()
    make_dir_link(local["shared"] / "QR_Codes" / "code.png", outside)
    bridge.put("shared", "QR_Codes/code.png", b"qr")
    final = make_mirror(bridge, local, tmp_path).fetch_one("shared", "QR_Codes/code.png", size=2, explicit=True)
    assert final == local["shared"] / "QR_Codes" / "code (1).png" and final.read_bytes() == b"qr"
    assert not list(outside.iterdir())
    assert mirror._is_link_or_reparse(local["shared"] / "QR_Codes" / "code.png")  # left as it was


# --- the cursor never skips a file (step 6) --------------------------------------------------------------------

def test_the_cursor_never_skips_a_file(bridge: FakeBridge, local: dict[str, Path], tmp_path: Path) -> None:
    voice = {"active": True}
    clock = {"t": 1000.0}
    mirror_ = make_mirror(bridge, local, tmp_path, voice=lambda: voice["active"], large_bytes=100,
                          mono=lambda: clock["t"])
    bridge.put("shared", "QR_Codes/a.png", b"a" * 10, age_s=60)
    bridge.put("shared", "QR_Codes/fresh.png", b"f" * 10, age_s=2)   # inside the bridge's 5 s exclusion now
    bridge.put("shared", "QR_Codes/flaky.png", b"k" * 10, age_s=30)
    bridge.put("shared", "Downloads/big.bin", b"B" * 500, age_s=40)  # large: waits while voice is active
    bridge.fail_file["QR_Codes/flaky.png"] = 1
    first = mirror_.sync_area("shared", full=True)
    assert (first.copied, first.failed, first.deferred) == (1, 1, 1)
    # the cursor stays below the oldest file that is not copied yet
    flaky_mtime = (bridge.shared / "QR_Codes" / "flaky.png").stat().st_mtime_ns
    big_mtime = (bridge.shared / "Downloads" / "big.bin").stat().st_mtime_ns
    assert mirror_.cursor("shared") * 1e9 < min(flaky_mtime, big_mtime)

    bridge.now += 20  # 'fresh' is old enough now and newer than the previous listing time - 5 s
    clock["t"] += 20  # flaky backs off: it holds the cursor but waits for its next try
    bridge.put("shared", "QR_Codes/late.png", b"l" * 10, age_s=10)
    second = mirror_.sync_area("shared")  # incremental, with the cursor
    assert (second.copied, second.backoff, second.deferred) == (2, 1, 1)  # fresh and late; flaky and big wait
    assert mirror_.cursor("shared") * 1e9 < min(flaky_mtime, big_mtime)
    bridge.now += 20
    clock["t"] += mirror.BACKOFF_FIRST_S
    assert mirror_.sync_area("shared").copied == 1  # flaky, on its next try
    voice["active"] = False
    bridge.now += 20
    third = mirror_.sync_area("shared")
    assert third.copied == 1
    copied = sorted(p.relative_to(local["shared"]).as_posix() for p in local["shared"].rglob("*") if p.is_file())
    assert copied == ["Downloads/big.bin", "QR_Codes/a.png", "QR_Codes/flaky.png", "QR_Codes/fresh.png",
                      "QR_Codes/late.png"]
    # every listing after the first one asked with a cursor; the server cursor is listing time - 5 s
    assert mirror_.cursor("shared") == pytest.approx(bridge.now - 5, abs=0.01)


def test_the_mirror_loop_polls_only_while_the_link_is_up(bridge: FakeBridge, local: dict[str, Path],
                                                         tmp_path: Path) -> None:
    bridge.put("shared", "QR_Codes/a.png", b"a" * 10)
    link = {"up": False}
    asked: list[int] = []
    http = bridge.http()
    mirror_ = MediaMirror(http=lambda: asked.append(1) or http, roots=lambda: {k: str(v) for k, v in local.items()},
                          state_path=tmp_path / "run" / mirror.STATE_FILE, voice_active=lambda: False,
                          ready=lambda: link["up"], poll_s=0.01)

    async def loop_for(seconds: float) -> None:
        task = asyncio.ensure_future(mirror_.run())
        await asyncio.sleep(seconds)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(loop_for(0.1))
    assert asked == [] and not (local["shared"] / "QR_Codes" / "a.png").exists()
    link["up"] = True
    asyncio.run(loop_for(0.5))
    assert (local["shared"] / "QR_Codes" / "a.png").read_bytes() == b"a" * 10


def test_cursor_below_is_strictly_below() -> None:
    for mtime_ns in (1_759_400_000_000_000_000, 1_759_400_000_123_456_789, 1_759_400_000_999_999_999):
        assert int(mirror.cursor_below(mtime_ns) * 1_000_000_000) < mtime_ns


def test_a_byte_cap_trip_holds_the_file_that_did_not_fit_for_the_next_pass(bridge: FakeBridge,
                                                                          local: dict[str, Path],
                                                                          tmp_path: Path) -> None:
    """The file that tripped the cap fails without being downloaded and holds the cursor, so the next pass (with
    room in the cap again) copies it; the file that fitted is copied in the first pass."""
    bridge.put("shared", "QR_Codes/a.png", b"a" * 10, age_s=60)
    bridge.put("shared", "QR_Codes/b.png", b"b" * 10, age_s=50)
    allowed = {"bytes": 10}

    def note(size: int) -> str | None:
        if size > allowed["bytes"]:
            return "DESKTOP_RATE_LIMITED"
        allowed["bytes"] -= size
        return None

    mirror_ = make_mirror(bridge, local, tmp_path, note_bytes=note)
    report = mirror_.sync_area("shared", full=True)
    assert report.copied == 1 and report.failed == 1
    assert ("/vortex/media/file", "QR_Codes/b.png") not in bridge.requests  # charged before, never downloaded
    allowed["bytes"] = 100
    assert mirror_.sync_area("shared").copied == 1
    assert len([p for p in local["shared"].rglob("*") if p.is_file()]) == 2


# --- pc.fetch: agent-signed sha256, auto-open list, never an executable (step 6) ------------------------------

class FakeNode:
    def __init__(self, tmp: Path, local: dict[str, Path]) -> None:
        self.data_root = tmp / "repo"
        self.run_dir = self.data_root / "run"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.config = types.SimpleNamespace(shared_dir=str(local["shared"]), outputs_dir=str(local["outputs"]),
                                            open_roots=[str(local["shared"]), str(local["outputs"])])
        self.audit_lines: list[tuple[str, dict[str, Any]]] = []
        self.audit = types.SimpleNamespace(record=lambda event, **fields: self.audit_lines.append((event, fields)))
        self.policy = types.SimpleNamespace(note_bytes=lambda bucket, size, **_kw: None)
        self.ctx = types.SimpleNamespace(call_id="call-0001", peer="agent:job1", origin="llm", room="jarvis-hybrid",
                                         cls="read", risk="low", approval=None)
        self.link = types.SimpleNamespace(peer=lambda ident: None)
        self.bridge_hint: dict[str, Any] = {"desktop_status": {"voice_active": False}}

    def call_context(self, call_id: str | None = None) -> Any:
        return self.ctx


@pytest.fixture
def fetch_env(bridge: FakeBridge, local: dict[str, Path], tmp_path: Path) -> Any:
    node = FakeNode(tmp_path, local)
    opened: list[Path] = []
    revealed: list[Path] = []
    mirror_ = make_mirror(bridge, local, tmp_path)
    caps = capabilities.Capabilities(node, mirror=lambda: mirror_, open_file=opened.append, reveal_file=revealed.append)
    return types.SimpleNamespace(node=node, caps=caps, opened=opened, revealed=revealed, bridge=bridge, local=local)


def fetch_args(relpath: str, data: bytes, *, area: str = "shared", open_: bool = True, **extra: Any) -> dict[str, Any]:
    return {"area": area, "relpath": relpath, "sha256": sha(data), "size": len(data), "open": open_, **extra}


@pytest.mark.parametrize("relpath", ["Downloads/setup.exe", "Downloads/run.bat", "Downloads/script.ps1",
                                     "Downloads/tool.py", "Downloads/archive.zip", "Downloads/pack.7z",
                                     "Downloads/app.msi",
                                     "Downloads/page.hta", "Downloads/macro.docm", "Downloads/notes.md",
                                     "Downloads/noext"])
def test_an_executable_or_unlisted_type_is_never_opened(fetch_env: Any, relpath: str) -> None:
    data = b"MZ\x90\x00 not really"
    fetch_env.bridge.put("shared", relpath, data)
    result = asyncio.run(fetch_env.caps.fetch(fetch_args(relpath, data)))
    assert result["status"] == "success", result
    assert fetch_env.opened == []
    assert [p.name for p in fetch_env.revealed] == [relpath.split("/")[-1]]
    assert result["data"]["opened"] is False and result["data"]["revealed"] is True


@pytest.mark.parametrize("relpath", ["QR_Codes/code.png", "Downloads/doc.pdf", "Downloads/song.mp3",
                                     "Downloads/clip.mp4", "Downloads/t.txt", "Downloads/d.csv", "Downloads/d.json",
                                     "Downloads/s.xlsx", "Downloads/w.docx", "Downloads/p.pptx", "Downloads/page.html"])
def test_an_auto_open_type_is_opened(fetch_env: Any, relpath: str) -> None:
    data = b"content"
    fetch_env.bridge.put("shared", relpath, data)
    result = asyncio.run(fetch_env.caps.fetch(fetch_args(relpath, data)))
    assert result["status"] == "success", result
    assert [p.name for p in fetch_env.opened] == [relpath.split("/")[-1]]
    assert fetch_env.revealed == []
    assert "path" not in result["data"]  # the absolute local path never leaves the laptop
    assert result["data"]["relpath"] == relpath
    assert (fetch_env.local["shared"] / result["data"]["relpath"]).read_bytes() == data


def test_fetch_without_open_only_saves(fetch_env: Any) -> None:
    data = b"png"
    fetch_env.bridge.put("shared", "QR_Codes/code.png", data)
    result = asyncio.run(fetch_env.caps.fetch(fetch_args("QR_Codes/code.png", data, open_=False)))
    assert result["status"] == "success"
    assert fetch_env.opened == [] and fetch_env.revealed == []
    assert (fetch_env.local["shared"] / "QR_Codes" / "code.png").read_bytes() == data


def test_fetch_sha_mismatch_is_refused_and_nothing_opens(fetch_env: Any) -> None:
    data = b"real bytes"
    fetch_env.bridge.put("shared", "QR_Codes/code.png", data)
    args = fetch_args("QR_Codes/code.png", data)
    args["sha256"] = sha(b"other bytes")
    result = asyncio.run(fetch_env.caps.fetch(args))
    assert result["status"] == "error" and result["error_code"] == "DELIVERY_FAILED"
    assert fetch_env.opened == [] and fetch_env.revealed == []
    assert not [p for p in fetch_env.local["shared"].rglob("*") if p.is_file()]


@pytest.mark.parametrize("change", [
    {"sha256": None}, {"sha256": "ABC"}, {"sha256": "A" * 64}, {"size": "3"}, {"size": True}, {"area": "vps"},
    {"open": "yes"}, {"extra": 1}, {"relpath": "QR_Codes/..\\x.png"}, {"relpath": "QR_Codes/CON.png"},
])
def test_fetch_bad_arguments_are_refused(fetch_env: Any, change: dict[str, Any]) -> None:
    data = b"x"
    fetch_env.bridge.put("shared", "QR_Codes/code.png", data)
    args = fetch_args("QR_Codes/code.png", data)
    for key, value in change.items():
        if value is None:
            args.pop(key)
        else:
            args[key] = value
    result = asyncio.run(fetch_env.caps.fetch(args))
    assert result["status"] == "error" and result["error_code"] == "BAD_ARGS"
    assert fetch_env.opened == [] and fetch_env.revealed == []


def test_fetch_of_an_already_mirrored_file_opens_the_copy(fetch_env: Any, tmp_path: Path) -> None:
    data = b"made on the vps"
    fetch_env.bridge.put("shared", "QR_Codes/code.png", data)
    mirror_ = fetch_env.caps._mirror()
    mirror_.sync_once()
    before = len(fetch_env.bridge.requests)
    result = asyncio.run(fetch_env.caps.fetch(fetch_args("QR_Codes/code.png", data)))
    assert result["status"] == "success"
    assert len(fetch_env.bridge.requests) == before  # no second download
    assert [p.name for p in fetch_env.opened] == ["code.png"]


def test_large_fetch_waits_for_voice_unless_opened(fetch_env: Any, bridge: FakeBridge, local: dict[str, Path],
                                                   tmp_path: Path) -> None:
    data = b"L" * 300
    bridge.put("shared", "Downloads/big.bin", data)
    mirror_ = make_mirror(bridge, local, tmp_path, voice=lambda: True, large_bytes=100)
    caps = capabilities.Capabilities(fetch_env.node, mirror=lambda: mirror_, open_file=fetch_env.opened.append,
                                     reveal_file=fetch_env.revealed.append)
    saved = asyncio.run(caps.fetch(fetch_args("Downloads/big.bin", data, open_=False)))
    assert saved["status"] == "error" and saved["error_code"] == "DELIVERY_FAILED"
    opened = asyncio.run(caps.fetch(fetch_args("Downloads/big.bin", data, open_=True)))
    assert opened["status"] == "success"
    assert fetch_env.revealed and not fetch_env.opened  # .bin is not on the auto-open list


def test_fetch_deliveries_cap(fetch_env: Any) -> None:
    data = b"x" * 10
    fetch_env.bridge.put("shared", "QR_Codes/code.png", data)
    fetch_env.caps._mirror()._note_bytes = lambda size: "DESKTOP_RATE_LIMITED"
    result = asyncio.run(fetch_env.caps.fetch(fetch_args("QR_Codes/code.png", data)))
    assert result["error_code"] == "DESKTOP_RATE_LIMITED"
    assert fetch_env.opened == []


# --- encrypted backup pull (step 4) ----------------------------------------------------------------------------

def test_backup_pull_keeps_every_snapshot_and_reports_size(bridge: FakeBridge, tmp_path: Path) -> None:
    dest = tmp_path / "LocalAppData" / "JARVIS" / "vps_backups"
    first = os.urandom(4096)
    snap = bridge.snapshots / "jarvis_20261003-020000.enc"
    snap.write_bytes(first)
    result = backup_pull.pull_latest(bridge.http(), dest)
    assert result.new and result.size == len(first) and result.sha256 == sha(first)
    assert result.encrypted is True
    assert (dest / "jarvis_20261003-020000.enc").read_bytes() == first
    assert (result.total_bytes, result.count) == (len(first), 1)
    again = backup_pull.pull_latest(bridge.http(), dest)  # the same snapshot: kept once
    assert again.new is False and again.count == 1
    second = os.urandom(2048)
    newer = bridge.snapshots / "jarvis_20261004-020000.enc"
    newer.write_bytes(second)
    os.utime(newer, ns=(snap.stat().st_mtime_ns + 10**9, snap.stat().st_mtime_ns + 10**9))
    third = backup_pull.pull_latest(bridge.http(), dest)
    assert third.new and third.count == 2 and third.total_bytes == len(first) + len(second)
    # a changed file under an old name never overwrites: ' (n)'
    snap.write_bytes(b"rewritten")
    newer.unlink()
    fourth = backup_pull.pull_latest(bridge.http(), dest)
    assert fourth.name == "jarvis_20261003-020000 (1).enc"
    assert (dest / "jarvis_20261003-020000.enc").read_bytes() == first
    assert not list(dest.glob("*.part")) and not (dest / backup_pull.LOCK_FILE).exists()


@pytest.mark.parametrize("name", ["evil.url", "Shortcut.LNK", "desktop.ini", "autorun.inf", "lib.library-ms",
                                  "q.searchConnector-ms", "s.scf", "a.website", "x.theme", "y.pif", "snap.part",
                                  "snap.tmp", "CON.enc", "a..b"])
def test_backup_snapshot_names_follow_the_mirror_exclusions(name: str) -> None:
    headers = {"content-disposition": f'attachment; filename="{name}"'}
    assert backup_pull.snapshot_name(headers, 1_760_000_000.0) == "jarvis_snapshot_20251009-085320.bin"


def test_backup_snapshot_name_keeps_a_plain_name() -> None:
    headers = {"content-disposition": 'attachment; filename="jarvis_20261003-020000.enc"'}
    assert backup_pull.snapshot_name(headers, 0.0) == "jarvis_20261003-020000.enc"


def test_backup_pull_marks_the_snapshot_and_never_keeps_a_shell_parsed_name(
        bridge: FakeBridge, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (bridge.snapshots / "evil.url").write_bytes(b"\x8a\x01ciphertext" * 20)
    marked: list[str] = []
    real = mirror.write_mark_of_the_web

    def mark(path: Path) -> None:
        marked.append(Path(path).name)
        real(path)

    monkeypatch.setattr(mirror, "write_mark_of_the_web", mark)
    dest = tmp_path / "dest"
    result = backup_pull.pull_latest(bridge.http(), dest, wall=lambda: 1_760_000_000.0)
    assert result.new and result.name.startswith("jarvis_snapshot_") and result.name.endswith(".bin")
    assert not list(dest.glob("*.url")) and marked and marked[0].endswith(".part")  # marked before the rename
    if os.name == "nt":  # Mark-of-the-Web on the kept file (ZoneId=3)
        with open(f"{result.path}:Zone.Identifier", encoding="ascii") as handle:
            assert "ZoneId=3" in handle.read()
    marked.clear()
    again = backup_pull.pull_latest(bridge.http(), dest, wall=lambda: 1_760_000_000.0)
    assert again.new is False and again.count == 1
    assert marked[-1] == result.name  # an identical snapshot already kept gets the mark too


def test_backup_pull_refuses_a_snapshot_it_cannot_mark(bridge: FakeBridge, tmp_path: Path,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    (bridge.snapshots / "s.enc").write_bytes(b"\x8a" * 64)

    def broken(_path: Path) -> None:
        raise OSError("no streams here")

    monkeypatch.setattr(mirror, "write_mark_of_the_web", broken)
    dest = tmp_path / "dest"
    with pytest.raises(backup_pull.BackupPullError) as info:
        backup_pull.pull_latest(bridge.http(), dest)
    assert info.value.reason == "motw"
    assert not [p for p in dest.iterdir() if p.name != backup_pull.LOCK_FILE]


def test_backup_pull_flags_a_plaintext_snapshot(bridge: FakeBridge, tmp_path: Path) -> None:
    (bridge.snapshots / "plain.db").write_bytes(b"SQLite format 3\x00" + b"\x00" * 100)
    result = backup_pull.pull_latest(bridge.http(), tmp_path / "dest")
    assert result.encrypted is False and result.new
    assert backup_pull.looks_encrypted(bytes([0x8a, 0x01]) + b"ciphertext" * 40)
    assert not backup_pull.looks_encrypted(b"PK\x03\x04rest")


def test_backup_pull_cli(bridge: FakeBridge, tmp_path: Path) -> None:
    (bridge.snapshots / "jarvis_snap.enc").write_bytes(b"\x01\x02\x03secret-ciphertext")
    run_dir = tmp_path / "run"
    out, err = io.StringIO(), io.StringIO()
    code = backup_pull.cli_main(["--dest", str(tmp_path / "dest")], http_factory=lambda: (bridge.http(), run_dir),
                                out=out, err=err)
    assert code == 0, err.getvalue()
    lines = dict(line.split("=", 1) for line in out.getvalue().splitlines())
    assert lines["SIZE"] == str(len(b"\x01\x02\x03secret-ciphertext")) and lines["COUNT"] == "1"
    assert Path(lines["PULLED"]).is_file() and lines["NEW"] == "true" and lines["ENCRYPTED"] == "true"
    assert list(lines) == ["PULLED", "NEW", "SIZE", "SHA256", "ENCRYPTED", "TOTAL_BYTES", "COUNT"]
    # the same snapshot again: KEPT, NEW=false, nothing new written
    out = io.StringIO()
    assert backup_pull.cli_main(["--dest", str(tmp_path / "dest")], http_factory=lambda: (bridge.http(), run_dir),
                                out=out, err=io.StringIO()) == 0
    again = dict(line.split("=", 1) for line in out.getvalue().splitlines())
    assert again["KEPT"] == lines["PULLED"] and again["NEW"] == "false" and again["COUNT"] == "1"
    state = backup_pull.read_state(run_dir)
    assert state["last_size"] == int(lines["SIZE"]) and "last_success" in state
    # no snapshot: exit 1 with a reason; unusable configuration: exit 2
    for path in bridge.snapshots.iterdir():
        path.unlink()
    err = io.StringIO()
    assert backup_pull.cli_main(["--dest", str(tmp_path / "dest")], http_factory=lambda: (bridge.http(), run_dir),
                                out=io.StringIO(), err=err) == 1
    assert err.getvalue().strip() == "ERROR=http_not_found"

    def broken() -> Any:
        raise ValueError("no node_config.json")

    err = io.StringIO()
    assert backup_pull.cli_main([], http_factory=broken, out=io.StringIO(), err=err) == 2
    assert err.getvalue().startswith("ERROR=config_")


def test_backup_puller_is_daily(bridge: FakeBridge, tmp_path: Path) -> None:
    (bridge.snapshots / "s.enc").write_bytes(b"\x09" * 50)
    clock = {"now": 1_760_000_000.0}
    node = types.SimpleNamespace(run_dir=tmp_path / "run", audit=types.SimpleNamespace(record=lambda *a, **k: None))
    http = bridge.http()
    puller = backup_pull.BackupPuller(node, http=lambda: http, dest=tmp_path / "dest", wall=lambda: clock["now"])
    assert puller.due()
    puller.pull_now()
    assert not puller.due()
    clock["now"] += backup_pull.DAILY_S - 1
    assert not puller.due()
    clock["now"] += 2
    assert puller.due()


def test_signed_requests_carry_no_unsigned_change(bridge: FakeBridge) -> None:
    """A request whose query is changed after signing is refused by the bridge's check (403)."""
    http = bridge.http()
    headers = http._signed("GET", "/media/index", {"area": "shared"}, b"")
    with httpx.Client(transport=httpx.MockTransport(bridge.handle)) as client:
        response = client.get(f"{BASE}/vortex/media/index", params={"area": "outputs"}, headers=headers)
    assert response.status_code == 403 and bridge.bad_signatures == 1


# --- voice_active from the bridge hints, through the real NodeRuntime (K-73 large-file rule) --------------------

class _Offline:
    connected = False


def make_node_runtime(tmp_path: Path, clock: dict[str, float]) -> NodeRuntime:
    data_root = tmp_path / "noderepo"
    (data_root / "run").mkdir(parents=True, exist_ok=True)
    (data_root / ".env").write_text("", encoding="utf-8")
    return NodeRuntime(node_id="laptop-test", keys=NodeKeys(dp.generate_signing_key(),
                                                            dp.generate_signing_key().public_key()),
                       data_root=data_root, run_dir=data_root / "run",
                       settings_source=ns.SettingsSource(data_root / ".env", data_root),
                       transport_factory=_Offline, tool_provider=types.SimpleNamespace(describe=None),
                       options=NodeOptions(run_mode="active"), mono=lambda: clock["t"])


def connect(node: NodeRuntime, sess: str) -> None:
    """The link state of a connected session (what DesktopLink.connected reads)."""
    node.link._transport = types.SimpleNamespace(connected=True)  # pylint: disable=protected-access
    node.link.sess = sess


def hint(typ: str, body: dict[str, Any]) -> dp.BridgeHint:
    return dp.parse_bridge_hint(dp.build_bridge_hint(typ, body, to="pc:laptop-test"))


def status_body(voice: bool) -> dict[str, Any]:
    return {"server_time": 1_760_000_000.0, "brain": "vps", "active_job": None, "link": "on", "limits": {},
            "desktop_status": {"online": True, "voice_active": voice, "mode": "full"}}


@pytest.fixture
def runtime(tmp_path: Path) -> Any:
    clock = {"t": 1000.0}
    made = make_node_runtime(tmp_path, clock)
    try:
        yield made, clock
    finally:
        made.close()


def test_voice_active_follows_the_hints_the_node_really_gets(runtime: Any) -> None:
    node, clock = runtime
    assert mirror.voice_active(node) is True             # nothing known yet: counts as active
    connect(node, "s" * 32)
    asyncio.run(node.handle_hint(hint("welcome", status_body(False))))
    assert mirror.voice_active(node) is False            # a fresh welcome snapshot
    # an alert-only status hint (what the bridge sends the node today) keeps every value before it
    asyncio.run(node.handle_hint(hint("status", {"alert": {"code": "CLOCK_SKEW", "severity": "warning"}})))
    assert node.bridge_hint["desktop_status"]["voice_active"] is False and node.bridge_hint["brain"] == "vps"
    assert mirror.voice_active(node) is False
    # scenario A: voice starts after the welcome and no status reaches the node: the snapshot goes stale
    clock["t"] += mirror.STATUS_FRESH_S + 1
    assert mirror.voice_active(node) is True
    # a bridge that pushes desktop_status in status hints: each change counts as long as the session lasts
    asyncio.run(node.handle_hint(hint("status", status_body(True))))
    assert mirror.voice_active(node) is True
    asyncio.run(node.handle_hint(hint("status", status_body(False))))
    clock["t"] += 3600
    assert mirror.voice_active(node) is False
    asyncio.run(node.handle_hint(hint("status", {"alert": {"code": "RATE", "severity": "warning"}})))
    assert mirror.voice_active(node) is False            # scenario B: an alert no longer wipes the value
    # a new link session: the old value is unknown until its welcome
    connect(node, "t" * 32)
    assert mirror.voice_active(node) is True
    asyncio.run(node.handle_hint(hint("welcome", status_body(False))))
    assert mirror.voice_active(node) is False
    clock["t"] += mirror.STATUS_FRESH_S + 1
    assert mirror.voice_active(node) is True             # a welcome alone is a snapshot, also on a later session
    asyncio.run(node.handle_hint(hint("welcome", status_body(True))))
    assert mirror.voice_active(node) is True
    node.link._transport = None  # pylint: disable=protected-access  # the link is down
    assert mirror.voice_active(node) is True


def test_a_large_file_waits_while_voice_started_after_the_welcome(runtime: Any, bridge: FakeBridge,
                                                                  local: dict[str, Path], tmp_path: Path) -> None:
    node, clock = runtime
    connect(node, "s" * 32)
    asyncio.run(node.handle_hint(hint("welcome", status_body(False))))
    mirror_ = make_mirror(bridge, local, tmp_path, voice=lambda: mirror.voice_active(node), large_bytes=100)
    clock["t"] += 120  # the user starts a voice session; the bridge does not tell the node
    bridge.put("shared", "Downloads/YouTube/video.mp4", b"V" * 500, age_s=30)
    report = mirror_.sync_area("shared", full=True)
    assert (report.copied, report.deferred) == (0, 1)
    asyncio.run(node.handle_hint(hint("status", status_body(False))))  # pushed: voice is idle again
    bridge.now += 20
    assert mirror_.sync_area("shared").copied == 1


# --- a file the mirror can never write is refused once and does not hold the cursor -----------------------------

def test_a_permanent_refusal_is_audited_once_and_never_holds_the_cursor(
        bridge: FakeBridge, local: dict[str, Path], tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="JARVIS-DESKTOP.mirror")
    audit: list[tuple[str, dict[str, Any]]] = []
    http = bridge.http()
    mirror_ = MediaMirror(http=lambda: http, roots=lambda: {k: str(v) for k, v in local.items()},
                          state_path=tmp_path / "run" / mirror.STATE_FILE, voice_active=lambda: False,
                          audit=lambda event, **fields: audit.append((event, fields)),
                          sensitive=lambda path: path.name.startswith("secret"))
    bridge.put("shared", "QR_Codes/secret.png", b"s" * 10, age_s=60)
    bridge.put("shared", "QR_Codes/fine.png", b"f" * 10, age_s=50)
    first = mirror_.sync_area("shared", full=True)
    assert (first.copied, first.refused, first.failed) == (1, 1, 0)
    assert mirror_.cursor("shared") == pytest.approx(bridge.now - 5, abs=0.01)  # not held below secret.png
    second = mirror_.sync_area("shared", full=True)
    assert (second.copied, second.refused, second.skipped) == (0, 1, 1)
    refusals = [fields for event, fields in audit if event == "mirror_refused"]
    assert refusals == [{"kind": "shared", "reason": "sensitive_target"}]
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1 and "secret" not in warnings[0].getMessage()


def test_a_repeated_copy_failure_logs_at_debug(bridge: FakeBridge, local: dict[str, Path], tmp_path: Path,
                                               caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="JARVIS-DESKTOP.mirror")
    clock = {"t": 1000.0}
    mirror_ = make_mirror(bridge, local, tmp_path, mono=lambda: clock["t"])
    bridge.put("shared", "QR_Codes/code.png", b"c" * 10)
    bridge.tamper["QR_Codes/code.png"] = b"c" * 11  # always the wrong size: a failure that holds the cursor
    for _ in range(3):
        assert mirror_.sync_area("shared", full=True).failed == 1
        clock["t"] += mirror.BACKOFF_MAX_S
    failures = [r for r in caplog.records if r.getMessage().startswith("Mirror copy failed")]
    assert [r.levelno for r in failures] == [logging.WARNING, logging.DEBUG, logging.DEBUG]


@pytest.mark.parametrize("name", ["code.png", "x" * 255, "é" * 127 + ".png", "\U0001f600" * 63],
                         ids=["short", "ascii_255", "latin_255_bytes", "emoji"])
def test_part_names_always_fit_255_bytes(name: str) -> None:
    part = mirror.part_name(name)
    assert part.endswith(".part") and len(part.encode("utf-8")) <= mirror.MAX_COMPONENT_BYTES
    assert len(part.encode("utf-16-le")) // 2 <= mirror.MAX_COMPONENT_BYTES
    assert part.startswith(name[:20])
    assert mirror.part_name(name) != part  # a fresh random part every time


def test_backup_cli_uses_the_node_measured_clock_offset(tmp_path: Path) -> None:
    from src.core.desktop import health  # pylint: disable=import-outside-toplevel

    run_dir = tmp_path / "run"
    assert backup_pull.saved_clock_offset(run_dir) is None
    status = health.StatusFile(run_dir / health.STATUS_FILE, wall=lambda: 1_760_000_000.0)
    status.write({"offset_s": 312.5})
    assert backup_pull.saved_clock_offset(run_dir, now=1_760_000_100.0) == 312.5
    assert backup_pull.saved_clock_offset(run_dir, now=1_760_000_000.0 + backup_pull.OFFSET_MAX_AGE_S + 1) is None
    status.write({"offset_s": None})
    assert backup_pull.saved_clock_offset(run_dir, now=1_760_000_100.0) is None
    status.write({"offset_s": True})
    assert backup_pull.saved_clock_offset(run_dir, now=1_760_000_100.0) is None


def test_backup_cli_explains_an_auth_refusal(tmp_path: Path) -> None:
    def refuse(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "signature"})

    http = NodeHttp(BASE, dp.generate_signing_key(), transport=httpx.MockTransport(refuse))
    err = io.StringIO()
    code = backup_pull.cli_main(["--dest", str(tmp_path / "dest")], http_factory=lambda: (http, tmp_path / "run"),
                                out=io.StringIO(), err=err)
    lines = err.getvalue().splitlines()
    assert code == 1 and lines[0] == "ERROR=http_auth" and lines[1].startswith("HINT=") and "clock" in lines[1]


# --- a failed file is neither charged nor downloaded on every pass (backoff) ------------------------------------

def test_a_blocked_folder_is_never_charged_and_backs_off(bridge: FakeBridge, local: dict[str, Path],
                                                         tmp_path: Path) -> None:
    (local["shared"] / "QR_Codes").write_bytes(b"a plain file where the folder should be")
    bridge.put("shared", "QR_Codes/code.png", b"q" * 1000)
    charged: list[int] = []
    tried: list[int] = []
    clock = {"t": 1000.0}
    mirror_ = make_mirror(bridge, local, tmp_path, note_bytes=lambda size: charged.append(size) or None,
                          mono=lambda: clock["t"])
    real_target_dir = mirror_._target_dir
    mirror_._target_dir = lambda root, components: tried.append(1) or real_target_dir(root, components)
    first = mirror_.sync_area("shared", full=True)
    assert (first.failed, first.copied) == (1, 0)
    for _ in range(2):  # the 20 s passes inside the first backoff step: no new try
        clock["t"] += mirror.POLL_S
        assert mirror_.sync_area("shared", full=True).backoff == 1
    assert len(tried) == 1
    clock["t"] = 1000.0 + mirror.BACKOFF_FIRST_S
    assert mirror_.sync_area("shared", full=True).failed == 1  # one try per step
    clock["t"] += mirror.BACKOFF_FIRST_S  # the step doubled: still waiting
    assert mirror_.sync_area("shared", full=True).backoff == 1
    assert len(tried) == 2
    assert charged == []  # the byte cap is charged only right before a download
    assert not [request for request in bridge.requests if request[0] == "/vortex/media/file"]
    # the cursor is still held below the file, so it arrives once the folder is fixed
    assert mirror_.cursor("shared") * 1e9 < (bridge.shared / "QR_Codes" / "code.png").stat().st_mtime_ns


def test_a_body_that_never_matches_its_sha256_is_not_downloaded_every_pass(bridge: FakeBridge, local: dict[str, Path],
                                                                         tmp_path: Path) -> None:
    bridge.put("shared", "QR_Codes/code.png", b"real" * 10)
    bridge.tamper["QR_Codes/code.png"] = b"evil" * 10  # the same size, another sha256 than the index says
    charged: list[int] = []
    clock = {"t": 1000.0}
    mirror_ = make_mirror(bridge, local, tmp_path, note_bytes=lambda size: charged.append(size) or None,
                          mono=lambda: clock["t"])

    def downloads() -> int:
        return len([request for request in bridge.requests if request[0] == "/vortex/media/file"])

    delay, seen = mirror.BACKOFF_FIRST_S, []
    for _ in range(4):
        assert mirror_.sync_area("shared", full=True).failed == 1
        seen.append(downloads())
        clock["t"] += mirror.POLL_S  # the next pass: no second download
        assert mirror_.sync_area("shared", full=True).backoff == 1
        assert downloads() == seen[-1]
        clock["t"] += delay  # the next step, doubled each time
        delay = min(delay * 2, mirror.BACKOFF_MAX_S)
    assert seen == [1, 2, 3, 4] and charged == [40] * 4
    assert not [p for p in local["shared"].rglob("*") if p.is_file()]
    # a new version of the file (another size or mtime) is tried at once
    del bridge.tamper["QR_Codes/code.png"]
    bridge.put("shared", "QR_Codes/code.png", b"fixed" * 10, age_s=30)
    assert mirror_.sync_area("shared", full=True).copied == 1


def test_a_cap_trip_pauses_longer_each_time(bridge: FakeBridge, local: dict[str, Path], tmp_path: Path) -> None:
    bridge.put("shared", "QR_Codes/a.png", b"a" * 10)
    clock = {"t": 1000.0}
    trips: list[int] = []
    allow = {"ok": False}

    def note(size: int) -> str | None:
        if allow["ok"]:
            return None
        trips.append(size)
        return "DESKTOP_RATE_LIMITED"

    mirror_ = make_mirror(bridge, local, tmp_path, note_bytes=note, mono=lambda: clock["t"])
    pauses = []
    for _ in range(3):
        mirror_.sync_once()
        pauses.append(mirror_._paused_until - clock["t"])
        assert mirror_.sync_once() == {}  # paused: nothing listed, nothing charged
        clock["t"] = mirror_._paused_until
    assert pauses == [mirror.RATE_PAUSE_FIRST_S, mirror.RATE_PAUSE_FIRST_S * 2, mirror.RATE_PAUSE_FIRST_S * 4]
    assert len(trips) == 3
    allow["ok"] = True
    assert mirror_.sync_once()["shared"].copied == 1
    assert mirror_._rate_pause_s == 0.0  # a copy resets the pause


def test_fetch_of_a_shell_parsed_name_is_refused(fetch_env: Any) -> None:
    for relpath in ("Downloads/link.lnk", "Downloads/site.url", "QR_Codes/x.library-ms", "QR_Codes/desktop.ini"):
        data = b"[InternetShortcut]\r\nIconFile=\\\\192.0.2.1\\share\\i.ico\r\n"
        fetch_env.bridge.put("shared", relpath, data)
        result = asyncio.run(fetch_env.caps.fetch(fetch_args(relpath, data)))
        assert result["status"] == "error" and result["error_code"] == "BAD_ARGS", relpath
    assert fetch_env.opened == [] and fetch_env.revealed == []
    assert not [p for p in fetch_env.local["shared"].rglob("*") if p.is_file()]
    # and the mirror never copies one either
    report = fetch_env.caps._mirror().sync_area("shared", full=True)
    assert report.refused == 4 and report.copied == 0


def test_a_large_fetch_with_voice_unknown_says_so(fetch_env: Any, bridge: FakeBridge, local: dict[str, Path],
                                                  tmp_path: Path) -> None:
    data = b"L" * 300
    bridge.put("shared", "Downloads/big.bin", data)
    mirror_ = make_mirror(bridge, local, tmp_path, voice=lambda: None, large_bytes=100)
    caps = capabilities.Capabilities(fetch_env.node, mirror=lambda: mirror_, open_file=fetch_env.opened.append,
                                     reveal_file=fetch_env.revealed.append)
    saved = asyncio.run(caps.fetch(fetch_args("Downloads/big.bin", data, open_=False)))
    assert saved["status"] == "error" and saved["error_code"] == "DELIVERY_FAILED"
    assert "voice session ke baad" not in saved["message"] and "khol kar" in saved["message"]
    assert mirror_.sync_area("shared", full=True).deferred == 1  # unknown counts as active for the mirror


# --- the deliveries cap: a file that can never fit, a file that does not fit now, refused names -----------------

class _CapNode:
    """A node whose policy is the real K-76 RateLimiter with a small deliveries cap; it counts the trips of
    DesktopPolicy.note_bytes (each one raises a toast, a warning alert and a desktop_cap_tripped audit line)."""

    def __init__(self, cap_bytes: int) -> None:
        from src.core.desktop import policy as policy_mod  # pylint: disable=import-outside-toplevel

        self.caps = policy_mod.RateLimiter()
        self.caps.BYTE_LIMITS = {"uploads": (10_000, 3600.0), "deliveries": (cap_bytes, 86400.0)}
        self.trips: list[str] = []
        self.policy = types.SimpleNamespace(_caps=self.caps, note_bytes=self.note_bytes)

    def note_bytes(self, bucket: str, size: int) -> str | None:
        code = self.caps.note_bytes(bucket, size)
        if code:
            self.trips.append(code)
        return code


def test_the_deliveries_cap_comes_from_the_node_policy() -> None:
    assert mirror.deliveries_cap(_CapNode(1000)) == 1000
    assert mirror.deliveries_cap(types.SimpleNamespace()) == 5 * 1024 * 1024 * 1024 == mirror.DELIVERIES_CAP_BYTES
    assert {"over_cap", "size"} <= mirror.PERMANENT_REASONS


def test_a_file_over_the_whole_cap_never_stalls_newer_files(bridge: FakeBridge, local: dict[str, Path],
                                                            tmp_path: Path) -> None:
    """A 2000-byte download listed before a newer 10-byte QR code, with a 1000-byte deliveries cap: the big file
    is refused once (never charged, so no toast, alert or cap audit line), the QR code is copied, and later
    passes neither retry nor re-audit the big one."""
    node = _CapNode(1000)
    bridge.put("shared", "Downloads/YouTube/huge.mp4", b"H" * 2000, age_s=120)
    bridge.put("shared", "QR_Codes/after.png", b"q" * 10, age_s=60)
    clock = {"t": 1000.0}
    audit: list[tuple[str, dict[str, Any]]] = []
    mirror_ = make_mirror(bridge, local, tmp_path, note_bytes=lambda size: node.note_bytes("deliveries", size),
                          audit=audit, mono=lambda: clock["t"], delivery_cap=mirror.deliveries_cap(node))
    copied = 0
    for _ in range(6):
        reports = mirror_.sync_once()
        assert reports, "never paused"
        copied += sum(report.copied for report in reports.values())
        clock["t"] += mirror.RATE_PAUSE_MAX_S + 1
    assert copied == 1
    assert (local["shared"] / "QR_Codes" / "after.png").read_bytes() == b"q" * 10
    assert not (local["shared"] / "Downloads" / "YouTube" / "huge.mp4").exists()
    assert node.trips == []
    assert [fields["reason"] for event, fields in audit if event == "mirror_refused"] == ["over_cap"]
    assert mirror_._rate_pause_s == 0.0
    cursor = mirror_.cursor("shared")
    assert cursor is not None and cursor > bridge.now - 60  # the refused file does not hold the cursor
    # pc.fetch of it fails with DELIVERY_FAILED over_cap and charges nothing either
    with pytest.raises(MirrorError) as caught:
        mirror_.fetch_one("shared", "Downloads/YouTube/huge.mp4", size=2000, explicit=True)
    assert (caught.value.code, caught.value.reason) == (mirror.DELIVERY_FAILED, "over_cap")
    assert node.trips == []


def test_a_cap_trip_still_copies_smaller_files_and_pauses_once(bridge: FakeBridge, local: dict[str, Path],
                                                              tmp_path: Path) -> None:
    """With 600 of 1000 bytes used today, a 500-byte file does not fit the room left: it waits (holding the
    cursor), a newer file as large is not even tried, the newer 10-byte file and the other area are still copied,
    the cap trips once and the mirror pauses once."""
    node = _CapNode(1000)
    assert node.caps.note_bytes("deliveries", 600) is None
    bridge.put("shared", "Downloads/mid.bin", b"M" * 500, age_s=120)
    bridge.put("shared", "Downloads/mid2.bin", b"N" * 500, age_s=100)
    bridge.put("shared", "QR_Codes/small.png", b"s" * 10, age_s=60)
    bridge.put("outputs", "leads/list.csv", b"c" * 20, age_s=50)
    clock = {"t": 1000.0}
    mirror_ = make_mirror(bridge, local, tmp_path, note_bytes=lambda size: node.note_bytes("deliveries", size),
                          mono=lambda: clock["t"], delivery_cap=1000)
    reports = mirror_.sync_once()
    assert (reports["shared"].copied, reports["shared"].failed, reports["shared"].deferred) == (1, 1, 1)
    assert reports["outputs"].copied == 1
    assert len(node.trips) == 1
    assert mirror_._paused_until - clock["t"] == mirror.RATE_PAUSE_FIRST_S
    cursor = mirror_.cursor("shared")
    assert cursor is not None and cursor < bridge.now - 120  # the file that did not fit holds the cursor
    # the next pass still finds no room: one more trip, and the pause doubles although small files were copied
    clock["t"] = mirror_._paused_until
    mirror_.sync_once()
    assert len(node.trips) == 2 and mirror_._paused_until - clock["t"] == mirror.RATE_PAUSE_FIRST_S * 2
    # room again (a new day): both large files are copied and the pause resets
    node.caps._bytes["deliveries"].clear()
    clock["t"] = mirror_._paused_until
    assert mirror_.sync_once()["shared"].copied == 2
    assert mirror_._rate_pause_s == 0.0 and len(node.trips) == 2


def test_two_cap_trips_stop_the_pass(bridge: FakeBridge, local: dict[str, Path], tmp_path: Path) -> None:
    trips: list[int] = []

    def note(size: int) -> str | None:
        trips.append(size)
        return "DESKTOP_RATE_LIMITED"

    for n, size in enumerate((50, 40, 30, 20)):
        bridge.put("shared", f"Downloads/f{n}.bin", b"x" * size, age_s=100 - n)
    mirror_ = make_mirror(bridge, local, tmp_path, note_bytes=note, delivery_cap=1000)
    report = mirror_.sync_once()["shared"]
    assert trips == [50, 40] and (report.failed, report.deferred, report.copied) == (2, 2, 0)


def test_a_refused_name_is_audited_once(bridge: FakeBridge, local: dict[str, Path], tmp_path: Path) -> None:
    """While a large file holds the cursor, every incremental listing returns a refused name again: it is
    counted each time but audited only once, and the audit line never holds the name."""
    audit: list[tuple[str, dict[str, Any]]] = []
    bridge.put("shared", "Downloads/YouTube/big.mp4", b"B" * 300, age_s=120)
    bridge.put("shared", "Downloads/YouTube/secret-title.url", b"[InternetShortcut]\r\n", age_s=60)
    mirror_ = make_mirror(bridge, local, tmp_path, voice=lambda: None, large_bytes=100, audit=audit)
    reports = [mirror_.sync_area("shared", full=n == 0) for n in range(5)]
    assert [report.refused for report in reports] == [1] * 5
    assert [report.deferred for report in reports] == [1] * 5
    refusals = [fields for event, fields in audit if event == "mirror_name_refused"]
    assert len(refusals) == 1 and refusals[0]["kind"] == "shared"
    assert all("secret-title" not in str(fields) for _event, fields in audit)


# --- voice_active through the real bridge status path (K-74: the bridge pushes it to the node) -----------------

class _RecordingSio:
    def __init__(self) -> None:
        self.sent: list[tuple[str, Any, Any]] = []

    async def emit(self, event: str, data: Any = None, to: Any = None, namespace: Any = None, **_: Any) -> None:
        self.sent.append((event, data, to))

    def hints(self, sid: str) -> list[dp.BridgeHint]:
        return [dp.parse_bridge_hint(data) for event, data, to in self.sent if event == "hint" and to == sid]


def _bridge_voice_session(node_ident: str) -> tuple[list[dp.BridgeHint], list[dp.BridgeHint]]:
    """Drives the real bridge DesktopLink: a node welcome, a voice turn, then a minute of quiet. Returns the hints
    the node socket and an agent peer socket received."""
    from src.core import bridge_desktop  # pylint: disable=import-outside-toplevel

    clock = {"wall": 1_760_000_000.0, "mono": 5000.0}
    sio = _RecordingSio()

    async def no_ui(*_args: Any) -> None:
        return None

    link = bridge_desktop.DesktopLink(sio=sio, store=lambda: None, emit_ui=no_ui, wall=lambda: clock["wall"],
                                      mono=lambda: clock["mono"])
    receiver = types.SimpleNamespace(bind=lambda *_a: None, peer=None)
    node_peer = bridge_desktop.LinkPeer(sid="sid-node", role=bridge_desktop.ROLE_PC, ident=node_ident,
                                        ip="198.51.100.7", receiver=receiver, since=clock["mono"])
    agent_peer = bridge_desktop.LinkPeer(sid="sid-agent", role=bridge_desktop.ROLE_AGENT, ident="agent:svc",
                                         ip="172.18.0.9", receiver=receiver, since=clock["mono"])
    link.peers = {node_peer.sid: node_peer, agent_peer.sid: agent_peer}
    link.node_sid, link.node_id, link.mode = node_peer.sid, node_ident.partition(":")[2], "full"

    async def session() -> None:
        link.last_seen, link._seen_mono = clock["wall"], clock["mono"]
        await link._welcome_node(node_peer)  # what _node_hello sends: the welcome and a status push
        await link._status_changed()
        link.note_voice_activity()  # a voice turn
        await link._status_changed()
        clock["wall"] += bridge_desktop.VOICE_ACTIVE_S + 1
        clock["mono"] += bridge_desktop.VOICE_ACTIVE_S + 1
        link.last_seen, link._seen_mono = clock["wall"], clock["mono"]  # the node's heartbeat keeps it online
        await link._status_changed()  # voice is idle again

    asyncio.run(session())
    return sio.hints(node_peer.sid), sio.hints(agent_peer.sid)


def _mirror_after_voice(runtime: Any, hints: list[dp.BridgeHint], bridge: FakeBridge, local: dict[str, Path],
                        tmp_path: Path) -> mirror.SyncReport:
    node, clock = runtime
    connect(node, "s" * 32)
    for item in hints:
        asyncio.run(node.handle_hint(item))
    clock["t"] += mirror.STATUS_FRESH_S + 600  # long after the welcome
    mirror_ = make_mirror(bridge, local, tmp_path, voice=lambda: mirror.voice_state(node), large_bytes=100)
    bridge.put("shared", "Downloads/YouTube/video.mp4", b"V" * 500, age_s=30)
    return mirror_.sync_area("shared", full=True)


def test_the_bridge_status_body_pushed_to_the_node_lets_a_large_file_mirror(runtime: Any, bridge: FakeBridge,
                                                                            local: dict[str, Path],
                                                                            tmp_path: Path) -> None:
    """The node side of the K-74 push: the status hints the real bridge builds (as an agent peer gets them),
    readdressed to the node, keep voice_active trusted for the session, so a large file mirrors after the 20 s
    window once voice is idle."""
    node_hints, agent_hints = _bridge_voice_session("pc:laptop-test")
    assert [h.typ for h in node_hints][:1] == ["welcome"]
    statuses = [h for h in agent_hints if h.typ == "status"]
    assert [h.body["desktop_status"]["voice_active"] for h in statuses][-2:] == [True, False]
    readdressed = [node_hints[0]] + [hint("status", dict(h.body)) for h in statuses]
    report = _mirror_after_voice(runtime, readdressed, bridge, local, tmp_path)
    assert (report.copied, report.deferred) == (1, 0)
    assert mirror.voice_state(runtime[0]) is False


def test_the_real_bridge_pushes_voice_status_to_the_node(runtime: Any, bridge: FakeBridge, local: dict[str, Path],
                                                         tmp_path: Path) -> None:
    """The hints the node socket really gets: the welcome, a status push right after it, and a status push for
    each voice_active change (on, then off a minute later), so long after the 20 s welcome window the node still
    knows voice is idle and a large file mirrors."""
    node_hints, _agent_hints = _bridge_voice_session("pc:laptop-test")
    assert [h.typ for h in node_hints] == ["welcome", "status", "status", "status"]
    assert [h.body["desktop_status"]["voice_active"] for h in node_hints] == [False, False, True, False]
    report = _mirror_after_voice(runtime, node_hints, bridge, local, tmp_path)
    assert (report.copied, report.deferred) == (1, 0)


def test_the_last_pushed_voice_state_holds_until_a_newer_hint(runtime: Any, bridge: FakeBridge,
                                                              local: dict[str, Path], tmp_path: Path) -> None:
    """A voice session the bridge pushes as active keeps large files waiting however long it lasts (no 20 s
    expiry turns it into a guess), and the push that ends it lets them mirror."""
    node, clock = runtime
    connect(node, "s" * 32)
    asyncio.run(node.handle_hint(hint("welcome", status_body(False))))
    asyncio.run(node.handle_hint(hint("status", status_body(False))))
    asyncio.run(node.handle_hint(hint("status", status_body(True))))
    mirror_ = make_mirror(bridge, local, tmp_path, voice=lambda: mirror.voice_state(node), large_bytes=100)
    bridge.put("shared", "Downloads/YouTube/video.mp4", b"V" * 500, age_s=30)
    clock["t"] += 3600
    assert mirror.voice_state(node) is True
    assert mirror_.sync_area("shared", full=True).deferred == 1
    asyncio.run(node.handle_hint(hint("status", status_body(False))))
    clock["t"] += 3600
    assert mirror.voice_state(node) is False
    assert mirror_.sync_area("shared", full=True).copied == 1


def test_an_unknown_voice_state_defers_large_files_and_is_logged_once(bridge: FakeBridge, local: dict[str, Path],
                                                                      tmp_path: Path,
                                                                      caplog: pytest.LogCaptureFixture) -> None:
    """No hint since connect: voice counts as active, so a large file waits (and holds the cursor) while a small
    one mirrors; the unknown state is logged once, not on every pass, and again only after it was known."""
    caplog.set_level(logging.INFO, logger="JARVIS-DESKTOP.mirror")
    voice: dict[str, bool | None] = {"value": None}
    mirror_ = make_mirror(bridge, local, tmp_path, voice=lambda: voice["value"], large_bytes=100)
    bridge.put("shared", "Downloads/big-secret.bin", b"B" * 500, age_s=40)
    bridge.put("shared", "QR_Codes/small.png", b"s" * 10, age_s=30)

    def unknown_lines() -> int:
        return sum("voice state is unknown" in record.getMessage() for record in caplog.records)

    reports = [mirror_.sync_area("shared", full=n == 0) for n in range(3)]
    assert [(r.copied, r.deferred) for r in reports] == [(1, 1), (0, 1), (0, 1)]
    assert reports[-1].cursor is not None and reports[-1].cursor < bridge.now - 40
    assert unknown_lines() == 1
    assert "big-secret" not in caplog.text and "Downloads" not in caplog.text
    voice["value"] = False
    assert mirror_.sync_area("shared").copied == 1
    voice["value"] = None
    bridge.now += 30  # a newer file, listed after the cursor
    bridge.put("shared", "Downloads/big2.bin", b"C" * 500, age_s=10)
    assert mirror_.sync_area("shared").deferred == 1
    assert unknown_lines() == 2
    # an explicit open still copies it
    assert mirror_.fetch_one("shared", "Downloads/big2.bin", size=500, explicit=True).read_bytes() == b"C" * 500


def test_a_node_without_any_hint_counts_voice_as_active(runtime: Any) -> None:
    node, _clock = runtime
    connect(node, "s" * 32)
    assert mirror.voice_state(node) is None and mirror.voice_active(node) is True
