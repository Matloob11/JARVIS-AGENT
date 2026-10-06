"""Plan task C-46: scripts/ops/env_defaults.py (append-only env defaults, contract K-35).

Only files under tmp_path are touched (env files and the backup root). The values below are throwaway fakes, and
the tests check that no value reaches stdout or stderr.
"""
# pylint: disable=redefined-outer-name
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from typing import Any

import pytest
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
FAKE_GOOGLE = "c46-fake-google-value"
FAKE_SIM = "c46-fake-sim-value"
ROOT_ENV = (f"# JARVIS PC\r\nGOOGLE_API_KEY={FAKE_GOOGLE}\r\nJARVIS_CONFIRM_TIMEOUT_S=45\r\n\r\n"
            f"SIM_LOOKUP_API_KEY={FAKE_SIM}\r\nJARVIS_SELF_REPAIR_APPLY=0\r\nUSER_CITY=Lahore\r\n")
OVERLAY_ENV = "﻿GOOGLE_API_KEY=c46-fake-overlay\nTAVILY_SEARCH_DEPTH=basic\n# keep me\nSIM_SOURCE="  # no EOL


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("jarvis_ops_test_env_defaults",
                                                  ROOT / "scripts" / "ops" / "env_defaults.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


env_defaults = _load()


@pytest.fixture
def env_files(tmp_path: Path) -> tuple[Path, Path]:
    root_env = tmp_path / "root.env"
    overlay = tmp_path / "overlay" / ".env"
    overlay.parent.mkdir()
    root_env.write_bytes(ROOT_ENV.encode("utf-8"))
    overlay.write_bytes(OVERLAY_ENV.encode("utf-8"))
    return root_env, overlay


def _run(capsys, tmp_path: Path, files: tuple[Path, ...], *args: str) -> tuple[int, str, str]:
    argv = [a for f in files for a in ("--file", str(f))] + ["--backup-root", str(tmp_path / "backups")] + list(args)
    code = env_defaults.main(argv)
    out, err = capsys.readouterr()
    return code, out, err


def _lines(raw: bytes) -> list[bytes]:
    return raw.splitlines(keepends=True)


def _assert_no_values(text: str, *values: str) -> None:
    for value in (FAKE_GOOGLE, FAKE_SIM, "c46-fake-overlay", *values):
        assert value not in text


def test_tools_info_appends_missing_keys_and_keeps_old_lines_byte_identical(env_files, tmp_path, capsys):
    before = {p: p.read_bytes() for p in env_files}
    code, out, err = _run(capsys, tmp_path, env_files, "--tools-info")
    assert code == 0, err

    for path, old in before.items():
        new = path.read_bytes()
        # sha256 of the pre-existing lines is unchanged: the new bytes start with the old bytes (plus at most the
        # line ending the overlay was missing at its end).
        old_lines = _lines(old)
        new_lines = _lines(new)
        head = old_lines[:-1]
        assert hashlib.sha256(b"".join(new_lines[:len(head)])).digest() == hashlib.sha256(b"".join(head)).digest()
        assert new_lines[len(head)].startswith(old_lines[-1].rstrip(b"\r\n"))
        values = dotenv_values(path)
        for key, default in env_defaults.TOOLS_INFO_DEFAULTS:
            assert key in values

    root_values = dotenv_values(env_files[0])
    overlay_values = dotenv_values(env_files[1])
    # Present keys keep their own values; missing ones get the K-35 defaults.
    assert root_values["JARVIS_CONFIRM_TIMEOUT_S"] == "45"
    assert root_values["SIM_LOOKUP_API_KEY"] == FAKE_SIM
    assert root_values["JARVIS_GROQ_VISION_MODEL"] == "qwen/qwen3.8-27b"
    assert root_values["TAVILY_SEARCH_DEPTH"] == "advanced"
    assert overlay_values["TAVILY_SEARCH_DEPTH"] == "basic"
    assert overlay_values["SIM_SOURCE"] == ""
    assert overlay_values["SIM_LOOKUP_API_KEY"] == ""
    assert overlay_values["JARVIS_SELF_REPAIR_APPLY"] == "0"

    # Line endings and BOM kept; the comment header sits above the appended block; each key appears once.
    root_text = env_files[0].read_bytes().decode("utf-8")
    assert "\n" not in root_text.replace("\r\n", "")
    assert f"# {env_defaults.TOOLS_INFO_COMMENT}\r\n" in root_text
    overlay_raw = env_files[1].read_bytes()
    assert overlay_raw.startswith("﻿".encode("utf-8")) and b"\r\n" not in overlay_raw
    assert overlay_raw.count(b"SIM_SOURCE=") == 1
    assert root_text.count("JARVIS_CONFIRM_TIMEOUT_S=") == 1

    assert "added JARVIS_TEXT_MODEL" in out and "kept SIM_LOOKUP_API_KEY" in out
    _assert_no_values(out + err, "gemini-3.5-flash", "qwen/qwen3.8-27b", "smtp.gmail.com", "openai/gpt-oss-120b")


def test_second_run_changes_nothing(env_files, tmp_path, capsys):
    assert _run(capsys, tmp_path, env_files, "--tools-info")[0] == 0
    after_first = [p.read_bytes() for p in env_files]
    code, out, _err = _run(capsys, tmp_path, env_files, "--tools-info")
    assert code == 0
    assert [p.read_bytes() for p in env_files] == after_first
    assert "no change" in out and "added " not in out


def test_existing_empty_line_is_kept_not_filled(env_files, tmp_path, capsys):
    before = env_files[1].read_bytes()
    code, out, _err = _run(capsys, tmp_path, (env_files[1],), "--add", "SIM_SOURCE=auto")
    assert code == 0
    assert env_files[1].read_bytes() == before
    assert "kept SIM_SOURCE" in out


def test_backup_made_outside_repo_before_writing(env_files, tmp_path, capsys):
    before = {p.name: p.read_bytes() for p in env_files}
    code, out, _err = _run(capsys, tmp_path, env_files, "--add", "X_C46_DEMO=c46-fake-plain")
    assert code == 0
    folders = list((tmp_path / "backups").iterdir())
    assert len(folders) == 1 and folders[0].name.startswith("env-")
    copies = sorted(folders[0].iterdir())
    assert len(copies) == 2
    assert {c.read_bytes() for c in copies} == set(before.values())
    assert f"backup: {folders[0]}" in out
    _assert_no_values(out, "c46-fake-plain")


def test_backup_root_inside_repo_is_refused(env_files, capsys):
    before = [p.read_bytes() for p in env_files]
    argv = [a for f in env_files for a in ("--file", str(f))]
    code = env_defaults.main(argv + ["--backup-root", str(ROOT / "tmp_backups_c46"), "--add", "X_C46=1"])
    _out, err = capsys.readouterr()
    assert code == 2 and "outside the repo" in err
    assert [p.read_bytes() for p in env_files] == before
    assert not (ROOT / "tmp_backups_c46").exists()


def test_dry_run_writes_nothing(env_files, tmp_path, capsys):
    before = [p.read_bytes() for p in env_files]
    code, out, _err = _run(capsys, tmp_path, env_files, "--tools-info", "--dry-run")
    assert code == 0 and "dry run" in out
    assert [p.read_bytes() for p in env_files] == before
    assert not (tmp_path / "backups").exists()


@pytest.mark.parametrize("item", ["GROQ_API_KEY=c46-fake-groq", "PINCHTAB_TOKEN=c46-fake-tok",
                                  "BRIDGE_HMAC_SECRET=c46-fake-hmac", "EMAIL_APP_PASSWORD=c46-fake-pw",
                                  "JARVIS_TOKEN_MODEL=c46-fake-x"])
def test_update_of_secret_like_name_is_refused(env_files, tmp_path, capsys, item):
    before = [p.read_bytes() for p in env_files]
    code, out, err = _run(capsys, tmp_path, env_files, "--update", item)
    assert code == 2 and "looks like a secret" in err
    assert [p.read_bytes() for p in env_files] == before
    _assert_no_values(out + err, item.partition("=")[2])


def test_update_sim_switches_allowed(env_files, tmp_path, capsys):
    """K-35 v15: SIM_LOOKUP_SHOW_ADDRESS / SIM_LOOKUP_DAILY_LIMIT are non-secret switches."""
    code, out, _err = _run(capsys, tmp_path, env_files, "--update", "SIM_LOOKUP_SHOW_ADDRESS=1",
                           "SIM_LOOKUP_DAILY_LIMIT=20")
    assert code == 0
    assert "SIM_LOOKUP_SHOW_ADDRESS" in out and "SIM_LOOKUP_DAILY_LIMIT" in out
    for p in env_files:
        assert b"SIM_LOOKUP_SHOW_ADDRESS=1" in p.read_bytes()


def test_update_outside_allow_list_is_refused(env_files, tmp_path, capsys):
    code, _out, err = _run(capsys, tmp_path, env_files, "--update", "USER_CITY=Karachi")
    assert code == 2 and "only JARVIS_SELF_REPAIR_APPLY" in err
    assert "Karachi" not in err


def test_add_secret_like_name_with_value_is_refused(env_files, tmp_path, capsys):
    before = [p.read_bytes() for p in env_files]
    code, out, err = _run(capsys, tmp_path, env_files, "--add", "POLLINATIONS_TOKEN=c46-fake-pollen")
    assert code == 2 and "only added empty" in err
    assert [p.read_bytes() for p in env_files] == before
    _assert_no_values(out + err, "c46-fake-pollen")


def test_update_allowed_switch_in_place(env_files, tmp_path, capsys):
    root_before = _lines(env_files[0].read_bytes())
    code, out, err = _run(capsys, tmp_path, env_files, "--update", "JARVIS_SELF_REPAIR_APPLY=1",
                          "TAVILY_SEARCH_DEPTH=advanced", "JARVIS_VISION_MODEL=gemini-3.8-flash")
    assert code == 0, err
    root_after = _lines(env_files[0].read_bytes())
    changed = [i for i, (a, b) in enumerate(zip(root_before, root_after)) if a != b]
    assert len(changed) == 1 and root_after[changed[0]] == b"JARVIS_SELF_REPAIR_APPLY=1\r\n"
    assert dotenv_values(env_files[1])["TAVILY_SEARCH_DEPTH"] == "advanced"
    assert dotenv_values(env_files[1])["JARVIS_SELF_REPAIR_APPLY"] == "1"
    assert dotenv_values(env_files[0])["JARVIS_VISION_MODEL"] == "gemini-3.8-flash"
    assert "updated JARVIS_SELF_REPAIR_APPLY" in out and "updated TAVILY_SEARCH_DEPTH" in out
    assert "added JARVIS_VISION_MODEL" in out
    _assert_no_values(out + err, "gemini-3.8-flash", "advanced")


def test_update_overrides_tools_info_default(env_files, tmp_path, capsys):
    code, _out, err = _run(capsys, tmp_path, (env_files[1],), "--tools-info", "--update",
                           "JARVIS_SELF_REPAIR_APPLY=1")
    assert code == 0, err
    raw = env_files[1].read_bytes()
    assert raw.count(b"JARVIS_SELF_REPAIR_APPLY=") == 1
    assert dotenv_values(env_files[1])["JARVIS_SELF_REPAIR_APPLY"] == "1"


def test_check_reports_present_and_missing_without_values(env_files, tmp_path, capsys):
    code, out, err = _run(capsys, tmp_path, env_files, "--check", "GOOGLE_API_KEY", "SIM_SOURCE",
                          "JARVIS_VISION_MODEL")
    assert code == 1, err
    assert "present GOOGLE_API_KEY" in out
    assert "missing SIM_SOURCE" in out and "missing JARVIS_VISION_MODEL" in out
    _assert_no_values(out + err)
    assert _run(capsys, tmp_path, env_files, "--tools-info")[0] == 0
    code, out, _err = _run(capsys, tmp_path, env_files, "--check", "JARVIS_VISION_MODEL", "SIM_LOOKUP_API_URL",
                           "JARVIS_SELF_REPAIR_APPLY")
    assert code == 0 and "0 missing" in out


def test_check_cannot_be_combined_with_edits(env_files, tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        _run(capsys, tmp_path, env_files, "--check", "A", "--add", "B=1")
    assert exc.value.code == 2


def test_bad_items_never_echo_values(env_files, tmp_path, capsys):
    code, out, err = _run(capsys, tmp_path, env_files, "--add", "c46-fake-loose-value")
    assert code == 2 and "c46-fake-loose-value" not in out + err
    code, out, err = _run(capsys, tmp_path, env_files, "--add", "A_C46=1", "A_C46=2")
    assert code == 2 and "given more than once: A_C46" in err
    with pytest.raises(SystemExit):
        _run(capsys, tmp_path, env_files, "--add", "OK=1", "--bogus=c46-fake-hidden")
    _out, err = capsys.readouterr()
    assert "c46-fake-hidden" not in err


def test_same_file_twice_and_missing_file(env_files, tmp_path, capsys):
    code, _out, err = _run(capsys, tmp_path, (env_files[0], env_files[0]), "--add", "A_C46=1")
    assert code == 2 and "twice" in err
    code, _out, err = _run(capsys, tmp_path, (tmp_path / "nope.env",), "--add", "A_C46=1")
    assert code == 2 and "not found" in err


def test_default_files_are_the_pc_pair():
    assert env_defaults.DEFAULT_FILES == (ROOT / ".env", ROOT / "deploy" / "windows-local" / ".env")
    names = [key for key, _value in env_defaults.TOOLS_INFO_DEFAULTS]
    assert "JARVIS_CONFIRM_TIMEOUT_S" not in names
    assert len(names) == len(set(names))
    for key, value in env_defaults.TOOLS_INFO_DEFAULTS:
        if env_defaults.secret_like(key):
            assert value == ""
