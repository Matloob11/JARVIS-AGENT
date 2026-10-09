"""C-138 follow-up: the test session hides the secrets of every real env file it can reach, also the main tree's .env
above a git worktree nested in it (.claude/worktrees/...), which dotenv's upward search finds when the worktree has
no .env of its own. Before, two secret-isolation tests failed there and printed a real value."""
from __future__ import annotations

from pathlib import Path

from tests import conftest


def test_an_env_file_above_the_project_root_counts_as_real(tmp_path: Path) -> None:
    root = conftest._PROJECT_ROOT  # pylint: disable=protected-access
    assert conftest._is_real_env_file(root / ".env")  # pylint: disable=protected-access
    assert conftest._is_real_env_file(root.parent / ".env")  # pylint: disable=protected-access
    assert conftest._is_real_env_file(root.parent / "bridge.env")  # pylint: disable=protected-access
    assert not conftest._is_real_env_file(root.parent / ".env.example")  # pylint: disable=protected-access
    assert not conftest._is_real_env_file(tmp_path / ".env")  # pylint: disable=protected-access
    assert not conftest._is_real_env_file(root.parent / "other" / ".env")  # pylint: disable=protected-access


def test_the_child_filter_knows_the_folders_above_the_root_too() -> None:
    source = conftest._CHILD_SITECUSTOMIZE  # pylint: disable=protected-access
    assert "full.parent not in root.parents" in source
    assert all(parent / ".env" in conftest._real_env_files() or not (parent / ".env").is_file()  # pylint: disable=protected-access
               for parent in conftest._PROJECT_ROOT.parents)  # pylint: disable=protected-access
