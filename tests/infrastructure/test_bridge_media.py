r"""Plan task C-117: the bridge's single media path (K-73 MEDIA-RELAY + FILES + BLOBS, DASH-C21/DASH-C30).

The tests drive src/core/bridge_media.py directly with throwaway temporary trees and keys; nothing touches the
live bridge, a real mount, a real key or the project's data folder. The media relay targets the Linux bridge
container, but this module imports on Windows too, so the POSIX-only behaviour (the O_NOFOLLOW + dir_fd walk and
the symlink-swap race) is tested under skipif(os.name != 'posix'); traversal, the dot-directory exclusion (both
walkers), the signature/length gate, the TTL, first-fetch delete and the taken-blob accounting, and HTTP Range stay
cross-platform. The taken-blob accounting is also checked against a real uvicorn h11 server (the bridge image's
protocol) on an ephemeral 127.0.0.1 port, with a client that does not read, and on the live GET /internal/blob/<id>
route of src/core/ui_bridge itself (its app_asgi, imported with BRIDGE_DB_PATH in tmp_path).

The module needs only the bridge image deps (deploy/hostinger/requirements-bridge.txt) plus pytest and
pytest-asyncio, no .env and nothing outside the repo and tmp_path, so the POSIX-only tests run in a one-off
python:3.12-slim container with --confcutdir=tests/infrastructure (no parent conftest is needed). Importing
ui_bridge creates its runtime folders (logs/, conversations/, ...) under the project root, so the container runs on
a writable copy of exactly what the bridge image copies (src/, services/) plus the tests; the repo itself is mounted
read-only and never written. From the repo root in Git Bash (MSYS_NO_PATHCONV=1 and pwd -W keep MSYS from
rewriting the -v path):

  MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W)":/repo:ro -e PYTHONDONTWRITEBYTECODE=1 python:3.12-slim \
    sh -c "mkdir -p /work/tests && cp -r /repo/src /repo/services /work/ && cp -r /repo/tests/__init__.py \
    /repo/tests/infrastructure /work/tests/ && cd /work && pip install -q -r \
    /repo/deploy/hostinger/requirements-bridge.txt pytest pytest-asyncio && python -m pytest \
    tests/infrastructure/test_bridge_media.py -q -p no:cacheprovider --confcutdir=tests/infrastructure \
    -o asyncio_mode=auto"

In PowerShell: docker run --rm -v "${PWD}:/repo:ro" -e PYTHONDONTWRITEBYTECODE=1 python:3.12-slim sh -c
"<the same sh command>". Expected: every test passes and none is skipped.
"""
# pylint: disable=redefined-outer-name
from __future__ import annotations

import asyncio
import hashlib
import os
import time
from pathlib import Path
from typing import Any

import pytest

from services.utils import desktop_protocol as dp
from src.core import bridge_media as bm

POSIX_ONLY = pytest.mark.skipif(os.name != "posix", reason="O_NOFOLLOW + dir_fd and symlinks are POSIX-only")


# ---------------------------------------------------------------- helpers


def write_file(path: Path, data: bytes, *, mtime: float | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


class RecordingBody:
    """An async body iterator that records whether it was ever iterated (so a test can assert the body of an
    unsigned or oversize request is never read)."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.read = False

    async def __aiter__(self):  # type: ignore[no-untyped-def]
        self.read = True
        for chunk in self._chunks:
            yield chunk


@pytest.fixture
def relay(tmp_path):
    """A MediaRelay over a fixed clock, with shared/ and outputs/ roots under tmp_path."""
    shared = tmp_path / "shared"
    outputs = tmp_path / "outputs"
    shared.mkdir()
    outputs.mkdir()
    now = time.time()
    rel = bm.MediaRelay(shared_dir=lambda: str(shared), outputs_dir=lambda: str(outputs), clock=lambda: now)
    return rel, shared, outputs, now


# ---------------------------------------------------------------- relpath validation (traversal)


@pytest.mark.parametrize("relpath", [
    "..", "../etc/passwd", "Generated_Images/../../secret", "/etc/passwd",
    "Generated_Images/\x00evil", "Generated_Images/a\\b", "C:/x", "Generated_Images/./x", "",
])
def test_safe_components_refuses_traversal(relpath):
    with pytest.raises(bm.MediaError) as exc:
        bm.safe_components(relpath)
    assert exc.value.status == 400 and exc.value.reason == "traversal"


def test_the_module_imports_without_config_dotenv_or_the_bridge_app():
    """The relay (and these tests) must run in a plain Linux python with only the bridge image deps: importing it
    pulls no config module, no .env loader and not the bridge app, only desktop_protocol."""
    import subprocess
    import sys

    repo = Path(__file__).resolve().parents[2]
    probe = ("import sys, src.core.bridge_media; "
             "print(','.join(sorted(m for m in sys.modules if m.split('.')[0] in "
             "('services', 'src', 'dotenv', 'livekit', 'socketio', 'fastapi'))))")
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    out = subprocess.run([sys.executable, "-c", probe], cwd=str(repo), env=env, capture_output=True, text=True,
                         timeout=60, check=True).stdout.strip()
    assert set(out.split(",")) == {"services", "services.utils", "services.utils.desktop_protocol", "src",
                                   "src.core", "src.core.bridge_media"}


def test_safe_components_accepts_a_normal_relpath():
    assert bm.safe_components("Generated_Images/sub/a.png") == ["Generated_Images", "sub", "a.png"]


def test_open_file_refuses_traversal_and_disallowed_subtrees(relay):
    rel, shared, _outputs, _now = relay
    write_file(shared / "Generated_Images" / "ok.png", b"x")
    write_file(shared / "Secret" / "hidden.txt", b"x")          # outside the allowed subtrees
    write_file(shared / "sim_lookup_history.jsonl", b"secret")  # an always-excluded name
    for bad in ("../x", "Secret/hidden.txt", "sim_lookup_history.jsonl", "Generated_Images"):
        with pytest.raises(bm.MediaError) as exc:
            rel.open_file(bm.AREA_SHARED, bad)
        assert exc.value.status in (400, 404)
    with pytest.raises(bm.MediaError):
        rel.open_file("bogus-area", "Generated_Images/ok.png")


# ---------------------------------------------------------------- index: entries and exclusions


def test_index_lists_allowed_files_with_entries_and_applies_exclusions(relay):
    rel, shared, outputs, now = relay
    old = now - 100
    content = b"hello-image-bytes"
    write_file(shared / "Generated_Images" / "a.png", content, mtime=old)
    write_file(shared / "Downloads" / "YouTube" / "clip.mp4", b"video", mtime=old)
    write_file(shared / "QR_Codes" / ".hidden.png", b"x", mtime=old)        # dotfile: excluded
    write_file(shared / "Downloads" / "part.bin.part", b"x", mtime=old)     # .part: excluded
    write_file(shared / "Downloads" / "scratch.tmp", b"x", mtime=old)       # .tmp: excluded
    write_file(shared / "Downloads" / "fresh.png", b"x", mtime=now)         # < 5 s old: excluded
    write_file(shared / "Secret" / "nope.png", b"x", mtime=old)             # not an allowed subtree
    write_file(outputs / "run.zip", b"zip", mtime=old)                      # outputs top-level *.zip: allowed

    index = rel.build_index(bm.AREA_SHARED)
    relpaths = {entry["relpath"] for entry in index["entries"]}
    assert relpaths == {"Generated_Images/a.png", "Downloads/YouTube/clip.mp4"}
    assert index["cursor"] == pytest.approx(now - 5.0, abs=0.01)

    entry = next(e for e in index["entries"] if e["relpath"] == "Generated_Images/a.png")
    assert set(entry) >= {"seq", "relpath", "size", "mtime_ns"}
    assert entry["size"] == len(content)
    assert entry["mtime_ns"] == int(old * 1_000_000_000) or abs(entry["mtime_ns"] - int(old * 1e9)) < 1_000_000
    assert entry["sha256"] == hashlib.sha256(content).hexdigest()
    assert [e["seq"] for e in index["entries"]] == sorted(e["seq"] for e in index["entries"])

    outputs_index = rel.build_index(bm.AREA_OUTPUTS)
    assert {e["relpath"] for e in outputs_index["entries"]} == {"run.zip"}


_DOT_DIR_FILES = (
    ("shared", "Downloads/.thumbnails/x.png"),
    ("shared", "Downloads/.cache/y.bin"),
    ("shared", "Generated_Images/sub/.hidden/z.png"),
    ("shared", "Downloads/YouTube/.git/objects/q.bin"),
    ("outputs", "Downloads/.cache/r.bin"),
    ("outputs", "leads/.private/l.csv"),
)


def _force_walker(monkeypatch, walker: str) -> None:
    """'native' keeps the platform's own walk (dir_fd on POSIX, lexical on Windows); 'lexical' forces the
    Windows-only fallback on any OS, so both walkers are exercised on Linux and the lexical one on Windows."""
    if walker == "lexical":
        monkeypatch.setattr(bm, "_HAS_DIRFD", False)
        monkeypatch.setattr(bm, "_LEXICAL_OK", True)


@pytest.mark.parametrize("walker", ["native", "lexical"])
def test_files_inside_a_dot_directory_are_never_listed_or_served(relay, monkeypatch, walker):
    """K-73 excludes dotfiles: every path component counts, so a regular file inside a dot-directory is neither
    listed (the node would mirror it) nor served, while its visible siblings still are."""
    rel, shared, outputs, now = relay
    _force_walker(monkeypatch, walker)
    roots = {"shared": shared, "outputs": outputs}
    for area, relpath in _DOT_DIR_FILES:
        write_file(roots[area].joinpath(*relpath.split("/")), b"hidden", mtime=now - 100)
    write_file(shared / "Downloads" / "visible.png", b"v", mtime=now - 100)
    write_file(shared / "Generated_Images" / "sub" / "shown.png", b"s", mtime=now - 100)
    write_file(outputs / "leads" / "list.csv", b"l", mtime=now - 100)

    assert {e["relpath"] for e in rel.build_index(bm.AREA_SHARED)["entries"]} == {
        "Downloads/visible.png", "Generated_Images/sub/shown.png"}
    assert {e["relpath"] for e in rel.build_index(bm.AREA_OUTPUTS)["entries"]} == {"leads/list.csv"}

    for area, relpath in _DOT_DIR_FILES:
        with pytest.raises(bm.MediaError) as exc:
            rel.open_file(area, relpath)          # the file exists and is regular, but is refused
        assert exc.value.status == 404
    fd, _st = rel.open_file(bm.AREA_SHARED, "Generated_Images/sub/shown.png")
    os.close(fd)


@pytest.mark.parametrize("walker", ["native", "lexical"])
def test_the_walk_never_enters_a_dot_directory(relay, monkeypatch, walker):
    rel, shared, _outputs, now = relay
    _force_walker(monkeypatch, walker)
    write_file(shared / "Downloads" / ".cache" / "deep" / "y.bin", b"x", mtime=now - 100)
    write_file(shared / "Downloads" / "plain" / "ok.bin", b"x", mtime=now - 100)
    entered: list[str] = []
    real_child, real_walk = bm._open_child_dir, bm.MediaRelay._walk_lexical   # pylint: disable=protected-access

    def recording_child(name: str, parent_fd: int) -> int:
        entered.append(name)
        return real_child(name, parent_fd)

    def recording_walk(self, path: str, prefix: str, ctx: Any, depth: int) -> None:  # type: ignore[no-untyped-def]
        entered.append(os.path.basename(path))
        real_walk(self, path, prefix, ctx, depth)

    monkeypatch.setattr(bm, "_open_child_dir", recording_child)
    monkeypatch.setattr(bm.MediaRelay, "_walk_lexical", recording_walk)
    assert {e["relpath"] for e in rel.build_index(bm.AREA_SHARED)["entries"]} == {"Downloads/plain/ok.bin"}
    assert "plain" in entered                                  # the walk did descend into normal directories
    assert ".cache" not in entered and "deep" not in entered   # but never into the dot-directory


def _short_name(path: Path) -> str:
    """The NTFS 8.3 short name of `path` ('' off Windows, or when the volume does not generate short names)."""
    if os.name != "nt":
        return ""
    import ctypes   # Windows-only API, imported here so the module stays importable on Linux

    buf = ctypes.create_unicode_buffer(32768)
    if not ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, len(buf)):  # type: ignore[attr-defined]
        return ""
    short = os.path.basename(buf.value)
    return "" if short == path.name else short


@pytest.mark.parametrize("walker", ["native", "lexical"])
def test_an_aliased_relpath_never_opens_a_hidden_or_excluded_file(relay, monkeypatch, walker):
    """NTFS opens a file by an alias the literal name checks never see: another letter case, trailing dots or
    spaces, or an 8.3 short name (THUMBN~1 for .thumbnails). open_file must refuse every alias of a hidden or
    excluded file, and the exact real name is the only way to a visible one. On a case-sensitive POSIX file system
    the aliases simply do not exist, so the refusals hold there too."""
    rel, shared, _outputs, now = relay
    _force_walker(monkeypatch, walker)
    write_file(shared / "Downloads" / "sim_lookup_history.jsonl", b"secret", mtime=now - 100)
    write_file(shared / "Downloads" / ".thumbnails" / "x.png", b"hidden", mtime=now - 100)
    write_file(shared / "Downloads" / "Photo.png", b"ok", mtime=now - 100)
    aliases = ["Downloads/SIM_LOOKUP_HISTORY.JSONL", "Downloads/Sim_Lookup_History.jsonl",
               "Downloads/sim_lookup_history.jsonl.", "Downloads/sim_lookup_history.jsonl ",
               "Downloads/sim_lookup_history.jsonl. .", "Downloads/photo.png", "Downloads/Photo.png.",
               "Downloads/Photo.png "]
    short_dir = _short_name(shared / "Downloads" / ".thumbnails")
    short_file = _short_name(shared / "Downloads" / "sim_lookup_history.jsonl")
    if short_dir:
        aliases.append(f"Downloads/{short_dir}/x.png")
    if short_file:
        aliases.append(f"Downloads/{short_file}")
    for relpath in aliases:
        with pytest.raises(bm.MediaError) as exc:
            rel.open_file(bm.AREA_SHARED, relpath)
        assert exc.value.status == 404, relpath
    assert {e["relpath"] for e in rel.build_index(bm.AREA_SHARED)["entries"]} == {"Downloads/Photo.png"}
    fd, st = rel.open_file(bm.AREA_SHARED, "Downloads/Photo.png")   # the listed real name still opens
    os.close(fd)
    assert st.st_size == 2


def test_index_cursor_returns_only_newer_files(relay):
    rel, shared, _outputs, now = relay
    write_file(shared / "Generated_Images" / "old.png", b"a", mtime=now - 100)
    write_file(shared / "Generated_Images" / "new.png", b"b", mtime=now - 10)
    index = rel.build_index(bm.AREA_SHARED, cursor=now - 50)
    assert {e["relpath"] for e in index["entries"]} == {"Generated_Images/new.png"}


def test_a_missing_mount_root_is_a_404_not_an_os_error(tmp_path):
    rel = bm.MediaRelay(shared_dir=lambda: str(tmp_path / "absent"), outputs_dir=lambda: str(tmp_path / "absent"))
    with pytest.raises(bm.MediaError) as exc:
        rel.open_file(bm.AREA_SHARED, "Generated_Images/a.png")
    assert exc.value.status == 404
    assert rel.build_index(bm.AREA_SHARED)["entries"] == []


def test_the_walk_and_the_open_stop_at_the_depth_bound(relay):
    rel, shared, _outputs, now = relay
    deep_dirs = ["d"] * bm._MAX_DEPTH          # pylint: disable=protected-access
    ok_rel = "/".join(["Generated_Images", *deep_dirs[:-1], "ok.png"])            # at the bound
    too_deep_rel = "/".join(["Generated_Images", *deep_dirs, "deep.png"])         # one level past it
    write_file(shared.joinpath(*ok_rel.split("/")), b"ok", mtime=now - 100)
    write_file(shared.joinpath(*too_deep_rel.split("/")), b"deep", mtime=now - 100)
    assert {e["relpath"] for e in rel.build_index(bm.AREA_SHARED)["entries"]} == {ok_rel}
    fd, _st = rel.open_file(bm.AREA_SHARED, ok_rel)
    os.close(fd)
    with pytest.raises(bm.MediaError) as exc:
        rel.open_file(bm.AREA_SHARED, too_deep_rel)
    assert exc.value.status == 404


def test_index_digests_are_cached_until_the_file_changes(relay, monkeypatch):
    rel, shared, _outputs, now = relay
    path = shared / "Generated_Images" / "a.png"
    write_file(path, b"first", mtime=now - 100)
    calls = {"n": 0}
    hashers = ("_hash_regular_posix", "_hash_regular_lexical")
    for name in hashers:
        real = getattr(bm, name)

        def counting(*args, _real=real, **kwargs):  # type: ignore[no-untyped-def]
            calls["n"] += 1
            return _real(*args, **kwargs)
        monkeypatch.setattr(bm, name, counting)
    first = rel.build_index(bm.AREA_SHARED)["entries"][0]["sha256"]
    again = rel.build_index(bm.AREA_SHARED)["entries"][0]["sha256"]
    assert first == again == hashlib.sha256(b"first").hexdigest()
    assert calls["n"] == 1                       # the second listing reused the digest
    write_file(path, b"second!", mtime=now - 50)   # new size and mtime: a new key, so it is hashed again
    assert rel.build_index(bm.AREA_SHARED)["entries"][0]["sha256"] == hashlib.sha256(b"second!").hexdigest()
    assert calls["n"] == 2


# ---------------------------------------------------------------- HTTP Range


def test_parse_range():
    assert bm.parse_range(None, 100) is None
    assert bm.parse_range("bytes=10-19", 100) == (10, 19)
    assert bm.parse_range("bytes=50-", 100) == (50, 99)
    assert bm.parse_range("bytes=-10", 100) == (90, 99)
    assert bm.parse_range("bytes=0-999", 100) == (0, 99)
    for bad in ("bytes=200-", "bytes=abc", "items=0-1", "bytes=-0", "bytes=+5-10", "bytes=5--3", "bytes=-",
                "bytes=20-10", "bytes=" + "9" * 40 + "-", "bytes=-" + "9" * 40, "bytes=١-٢"):
        with pytest.raises(bm.MediaError) as exc:
            bm.parse_range(bad, 100)
        assert exc.value.status == 416
    with pytest.raises(bm.MediaError) as exc:
        bm.parse_range("bytes=0-0", 0)                 # an empty file has no satisfiable range
    assert exc.value.status == 416
    assert bm.parse_range("bytes=-500", 100) == (0, 99)   # a suffix longer than the file is the whole file


def test_open_file_and_stream_a_range(relay):
    rel, shared, _outputs, now = relay
    content = bytes(range(100))
    write_file(shared / "Generated_Images" / "pic.bin", content, mtime=now - 100)
    fd, st = rel.open_file(bm.AREA_SHARED, "Generated_Images/pic.bin")
    assert st.st_size == 100
    start, end = bm.parse_range("bytes=10-19", st.st_size)
    got = b"".join(bm.stream_fd(fd, start, end - start + 1))
    assert got == content[10:20]

    fd2, _ = rel.open_file(bm.AREA_SHARED, "Generated_Images/pic.bin")
    assert b"".join(bm.stream_fd(fd2, 0, 100)) == content


# ---------------------------------------------------------------- symlink-race safety (POSIX only)


@POSIX_ONLY
def test_a_symlink_swapped_in_between_listing_and_open_is_refused(relay):
    rel, shared, _outputs, now = relay
    target = shared / "Generated_Images" / "shot.png"
    write_file(target, b"a-real-image", mtime=now - 100)
    # listing sees the real regular file
    index = rel.build_index(bm.AREA_SHARED)
    assert "Generated_Images/shot.png" in {e["relpath"] for e in index["entries"]}
    # a symlink is swapped in at the same relpath (the classic race): the open must refuse it
    target.unlink()
    os.symlink("/proc/1/environ", target)
    with pytest.raises(bm.MediaError) as exc:
        rel.open_file(bm.AREA_SHARED, "Generated_Images/shot.png")
    assert exc.value.status == 404


@POSIX_ONLY
def test_symlinks_are_never_listed(relay):
    rel, shared, _outputs, now = relay
    write_file(shared / "Generated_Images" / "real.png", b"x", mtime=now - 100)
    os.symlink("/etc/hostname", shared / "Generated_Images" / "link.png")
    os.symlink("/etc", shared / "Generated_Images" / "linkdir")
    index = rel.build_index(bm.AREA_SHARED)
    assert {e["relpath"] for e in index["entries"]} == {"Generated_Images/real.png"}


@POSIX_ONLY
def test_a_symlinked_path_component_is_refused(relay):
    rel, shared, _outputs, now = relay
    (shared / "Generated_Images").mkdir(parents=True)
    os.symlink("/etc", shared / "Generated_Images" / "evildir")
    with pytest.raises(bm.MediaError):
        rel.open_file(bm.AREA_SHARED, "Generated_Images/evildir/hostname")


@POSIX_ONLY
def test_the_race_safe_dir_fd_path_is_active_on_posix():
    # os.lstat is never in os.supports_dir_fd; the gate must use os.stat(dir_fd=, follow_symlinks=False), or every
    # open and walk on the Linux bridge would silently take the race-unsafe lexical fallback.
    assert bm._HAS_DIRFD is True
    assert bm._LEXICAL_OK is False


def _forbid_lexical(monkeypatch) -> None:
    def boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("the lexical fallback must never run on POSIX")
    monkeypatch.setattr(bm, "_open_regular_lexical", boom)
    monkeypatch.setattr(bm.MediaRelay, "_walk_lexical", boom)
    monkeypatch.setattr(bm.MediaRelay, "_descend_lexical", boom)
    monkeypatch.setattr(bm, "_hash_regular_lexical", boom)


def _swap_dir_for_symlink_on_open(monkeypatch, victim: Path, link_target: str) -> dict[str, bool]:
    """Patches the dir-fd opener so that, at the moment `victim` is opened (after every check before it), the
    directory is moved away and a symlink to `link_target` is put in its place: the check-then-open race."""
    real_open_child = bm._open_child_dir
    state = {"swapped": False}

    def racing_open_child(name: str, parent_fd: int) -> int:
        if name == victim.name and not state["swapped"]:
            victim.rename(victim.with_name(victim.name + "_moved"))
            os.symlink(link_target, victim)
            state["swapped"] = True
        return real_open_child(name, parent_fd)

    monkeypatch.setattr(bm, "_open_child_dir", racing_open_child)
    return state


@POSIX_ONLY
@pytest.mark.parametrize("target", ["outside", "/proc/self"])
def test_an_intermediate_dir_swapped_for_a_symlink_mid_open_is_refused(relay, tmp_path, monkeypatch, target):
    rel, shared, _outputs, now = relay
    outside = tmp_path / "outside"
    write_file(outside / "environ", b"SECRET=never-served", mtime=now - 100)
    link_target = str(outside) if target == "outside" else target
    if not os.path.isdir(link_target):
        pytest.skip("no /proc on this POSIX host")
    sub = shared / "Generated_Images" / "sub"
    write_file(sub / "environ", b"a-real-file", mtime=now - 100)
    _forbid_lexical(monkeypatch)
    state = _swap_dir_for_symlink_on_open(monkeypatch, sub, link_target)
    with pytest.raises(bm.MediaError) as exc:
        rel.open_file(bm.AREA_SHARED, "Generated_Images/sub/environ")
    assert state["swapped"] is True          # the swap really happened between the check and the open
    assert exc.value.status == 404


@POSIX_ONLY
def test_an_intermediate_dir_swapped_for_a_symlink_mid_walk_is_not_listed(relay, tmp_path, monkeypatch):
    rel, shared, _outputs, now = relay
    outside = tmp_path / "outside"
    write_file(outside / "secret.png", b"SECRET", mtime=now - 100)
    write_file(shared / "Generated_Images" / "keep.png", b"ok", mtime=now - 100)
    sub = shared / "Generated_Images" / "sub"
    write_file(sub / "inner.png", b"inner", mtime=now - 100)
    _forbid_lexical(monkeypatch)
    state = _swap_dir_for_symlink_on_open(monkeypatch, sub, str(outside))
    index = rel.build_index(bm.AREA_SHARED)
    assert state["swapped"] is True
    assert {e["relpath"] for e in index["entries"]} == {"Generated_Images/keep.png"}


@POSIX_ONLY
@pytest.mark.parametrize("kind", ["fifo", "symlink"])
def test_hashing_never_follows_a_symlink_or_blocks_on_a_fifo(tmp_path, kind):
    import threading

    real = tmp_path / "real.bin"
    real.write_bytes(b"real-bytes")
    swapped = tmp_path / "swapped"
    if kind == "fifo":
        os.mkfifo(swapped)              # a FIFO with no writer would block a plain open() forever
    else:
        os.symlink(real, swapped)
    results: dict[str, Any] = {}

    def run() -> None:
        dir_fd = os.open(str(tmp_path), os.O_RDONLY | os.O_DIRECTORY)
        try:
            results["posix"] = bm._hash_regular_posix("swapped", dir_fd, 10)
        finally:
            os.close(dir_fd)
        results["lexical"] = bm._hash_regular_lexical(str(swapped), 10)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout=10)
    assert not worker.is_alive(), "hashing blocked"
    assert results == {"posix": None, "lexical": None}


def test_a_posix_build_without_dir_fd_refuses_instead_of_falling_back(relay, monkeypatch):
    rel, shared, _outputs, now = relay
    write_file(shared / "Generated_Images" / "pic.png", b"x", mtime=now - 100)
    monkeypatch.setattr(bm, "_HAS_DIRFD", False)
    monkeypatch.setattr(bm, "_LEXICAL_OK", False)
    for call in (lambda: rel.open_file(bm.AREA_SHARED, "Generated_Images/pic.png"),
                 lambda: rel.build_index(bm.AREA_SHARED)):
        with pytest.raises(bm.MediaError) as exc:
            call()
        assert exc.value.status == 503 and exc.value.reason == "platform"


def test_lexical_hash_reads_only_regular_files(tmp_path):
    data = b"hash-me" * 1000
    write_file(tmp_path / "f.bin", data)
    assert bm._hash_regular_lexical(str(tmp_path / "f.bin"), len(data)) == hashlib.sha256(data).hexdigest()
    assert bm._hash_regular_lexical(str(tmp_path), 0) is None          # a directory is never hashed
    assert bm._hash_regular_lexical(str(tmp_path / "f.bin"), bm.SHA256_MAX_BYTES + 1) is None


def test_stream_fd_closes_an_unstarted_stream_when_dropped(tmp_path):
    import gc

    write_file(tmp_path / "f.bin", b"0123456789")
    fd = os.open(str(tmp_path / "f.bin"), os.O_RDONLY | getattr(os, "O_BINARY", 0))
    stream = bm.stream_fd(fd, 0, 10)   # never iterated: a client left before the headers were sent
    del stream
    gc.collect()
    with pytest.raises(OSError):
        os.fstat(fd)

    fd2 = os.open(str(tmp_path / "f.bin"), os.O_RDONLY | getattr(os, "O_BINARY", 0))
    stream2 = bm.stream_fd(fd2, 2, 5, chunk_size=2)
    assert next(stream2) == b"23"
    stream2.close()                    # an early close releases it too
    with pytest.raises(OSError):
        os.fstat(fd2)
    assert list(stream2) == []

    fd3 = os.open(str(tmp_path / "f.bin"), os.O_RDONLY | getattr(os, "O_BINARY", 0))
    assert b"".join(bm.stream_fd(fd3, 8, 100)) == b"89"   # past EOF just ends
    with pytest.raises(OSError):
        os.fstat(fd3)


# ---------------------------------------------------------------- blob store: TTL, first-fetch delete, caps


def test_blob_ttl_and_first_fetch_delete():
    clock = {"t": 1000.0}
    store = bm.BlobStore(ttl_s=900.0, clock=lambda: clock["t"])
    blob_id = store.put(b"ciphertext-bytes")
    assert store.stats()["count"] == 1
    assert store.take(blob_id) == b"ciphertext-bytes"
    assert store.take(blob_id) is None            # deleted after the first fetch
    assert store.stats()["count"] == 0

    other = store.put(b"xyz")
    clock["t"] += 901.0                            # past the TTL
    assert store.take(other) is None               # expired
    assert store.stats() == {"count": 0, "total_bytes": 0, "reserved_bytes": 0, "taken_bytes": 0, "active_uploads": 0}


def test_blob_store_caps_total_bytes():
    store = bm.BlobStore(max_total=10, clock=lambda: 0.0)
    store.put(b"12345")
    with pytest.raises(bm.MediaError) as exc:
        store.put(b"123456")                       # would exceed 10 bytes total
    assert exc.value.status == 507


def test_blob_store_limits_concurrent_uploads():
    store = bm.BlobStore(max_concurrent=2)
    assert store.acquire() is True
    assert store.acquire() is True
    assert store.acquire() is False                # only two at a time
    store.release()
    assert store.acquire() is True


def test_blob_store_take_rejects_a_malformed_id():
    store = bm.BlobStore()
    assert store.take("not-a-hex-id") is None
    assert store.take(None) is None


def test_a_taken_blob_stays_counted_until_its_bytes_are_freed():
    """take() deletes the blob from the store, but its bytes stay in RAM while the response holds them, so they
    keep counting against the total cap until the last reference is gone."""
    store = bm.BlobStore(max_total=100, clock=lambda: 0.0)
    blob_id = store.put(b"a" * 80)
    view = store.take(blob_id)
    assert store.stats() == {"count": 0, "total_bytes": 0, "reserved_bytes": 0, "taken_bytes": 80,
                             "active_uploads": 0}
    with pytest.raises(bm.MediaError) as exc:
        store.acquire(30)                          # 80 still in RAM + 30 > 100
    assert exc.value.status == 507
    with pytest.raises(bm.MediaError):
        store.put(b"b" * 30)
    assert store.take(blob_id) is None             # still a first-fetch delete
    assert bytes(view) == b"a" * 80
    del view                                       # the response body is gone: the bytes left RAM
    assert store.stats()["taken_bytes"] == 0
    assert store.acquire(30) is True


async def _collect(chunks: Any) -> list[memoryview]:
    return [chunk async for chunk in chunks]


class _Progress:
    """Counts the body bytes the server has accepted from blob_chunks() (only the test's view of the stream)."""

    def __init__(self) -> None:
        self.accepted = 0


def _blob_app(store: bm.BlobStore, *, chunk_size: int = 256 * 1024, progress: _Progress | None = None,
              one_shot: bool = False) -> Any:
    """GET /internal/blob/<id> as src/core/ui_bridge.py must serve it: take()'s view streamed through
    bm.blob_chunks() in a StreamingResponse with a Content-Length (the bridge image's own FastAPI, no ui_bridge
    import, no .env). one_shot=True is the old Response(content=view) route, kept only as the control that shows
    why the route must stream."""
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse, Response, StreamingResponse

    app = FastAPI()

    async def counted(chunks: Any) -> Any:
        async for chunk in chunks:
            yield chunk
            if progress is not None:
                progress.accepted += len(chunk)   # resumed only once the server has accepted this chunk

    @app.get("/internal/blob/{blob_id}")
    async def internal_blob(blob_id: str) -> Any:
        data = store.take(blob_id)
        if data is None:
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        if one_shot:
            return Response(content=data, media_type="application/octet-stream")
        chunks = bm.blob_chunks(data, chunk_size=chunk_size)
        return StreamingResponse(chunks if progress is None else counted(chunks),
                                 headers={"Content-Length": str(len(data))}, media_type="application/octet-stream")

    return app


def _get_scope(path: str, port: int = 5002) -> dict[str, Any]:
    # spec_version 2.3, as uvicorn reports it: Starlette then streams in a task group next to a disconnect listener.
    # 'server' is the listener's own socket address, as uvicorn sets it: 5002 is the bridge's internal listener.
    return {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
            "method": "GET", "scheme": "http", "path": path, "raw_path": path.encode(), "root_path": "",
            "query_string": b"", "headers": [], "client": ("127.0.0.1", 50000), "server": ("127.0.0.1", port)}


def _receiver(disconnected: asyncio.Event) -> Any:
    """An ASGI receive like uvicorn's: the (empty) request body first, then it waits until the client is gone."""
    state = {"body_sent": False}

    async def receive() -> dict[str, Any]:
        if not state["body_sent"]:
            state["body_sent"] = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await disconnected.wait()
        return {"type": "http.disconnect"}

    return receive


async def _run_request(app: Any, path: str, receive: Any, send: Any, *, port: int = 5002) -> None:
    """Runs one request the way uvicorn does: in a task of its own, dropped when it is done (a finished task
    keeps its cancellation state, and with it the cancelled response, only while someone still holds the task).
    The loop then runs on for a few turns, as a serving loop does, so the done callbacks it scheduled for the
    finished task have run and let go of it."""
    task = asyncio.create_task(app(_get_scope(path, port), receive, send))
    try:
        await asyncio.wait_for(task, 5)
    finally:
        del task
        for _ in range(3):
            await asyncio.sleep(0)


async def test_a_fetched_blob_is_counted_until_its_last_chunk_has_been_accepted():
    store = bm.BlobStore(max_total=100, clock=lambda: 0.0)
    app = _blob_app(store, chunk_size=32)
    body = bytes(range(80))
    blob_id = store.put(body)
    sent: list[dict[str, Any]] = []
    taken_while_sending: list[int] = []

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.body":
            taken_while_sending.append(store.stats()["taken_bytes"])
            message = {**message, "body": bytes(message["body"])}   # the server copies each chunk (as h11 does)
        sent.append(message)

    await _run_request(app, f"/internal/blob/{blob_id}", _receiver(asyncio.Event()), send)
    assert sent[0]["status"] == 200 and (b"content-length", b"80") in sent[0]["headers"]
    assert [m["body"] for m in sent[1:]] == [body[:32], body[32:64], body[64:], b""]
    assert [m["more_body"] for m in sent[1:]] == [True, True, True, False]
    assert taken_while_sending[:3] == [80, 80, 80]   # counted while every data chunk is handed over
    del sent                                       # the server is done with what it was given
    assert store.stats() == {"count": 0, "total_bytes": 0, "reserved_bytes": 0, "taken_bytes": 0,
                             "active_uploads": 0}
    assert store.acquire(100) is True              # the whole cap is free again


async def test_a_fetched_blob_is_released_when_the_client_disconnects_mid_stream():
    store = bm.BlobStore(max_total=100, clock=lambda: 0.0)
    app = _blob_app(store, chunk_size=16)
    blob_id = store.put(b"z" * 80)
    gone = asyncio.Event()
    seen: list[int] = []

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.body":
            seen.append(store.stats()["taken_bytes"])
            gone.set()                             # the client left: uvicorn's send returns, receive disconnects
            await asyncio.sleep(0.01)

    await _run_request(app, f"/internal/blob/{blob_id}", _receiver(gone), send)
    assert seen and seen[0] == 80 and len(seen) < 5   # the stream was cut off before all five chunks
    assert store.acquire(80) is True               # the dropped stream freed the blob (at once or on the gc retry)
    assert store.stats()["taken_bytes"] == 0 and store.stats()["reserved_bytes"] == 80
    assert store.take(blob_id) is None             # the blob was still deleted on that first fetch


async def test_a_fetched_blob_is_released_after_a_send_error():
    store = bm.BlobStore(max_total=100, clock=lambda: 0.0)
    app = _blob_app(store)
    blob_id = store.put(b"z" * 80)
    seen: list[int] = []

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.body":
            seen.append(store.stats()["taken_bytes"])
            raise OSError("transport gone")        # the transport fails while the body is being sent

    with pytest.raises(OSError):
        await _run_request(app, f"/internal/blob/{blob_id}", _receiver(asyncio.Event()), send)
    assert seen == [80]
    # whether the failed response was freed at once or sits in a traceback cycle, the next booking reclaims it
    assert store.acquire(80) is True
    assert store.stats()["taken_bytes"] == 0 and store.stats()["reserved_bytes"] == 80
    assert store.take(blob_id) is None


def test_blob_chunks_holds_the_view_until_exhausted_and_drops_it_when_closed():
    store = bm.BlobStore(max_total=100, clock=lambda: 0.0)
    view = store.take(store.put(b"q" * 50))
    chunks = bm.blob_chunks(view, chunk_size=20)
    del view

    async def drive() -> None:
        assert len(await chunks.__anext__()) == 20
        assert len(await chunks.__anext__()) == 20
        assert len(await chunks.__anext__()) == 10
        assert store.stats()["taken_bytes"] == 50   # the last chunk was handed over, the stream is not done yet
        with pytest.raises(StopAsyncIteration):
            await chunks.__anext__()
        assert store.stats()["taken_bytes"] == 0    # exhausted: the view is dropped and the bytes leave RAM

    asyncio.run(drive())

    early = bm.blob_chunks(store.take(store.put(b"r" * 30)), chunk_size=8)
    assert store.stats()["taken_bytes"] == 30
    asyncio.run(early.aclose())                    # closed before it ever started
    assert store.stats()["taken_bytes"] == 0


# ---------------------------------------------------------------- a real uvicorn h11 server (the bridge image's)

_BIG_BLOB = 16 * 1024 * 1024    # far above every socket and transport buffer between the two ends


class _H11Server:
    """The bridge image's uvicorn with its h11 protocol (the image installs no httptools), serving `app` on an
    ephemeral 127.0.0.1 port inside the test's event loop."""

    def __init__(self, app: Any) -> None:
        import uvicorn

        self._server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, http="h11", ws="none",
                                                     lifespan="off", log_config=None, log_level="warning",
                                                     access_log=False))
        self._task: asyncio.Task[None] | None = None
        self.port = 0

    async def __aenter__(self) -> _H11Server:
        self._task = asyncio.create_task(self._server.serve())
        deadline = time.monotonic() + 10
        while not self._server.started:
            assert not self._task.done() and time.monotonic() < deadline, "uvicorn did not start"
            await asyncio.sleep(0.01)
        self.port = self._server.servers[0].sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        self._server.should_exit = True
        assert self._task is not None
        await asyncio.wait_for(self._task, 15)


async def _slow_client(port: int, blob_id: str) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """A client that sends the request and then reads nothing until told to: a small receive buffer and a small
    StreamReader limit, so the server soon has to wait for it."""
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8192)
    sock.setblocking(False)
    await asyncio.get_running_loop().sock_connect(sock, ("127.0.0.1", port))
    reader, writer = await asyncio.open_connection(sock=sock, limit=16 * 1024)
    writer.write(f"GET /internal/blob/{blob_id} HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n".encode())
    await writer.drain()
    return reader, writer


async def _read_response(reader: asyncio.StreamReader) -> tuple[bytes, bytes]:
    head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 30)
    length = next(int(line.split(b":", 1)[1]) for line in head.split(b"\r\n")
                  if line.lower().startswith(b"content-length:"))
    return head, await asyncio.wait_for(reader.readexactly(length), 60)


async def _wait_until(predicate: Any, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return predicate()


async def test_a_streamed_blob_stays_counted_while_a_real_h11_client_has_not_read_it():
    """The bridge's real server path: uvicorn h11 copies every chunk it accepts into its transport buffer, so the
    blob must stay counted until the server has accepted the last chunk. A client that reads nothing stalls the
    stream on uvicorn's write flow control, with the blob still counted in full."""
    store = bm.BlobStore(max_total=_BIG_BLOB + 1024, max_blob=_BIG_BLOB, clock=lambda: 0.0)
    progress = _Progress()
    body = os.urandom(_BIG_BLOB)
    digest = hashlib.sha256(body).digest()
    blob_id = store.put(body)
    del body
    async with _H11Server(_blob_app(store, progress=progress)) as server:
        reader, writer = await _slow_client(server.port, blob_id)
        try:
            assert await _wait_until(lambda: progress.accepted > 0)
            # wait until the server is stuck on flow control: no chunk accepted for a while
            last, still = -1, 0
            deadline = time.monotonic() + 20
            while still < 10 and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
                still = still + 1 if progress.accepted == last else 0
                last = progress.accepted
            assert 0 < progress.accepted < _BIG_BLOB         # stalled part way: the client has not read it
            assert store.stats()["taken_bytes"] == _BIG_BLOB  # and the whole blob is still counted
            with pytest.raises(bm.MediaError) as exc:
                store.acquire(2048)                           # so the cap still refuses a new upload
            assert exc.value.status == 507

            head, received = await _read_response(reader)    # the client reads it all now
            assert head.startswith(b"HTTP/1.1 200") and hashlib.sha256(received).digest() == digest
            del received
        finally:
            writer.close()
        assert await _wait_until(lambda: store.stats()["taken_bytes"] == 0)
        assert store.acquire(_BIG_BLOB) is True
        store.release(_BIG_BLOB)


async def test_a_streamed_blob_is_released_when_a_real_h11_client_disconnects_mid_stream():
    store = bm.BlobStore(max_total=_BIG_BLOB + 1024, max_blob=_BIG_BLOB, clock=lambda: 0.0)
    progress = _Progress()
    blob_id = store.put(os.urandom(_BIG_BLOB))
    async with _H11Server(_blob_app(store, progress=progress)) as server:
        _reader, writer = await _slow_client(server.port, blob_id)
        assert await _wait_until(lambda: progress.accepted > 0)
        assert store.stats()["taken_bytes"] == _BIG_BLOB
        writer.transport.abort()                   # the client leaves before reading the body

        def booked() -> bool:                      # each try collects once if a cycle still pins the stream
            try:
                return store.acquire(_BIG_BLOB)
            except bm.MediaError:
                return False

        assert await _wait_until(booked)
        assert store.stats()["taken_bytes"] == 0 and store.take(blob_id) is None
        store.release(_BIG_BLOB)


async def test_a_one_shot_response_on_real_h11_drops_the_count_before_the_client_reads():
    """The control for the test above: the old one-shot Response(content=view) route lets uvicorn h11 copy the
    whole body into its transport buffer at once, so the blob is uncounted while the client has read nothing.
    This is why GET /internal/blob/<id> must stream through blob_chunks()."""
    store = bm.BlobStore(max_total=_BIG_BLOB + 1024, max_blob=_BIG_BLOB, clock=lambda: 0.0)
    blob_id = store.put(os.urandom(_BIG_BLOB))
    async with _H11Server(_blob_app(store, one_shot=True)) as server:
        reader, writer = await _slow_client(server.port, blob_id)
        try:
            # fetched (no longer stored) and already uncounted, while the client has not read a single byte
            assert await _wait_until(lambda: store.stats()["count"] == 0 and store.stats()["taken_bytes"] == 0)
            assert store.acquire(_BIG_BLOB) is True   # booked again although the copy still waits for the client
            store.release(_BIG_BLOB)
            _head, received = await _read_response(reader)
            assert len(received) == _BIG_BLOB
        finally:
            writer.close()


# ---------------------------------------------------------------- the live route: src/core/ui_bridge itself


@pytest.fixture
def live_bridge(monkeypatch, tmp_path):
    """The bridge app uvicorn really serves (src.core.ui_bridge.app_asgi: Socket.IO around FastAPI with its
    InternalOnly and CORS middleware), with a fresh BlobStore in place of its blob_store. Importing it needs only
    the bridge image deps; BRIDGE_DB_PATH keeps its SQLite store in tmp_path."""
    import importlib

    monkeypatch.setenv("BRIDGE_DB_PATH", str(tmp_path / "bridge-import.db"))
    module = importlib.import_module("src.core.ui_bridge")
    store = bm.BlobStore(clock=lambda: 0.0)
    monkeypatch.setattr(module, "blob_store", store)
    return module, store


async def test_the_live_internal_blob_route_streams_the_blob_and_keeps_it_counted(live_bridge):
    """GET /internal/blob/<id> of ui_bridge itself: the body goes out as several more_body chunks (never one
    whole-body message the server would copy at once), the blob stays counted in full until the last chunk has
    been accepted, and it is freed and deleted afterwards. The public listener never reaches the route."""
    module, store = live_bridge
    body = os.urandom(3 * 256 * 1024 + 123)        # above the 256 KiB stream chunk: at least two data chunks
    blob_id = store.put(body)

    public: list[dict[str, Any]] = []

    async def public_send(message: dict[str, Any]) -> None:
        public.append(message)

    await _run_request(module.app_asgi, f"/internal/blob/{blob_id}", _receiver(asyncio.Event()), public_send,
                       port=5001)
    assert public[0]["status"] == 404
    assert store.stats()["count"] == 1 and store.stats()["taken_bytes"] == 0   # not taken from the public side

    sent: list[dict[str, Any]] = []
    taken_while_sending: list[int] = []

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.body":
            taken_while_sending.append(store.stats()["taken_bytes"])
            message = {**message, "body": bytes(message["body"])}   # the server copies each chunk (as h11 does)
        sent.append(message)

    await _run_request(module.app_asgi, f"/internal/blob/{blob_id}", _receiver(asyncio.Event()), send)
    start, messages = sent[0], sent[1:]
    headers = dict(start["headers"])
    assert start["status"] == 200
    assert headers[b"content-length"] == str(len(body)).encode()
    assert headers[b"content-type"] == b"application/octet-stream"
    data = [m for m in messages if m["body"]]
    chunks = [(len(m["body"]), m.get("more_body", False)) for m in data]
    assert len(chunks) >= 2, chunks                # streamed in chunks, never one whole-body message
    assert all(more is True for _size, more in chunks), chunks
    assert messages[-1].get("more_body", False) is False
    assert b"".join(m["body"] for m in data) == body
    assert taken_while_sending[:len(data)] == [len(body)] * len(data)   # counted until the last chunk is accepted
    del sent, start, messages, data
    assert store.acquire(store.max_blob) is True   # freed once the response is done (at once or on the gc retry)
    store.release(store.max_blob)
    assert store.stats()["taken_bytes"] == 0
    assert store.take(blob_id) is None             # first-fetch delete

    missing: list[dict[str, Any]] = []

    async def missing_send(message: dict[str, Any]) -> None:
        missing.append(message)

    await _run_request(module.app_asgi, f"/internal/blob/{blob_id}", _receiver(asyncio.Event()), missing_send)
    assert missing[0]["status"] == 404             # fetched once: gone


async def test_the_live_internal_blob_route_stays_counted_while_a_real_h11_client_has_not_read_it(
        live_bridge, monkeypatch):
    """ui_bridge's route on the bridge image's real uvicorn h11 server, with a client that reads nothing at first.
    A one-shot body would be copied into the transport and uncounted within moments (see the control test above);
    the streamed body keeps the whole blob counted, so the cap still refuses a new upload, until it is sent."""
    from src.core import bridge_desktop

    module, _store = live_bridge
    store = bm.BlobStore(max_total=_BIG_BLOB + 1024, max_blob=_BIG_BLOB, clock=lambda: 0.0)
    monkeypatch.setattr(module, "blob_store", store)
    body = os.urandom(_BIG_BLOB)
    digest = hashlib.sha256(body).digest()
    blob_id = store.put(body)
    del body
    async with _H11Server(module.app_asgi) as server:
        # is_internal_scope reads INTERNAL_PORT at call time: this ephemeral listener stands in for :5002
        monkeypatch.setattr(bridge_desktop, "INTERNAL_PORT", server.port)
        reader, writer = await _slow_client(server.port, blob_id)
        try:
            assert await _wait_until(lambda: store.stats()["count"] == 0)   # fetched: no longer stored
            assert not await _wait_until(lambda: store.stats()["taken_bytes"] < _BIG_BLOB, timeout=2.0)
            with pytest.raises(bm.MediaError) as exc:
                store.acquire(2048)                           # the cap still sees the blob in RAM
            assert exc.value.status == 507

            head, received = await _read_response(reader)    # the client reads it all now
            assert head.startswith(b"HTTP/1.1 200") and hashlib.sha256(received).digest() == digest
            del received
        finally:
            writer.close()
        assert await _wait_until(lambda: store.stats()["taken_bytes"] == 0)
        assert store.acquire(_BIG_BLOB) is True
        store.release(_BIG_BLOB)


def test_a_taken_blob_pinned_by_a_reference_cycle_is_reclaimed_before_a_booking_is_refused():
    import gc

    store = bm.BlobStore(max_total=100, clock=lambda: 0.0)
    was_enabled = gc.isenabled()
    gc.disable()                                   # the collector must not run on its own during the test
    try:
        cycle: list[Any] = [store.take(store.put(b"c" * 70))]
        cycle.append(cycle)                        # a finished response kept alive only by a cycle
        del cycle
        assert store.stats()["taken_bytes"] == 70  # not freed yet: only the collector can free it
        assert store.acquire(60) is True           # 70 + 60 > 100, so acquire collects once and then fits
        assert store.stats()["taken_bytes"] == 0
        store.release(60)
        assert store.put(b"d" * 100)               # put() reclaims the same way
    finally:
        if was_enabled:
            gc.enable()


# ---------------------------------------------------------------- receive_blob: verify before reading the body


def _headers_list(mapping: dict[str, str]) -> list[tuple[bytes, bytes]]:
    return [(k.encode("latin-1"), v.encode("latin-1")) for k, v in mapping.items()]


async def test_a_signed_blob_is_stored_and_fetched_once():
    key = dp.generate_signing_key()
    store = bm.BlobStore()
    body = b"agent-sealed-ciphertext-" + b"z" * 100
    headers = dp.sign_http_request(key, method="POST", path="/desktop/blob", body=body)
    recorder = RecordingBody([body[:50], body[50:]])
    blob_id, size = await bm.receive_blob(
        public_key=key.public_key(), seen=None, headers=headers, method="POST", path="/desktop/blob",
        query=b"", content_length=len(body), body_iter=recorder, store=store)
    assert recorder.read is True and size == len(body)
    assert store.take(blob_id) == body
    assert store.take(blob_id) is None


async def test_a_received_blob_is_stored_without_a_second_copy():
    key = dp.generate_signing_key()
    store = bm.BlobStore()
    body = bytes(range(256)) * 40
    headers = dp.sign_http_request(key, method="POST", path="/desktop/blob", body=body)
    blob_id, size = await bm.receive_blob(
        public_key=key.public_key(), seen=None, headers=headers, method="POST", path="/desktop/blob",
        query=b"", content_length=len(body), body_iter=RecordingBody([body[:777], body[777:3000], body[3000:]]),
        store=store)
    assert size == len(body) and store.stats()["total_bytes"] == len(body)
    data = store.take(blob_id)
    assert isinstance(data, memoryview) and data.readonly and bytes(data) == body
    assert isinstance(data.obj, bytearray) and len(data.obj) == len(body)   # the exact preallocated buffer


def test_put_keeps_a_blob_buffer_as_given_and_copies_anything_else_once():
    store = bm.BlobStore()
    buffer = bm.new_blob_buffer(6)
    buffer[:] = b"sealed"
    view = store.take(store.put(buffer))
    assert view == b"sealed" and view.obj is buffer and view.readonly   # receive_blob's buffer: never copied
    # GET /internal/blob/<id> streams take()'s view with blob_chunks(): slices of the same buffer, never a copy
    chunks = asyncio.run(_collect(bm.blob_chunks(view, chunk_size=4)))
    assert [bytes(c) for c in chunks] == [b"seal", b"ed"] and all(c.obj is buffer for c in chunks)

    plain = bytearray(b"plain")
    other = store.take(store.put(plain))   # any other bytes-like value is copied once into a tracked buffer
    assert other == b"plain" and other.readonly and other.obj is not plain
    assert store.take(store.put(b"bytes")) == b"bytes"


async def test_a_body_shorter_than_its_signed_length_is_refused_and_released():
    key = dp.generate_signing_key()
    store = bm.BlobStore()
    body = b"q" * 100
    headers = dp.sign_http_request(key, method="POST", path="/desktop/blob", body=body)
    with pytest.raises(bm.MediaError) as exc:
        await bm.receive_blob(public_key=key.public_key(), seen=None, headers=headers, method="POST",
                              path="/desktop/blob", query=b"", content_length=len(body),
                              body_iter=RecordingBody([body[:60]]), store=store)
    assert exc.value.status == dp.HTTP_STATUS.get("length", 400)
    assert store.stats() == {"count": 0, "total_bytes": 0, "reserved_bytes": 0, "taken_bytes": 0, "active_uploads": 0}


async def test_an_unsigned_body_is_never_read():
    store = bm.BlobStore()
    recorder = RecordingBody([b"x" * 1000])
    with pytest.raises(bm.MediaError) as exc:
        await bm.receive_blob(public_key=dp.generate_signing_key().public_key(), seen=None, headers={},
                              method="POST", path="/desktop/blob", query=b"", content_length=1000,
                              body_iter=recorder, store=store)
    assert exc.value.status == 400          # missing X-Desk-* headers -> format
    assert recorder.read is False           # the body was never iterated
    assert store.stats()["active_uploads"] == 0


async def test_an_oversize_body_gets_413_before_the_body_is_read():
    oversize = 40 * 1024 * 1024
    headers = {
        "X-Desk-Ts": str(int(time.time())),
        "X-Desk-Nonce": "a" * 32,
        "X-Desk-Len": str(oversize),        # format-valid but above the 30 MiB cap
        "X-Desk-Sha256": "b" * 64,
        "X-Desk-Sig": "c" * 128,
    }
    store = bm.BlobStore()
    recorder = RecordingBody([b"x" * 4096])
    with pytest.raises(bm.MediaError) as exc:
        await bm.receive_blob(public_key=dp.generate_signing_key().public_key(), seen=None,
                              headers=_headers_list(headers), method="POST", path="/desktop/blob", query=b"",
                              content_length=oversize, body_iter=recorder, store=store)
    assert exc.value.status == 413          # the signed length cap, before the body
    assert recorder.read is False
    assert store.stats()["active_uploads"] == 0


async def test_a_body_longer_than_its_signed_length_is_cut_off():
    key = dp.generate_signing_key()
    store = bm.BlobStore()
    body = b"the-signed-body"
    headers = dp.sign_http_request(key, method="POST", path="/desktop/blob", body=body)
    # the client streams more than it signed for: the hard cap stops the read and refuses it
    recorder = RecordingBody([body, b"EXTRA-BYTES-NOT-SIGNED"])
    with pytest.raises(bm.MediaError) as exc:
        await bm.receive_blob(public_key=key.public_key(), seen=None, headers=headers, method="POST",
                              path="/desktop/blob", query=b"", content_length=None, body_iter=recorder, store=store)
    assert exc.value.status == 400 and exc.value.reason == "length"
    assert store.stats() == {"count": 0, "total_bytes": 0, "reserved_bytes": 0, "taken_bytes": 0, "active_uploads": 0}


class GatedBody:
    """An async body iterator that yields its first chunk, then waits on `gate` before the rest, so a test can hold
    an upload mid-stream (slot taken, signed length booked) while it starts another one."""

    def __init__(self, chunks: list[bytes], gate: asyncio.Event) -> None:
        self._chunks = chunks
        self._gate = gate
        self.started = asyncio.Event()
        self.read = False

    async def __aiter__(self):  # type: ignore[no-untyped-def]
        self.read = True
        yield self._chunks[0]
        self.started.set()
        await self._gate.wait()
        for chunk in self._chunks[1:]:
            yield chunk


def _signed_upload(key: Any, body: bytes) -> dict[str, Any]:
    headers = dp.sign_http_request(key, method="POST", path="/desktop/blob", body=body)
    return {"public_key": key.public_key(), "seen": None, "headers": headers, "method": "POST",
            "path": "/desktop/blob", "query": b"", "content_length": len(body)}


async def test_signed_lengths_in_flight_count_against_the_total_before_any_body_is_read():
    """Two uploads streaming at once book their signed lengths against the total at acquire; a third whose signed
    length would pass the cap is refused (507) before its body is read, so stored + streaming bytes never pass
    BLOB_MAX_TOTAL. When the two finish, their bookings become the stored blobs and nothing stays booked."""
    key = dp.generate_signing_key()
    store = bm.BlobStore(max_total=250, max_concurrent=3, clock=lambda: 0.0)
    body_a, body_b, body_c = b"a" * 100, b"b" * 100, b"c" * 100
    gate = asyncio.Event()
    gated_a = GatedBody([body_a[:10], body_a[10:]], gate)
    gated_b = GatedBody([body_b[:10], body_b[10:]], gate)
    task_a = asyncio.create_task(bm.receive_blob(**_signed_upload(key, body_a), body_iter=gated_a, store=store))
    task_b = asyncio.create_task(bm.receive_blob(**_signed_upload(key, body_b), body_iter=gated_b, store=store))
    await asyncio.wait_for(asyncio.gather(gated_a.started.wait(), gated_b.started.wait()), timeout=5)
    assert store.stats() == {"count": 0, "total_bytes": 0, "reserved_bytes": 200, "taken_bytes": 0, "active_uploads": 2}

    third = RecordingBody([body_c])
    with pytest.raises(bm.MediaError) as exc:
        await bm.receive_blob(**_signed_upload(key, body_c), body_iter=third, store=store)
    assert exc.value.status == 507 and exc.value.reason == "capacity"
    assert third.read is False                       # refused at acquire, before a single body byte
    assert store.stats()["active_uploads"] == 2      # no slot was taken by the refused upload

    gate.set()
    (id_a, size_a), (id_b, size_b) = await asyncio.wait_for(asyncio.gather(task_a, task_b), timeout=5)
    assert (size_a, size_b) == (100, 100)
    assert store.stats() == {"count": 2, "total_bytes": 200, "reserved_bytes": 0, "taken_bytes": 0, "active_uploads": 0}
    assert store.take(id_a) == body_a and store.take(id_b) == body_b
    assert store.stats() == {"count": 0, "total_bytes": 0, "reserved_bytes": 0, "taken_bytes": 0, "active_uploads": 0}


async def test_stored_blobs_plus_a_streaming_booking_refuse_the_next_upload_before_its_body():
    """With the default two slots: stored blobs and one upload in flight leave too little room, so the next
    upload's signed length is refused at acquire (507) and its body is never read."""
    key = dp.generate_signing_key()
    store = bm.BlobStore(max_total=300, clock=lambda: 0.0)
    store.put(b"s" * 150)                            # already stored
    gate = asyncio.Event()
    streaming = GatedBody([b"x" * 10, b"x" * 90], gate)
    task = asyncio.create_task(bm.receive_blob(**_signed_upload(key, b"x" * 100), body_iter=streaming,
                                               store=store))
    await asyncio.wait_for(streaming.started.wait(), timeout=5)
    late = RecordingBody([b"y" * 60])
    with pytest.raises(bm.MediaError) as exc:
        await bm.receive_blob(**_signed_upload(key, b"y" * 60), body_iter=late, store=store)   # 150+100+60 > 300
    assert exc.value.status == 507 and late.read is False
    gate.set()
    await asyncio.wait_for(task, timeout=5)
    assert store.stats() == {"count": 2, "total_bytes": 250, "reserved_bytes": 0, "taken_bytes": 0, "active_uploads": 0}


async def test_a_failed_upload_releases_its_booking_and_slot():
    key = dp.generate_signing_key()
    store = bm.BlobStore(max_total=100, clock=lambda: 0.0)
    body = b"z" * 80
    upload = _signed_upload(key, body)
    with pytest.raises(bm.MediaError) as exc:
        await bm.receive_blob(**upload, body_iter=RecordingBody([b"q" * 80]), store=store)   # wrong digest
    assert exc.value.reason == "digest"
    assert store.stats() == {"count": 0, "total_bytes": 0, "reserved_bytes": 0, "taken_bytes": 0, "active_uploads": 0}
    # the full booking is free again: an upload of the same size now fits
    blob_id, size = await bm.receive_blob(**_signed_upload(key, body), body_iter=RecordingBody([body]),
                                          store=store)
    assert size == 80 and store.take(blob_id) == body


def test_blob_store_acquire_refuses_a_booking_above_the_caps():
    store = bm.BlobStore(max_total=100, max_blob=60, clock=lambda: 0.0)
    with pytest.raises(bm.MediaError) as exc:
        store.acquire(61)                            # above the per-blob cap
    assert exc.value.status == 413
    assert store.acquire(60) is True
    with pytest.raises(bm.MediaError) as exc:
        store.acquire(41)                            # 60 booked + 41 > 100
    assert exc.value.status == 507
    assert store.stats() == {"count": 0, "total_bytes": 0, "reserved_bytes": 60, "taken_bytes": 0, "active_uploads": 1}
    store.release(60)
    assert store.stats() == {"count": 0, "total_bytes": 0, "reserved_bytes": 0, "taken_bytes": 0, "active_uploads": 0}


async def test_a_tampered_body_fails_the_digest():
    key = dp.generate_signing_key()
    store = bm.BlobStore()
    body = b"original-bytes"
    headers = dp.sign_http_request(key, method="POST", path="/desktop/blob", body=body)
    recorder = RecordingBody([b"tampered-bytes"])   # same length, different content
    with pytest.raises(bm.MediaError) as exc:
        await bm.receive_blob(public_key=key.public_key(), seen=None, headers=headers, method="POST",
                              path="/desktop/blob", query=b"", content_length=len(body), body_iter=recorder,
                              store=store)
    assert exc.value.status == 400 and exc.value.reason == "digest"
    assert store.stats()["count"] == 0


# ---------------------------------------------------------------- backup pull


def test_backup_relay_streams_the_newest_snapshot(tmp_path):
    snaps = tmp_path / "snapshots"
    snaps.mkdir()
    write_file(snaps / "2026-10-01.enc", b"old-snapshot", mtime=time.time() - 1000)
    write_file(snaps / "2026-10-03.enc", b"new-snapshot", mtime=time.time() - 10)
    backup = bm.BackupRelay(snapshots_dir=lambda: str(snaps))
    fd, st, name = backup.open_latest()
    assert name == "2026-10-03.enc"
    assert b"".join(bm.stream_fd(fd, 0, st.st_size)) == b"new-snapshot"


def test_backup_relay_404_when_empty(tmp_path):
    snaps = tmp_path / "snapshots"
    snaps.mkdir()
    backup = bm.BackupRelay(snapshots_dir=lambda: str(snaps))
    with pytest.raises(bm.MediaError) as exc:
        backup.open_latest()
    assert exc.value.status == 404
    missing = bm.BackupRelay(snapshots_dir=lambda: str(tmp_path / "nope"))
    with pytest.raises(bm.MediaError):
        missing.open_latest()
