"""Desktop Node path policy tests (plan task C-121 steps 4 and 12; K-76).

The mandatory argument hooks: edit/append Notepad is a basename only inside Jarvis_Outputs; open_notepad_file,
folder_file, play_file, zip_files and pc.read_file resolve inside the open roots and must not be sensitive.
The extended sensitive set keeps the repo (except Jarvis_Outputs), .venv*, any *.pth, Startup, the node's
%LOCALAPPDATA%\\JARVIS, ~/.ssh, *.env, id_*, .coord, chroma_db, conversations, logs and password-manager data
out of reach. Approval is mocked; no real hooks or tray are used.
"""
# pylint: disable=redefined-outer-name
from __future__ import annotations

import types
from pathlib import Path

import pytest

from services.ai_core import tool_placement as tp
from services.utils import desktop_protocol as dp
from src.core.desktop import policy as policy_mod
from src.core.desktop.executor import INPUT_LOCK, ToolSpec
from src.core.desktop.node import PendingCall
from src.core.desktop.policy import (
    APPROVAL_INVALID,
    APPROVAL_REQUIRED,
    PATH_NOT_ALLOWED,
    DesktopPolicy,
    PersistentState,
    is_basename,
    is_sensitive_path,
    resolve_in_roots,
    windows_target_name,
)
from src.core.desktop.presence import ManualPresence


async def _noop(_args):
    return None


def _spec(tool: str, cls: str, *, risk: str = "low", exclusive: str | None = None) -> ToolSpec:
    return ToolSpec(name=tool, cls=cls, param_schema_sha256="a" * 64, invoke=_noop, exclusive=exclusive, risk=risk)


def make_call(tool: str, cls: str, *, args: dict, approval: dict | None = None, risk: str = "low",
              exclusive: str | None = None) -> PendingCall:
    spec = _spec(tool, cls, risk=risk, exclusive=exclusive)
    return PendingCall(call_id="callid123", tool=tool, cls=cls, origin="llm", room="jarvis-hybrid",
                       peer="agent:abc", args=dict(args), args_sha256=dp.args_sha256(dict(args)), approval=approval,
                       risk=spec.call_risk(args), spec=spec, job_id="")


def ui_approval(args: dict) -> dict:
    return {"confirm_id": "c", "channel": "ui", "risk": "high", "args_sha256": dp.args_sha256(dict(args))}


class Env:
    def __init__(self, tmp_path: Path) -> None:
        self.data_root = tmp_path / "repo"
        self.outputs = self.data_root / "Jarvis_Outputs"
        self.docs = tmp_path / "Docs"
        self.appdata = tmp_path / "appdata" / "JARVIS"
        for folder in (self.data_root, self.outputs, self.docs, self.appdata):
            folder.mkdir(parents=True, exist_ok=True)
        self.state = PersistentState(tmp_path / "run" / "desktop_state.json", default_mode="full")
        self.policy = DesktopPolicy(
            state=self.state, settings=lambda: types.SimpleNamespace(
                desktop_control=True, presence_window_s=600, capture_presence_s=120, origin_presence_gate=False),
            presence=ManualPresence(present=True), open_roots=lambda: [str(self.docs), str(self.outputs)],
            data_root=self.data_root, outputs_dir=self.outputs, jarvis_appdata=self.appdata)

    def check(self, call: PendingCall) -> str | None:
        return self.policy.check(call)


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


# --- Notepad basename-only, inside Jarvis_Outputs ------------------------------------------------------------

def test_notepad_basename_allowed(env: Env) -> None:
    assert env.check(make_call("edit_notepad_file_tool", tp.INPUT, args={"filename": "notes.txt",
                                                                         "new_content": "hi"},
                               exclusive=INPUT_LOCK)) is None


def test_notepad_traversal_refused(env: Env) -> None:
    for bad in ("..\\secrets.txt", "../secrets.txt", "sub\\notes.txt", "C:\\notes.txt", "/etc/notes"):
        call = make_call("edit_notepad_file_tool", tp.INPUT, args={"filename": bad, "new_content": "x"},
                         exclusive=INPUT_LOCK)
        assert env.check(call) == PATH_NOT_ALLOWED, bad


def test_notepad_pth_refused(env: Env) -> None:
    call = make_call("append_notepad_file_tool", tp.INPUT, args={"filename": "sitecustomize.pth",
                                                                 "append_text": "x"}, exclusive=INPUT_LOCK)
    assert env.check(call) == PATH_NOT_ALLOWED


# --- resolve_in_roots + is_sensitive_path tools --------------------------------------------------------------

def test_open_notepad_file_inside_roots_allowed(env: Env) -> None:
    target = env.docs / "report.txt"
    call = make_call("open_notepad_file", tp.INPUT, args={"file_path": str(target)}, exclusive=INPUT_LOCK)
    assert env.check(call) is None


def test_open_notepad_file_outside_roots_refused(env: Env) -> None:
    outside = env.docs.parent / "elsewhere" / "report.txt"
    call = make_call("open_notepad_file", tp.INPUT, args={"file_path": str(outside)}, exclusive=INPUT_LOCK)
    assert env.check(call) == PATH_NOT_ALLOWED


def test_open_notepad_file_into_repo_refused(env: Env) -> None:
    inside_repo = env.data_root / "src" / "core" / "x.py"
    call = make_call("open_notepad_file", tp.INPUT, args={"file_path": str(inside_repo)}, exclusive=INPUT_LOCK)
    assert env.check(call) == PATH_NOT_ALLOWED


def test_pc_read_file_sensitive_refused_even_with_approval(env: Env) -> None:
    ssh = env.docs / ".ssh" / "id_rsa"
    args = {"path": str(ssh)}
    call = make_call("pc.read_file", tp.EXPOSURE, args=args, approval=ui_approval(args), risk="medium")
    assert env.check(call) == PATH_NOT_ALLOWED


def test_pc_read_file_in_roots_runs_with_approval(env: Env) -> None:
    target = env.docs / "data.csv"
    args = {"path": str(target)}
    call = make_call("pc.read_file", tp.EXPOSURE, args=args, approval=ui_approval(args), risk="medium")
    assert env.check(call) is None


def test_pc_read_file_needs_approval_first(env: Env) -> None:
    target = env.docs / "data.csv"
    call = make_call("pc.read_file", tp.EXPOSURE, args={"path": str(target)}, risk="medium")
    assert env.check(call) == APPROVAL_REQUIRED  # confirm-class exposure before the path check


# --- opening an executable escalates to code_exec ------------------------------------------------------------

def test_folder_file_executable_is_code_exec(env: Env) -> None:
    exe = env.docs / "installer.exe"
    assert env.check(make_call("folder_file", tp.INPUT, args={"path": str(exe)},
                               exclusive=INPUT_LOCK)) == APPROVAL_REQUIRED
    args = {"path": str(exe)}
    ok = make_call("folder_file", tp.INPUT, args=args, exclusive=INPUT_LOCK, approval=ui_approval(args))
    assert env.check(ok) is None  # with approval the launch runs; the .exe is inside an open root


def test_folder_file_document_runs(env: Env) -> None:
    doc = env.docs / "notes.txt"
    assert env.check(make_call("folder_file", tp.INPUT, args={"path": str(doc)}, exclusive=INPUT_LOCK)) is None


# Windows opens what it normalises the name to: 'setup.exe.' and 'setup.exe. ' are setup.exe, and a ':' stream
# suffix names the same file. Scripts and LOLBin types run code when opened (python.exe runs .py/.pyw, Git
# Bash runs .sh, mmc runs .msc, hh runs .chm). No extension or an unknown one is never trusted either.
_OPENS_MACRO_CAPABLE_NAMES = ["old.doc", "old.xls", "old.ppt", "show.pps", "letter.rtf", "LETTER.RTF."]
_OPENS_CODE = ["setup.exe.", "setup.exe. ", "setup.exe. .", "setup.exe::$DATA", "setup.exe:payload", "run.py",
               "run.pyw", "deploy.sh", "x.msc", "x.chm", "x.appref-ms", "x.settingcontent-ms", "x.scf", "x.inf",
               "x.msp", "x.xll", "x.diagcab", "x.application", "x.lnk", "x.hta", "x.ps1", "book.xlsm",
               "archive.zip", "README", "data.unknownext", "RUN.PY", "x.Py.", "a.txt:hidden.exe",
               "report.pdf:x"] + _OPENS_MACRO_CAPABLE_NAMES
_OPENS_PLAIN = ["report.pdf", "notes.txt", "song.mp3", "clip.mp4", "photo.JPG", "sheet.xlsx", "slides.pptx",
                "page.html", "report.pdf."]


@pytest.mark.parametrize("tool,arg", [("folder_file", "path"), ("play_file", "name")])
@pytest.mark.parametrize("name", _OPENS_CODE)
def test_opening_an_executable_or_script_in_any_spelling_needs_approval(env: Env, tool: str, arg: str,
                                                                       name: str) -> None:
    args = {arg: str(env.docs / name)}
    assert env.check(make_call(tool, tp.INPUT, args=args, exclusive=INPUT_LOCK)) == APPROVAL_REQUIRED, name
    # code_exec: a spoken yes never approves it (the agent asks again at high risk), the dashboard Allow does
    voice = {"confirm_id": "c", "channel": "voice", "risk": "medium", "args_sha256": dp.args_sha256(args),
             "voice_id_verified": True}
    assert env.check(make_call(tool, tp.INPUT, args=args, exclusive=INPUT_LOCK, approval=voice)) == APPROVAL_REQUIRED
    assert env.check(make_call(tool, tp.INPUT, args=args, exclusive=INPUT_LOCK, approval=ui_approval(args))) is None


@pytest.mark.parametrize("name", ["setup.exe.", "run.py", "deploy.sh", "x.chm"])
def test_an_existing_file_dropped_into_an_open_root_is_judged_the_same(env: Env, name: str) -> None:
    # the attack path: pc.fetch (open=False) or a download puts the file in an open root, then folder_file opens it
    real = env.docs / name.rstrip(" .")
    real.write_bytes(b"payload")
    for spelling in (str(real), str(env.docs / name)):
        call = make_call("folder_file", tp.INPUT, args={"path": spelling}, exclusive=INPUT_LOCK)
        assert env.check(call) == APPROVAL_REQUIRED, spelling


@pytest.mark.parametrize("name", _OPENS_PLAIN)
def test_opening_a_document_or_media_file_runs(env: Env, name: str) -> None:
    for tool, arg in (("folder_file", "path"), ("play_file", "name")):
        call = make_call(tool, tp.INPUT, args={arg: str(env.docs / name)}, exclusive=INPUT_LOCK)
        assert env.check(call) is None, (tool, name)


def test_opening_a_folder_runs_but_a_folder_named_like_an_executable_does_not(env: Env) -> None:
    folder = env.docs / "Projects"
    folder.mkdir()
    assert env.check(make_call("folder_file", tp.INPUT, args={"path": str(folder)}, exclusive=INPUT_LOCK)) is None
    dotted = env.docs / "release.v2"
    dotted.mkdir()
    assert env.check(make_call("folder_file", tp.INPUT, args={"path": str(dotted)}, exclusive=INPUT_LOCK)) is None
    tricky = env.docs / "tool.exe"
    tricky.mkdir()
    assert env.check(make_call("folder_file", tp.INPUT, args={"path": str(tricky)},
                               exclusive=INPUT_LOCK)) == APPROVAL_REQUIRED
    missing = env.docs / "NotThereYet"  # no extension and not a folder (yet): never trusted
    assert env.check(make_call("folder_file", tp.INPUT, args={"path": str(missing)},
                               exclusive=INPUT_LOCK)) == APPROVAL_REQUIRED


@pytest.mark.parametrize("relpath", ["setup.exe.", "setup.exe::$DATA", "run.py", "deploy.sh", "x.chm", "README"])
def test_fetch_auto_open_uses_the_same_normalisation(env: Env, relpath: str) -> None:
    args = {"area": "outputs", "relpath": relpath, "sha256": "a" * 64, "size": 10, "open": True}
    assert env.check(make_call("pc.fetch", tp.READ, args=args)) == APPROVAL_REQUIRED, relpath
    assert env.check(make_call("pc.fetch", tp.READ, args=dict(args, open=False))) is None  # a copy is a read


@pytest.mark.parametrize("relpath", ["notes.md", "old.doc", "old.xls", "old.ppt", "show.pps", "letter.rtf",
                                     "logo.svg", "book.epub", "doc.xps", "sheet.ods", "subs.srt"])
def test_fetch_auto_opens_only_the_k73_types(env: Env, relpath: str) -> None:
    # K-73 auto-opens audio, video, image, pdf, txt, csv, json, xlsx, docx, pptx and html only; any other open is
    # code_exec (an actuation that needs the dashboard Allow), even a type folder_file may open
    args = {"area": "outputs", "relpath": relpath, "sha256": "a" * 64, "size": 10, "open": True}
    assert env.check(make_call("pc.fetch", tp.READ, args=args)) == APPROVAL_REQUIRED, relpath
    assert env.check(make_call("pc.fetch", tp.READ, args=args, approval=ui_approval(args))) is None, relpath


@pytest.mark.parametrize("relpath", ["song.mp3", "clip.mkv", "photo.JPG", "report.pdf", "notes.txt", "data.csv",
                                     "data.json", "sheet.xlsx", "letter.docx", "slides.pptx", "page.html"])
def test_fetch_auto_opens_the_k73_types_without_approval(env: Env, relpath: str) -> None:
    args = {"area": "outputs", "relpath": relpath, "sha256": "a" * 64, "size": 10, "open": True}
    assert env.check(make_call("pc.fetch", tp.READ, args=args)) is None, relpath


def test_k73_set_is_a_subset_of_the_open_safe_set() -> None:
    assert policy_mod.K73_AUTO_OPEN_EXTS <= policy_mod.OPEN_SAFE_EXTS
    assert not {".doc", ".xls", ".ppt", ".pps", ".rtf"} & policy_mod.OPEN_SAFE_EXTS
    assert not {".svg", ".md", ".epub", ".xps", ".doc", ".rtf"} & policy_mod.K73_AUTO_OPEN_EXTS


@pytest.mark.parametrize("value,expected", [
    ("C:\\Docs\\setup.exe.", "setup.exe"), ("C:/Docs/setup.exe. .", "setup.exe"), ("setup.exe::$DATA", "setup.exe"),
    ("C:\\Docs\\a.txt:hidden.exe", "a.txt"), ("C:x.EXE", "x.exe"), ("", ""), (None, "")])
def test_windows_target_name(value, expected) -> None:
    assert windows_target_name(value) == expected


# --- is_sensitive_path unit --------------------------------------------------------------------------------

def test_is_sensitive_path_set(tmp_path: Path) -> None:
    data_root = tmp_path / "repo"
    outputs = data_root / "Jarvis_Outputs"
    appdata = tmp_path / "AppData" / "Local" / "JARVIS"

    def sensitive(path: Path) -> bool:
        return is_sensitive_path(path, data_root=data_root, outputs_dir=outputs, jarvis_appdata=appdata)

    assert sensitive(data_root / "src" / "core" / "agent.py") is True
    assert sensitive(outputs / "ok.txt") is False
    assert sensitive(data_root / ".venv_312" / "x") is True
    assert sensitive(tmp_path / "pkg" / "sitecustomize.pth") is True
    assert sensitive(tmp_path / "home" / ".ssh" / "config") is True
    assert sensitive(tmp_path / "proj" / ".env") is True
    assert sensitive(tmp_path / "proj" / "prod.env") is True
    assert sensitive(tmp_path / "home" / "id_ed25519") is True
    assert sensitive(appdata / "node" / "keys.dpapi") is True
    for segment in (".coord", "chroma_db", "conversations", "logs"):
        assert sensitive(tmp_path / "x" / segment / "f") is True
    assert sensitive(tmp_path / "vault.kdbx") is True
    assert sensitive(tmp_path / "home" / "Documents" / "plain.txt") is False


def test_resolve_in_roots(tmp_path: Path) -> None:
    data_root = tmp_path / "repo"
    outputs = data_root / "Jarvis_Outputs"
    docs = tmp_path / "Docs"
    for folder in (data_root, outputs, docs):
        folder.mkdir(parents=True, exist_ok=True)
    roots = [str(docs), str(outputs)]
    ctx = {"data_root": data_root, "outputs_dir": outputs, "jarvis_appdata": None}
    assert resolve_in_roots(str(docs / "a.txt"), roots, **ctx) == (docs / "a.txt").resolve()
    assert resolve_in_roots(str(outputs / "a.txt"), roots, **ctx) == (outputs / "a.txt").resolve()
    assert resolve_in_roots(str(tmp_path / "other" / "a.txt"), roots, **ctx) is None  # outside the roots
    assert resolve_in_roots(str(docs / ".ssh" / "key"), roots, **ctx) is None  # sensitive
    assert resolve_in_roots("\\\\server\\share\\a.txt", roots, **ctx) is None  # UNC / network
    assert resolve_in_roots("", roots, **ctx) is None
    assert resolve_in_roots(None, roots, **ctx) is None


def test_is_basename() -> None:
    assert is_basename("notes.txt") is True
    for bad in ("", "..", "../x", "..\\x", "a/b.txt", "a\\b.txt", "C:\\x.txt", None, 5):
        assert is_basename(bad) is False
