"""Lane checker and the plan task C-05 pre-commit rules: JARVIS_AGENT, lanes, append-only done_highlight.md.

The commit tests never touch this repo: each one builds a throwaway git repo under tmp_path, copies
scripts/coord/*.py and .coord/lanes.json into it and commits there with the real guard as pre-commit hook.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COORD = ROOT / "scripts" / "coord"
LANES_JSON = ROOT / ".coord" / "lanes.json"
HAS_GIT = shutil.which("git") is not None
needs_git = pytest.mark.skipif(not HAS_GIT, reason="git is not installed")


def _load_check_lane():
    spec = importlib.util.spec_from_file_location("jarvis_coord_check_lane", COORD / "check_lane.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look the module up while the class is built
    spec.loader.exec_module(module)
    return module


check_lane = _load_check_lane()
LANES = check_lane.load_lanes(str(LANES_JSON))


def _env(agent: str | None = None) -> dict:
    """Environment without GIT_* (so git can only see the repo in cwd) and with JARVIS_AGENT only when given."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_") and k != "JARVIS_AGENT"}
    if agent is not None:
        env["JARVIS_AGENT"] = agent
    return env


def _git(repo: Path, *args: str, agent: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, env=_env(agent), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=False)


# ---------------------------------------------------------------- check_lane.py


@pytest.mark.parametrize(("agent", "path", "expected"), [
    ("cursor", "src/core/ui_bridge.py", 1),
    ("cursor", "stonix_ui/src/app/App.tsx", 0),
    ("claude", "services/automation/jarvis_reminders.py", 1),
    ("claude", "src/core/ui_bridge.py", 0),
    ("cursor", "services/automation/jarvis_reminders.py", 0),
    ("cursor", "docs/CONTRACTS.md", 1),
    ("claude", "docs/CONTRACTS.md", 0),
    ("cursor", "tests/test_whatsapp.py", 0),
    ("claude", "tests/test_whatsapp.py", 1),
    ("cursor", "done_highlight.md", 0),
    ("claude", "plan.md", 0),
    ("cursor", "a_new_root_file.txt", 1),
    ("user", "src/core/ui_bridge.py", 0),
])
def test_agent_mode_exit_code(agent, path, expected, capsys):
    assert check_lane.main(["--agent", agent, path]) == expected
    out = capsys.readouterr().out
    assert (path in out) == bool(expected)


def test_agent_mode_lists_only_paths_outside_the_lane():
    proc = subprocess.run([sys.executable, str(COORD / "check_lane.py"), "--agent", "cursor",
                           "src/core/ui_bridge.py", "stonix_ui/src/app/App.tsx"],
                          capture_output=True, text=True, cwd=ROOT, check=False)
    assert proc.returncode == 1
    assert "src/core/ui_bridge.py (owner: claude)" in proc.stdout
    assert "App.tsx" not in proc.stdout


@pytest.mark.parametrize(("path", "owner"), [
    ("stonix_ui/electron/main.cjs", "cursor"),
    ("tests/test_whatsapp.py", "cursor"),
    ("tests/test_reminders.py", "cursor"),
    ("tests/tools/side_effect_guard.py", "cursor"),
    ("services/system/jarvis_desktop_common.py", "cursor"),
    ("tests/conftest.py", "claude"),
    ("tests/__init__.py", "claude"),
    ("tests/exhaustive/test_all_tools.py", "claude"),
    ("tests/test_llm_pattern.py", "claude"),
    ("services/ai_core/agent_loops.py", "claude"),
    (".cursor/rules/jarvis-collab.mdc", "claude"),
    (".coord/lanes.json", "claude"),
    ("docs/CONTRACTS.md", "claude"),
    ("deploy/hostinger/.env.example", "claude"),
    ("plan.md", "shared"),
    ("done_highlight.md", "shared"),
    ("src\\core\\ui_bridge.py", "claude"),
    ("./stonix_ui/package.json", "cursor"),
    ("stonix_ui/../src/core/agent.py", "claude"),
    ("a_new_root_file.txt", "unowned"),
])
def test_whoowns(path, owner, capsys):
    assert check_lane.main(["--whoowns", path]) == (1 if owner == "unowned" else 0)
    assert capsys.readouterr().out.strip() == owner


def test_every_tracked_file_has_exactly_one_owner():
    if not HAS_GIT:
        pytest.skip("git is not installed")
    proc = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, env=_env(), capture_output=True, check=False)
    if proc.returncode != 0:
        pytest.skip("not a git checkout")
    files = [p for p in proc.stdout.decode("utf-8", "replace").split("\0") if p]
    unowned = [f for f in files if not check_lane.best_rules(f, LANES)]
    ambiguous = [f for f in files if len({r.owner for r in check_lane.best_rules(f, LANES)}) > 1]
    assert not unowned, f"add these paths to .coord/lanes.json: {unowned[:20]}"
    assert not ambiguous, f"two lanes match these paths equally well: {ambiguous[:20]}"


def test_lanes_file_rejects_unknown_keys():
    with pytest.raises(ValueError):
        check_lane.parse_lanes('{"claude": ["src/**"], "curser": ["stonix_ui/**"]}')


@pytest.mark.parametrize(("old", "new", "removed"), [
    ("a\nb\n", "a\nb\nc\n", []),            # append
    ("a\nb", "a\nb\nc\n", []),              # old file had no final newline
    ("a\r\nb\r\n", "a\nb\nc\n", []),        # line endings only
    ("a\nb\n", "a\nX\nb\n", [2]),           # an insertion between old lines is rejected (append at the end only)
    ("e1\n- c: pending\n", "e1\n- c: sha\n- c: pending\n", [2]),  # repeated template line: hidden edit
    ("", "first entry\n", []),              # new file
    ("a\nb\nc\n", "a\nc\nd\n", [2]),        # removed line
    ("a\nb\nc\n", "a\nB\nc\n", [2]),        # edited line
    ("a\nb\nc\n", "c\na\nb\n", [3]),        # moved line
    ("a\nb\n", "", [1, 2]),                 # emptied or deleted file
])
def test_removed_lines(old, new, removed):
    assert check_lane.removed_lines(old, new) == removed


# ---------------------------------------------------------------- restore_unicode.py


def _load_restore_unicode():
    spec = importlib.util.spec_from_file_location("jarvis_coord_restore_unicode", COORD / "restore_unicode.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


restore_unicode = _load_restore_unicode()

# Board rows and Urdu text, as in plan.md; a middle dot, an arrow, an em dash and Urdu letters.
PLAN_TEXT = (
    "# Plan\n\n"
    "### 2.6 Runtime lock · sirf EK worker\n\n"
    "| X-11 | cursor | P0 | Harness → tests | C-06 · X-03 | BLOCKED (waiting C-06) |\n"
    "| X-12 | cursor | P0 | Legacy gating — tools | X-11 | BLOCKED (waiting X-11) |\n"
    "- [2026-10-01 14:30] claude → cursor: سلام note? kept as is\n"
)
BINARY_BYTES = b"\x89BIN\x00\xff\xfe\x80 ? \x00 tail\n"


def _ascii_save(text: str) -> str:
    """What an ASCII save does: every non-ASCII character becomes '?'."""
    return "".join(ch if ord(ch) < 128 else "?" for ch in text)


def test_loss_lines_finds_lines_saved_as_ascii_even_when_also_edited():
    new = _ascii_save(PLAN_TEXT).replace("BLOCKED (waiting C-06)", "DONE (01 Oct 17:13)")
    assert restore_unicode.loss_lines(PLAN_TEXT, new) == [3, 5, 6, 7]


def test_restore_puts_back_only_the_lost_characters_and_keeps_edits():
    new = _ascii_save(PLAN_TEXT).replace("BLOCKED (waiting C-06)", "DONE (01 Oct 17:13)")
    new += "- [2026-10-01 17:30] cursor -> claude: new line with a real question?\n"
    fixed, count = restore_unicode.restore(PLAN_TEXT, new)
    assert count == 9
    assert fixed == PLAN_TEXT.replace("BLOCKED (waiting C-06)", "DONE (01 Oct 17:13)") + \
        "- [2026-10-01 17:30] cursor -> claude: new line with a real question?\n"
    assert restore_unicode.loss_lines(PLAN_TEXT, fixed) == []


def test_a_real_question_mark_and_unchanged_unicode_are_not_a_loss():
    edited = PLAN_TEXT.replace("kept as is", "kept as is?") + "- new? line\n"
    assert restore_unicode.loss_lines(PLAN_TEXT, edited) == []
    assert restore_unicode.restore(PLAN_TEXT, edited) == (edited, 0)


# ---------------------------------------------------------------- pre-commit hook (throwaway repo)


@pytest.fixture(scope="module", name="sandbox_template")
def fixture_sandbox_template(tmp_path_factory):
    """Throwaway git repo (never this one) whose pre-commit hook runs the real guard with the real lanes file."""
    base = tmp_path_factory.mktemp("lane_sandbox")
    repo = base / "repo"
    (repo / "scripts" / "coord").mkdir(parents=True)
    for name in ("precommit.py", "check_lane.py", "restore_unicode.py"):
        shutil.copy2(COORD / name, repo / "scripts" / "coord" / name)
    (repo / ".coord").mkdir()
    shutil.copy2(LANES_JSON, repo / ".coord" / "lanes.json")
    (repo / "docs").mkdir()
    (repo / "docs" / "CONTRACTS.md").write_text("# contracts\n", encoding="utf-8")
    (repo / "stonix_ui").mkdir()
    (repo / "stonix_ui" / "app.ts").write_text("export {};\n", encoding="utf-8")
    (repo / "done_highlight.md").write_bytes(b"# log\n\n### entry 1\n- first\n- more\n")
    (repo / "plan.md").write_bytes(PLAN_TEXT.encode("utf-8"))
    (repo / "data.bin").write_bytes(BINARY_BYTES)
    hooks = base / "hooks"
    hooks.mkdir()
    hook = hooks / "pre-commit"
    python = Path(sys.executable).as_posix()
    hook.write_bytes(f'#!/bin/sh\nexec "{python}" "$(git rev-parse --show-toplevel)/scripts/coord/precommit.py"\n'.encode())
    hook.chmod(0o755)
    assert _git(repo, "init", "-q").returncode == 0
    for key, value in (("user.name", "lane test"), ("user.email", "lane-test@example.invalid"),
                       ("commit.gpgsign", "false"), ("core.autocrlf", "false"), ("core.hooksPath", hooks.as_posix())):
        assert _git(repo, "config", key, value).returncode == 0
    assert _git(repo, "add", "scripts", ".coord", "docs", "stonix_ui", "done_highlight.md", "plan.md",
                "data.bin").returncode == 0
    first = _git(repo, "commit", "-q", "-m", "baseline", agent="user")
    assert first.returncode == 0, first.stdout + first.stderr
    return repo


@pytest.fixture(name="sandbox")
def fixture_sandbox(sandbox_template, tmp_path):
    """A fresh copy of the throwaway repo for one test."""
    repo = tmp_path / "repo"
    shutil.copytree(sandbox_template, repo)
    return repo


def _head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _append(path: Path, text: str) -> None:
    with open(path, "a", encoding="utf-8", newline="") as fh:
        fh.write(text)


def _commit(repo: Path, *paths: str, agent: str | None = None):
    """git commit -m t -- <paths>; returns (exit code, hook output, whether HEAD moved)."""
    before = _head(repo)
    proc = _git(repo, "commit", "-m", "t", "--", *paths, agent=agent)
    return proc.returncode, proc.stdout + proc.stderr, _head(repo) != before


@needs_git
def test_commit_without_agent_is_rejected_with_instructions(sandbox):
    _append(sandbox / "docs" / "CONTRACTS.md", "# lane test\n")
    code, out, moved = _commit(sandbox, "docs/CONTRACTS.md")
    assert code != 0 and not moved
    assert "JARVIS_AGENT is not set" in out
    assert 'JARVIS_AGENT=cursor git commit -m "[cursor] X-NN: <summary>" -- <paths>' in out
    assert "JARVIS_AGENT=claude git commit" in out and "$env:JARVIS_AGENT='cursor'" in out


@needs_git
@pytest.mark.parametrize("agent", ["bob", "cursorr", " "])
def test_wrong_agent_is_rejected(sandbox, agent):
    _append(sandbox / "stonix_ui" / "app.ts", "// change\n")
    code, out, moved = _commit(sandbox, "stonix_ui/app.ts", agent=agent)
    assert code != 0 and not moved
    assert "JARVIS_AGENT" in out and "JARVIS_AGENT=cursor (Cursor) or JARVIS_AGENT=claude (Claude)" in out


@needs_git
def test_cursor_commit_of_contracts_is_rejected(sandbox):
    _append(sandbox / "docs" / "CONTRACTS.md", "# lane test\n")
    code, out, moved = _commit(sandbox, "docs/CONTRACTS.md", agent="cursor")
    assert code != 0 and not moved
    assert "docs/CONTRACTS.md:0 OTHER_LANE (owner: claude)" in out
    assert "check_lane.py --whoowns" in out


@needs_git
def test_claude_commit_of_contracts_passes(sandbox):
    _append(sandbox / "docs" / "CONTRACTS.md", "# lane test\n")
    code, out, moved = _commit(sandbox, "docs/CONTRACTS.md", agent="claude")
    assert code == 0 and moved, out


@needs_git
def test_cursor_commit_of_its_own_lane_passes(sandbox):
    _append(sandbox / "stonix_ui" / "app.ts", "// change\n")
    code, out, moved = _commit(sandbox, "stonix_ui/app.ts", agent="Cursor")
    assert code == 0 and moved, out


@needs_git
def test_user_commit_has_no_lane_limit(sandbox):
    _append(sandbox / "docs" / "CONTRACTS.md", "# by the user\n")
    _append(sandbox / "stonix_ui" / "app.ts", "// by the user\n")
    code, out, moved = _commit(sandbox, "docs/CONTRACTS.md", "stonix_ui/app.ts", agent="user")
    assert code == 0 and moved, out


@needs_git
def test_deleting_another_lanes_file_is_rejected(sandbox):
    (sandbox / "docs" / "CONTRACTS.md").unlink()
    code, out, moved = _commit(sandbox, "docs/CONTRACTS.md", agent="cursor")
    assert code != 0 and not moved
    assert "docs/CONTRACTS.md:0 OTHER_LANE (owner: claude)" in out


@needs_git
def test_unstaged_lanes_edit_does_not_widen_a_lane(sandbox):
    lanes_file = sandbox / ".coord" / "lanes.json"
    lanes = json.loads(lanes_file.read_text(encoding="utf-8"))
    lanes["cursor"].append("docs/**")
    lanes_file.write_text(json.dumps(lanes), encoding="utf-8")  # changed on disk only, not staged
    _append(sandbox / "docs" / "CONTRACTS.md", "# lane test\n")
    code, out, moved = _commit(sandbox, "docs/CONTRACTS.md", agent="cursor")
    assert code != 0 and not moved
    assert "OTHER_LANE" in out


@needs_git
def test_staged_lanes_edit_cannot_widen_a_lane_in_the_same_commit(sandbox):
    lanes_file = sandbox / ".coord" / "lanes.json"
    lanes = json.loads(lanes_file.read_text(encoding="utf-8"))
    lanes["cursor"] += ["docs/**", ".coord/**"]
    lanes_file.write_text(json.dumps(lanes), encoding="utf-8")
    _append(sandbox / "docs" / "CONTRACTS.md", "# lane test\n")
    assert _git(sandbox, "add", ".coord/lanes.json", "docs/CONTRACTS.md").returncode == 0
    code, out, moved = _commit(sandbox, ".coord/lanes.json", "docs/CONTRACTS.md", agent="cursor")
    assert code != 0 and not moved
    assert "docs/CONTRACTS.md:0 OTHER_LANE (owner: claude)" in out
    assert ".coord/lanes.json:0 OTHER_LANE (owner: claude)" in out


@needs_git
def test_mojibake_line_is_rejected_with_a_hint(sandbox):
    path = sandbox / "stonix_ui" / "app.ts"
    with open(path, "ab") as fh:
        fh.write("// status \u00c2\u00b7 done \u00e2\u20ac\u201d ok\n".encode("utf-8"))
    code, out, moved = _commit(sandbox, "stonix_ui/app.ts", agent="cursor")
    assert code != 0 and not moved
    assert "stonix_ui/app.ts:2 MOJIBAKE" in out
    assert "-Encoding UTF8" in out


@needs_git
def test_real_unicode_is_not_mojibake(sandbox):
    path = sandbox / "stonix_ui" / "app.ts"
    with open(path, "ab") as fh:
        fh.write("// status \u00b7 done \u2014 \u2192 caf\u00e9 \u2764\n".encode("utf-8"))
    code, out, moved = _commit(sandbox, "stonix_ui/app.ts", agent="cursor")
    assert code == 0 and moved, out


@needs_git
def test_staged_removal_of_a_done_highlight_line_is_rejected(sandbox):
    log = sandbox / "done_highlight.md"
    log.write_bytes(b"# log\n\n### entry 1\n\n### entry 2\n- second\n")  # '- first' and '- more' removed
    assert _git(sandbox, "add", "done_highlight.md").returncode == 0
    before = _head(sandbox)
    proc = _git(sandbox, "commit", "-m", "t", agent="claude")  # commits the staged index
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0 and _head(sandbox) == before
    assert "done_highlight.md:4-5 APPEND_ONLY" in out
    assert "append-only" in out


@needs_git
def test_edited_done_highlight_line_is_rejected_for_every_agent(sandbox):
    log = sandbox / "done_highlight.md"
    log.write_bytes(log.read_bytes().replace(b"- first", b"- rewritten"))
    for agent in ("cursor", "claude", "user"):
        code, out, moved = _commit(sandbox, "done_highlight.md", agent=agent)
        assert code != 0 and not moved
        assert "done_highlight.md:4 APPEND_ONLY" in out


@needs_git
def test_appending_to_done_highlight_passes(sandbox):
    _append(sandbox / "done_highlight.md", "\n### entry 2\n- second\n")
    code, out, moved = _commit(sandbox, "done_highlight.md", agent="cursor")
    assert code == 0 and moved, out


@needs_git
def test_line_endings_plus_append_pass(sandbox):
    (sandbox / "done_highlight.md").write_bytes(
        b"# log\r\n\r\n### entry 1\r\n- first\r\n- more\r\n\r\n### entry 2\r\n- second\r\n")
    code, out, moved = _commit(sandbox, "done_highlight.md", agent="claude")
    assert code == 0 and moved, out


@needs_git
def test_plan_saved_as_ascii_is_rejected_with_the_restore_command(sandbox):
    plan = sandbox / "plan.md"
    plan.write_bytes(_ascii_save(PLAN_TEXT).replace("BLOCKED (waiting C-06)", "DONE (01 Oct 17:13)").encode())
    code, out, moved = _commit(sandbox, "plan.md", agent="cursor")
    assert code != 0 and not moved
    assert "plan.md:3 ENCODING_LOSS" in out and "plan.md:5-7 ENCODING_LOSS" in out
    assert "python scripts/coord/restore_unicode.py plan.md --write" in out


@needs_git
def test_restore_unicode_command_repairs_the_file_and_the_commit_passes(sandbox):
    plan = sandbox / "plan.md"
    plan.write_bytes(_ascii_save(PLAN_TEXT).replace("BLOCKED (waiting C-06)", "DONE (01 Oct 17:13)").encode())
    proc = subprocess.run([sys.executable, "scripts/coord/restore_unicode.py", "plan.md", "--write"], cwd=sandbox,
                          env=_env(), capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "9 character(s) to restore" in proc.stdout
    assert plan.read_bytes().decode("utf-8") == PLAN_TEXT.replace("BLOCKED (waiting C-06)", "DONE (01 Oct 17:13)")
    code, out, moved = _commit(sandbox, "plan.md", agent="cursor")
    assert code == 0 and moved, out


@needs_git
def test_unicode_kept_status_edit_of_plan_passes(sandbox):
    plan = sandbox / "plan.md"
    plan.write_bytes(PLAN_TEXT.replace("BLOCKED (waiting X-11)", "DOING (cursor, 01 Oct 17:20)").encode("utf-8"))
    code, out, moved = _commit(sandbox, "plan.md", agent="cursor")
    assert code == 0 and moved, out


@needs_git
def test_changed_binary_file_is_not_an_encoding_loss(sandbox):
    (sandbox / "data.bin").write_bytes(BINARY_BYTES.replace(b"\xff", b"?"))
    code, out, moved = _commit(sandbox, "data.bin", agent="user")
    assert code == 0 and moved, out
