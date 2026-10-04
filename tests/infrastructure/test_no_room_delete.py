"""Plan task C-23 (K-44 v9): nothing deletes the JARVIS room or a dispatch.

An AST walk over every .py file under src/, services/, scripts/ and deploy/ (comments and docstrings never count):
- delete_room( and DeleteRoomRequest only inside src/core/bridge_rooms.py's reset_room (the audited admin CLI), plus
  the probe and canary scripts that own their own rooms (K-57: scripts/smoke/voice_probe.py, vps_agent_canary.py);
- delete_dispatch( nowhere: on LiveKit v1.13.7 it terminates the dispatch's jobs, a live agent included;
- remove_participant( only in src/core/bridge_rooms.py (the room owner's removal rules);
- delete_room_on_close=True nowhere (livekit-agents would delete the room when a session closes);
- a CreateRoomRequest / create_room with agents= only in deploy/windows-local/ensure_room.py (its LiveKit fallback).
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCANNED = ("src", "services", "scripts", "deploy")
ROOM_OWNER = "src/core/bridge_rooms.py"
OWN_ROOM_SCRIPTS = {"scripts/smoke/voice_probe.py", "scripts/smoke/vps_agent_canary.py"}  # K-57 (later)
AGENTS_ALLOWED = {"deploy/windows-local/ensure_room.py"}


def _python_files() -> list[Path]:
    files: list[Path] = []
    for folder in SCANNED:
        files += [path for path in (ROOT / folder).rglob("*.py") if "__pycache__" not in path.parts]
    return sorted(files)


def _relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _name(node: ast.AST) -> str:
    """The called or referenced name: the attribute for x.y, the id for a bare name."""
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


class _Finder(ast.NodeVisitor):
    """Records each finding with the file and the enclosing function (by name)."""

    def __init__(self, relpath: str) -> None:
        self.relpath = relpath
        self.functions: list[str] = []
        self.found: list[tuple[str, str, str, int]] = []  # (what, file, function, line)

    def _add(self, what: str, node: ast.AST) -> None:
        self.found.append((what, self.relpath, self.functions[-1] if self.functions else "", node.lineno))

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self.functions.append(node.name)
        self.generic_visit(node)
        self.functions.pop()

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        called = _name(node.func)
        if called in ("delete_room", "delete_dispatch", "remove_participant"):
            self._add(called, node)
        keywords = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        if "agents" in keywords and called in ("CreateRoomRequest", "create_room"):
            self._add("create_room_with_agents", node)
        flag = keywords.get("delete_room_on_close")
        if isinstance(flag, ast.Constant) and flag.value is True:
            self._add("delete_room_on_close_true", node)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        if node.attr == "DeleteRoomRequest":
            self._add("DeleteRoomRequest", node)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        if node.id == "DeleteRoomRequest":
            self._add("DeleteRoomRequest", node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        for alias in node.names:
            if alias.name == "DeleteRoomRequest":
                self._add("DeleteRoomRequest", node)


def _findings() -> list[tuple[str, str, str, int]]:
    found: list[tuple[str, str, str, int]] = []
    for path in _python_files():
        finder = _Finder(_relative(path))
        finder.visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        found += finder.found
    return found


FINDINGS = _findings()


def _where(what: str) -> list[tuple[str, str, int]]:
    return [(relpath, function, line) for kind, relpath, function, line in FINDINGS if kind == what]


def test_the_scan_sees_the_code():
    files = {_relative(path) for path in _python_files()}
    assert {ROOM_OWNER, "src/core/ui_bridge.py", "deploy/windows-local/ensure_room.py", "scripts/agent_ctl.py",
            "src/core/agent_runner.py"} <= files


def test_only_the_admin_reset_room_deletes_a_room():
    for what in ("delete_room", "DeleteRoomRequest"):
        offenders = [(relpath, function, line) for relpath, function, line in _where(what)
                     if not ((relpath, function) == (ROOM_OWNER, "reset_room") or relpath in OWN_ROOM_SCRIPTS)]
        assert offenders == [], f"{what} outside bridge_rooms.reset_room: {offenders}"
    assert (ROOM_OWNER, "reset_room") in {(relpath, function) for relpath, function, _ in _where("delete_room")}


def test_nothing_deletes_a_dispatch():
    assert _where("delete_dispatch") == []


def test_only_the_room_owner_removes_a_participant():
    assert {relpath for relpath, _function, _line in _where("remove_participant")} <= {ROOM_OWNER}


def test_no_session_deletes_its_room_on_close():
    assert _where("delete_room_on_close_true") == []


def test_only_ensure_rooms_fallback_creates_a_room_with_an_agents_list():
    assert {relpath for relpath, _function, _line in _where("create_room_with_agents")} <= AGENTS_ALLOWED


def test_the_finder_catches_every_form():
    """The rules above would see a delete written in any of these ways (a docstring or comment never counts)."""
    source = '''
"""delete_room( in a docstring is fine"""
from livekit.api import DeleteRoomRequest
async def anywhere(lk, ctx, api):
    await lk.room.delete_room(api.DeleteRoomRequest(room="x"))  # delete_room( in a comment is fine
    ctx.delete_room()
    await lk.agent_dispatch.delete_dispatch("AD", "x")
    await lk.room.remove_participant(None)
    api.CreateRoomRequest(name="x", agents=[])
    api.RoomOptions(delete_room_on_close=True)
'''
    finder = _Finder("example.py")
    finder.visit(ast.parse(source))
    kinds = [what for what, *_rest in finder.found]
    assert kinds.count("delete_room") == 2 and kinds.count("DeleteRoomRequest") == 2
    assert {"delete_dispatch", "remove_participant", "create_room_with_agents",
            "delete_room_on_close_true"} <= set(kinds)
