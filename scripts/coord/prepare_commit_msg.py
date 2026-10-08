"""
prepare-commit-msg guard (coordination, 08 Oct): an agent never amends or reuses the OTHER agent's commit.

Both agents commit in one working tree, so HEAD can be the other agent's commit at any moment. On 08 Oct Cursor's
`git commit --amend` rewrote a Claude commit (HEAD had just become Claude's) and the `git reset HEAD~1` that undid
it dropped the commit from main; nothing was lost only because the files were still in the tree. git passes
prepare-commit-msg the source 'commit' and the commit's sha for --amend, -c and -C: when that commit's subject
carries the other agent's tag ('[claude]' / '[cursor]', the commit-message rule in CLAUDE.md), the commit is
refused. Exit 0 lets the commit go on, 1 refuses it.
"""
from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Sequence

TAGS = {"claude": "[claude]", "cursor": "[cursor]"}


def _subject(sha: str) -> str:
    done = subprocess.run(["git", "log", "-1", "--format=%s", sha], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=False)
    return done.stdout.strip()


def check(argv: Sequence[str], agent: str | None, subject_of: Callable[[str], str] = _subject) -> str | None:
    """The refusal message, or None when the commit may go on. argv as git passes it: (script, msg file, source,
    sha)."""
    source = argv[2] if len(argv) > 2 else ""
    sha = argv[3] if len(argv) > 3 else ""
    if source != "commit" or not sha:
        return None
    subject = subject_of(sha)
    owner = next((name for name, tag in TAGS.items() if subject.startswith(tag)), None)
    me = (agent or "").strip().lower()
    if owner is None or owner == me:
        return None
    return (f"JARVIS coordination guard: {sha[:8]} is a {owner} commit ({subject[:70]}). Never amend or reuse the "
            f"other agent's commit (HEAD may have just become theirs): make a new commit of your own paths instead.")


def main(argv: Sequence[str]) -> int:
    refusal = check(argv, os.environ.get("JARVIS_AGENT"))
    if refusal:
        print(refusal, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
