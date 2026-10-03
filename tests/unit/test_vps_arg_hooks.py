"""C-124 step 5: the VPS argument hooks (tool_placement.apply_vps_arg_hooks) and their installation on
the registered tools (remote_compat.install_arg_hooks).

send_email's attachment must lie inside Jarvis_Outputs, Jarvis_Shared or the blob dir and never in /proc,
/sys, /etc or the agent data folders (send_email('/proc/self/environ') is refused before the tool runs); a
recipient other than the owner makes the call K-45 'high' (ConfirmFlow); download_images' folder_name,
the QR filename and the zip name become basenames. The PC brain (local mode) hooks nothing, so
send_email attachments from Jarvis_Outputs on Windows work as today. No mail is sent: the tools here are
fakes with the real tools' signatures.
"""
# pylint: disable=protected-access,redefined-outer-name
from __future__ import annotations

import functools
import inspect
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from services.ai_core import jarvis_confirm, remote_compat
from services.ai_core import tool_placement as tp

OWNER = "owner.c124@example.test"


@pytest.fixture
def app(tmp_path) -> SimpleNamespace:
    """A VPS-like tree in tmp_path: /app with Jarvis_Outputs, Jarvis_Shared, the data folders and a blob dir."""
    root = tmp_path / "app"
    outputs, shared, blobs = root / "Jarvis_Outputs", root / "Jarvis_Shared", tmp_path / "blobs"
    for folder in (outputs / "Downloads", shared, blobs, *(root / name for name in tp.VPS_DATA_DIRS)):
        folder.mkdir(parents=True, exist_ok=True)
    (outputs / "report.pdf").write_bytes(b"%PDF c124")
    (shared / "photo.jpg").write_bytes(b"jpg")
    (blobs / "pulled.docx").write_bytes(b"docx")
    (root / "chroma_db" / "chroma.sqlite3").write_bytes(b"db")
    (root / "data" / "identity.json").write_text("{}", encoding="utf-8")
    (root / ".env").write_text("X=1", encoding="utf-8")
    roots = tp.vps_hook_roots(str(root), str(outputs), str(shared), str(blobs))
    return SimpleNamespace(root=root, outputs=outputs, shared=shared, blobs=blobs, roots=roots)


def _hooks(name: str, app: SimpleNamespace, **arguments: Any) -> tp.HookOutcome:
    return tp.apply_vps_arg_hooks(name, arguments, roots=app.roots, owners=[OWNER])


# ---------------------------------------------------------------- attachments


@pytest.mark.parametrize("path", ["/proc/self/environ", "/proc/1/environ", "/sys/kernel/notes",
                                  "/etc/passwd", "/app/chroma_db/chroma.sqlite3", "/app/logs/jarvis.log",
                                  "/app/conversations/x.json", "/app/data/bridge.db", "/app/backups/a.zip",
                                  "/app/.env.agent"])
def test_send_email_never_attaches_a_system_or_agent_data_file(app, path):
    with pytest.raises(tp.ArgHookError) as refused:
        _hooks("send_email", app, recipient=OWNER, subject="s", body="b", attachment_path=path)
    assert refused.value.code == tp.HOOK_DENIED and refused.value.arg == "attachment_path"
    assert path not in str(refused.value)  # the value never reaches the message


def test_the_agent_data_folders_of_this_tree_are_denied_too(app):
    for path in (app.root / "chroma_db" / "chroma.sqlite3", app.root / "data" / "identity.json", app.root / ".env"):
        with pytest.raises(tp.ArgHookError) as refused:
            _hooks("send_email", app, recipient=OWNER, attachment_path=str(path))
        assert refused.value.code == tp.HOOK_DENIED


def test_attachments_inside_outputs_shared_and_blobs_pass_as_real_paths(app):
    cases = {str(app.outputs / "report.pdf"): app.outputs / "report.pdf",
             "report.pdf": app.outputs / "report.pdf",  # a relative name: Jarvis_Outputs
             "Jarvis_Outputs/report.pdf": app.outputs / "report.pdf",
             str(app.shared / "photo.jpg"): app.shared / "photo.jpg",
             str(app.blobs / "pulled.docx"): app.blobs / "pulled.docx"}
    for given, expected in cases.items():
        outcome = _hooks("send_email", app, recipient=OWNER, attachment_path=given)
        assert outcome.arguments["attachment_path"] == os.path.realpath(expected)
        assert outcome.risk is None
    assert _hooks("send_email", app, recipient=OWNER, attachment_path="").arguments["attachment_path"] == ""


@pytest.mark.parametrize("given", ["../../etc/passwd", "Jarvis_Outputs/../chroma_db/chroma.sqlite3",
                                   "C:\\Users\\Sir\\Documents\\secret.txt", "~/.ssh/id_rsa", "x\x00y"])
def test_escapes_and_foreign_paths_are_refused(app, given):
    with pytest.raises(tp.ArgHookError) as refused:
        _hooks("send_email", app, recipient=OWNER, attachment_path=given)
    assert refused.value.code in (tp.HOOK_DENIED, tp.HOOK_OUTSIDE, tp.HOOK_BAD_VALUE)


def test_a_symlink_out_of_the_outputs_is_followed_and_refused(app):
    link = app.outputs / "innocent.txt"
    try:
        link.symlink_to(app.root / "chroma_db" / "chroma.sqlite3")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks need extra rights on this Windows account")
    with pytest.raises(tp.ArgHookError) as refused:
        _hooks("send_email", app, recipient=OWNER, attachment_path=str(link))
    assert refused.value.code == tp.HOOK_DENIED


def test_the_default_roots_are_the_vps_paths():
    roots = tp.vps_hook_roots("/app", "/app/Jarvis_Outputs", "/app/Jarvis_Shared")
    norm = {os.path.normpath(p) for p in roots.attachments}
    assert {os.path.normpath(p) for p in ("/app/Jarvis_Outputs", "/app/Jarvis_Shared", tp.VPS_BLOB_DIR)} <= norm
    denied = {os.path.normpath(p) for p in roots.denied}
    for path in ("/proc", "/sys", "/etc", *(f"/app/{name}" for name in tp.VPS_DATA_DIRS)):
        assert os.path.normpath(path) in denied


# ---------------------------------------------------------------- recipients, names, zip folders


@pytest.mark.parametrize(("recipient", "risk"), [
    (OWNER, None), (OWNER.upper(), None), (f"Sir Matloob <{OWNER}>", None),
    ("someone@example.test", "high"), (f"{OWNER}, someone@example.test", "high"), ("", "high"),
])
def test_a_recipient_other_than_the_owner_is_k45_high(app, recipient, risk):
    assert _hooks("send_email", app, recipient=recipient).risk == risk
    assert _hooks("autonomous_research_and_email", app, query="q", recipient=recipient).risk == risk
    assert tp.apply_vps_arg_hooks("send_email", {"recipient": OWNER}, roots=app.roots, owners=[]).risk == "high"


@pytest.mark.parametrize(("tool", "arg", "given", "expected"), [
    ("download_images", "folder_name", "../../etc", "etc"),
    ("download_images", "folder_name", "C:\\Users\\Sir\\Desktop\\Cats", "Cats"),
    ("download_images", "folder_name", "/app/data/", "data"),
    ("download_images", "folder_name", "Cats", "Cats"),
    ("generate_qr_code", "filename", "../../../app/chroma_db/x.png", "x.png"),
    ("generate_qr_code", "filename", "C:evil.png", "evil.png"),
])
def test_model_supplied_names_become_basenames(app, tool, arg, given, expected):
    assert _hooks(tool, app, **{arg: given}).arguments[arg] == expected


@pytest.mark.parametrize("given", ["..", ".", "", "/", "..\\"])
def test_a_name_with_nothing_usable_falls_back_to_the_tool_default(app, given):
    assert "folder_name" not in _hooks("download_images", app, query="cats", folder_name=given).arguments


def test_zip_files_on_the_vps_stays_inside_the_outputs(app):
    (app.outputs / "Downloads" / "a.txt").write_text("a", encoding="utf-8")
    outcome = _hooks("zip_files", app, folder_path="Downloads", zip_name="../../x.zip")
    assert outcome.arguments == {"folder_path": os.path.realpath(app.outputs / "Downloads"), "zip_name": "x.zip"}
    # Relative names resolve in Jarvis_Outputs (absolute tmp paths here would look like Windows paths).
    for bad in (".", "Downloads/..", "../data", "/etc", "../chroma_db", "/app/logs"):
        with pytest.raises(tp.ArgHookError):
            _hooks("zip_files", app, folder_path=bad)
    with pytest.raises(tp.ArgHookError):  # strictly inside: never the whole outputs folder
        tp.contained_path(str(app.outputs), app.roots.media, app.roots.denied, app.roots.relative_bases,
                          hook="vps_media_path", arg="folder_path", strict=True)
    # A Windows path is routed to the laptop: the node's open_roots check applies there, not these.
    laptop = _hooks("zip_files", app, folder_path="C:\\Users\\Sir\\Documents\\Project", zip_name="a/b.zip")
    assert laptop.arguments == {"folder_path": "C:\\Users\\Sir\\Documents\\Project", "zip_name": "a/b.zip"}


def test_tools_without_vps_hooks_are_untouched(app):
    assert tp.vps_hooks("get_weather") == () and tp.vps_hooks("play_music") == ()
    assert _hooks("play_music", app, file_path="/proc/self/environ").arguments == {"file_path": "/proc/self/environ"}
    assert dict(tp.vps_hooks("send_email")) == {"vps_attachment": "attachment_path",
                                                "owner_recipient": "recipient"}


# ---------------------------------------------------------------- hooked tool wrappers


def _fake_tool(name: str, risk: Any = "low") -> tuple[Any, list[dict[str, Any]]]:
    """A registered-tool stand-in with the real send_email signature; records each call."""
    calls: list[dict[str, Any]] = []

    async def send_email(recipient: str, subject: str, body: str, attachment_path: str = "") -> dict:
        """Sends a real email."""
        return {"status": "success"}

    @functools.wraps(send_email)
    async def tool_wrapper(*args: Any, **kwargs: Any) -> Any:
        bound = inspect.signature(send_email).bind(*args, **{k: v for k, v in kwargs.items() if k != "_confirmed"})
        calls.append({**bound.arguments, "_confirmed": kwargs.get("_confirmed")})
        return {"status": "success", "tool": name}

    tool_wrapper.__name__ = name
    tool_wrapper.__signature__ = inspect.signature(send_email)  # type: ignore[attr-defined]
    tool_wrapper.__jarvis_meta__ = {"risk": risk}  # type: ignore[attr-defined]
    tool_wrapper.__is_jarvis_tool__ = True  # type: ignore[attr-defined]
    return tool_wrapper, calls


@pytest.fixture
def vps(monkeypatch, app):
    monkeypatch.setenv("JARVIS_DESKTOP_MODE", "remote")
    monkeypatch.setenv("EMAIL_USER", OWNER)
    monkeypatch.setattr(remote_compat, "_hook_roots", lambda: app.roots)
    monkeypatch.setattr(remote_compat, "_desktop_online", lambda: False)
    return app


class _Broker:
    def __init__(self) -> None:
        self.pending: list[dict[str, Any]] = []

    async def create_pending(self, tool: str, summary: str, risk: str, **kwargs: Any) -> dict[str, Any]:
        self.pending.append({"tool": tool, "summary": summary, "risk": risk, **kwargs})
        return {"status": "needs_confirmation", "tool": tool, "confirm_id": "c124"}


@pytest.fixture
def broker(monkeypatch) -> _Broker:
    fake = _Broker()
    monkeypatch.setattr(jarvis_confirm, "confirmation_broker", fake)
    return fake


async def test_send_email_proc_self_environ_is_refused_and_the_tool_never_runs(vps, broker):
    tool, calls = _fake_tool("send_email")
    hooked = remote_compat.hook_tool(tool)
    result = await hooked(OWNER, "env", "see attached", "/proc/self/environ")
    assert result["status"] == "error" and result["error_code"] == tp.HOOK_DENIED
    assert calls == [] and broker.pending == []


async def test_an_owner_mail_runs_with_the_checked_attachment(vps, broker):
    tool, calls = _fake_tool("send_email")
    hooked = remote_compat.hook_tool(tool)
    assert (await hooked(recipient=OWNER, subject="s", body="b", attachment_path="report.pdf"))["status"] == "success"
    assert calls == [{"recipient": OWNER, "subject": "s", "body": "b",
                      "attachment_path": os.path.realpath(vps.outputs / "report.pdf"), "_confirmed": None}]
    assert broker.pending == []


async def test_a_mail_to_someone_else_waits_for_confirmflow_then_runs_hooked_again(vps, broker):
    tool, calls = _fake_tool("send_email")
    hooked = remote_compat.hook_tool(tool)
    result = await hooked("friend@example.test", "s", "b", "report.pdf")
    assert result["status"] == "needs_confirmation" and calls == []
    (pending,) = broker.pending
    assert pending["risk"] == "high" and pending["tool"] == "send_email"
    assert "friend@example.test" not in pending["summary"]
    assert await pending["run"]() == {"status": "success", "tool": "send_email"}  # Sir approved it
    assert calls[0]["_confirmed"] is True
    assert calls[0]["attachment_path"] == os.path.realpath(vps.outputs / "report.pdf")


async def test_a_tool_that_already_asks_is_not_asked_twice(vps, broker):
    tool, calls = _fake_tool("send_email", risk="high")
    await remote_compat.hook_tool(tool)("friend@example.test", "s", "b")
    assert broker.pending == [] and len(calls) == 1  # its own wrapper asks (K-14)


def test_the_hooked_tool_keeps_the_llm_schema(vps):
    tool, _ = _fake_tool("send_email")
    hooked = remote_compat.hook_tool(tool)
    assert hooked.__name__ == "send_email" and hooked.__doc__ == tool.__doc__
    assert inspect.signature(hooked) == inspect.signature(tool)
    assert hooked.__jarvis_meta__ == tool.__jarvis_meta__ and remote_compat.hook_tool(hooked) is hooked
    from livekit.agents import llm  # pylint: disable=import-outside-toplevel
    from livekit.agents.llm import utils as llm_utils  # pylint: disable=import-outside-toplevel

    def schema(func: Any) -> Any:
        return llm_utils.build_legacy_openai_schema(llm.function_tool(func), internally_tagged=True)

    assert schema(hooked) == schema(tool)


def test_install_arg_hooks_wraps_only_hooked_tools_in_remote_mode(vps, monkeypatch):
    send, _ = _fake_tool("send_email")
    weather, _ = _fake_tool("get_weather")
    manager = SimpleNamespace(tools=[send, weather])
    assert remote_compat.install_arg_hooks(manager, mode="local") == []
    assert manager.tools == [send, weather]  # the PC brain: nothing changes
    assert remote_compat.install_arg_hooks(manager) == ["send_email"]
    assert getattr(manager.tools[0], remote_compat.HOOKED_FLAG) and manager.tools[1] is weather
    first = manager.tools[0]
    assert remote_compat.install_arg_hooks(manager) == ["send_email"] and manager.tools[0] is first


async def test_the_pc_brain_still_sends_attachments_from_jarvis_outputs_unhooked(monkeypatch, app):
    monkeypatch.delenv("JARVIS_DESKTOP_MODE", raising=False)
    tool, calls = _fake_tool("send_email")
    manager = SimpleNamespace(tools=[tool])
    assert remote_compat.install_arg_hooks(manager) == []
    windows_style = str(Path.home() / "Desktop" / "JARVIS-AGENT-main" / "Jarvis_Outputs" / "cv.pdf")
    await manager.tools[0]("someone@example.test", "s", "b", windows_style)
    assert calls[0]["attachment_path"] == windows_style
