"""Desktop Node settings source, data root and environment hygiene (plan task C-120 step 3; K-72, K-15).

Every .env, node_config.json and secrets document lives in tmp_path with throwaway values; nothing reads the
real PC root .env or %LOCALAPPDATA%, and no process is spawned (EnvGuard refuses before Popen runs).
"""
# pylint: disable=redefined-outer-name
from __future__ import annotations

import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

from src.core.desktop import settings as ns

THROWAWAY = "throwaway-value-0123456789"  # never a real secret


@pytest.fixture
def data_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    return root


def _write_env(root: Path, text: str) -> Path:
    path = root / ".env"
    path.write_text(text, encoding="utf-8")
    return path


def _config() -> types.SimpleNamespace:
    return types.SimpleNamespace(desktop_control_enabled=True, clipboard_helper_enabled=True, clipboard_speak=False)


# --- the allow-listed parser ---------------------------------------------------------------------------------

def test_parser_reads_only_allow_listed_keys_and_unquotes():
    text = ("\ufeff# comment\n"
            f"GOOGLE_API_KEY={THROWAWAY}\n"
            "export JARVIS_DESKTOP_CONTROL=false\n"
            "JARVIS_CLIPBOARD_SPEAK='1'   # inline comment after quotes\n"
            'JARVIS_OUTPUTS_DIR="C:\\Data\\Out" # a Windows path keeps its backslashes\n'
            "JARVIS_PRESENCE_WINDOW_S=300 # five minutes\n"
            f"PINCHTAB_TOKEN={THROWAWAY}\n"
            "NOT_A_SETTING=1\n"
            "JARVIS_DESKTOP_CONTROL=off\n")  # the last assignment wins
    values = ns.parse_env_text(text)
    assert values == {"JARVIS_DESKTOP_CONTROL": "off", "JARVIS_CLIPBOARD_SPEAK": "1",
                      "JARVIS_OUTPUTS_DIR": "C:\\Data\\Out", "JARVIS_PRESENCE_WINDOW_S": "300"}
    assert THROWAWAY not in json.dumps(values)


def test_no_secret_looking_name_is_allow_listed():
    assert not [name for name in ns.ALLOWED_KEYS if ns.SECRET_NAME_RE.search(name)]
    assert {"PINCHTAB_TOKEN", "CONTROLLER_TOKEN"}.isdisjoint(ns.ALLOWED_KEYS)


# --- kill switch and live keys -------------------------------------------------------------------------------

def test_kill_switch_false_applies_at_start_and_within_one_poll_after_a_change(data_root):
    env_file = _write_env(data_root, "JARVIS_DESKTOP_CONTROL=false\nJARVIS_CLIPBOARD_HELPER=0\n")
    source = ns.SettingsSource(env_file, data_root)
    environ: dict[str, str] = {}
    config = _config()

    settings = source.load()
    ns.apply_live_settings(settings, environ=environ, config=config)
    assert settings.desktop_control is False and config.desktop_control_enabled is False
    assert config.clipboard_helper_enabled is False
    assert environ["JARVIS_DESKTOP_CONTROL"] == "false"
    assert source.poll() is None  # nothing changed

    env_file.write_text("JARVIS_DESKTOP_CONTROL=true\n", encoding="utf-8")
    assert source.poll() is None  # a loosening applies once a second poll reads the same file
    change = source.poll()  # the node polls every POLL_S (2 s)
    assert change is not None
    previous, current = change
    ns.apply_live_settings(current, environ=environ, config=config)
    assert previous.desktop_control is False and current.desktop_control is True
    assert config.desktop_control_enabled is True and config.clipboard_helper_enabled is True
    assert environ["JARVIS_DESKTOP_CONTROL"] == "true"
    assert not current.needs_restart(previous)  # live keys never restart the node
    assert ns.POLL_S <= 2.0

    env_file.write_text("JARVIS_DESKTOP_CONTROL=false\n", encoding="utf-8")
    previous, current = source.poll()  # turning the kill switch off: the first poll applies it
    assert previous.desktop_control is True and current.desktop_control is False


def test_a_missing_env_file_gives_the_defaults(data_root):
    settings = ns.SettingsSource(data_root / ".env", data_root).load()
    assert settings.desktop_control is True and settings.link_url == ns.DEFAULT_LINK_URL
    assert (settings.presence_window_s, settings.capture_presence_s) == (600, 120)
    assert settings.origin_presence_gate is False


def test_restart_keys_need_a_restart(data_root):
    env_file = _write_env(data_root, "JARVIS_OUTPUTS_DIR=Out\n")
    source = ns.SettingsSource(env_file, data_root)
    source.load()
    env_file.write_text("JARVIS_OUTPUTS_DIR=Elsewhere\n", encoding="utf-8")
    assert source.poll() is None  # confirmed by the next poll: a file read mid-write never restarts the node
    previous, current = source.poll()
    assert current.needs_restart(previous)
    env_file.write_text("JARVIS_OUTPUTS_DIR=Elsewhere\nJARVIS_LINK_URL=https://example.invalid\n",
                        encoding="utf-8")
    assert source.poll() is None
    previous, current = source.poll()
    assert current.needs_restart(previous) and current.link_url == "https://example.invalid"


def test_an_unreadable_or_half_written_env_never_releases_the_kill_switch(data_root, monkeypatch):
    full = "JARVIS_DESKTOP_CONTROL=false\nJARVIS_OUTPUTS_DIR=Out\n"
    env_file = _write_env(data_root, full)
    source = ns.SettingsSource(env_file, data_root)
    assert source.load().desktop_control is False
    real_read_text = Path.read_text

    def sharing_violation(self: Path, *args: object, **kwargs: object) -> str:
        if self == env_file:
            raise PermissionError(13, "the file is in use")
        return real_read_text(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", sharing_violation)
    assert source.poll() is None and source.current.desktop_control is False  # kept, never the defaults
    monkeypatch.setattr(ns.SettingsSource, "LOAD_RETRY_S", 0.0)
    with pytest.raises(ns.NodeConfigError):  # at start the node never runs without its kill switch
        ns.SettingsSource(env_file, data_root).load()
    monkeypatch.undo()

    env_file.write_text("", encoding="utf-8")  # truncated by a writer and read once mid-write
    assert source.poll() is None and source.current.desktop_control is False
    env_file.write_text(full, encoding="utf-8")
    assert source.poll() is None and source.current.desktop_control is False  # nothing changed in the end
    env_file.unlink()  # really deleted: the defaults, once a second poll agrees
    assert source.poll() is None
    previous, current = source.poll()
    assert current.desktop_control is True and current.needs_restart(previous)


# --- secrets never reach os.environ --------------------------------------------------------------------------

def test_secret_names_in_the_root_env_never_reach_the_environment(data_root, tmp_path):
    env_file = _write_env(data_root, "\n".join([
        f"GOOGLE_API_KEY={THROWAWAY}", f"LIVEKIT_API_SECRET={THROWAWAY}", f"EMAIL_USER={THROWAWAY}",
        f"PINCHTAB_TOKEN={THROWAWAY}", f"CONTROLLER_TOKEN={THROWAWAY}", f"DESKTOP_PC_SIGNING_KEY={THROWAWAY}",
        "JARVIS_DESKTOP_CONTROL=false", "WHATSAPP_TEST_NUMBERS=+920000000000"]) + "\n")
    settings = ns.SettingsSource(env_file, data_root).load()
    inherited = {"PATH": "C:\\Windows", "VORTEX_SECURITY_TOKEN": THROWAWAY, "OPENROUTER_API_KEY": THROWAWAY,
                 "JARVIS_DESKTOP_CONTROL": "true", "SIM_LOOKUP_API_URL": THROWAWAY}
    root = ns.DataRoot(data_root, data_root / "run")

    removed = ns.apply_environment(settings, root, environ=inherited)

    assert sorted(removed) == ["OPENROUTER_API_KEY", "SIM_LOOKUP_API_URL", "VORTEX_SECURITY_TOKEN"]
    assert THROWAWAY not in json.dumps(inherited)
    assert ns.secret_names(inherited) == []
    assert inherited["JARVIS_DESKTOP_CONTROL"] == "false"  # the PC root .env is the only source
    assert inherited["WHATSAPP_TEST_NUMBERS"] == "+920000000000"
    assert inherited["JARVIS_ROLE"] == "desktop" and inherited["UI_BRIDGE_URL"] == ns.DEAD_BRIDGE_URL
    assert inherited["PATH"] == "C:\\Windows"


def test_scrub_reports_names_and_assert_clean_names_no_value():
    environ = {"A_TOKEN": THROWAWAY, "PYTHONHASHSEED": "0", "HOME": "x"}
    assert ns.scrub_secrets(environ) == ["A_TOKEN", "PYTHONHASHSEED"]
    assert environ == {"HOME": "x"}
    with pytest.raises(ns.SecretEnvError) as raised:
        ns.assert_clean({"MY_PASSWORD": THROWAWAY})
    assert "MY_PASSWORD" in str(raised.value) and THROWAWAY not in str(raised.value)
    ns.assert_clean({"HOME": "x", "JARVIS_DESKTOP_CONTROL": "true"})


def test_env_guard_refuses_a_spawn_with_a_secret_before_popen_runs(monkeypatch):
    started: list[object] = []
    # Only the env= mapping is checked here: the test process itself may hold names from earlier tests.
    guard = ns.EnvGuard(check=lambda env: ns.assert_clean(env) if env is not None else None)
    real_system = os.system
    guard.install()
    try:
        assert guard.installed and os.system is not real_system
        # A safety net: even a guard bug could never start a process here.
        monkeypatch.setattr(subprocess.Popen, "_execute_child", lambda *a, **k: started.append(a))
        with pytest.raises(ns.SecretEnvError):
            subprocess.Popen([sys.executable, "-c", "pass"],  # pylint: disable=consider-using-with
                             env={"GOOGLE_API_KEY": THROWAWAY})
    finally:
        guard.uninstall()
    assert not guard.installed and os.system is real_system
    assert not started


def test_neutralize_dotenv_makes_load_dotenv_a_noop(tmp_path, monkeypatch):
    import dotenv  # pylint: disable=import-outside-toplevel
    import dotenv.main  # pylint: disable=import-outside-toplevel

    for module in (dotenv, dotenv.main):
        monkeypatch.setattr(module, "load_dotenv", module.load_dotenv)  # restored after the test
        monkeypatch.setattr(module, "dotenv_values", module.dotenv_values)
    env_file = tmp_path / ".env"
    env_file.write_text("C120_NEUTRALIZE_PROBE=1\n", encoding="utf-8")
    monkeypatch.delenv("C120_NEUTRALIZE_PROBE", raising=False)

    ns.neutralize_dotenv()
    from dotenv import load_dotenv  # pylint: disable=import-outside-toplevel,reimported

    assert load_dotenv(env_file) is False
    assert dotenv.dotenv_values(env_file) == {}
    assert "C120_NEUTRALIZE_PROBE" not in os.environ


# --- paths and the data root ---------------------------------------------------------------------------------

def test_relative_paths_resolve_under_the_data_root_never_the_snapshot(data_root):
    env_file = _write_env(data_root, "JARVIS_OUTPUTS_DIR=Out\nJARVIS_MEDIA_DIRS=media/a;media/b\n"
                                     "WHATSAPP_CONTACTS_FILE=data/c.json\nJARVIS_SHARED_DIR=~/Shared\n")
    settings = ns.SettingsSource(env_file, data_root).load()
    snapshot = ns.code_root()
    assert settings.environ["JARVIS_OUTPUTS_DIR"] == str(data_root / "Out")
    assert settings.environ["JARVIS_MEDIA_DIRS"] == f"{data_root / 'media/a'};{data_root / 'media/b'}"
    assert settings.environ["WHATSAPP_CONTACTS_FILE"] == str(data_root / "data" / "c.json")
    assert Path(settings.environ["JARVIS_SHARED_DIR"]).is_absolute()
    for name in ("JARVIS_OUTPUTS_DIR", "WHATSAPP_CONTACTS_FILE"):
        assert not Path(settings.environ[name]).resolve().is_relative_to(snapshot.resolve())

    defaults = ns.node_defaults(data_root, data_root / "run")
    assert defaults["LOG_FILE"] == str(data_root / "logs" / "desktop_node_tools.log")  # services loggers
    assert ns.NODE_LOG_FILE.name == "desktop_node.log"  # the node's own log, one handler
    assert defaults["JARVIS_OUTPUTS_DIR"] == str(data_root / "Jarvis_Outputs")
    assert defaults["WHATSAPP_CONTACTS_FILE"] == str(data_root / "data" / "whatsapp_contacts.json")
    assert defaults["JARVIS_RUN_DIR"] == str(data_root / "run")


def test_node_config_is_read_from_the_node_home_and_never_points_into_it(tmp_path, data_root):
    home = tmp_path / "node_home"
    home.mkdir()
    environ = {ns.NODE_HOME_ENV: str(home)}
    with pytest.raises(ns.NodeConfigError, match="missing"):
        ns.load_node_config(environ=environ)

    (home / ns.NODE_CONFIG_FILE).write_text(json.dumps({"data_root": str(data_root), "node_id": "laptop-1"}),
                                            encoding="utf-8")
    cfg = ns.load_node_config(environ=environ)
    assert cfg.data_root == data_root and cfg.run_dir == data_root / "run"
    assert cfg.node_id == "laptop-1" and cfg.owner_room == ns.DEFAULT_OWNER_ROOM

    snapshot = home / "abc123"
    snapshot.mkdir()
    for bad in ({"data_root": str(snapshot)}, {"data_root": "relative/path"},
                {"data_root": str(data_root), "run_dir": str(snapshot / "run")},
                {"data_root": str(data_root), "agent_public_key": "not-hex"}):
        (home / ns.NODE_CONFIG_FILE).write_text(json.dumps(bad), encoding="utf-8")
        with pytest.raises(ns.NodeConfigError):
            ns.load_node_config(environ=environ)


def test_data_root_from_env_needs_both_absolute_variables(data_root):
    with pytest.raises(ns.NodeConfigError):
        ns.data_root_from_env({})
    with pytest.raises(ns.NodeConfigError):
        ns.data_root_from_env({ns.DATA_ROOT_ENV: "repo", ns.RUN_DIR_ENV: "repo/run"})
    root = ns.data_root_from_env({ns.DATA_ROOT_ENV: str(data_root), ns.RUN_DIR_ENV: str(data_root / "run")})
    assert root.env_file == data_root / ".env" and root.logs_dir == data_root / "logs"


def test_link_url_must_be_https():
    assert ns.link_target("https://jarvis.example.test/vortex") == ("https://jarvis.example.test", "vortex/socket.io")
    for bad in ("http://jarvis.example.test", "wss://x", "https://", "https://user@host"):
        with pytest.raises(ns.NodeConfigError):
            ns.link_target(bad)


def test_node_secrets_document_round_trip_never_shows_a_value():
    doc = ns.node_secrets_document({"PINCHTAB_TOKEN": THROWAWAY, "OTHER": "ignored"})
    assert ns.parse_node_secrets(doc) == {"PINCHTAB_TOKEN": THROWAWAY}
    for bad in (b"not json", json.dumps({"kind": "other", "v": 1}).encode(),
                json.dumps({"kind": ns.SECRETS_KIND, "v": 1, "PINCHTAB_TOKEN": 5}).encode()):
        with pytest.raises(ValueError) as raised:
            ns.parse_node_secrets(bad)
        assert THROWAWAY not in str(raised.value)
