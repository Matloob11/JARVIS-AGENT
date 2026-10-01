"""scripts/coord/wait_for_work.py: which board tasks and handoff lines wake an idle agent."""
from __future__ import annotations

import importlib.util
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location("jarvis_coord_wait_for_work",
                                                  ROOT / "scripts" / "coord" / "wait_for_work.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


w = _load()

BOARD = """# Plan

## 7. Board

| ID | Agent | P | Title | Depends on | Status |
|---|---|---|---|---|---|
| C-05 | claude | P0 | Lanes | C-04 | DONE (01 Oct 14:20) |
| C-06 | claude | P0 | Tests | — | TODO |
| C-07 | claude | P1 | Smoke | C-05 | NOT NEEDED (user rule) |
| X-01 | cursor | P3 | Done already | — | DONE (01 Oct 12:00) |
| X-02 | cursor | P1 | Waits on done tasks | X-01, C-05, C-07, K-12 (ToolResult) | BLOCKED (waiting C-05) |
| X-03 | cursor | P0 | Waits on open Claude task | C-06 | BLOCKED (waiting C-06) |
| X-04 | cursor | P2 | In progress | — | DOING (cursor, 01 Oct 17:00) |
| X-05 | cursor | P0 | No deps | — | TODO |
| X-06 | cursor | P3 | Skipped | — | NOT NEEDED (user rule) |

## 11. Handoff: Claude ↔ Cursor messages

Format: `- [YYYY-MM-DD HH:MM] from → to: message (task id)`.

- [2026-10-01 14:30] claude → cursor: start X-05 (talks about claude too)
- [2026-10-01 14:31] claude -> cursor: ascii arrow
- [2026-10-01 14:32] claude → user/cursor: both
- [2026-10-01 14:33] claude → all: everyone
- [2026-10-01 14:34] cursor → claude: REQ for cursor work done
  - [2026-10-01 14:35] claude → cursor: indented reply is not a new line
- [2026-10-01 14:36] claude → user: only the user

## 12. Risks
- [2026-10-01 14:40] claude → cursor: outside section 11
"""


def test_open_work_uses_dependencies_not_the_stale_status():
    rows = w.parse_board(BOARD)
    doing, ready = w.open_work(rows, "cursor")
    assert doing == ["X-04"]
    assert ready == ["X-05", "X-02"]  # P0 before P1; X-03 still waits for C-06; DONE / NOT NEEDED never listed


def test_open_work_for_claude():
    doing, ready = w.open_work(w.parse_board(BOARD), "claude")
    assert doing == [] and ready == ["C-06"]


def test_handoff_lines_addressed_to_the_agent_only():
    got = w.handoff_lines(BOARD, "cursor")
    assert [ln[14:19] for ln in got] == ["14:30", "14:31", "14:32", "14:33"]
    assert [ln[14:19] for ln in w.handoff_lines(BOARD, "claude")] == ["14:33", "14:34"]


@pytest.fixture(name="repo")
def fixture_repo(tmp_path, monkeypatch):
    plan = tmp_path / "plan.md"
    plan.write_bytes(BOARD.replace("\n", "\r\n").encode("utf-8"))
    monkeypatch.setattr(w, "ROOT", str(tmp_path))
    monkeypatch.setattr(w, "PLAN", str(plan))
    return tmp_path


def _no_tasks(text: str) -> str:
    """The same plan with every cursor task finished (only messages can wake the agent)."""
    for tid in ("X-02", "X-03", "X-04", "X-05"):
        start = text.index(f"| {tid} |")
        end = text.index("\n", start)
        row = text[start:end]
        text = text[:start] + row[:row.rstrip(" |").rfind("|") + 1] + " DONE (01 Oct 18:00) |" + text[end:]
    return text


def test_tasks_wake_the_agent_and_first_run_marks_old_messages_read(repo, capsys):
    assert w.main(["--agent", "cursor", "--max-wait", "0"]) == 0
    out = capsys.readouterr().out
    assert "RESUME X-04" in out and "START  X-05" in out and "START  X-02" in out
    assert not any(line.startswith("MSG") for line in out.splitlines())  # first run: older messages count as read
    assert (repo / "run" / "wait_for_work_cursor.json").is_file()


def test_no_work_returns_2_and_a_new_message_wakes_the_agent(repo, capsys):
    plan = repo / "plan.md"
    plan.write_text(_no_tasks(BOARD), encoding="utf-8")
    assert w.main(["--agent", "cursor", "--max-wait", "0"]) == 2
    assert "NO WORK yet" in capsys.readouterr().out
    text = plan.read_text(encoding="utf-8").replace(
        "\n## 12.", "\n- [2026-10-01 18:05] claude → cursor: C-27 DONE, X-26 is open\n\n## 12.", 1)
    plan.write_text(text, encoding="utf-8")
    assert w.main(["--agent", "cursor", "--max-wait", "0"]) == 0
    out = capsys.readouterr().out
    assert "MSG    - [2026-10-01 18:05] claude → cursor: C-27 DONE" in out
    assert w.main(["--agent", "cursor", "--max-wait", "0"]) == 2  # the same message does not wake it twice


def test_waits_until_work_appears(repo, capsys):
    plan = repo / "plan.md"
    plan.write_text(_no_tasks(BOARD), encoding="utf-8")
    assert w.main(["--agent", "cursor", "--max-wait", "0"]) == 2  # first run: remember the old messages

    def unblock():
        time.sleep(0.5)
        plan.write_text(BOARD, encoding="utf-8")

    thread = threading.Thread(target=unblock)
    thread.start()
    started = time.monotonic()
    code = w.main(["--agent", "cursor", "--max-wait", "10", "--poll", "1"])
    thread.join()
    assert code == 0 and time.monotonic() - started < 5
    assert "START  X-05" in capsys.readouterr().out
