"""scripts/coord/prepare_commit_msg.py: an agent never amends or reuses the other agent's commit (08 Oct)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("prepare_commit_msg", ROOT / "scripts" / "coord" / "prepare_commit_msg.py")
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)  # type: ignore[union-attr]


def _subjects(table: dict[str, str]):
    return lambda sha: table[sha]


def test_amending_the_other_agents_commit_is_refused():
    subjects = _subjects({"aaa111": "[claude] K-71: close_window placed", "bbb222": "[cursor] X-61: smoke harness"})
    refusal = guard.check(["hook", "MSG", "commit", "aaa111"], "cursor", subjects)
    assert refusal and "claude commit" in refusal
    assert guard.check(["hook", "MSG", "commit", "bbb222"], "claude", subjects)


def test_own_commits_plain_commits_and_untagged_ones_go_on():
    subjects = _subjects({"aaa111": "[claude] C-33 step B", "ccc333": "Merge branch x"})
    assert guard.check(["hook", "MSG", "commit", "aaa111"], "claude", subjects) is None  # amending one's own
    assert guard.check(["hook", "MSG", "message"], "cursor", subjects) is None  # git commit -m
    assert guard.check(["hook", "MSG"], "cursor", subjects) is None  # plain git commit
    assert guard.check(["hook", "MSG", "commit", "ccc333"], "cursor", subjects) is None  # no agent tag


def test_without_jarvis_agent_any_tagged_commit_is_refused():
    subjects = _subjects({"aaa111": "[cursor] X-44 board"})
    assert guard.check(["hook", "MSG", "commit", "aaa111"], None, subjects)
