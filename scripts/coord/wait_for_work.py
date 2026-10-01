"""Block until an agent has work in plan.md, then print it (Claude/Cursor idle loop).

Work for an agent is:
  - its own tasks marked DOING (resume them first),
  - its open tasks (not DONE / NOT NEEDED) whose task dependencies on the board are all DONE or NOT NEEDED
    (a stale "BLOCKED (waiting X)" status does not matter, the dependencies decide),
  - new plan.md section 11 handoff lines addressed to it ("-> cursor", "→ all", "→ user/cursor", ...).

Usage (repo root; stdlib only, any Python 3):
    python scripts/coord/wait_for_work.py --agent cursor                 # wait up to 5 min
    python scripts/coord/wait_for_work.py --agent cursor --max-wait 0    # check once, never wait
Exit code: 0 work found (printed), 2 nothing yet (run it again), 1 error.
Handoff lines already shown are remembered in run/wait_for_work_<agent>.json (git-ignored).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PLAN = os.path.join(ROOT, "plan.md")
AGENTS = ("claude", "cursor")
ROW = re.compile(r"^\| ((?:C|X)-\d+) \| (claude|cursor) \| (P\d) \| (.*?) \| (.*?) \| (.*?) \|\s*$")
TASK_ID = re.compile(r"\b([CX]-\d+)\b")
# "- [2026-10-01 14:30] claude → cursor: text" (also "->", and targets like "user/cursor" or "all")
HANDOFF = re.compile(r"^- \[[^\]]+\]\s+[\w/ ]+?\s*(?:→|->)\s*([\w/ ,]+?)\s*:")


def finished(status: str) -> bool:
    return status.startswith("DONE") or status.startswith("NOT NEEDED")


def parse_board(text: str) -> dict[str, dict[str, str]]:
    rows = {}
    for line in text.split("\n"):
        m = ROW.match(line)
        if m:
            rows[m.group(1)] = {"agent": m.group(2), "pri": m.group(3), "title": m.group(4),
                                "deps": m.group(5), "status": m.group(6)}
    return rows


def open_work(rows: dict[str, dict[str, str]], agent: str) -> tuple[list[str], list[str]]:
    """(tasks the agent is DOING, tasks it can start now), both sorted by priority then id number."""
    def order(tid: str) -> tuple[str, int]:
        return rows[tid]["pri"], int(tid.split("-")[1])

    doing, ready = [], []
    for tid, row in rows.items():
        if row["agent"] != agent or finished(row["status"]):
            continue
        if row["status"].startswith("DOING"):
            doing.append(tid)
            continue
        deps = [d for d in TASK_ID.findall(row["deps"]) if d in rows and d != tid]
        if all(finished(rows[d]["status"]) for d in deps):
            ready.append(tid)
    return sorted(doing, key=order), sorted(ready, key=order)


def handoff_lines(text: str, agent: str) -> list[str]:
    """Section 11 lines addressed to the agent (or to all)."""
    lines = text.split("\n")
    try:
        start = next(i for i, ln in enumerate(lines) if ln.startswith("## 11."))
    except StopIteration:
        return []
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    out = []
    for ln in lines[start + 1:end]:
        m = HANDOFF.match(ln)
        if m:
            targets = set(re.split(r"[/ ,]+", m.group(1).lower()))
            if agent in targets or "all" in targets:
                out.append(ln)
    return out


def _state_path(agent: str) -> str:
    return os.path.join(ROOT, "run", f"wait_for_work_{agent}.json")


def _digest(line: str) -> str:
    return hashlib.sha256(line.encode("utf-8")).hexdigest()[:16]


def load_seen(agent: str) -> set[str] | None:
    """Digests of handoff lines already shown; None on the very first run."""
    try:
        with open(_state_path(agent), encoding="utf-8") as fh:
            return set(json.load(fh).get("seen", []))
    except (OSError, ValueError):
        return None


def save_seen(agent: str, seen: set[str]) -> None:
    path = _state_path(agent)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"seen": sorted(seen)}, fh)


def check(agent: str, seen: set[str] | None) -> tuple[list[str], set[str]]:
    """Report lines for the agent's current work, and the updated seen set."""
    with open(PLAN, "rb") as fh:
        text = fh.read().decode("utf-8", errors="replace").replace("\r\n", "\n")
    rows = parse_board(text)
    doing, ready = open_work(rows, agent)
    messages = handoff_lines(text, agent)
    digests = {_digest(m) for m in messages}
    # First run: treat older messages as read, so only messages from now on wake the agent.
    new = [] if seen is None else [m for m in messages if _digest(m) not in seen]
    report = []
    for tid in doing:
        report.append(f"RESUME {tid} {rows[tid]['pri']} {rows[tid]['title']}  [{rows[tid]['status']}]")
    for tid in ready:
        report.append(f"START  {tid} {rows[tid]['pri']} {rows[tid]['title']}")
    report += [f"MSG    {m}" for m in new]
    return report, (seen or set()) | digests


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Wait until plan.md has work for an agent.")
    parser.add_argument("--agent", required=True, choices=AGENTS)
    parser.add_argument("--max-wait", type=int, default=300, help="seconds to wait for work (default 300)")
    parser.add_argument("--poll", type=int, default=30, help="seconds between checks (default 30)")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    deadline = time.monotonic() + max(0, args.max_wait)
    seen = load_seen(args.agent)
    while True:
        try:
            report, seen_now = check(args.agent, seen)
        except OSError as exc:
            print(f"cannot read plan.md: {exc}")
            return 1
        save_seen(args.agent, seen_now)
        seen = seen_now
        if report:
            print(f"WORK for {args.agent} ({time.strftime('%H:%M')}): do RESUME first, then START in this order;"
                  " read every MSG.")
            print("\n".join(report))
            return 0
        if time.monotonic() >= deadline:
            print(f"NO WORK yet for {args.agent} ({time.strftime('%H:%M')}). Run this command again.")
            return 2
        time.sleep(max(1, min(args.poll, deadline - time.monotonic())))


if __name__ == "__main__":
    sys.exit(main())
