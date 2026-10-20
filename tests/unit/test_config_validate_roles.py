"""C-112: config.validate(role, mode) asks each (role, mode) pair only for the keys it uses (K-72), and
validate() without arguments keeps the original check.

Every test builds a fresh JarvisConfig with the relevant variables removed and load_dotenv switched off, so
the real .env files are never read. The values below are fakes; no test prints one.
"""
# pylint: disable=redefined-outer-name
from __future__ import annotations

import logging
from collections.abc import Callable

import pytest

from services.utils import jarvis_config
from services.utils.jarvis_config import JarvisConfig, RuntimeProfile

ENV_NAMES = (
    "GOOGLE_API_KEY", "VORTEX_SECURITY_TOKEN", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "AGENT_TOKEN",
    "BRIDGE_HMAC_SECRET", "JARVIS_ENCRYPTION_KEY", "DESKTOP_AGENT_SIGNING_KEY", "DESKTOP_PC_SIGNING_KEY",
    "DESKTOP_AGENT_PUBLIC_KEY", "DESKTOP_PC_PUBLIC_KEY", "LEGACY_TOKEN_AUTH", "VORTEX_ENV", "JARVIS_ROLE",
    "JARVIS_BRAIN", "JARVIS_DESKTOP_MODE", "JARVIS_USER_ID",
)
PAIRS = [(role, mode) for role in ("agent", "svc", "bridge", "desktop") for mode in ("local", "remote")]
# What each pair needs, written out here rather than read from the code under test.
NEEDS: dict[tuple[str, str], set[str]] = {
    ("agent", "local"): {"GOOGLE_API_KEY", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "VORTEX_SECURITY_TOKEN"},
    ("agent", "remote"): {"GOOGLE_API_KEY", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET",
                          "JARVIS_ENCRYPTION_KEY", "DESKTOP_AGENT_SIGNING_KEY"},
    ("svc", "local"): {"JARVIS_ENCRYPTION_KEY", "DESKTOP_AGENT_SIGNING_KEY"},
    ("svc", "remote"): {"JARVIS_ENCRYPTION_KEY", "DESKTOP_AGENT_SIGNING_KEY"},
    ("bridge", "local"): {"LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "VORTEX_SECURITY_TOKEN"},
    ("bridge", "remote"): {"LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "VORTEX_SECURITY_TOKEN"},
    ("desktop", "local"): {"DESKTOP_PC_SIGNING_KEY", "DESKTOP_AGENT_PUBLIC_KEY"},
    ("desktop", "remote"): {"DESKTOP_PC_SIGNING_KEY", "DESKTOP_AGENT_PUBLIC_KEY"},
}
# Missing these raises even outside production: memory written with a throw-away key is lost.
FATAL = {"JARVIS_ENCRYPTION_KEY"}

# Env layouts (names only; K-47). Old = one file per host; new = the split C-127 makes.
OLD_PC_ENV = {"GOOGLE_API_KEY", "VORTEX_SECURITY_TOKEN", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET",
              "JARVIS_ENCRYPTION_KEY"}
NEW_PC_ENV = {"GOOGLE_API_KEY", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "AGENT_TOKEN", "BRIDGE_HMAC_SECRET",
              "JARVIS_ENCRYPTION_KEY"}
AGENT_ENV = {"GOOGLE_API_KEY", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "JARVIS_ENCRYPTION_KEY",
             "DESKTOP_AGENT_SIGNING_KEY", "DESKTOP_AGENT_PUBLIC_KEY", "DESKTOP_PC_PUBLIC_KEY"}
OLD_BRIDGE_ENV = {"LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "VORTEX_SECURITY_TOKEN", "GOOGLE_API_KEY",
                  "JARVIS_ENCRYPTION_KEY"}
NEW_BRIDGE_ENV = {"LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "VORTEX_SECURITY_TOKEN", "AGENT_TOKEN",
                  "BRIDGE_HMAC_SECRET", "DESKTOP_AGENT_PUBLIC_KEY", "DESKTOP_PC_PUBLIC_KEY"}


def _fake(name: str) -> str:
    return f"c112-fake-{name.lower()}-0123456789"


@pytest.fixture
def make_config(monkeypatch, tmp_path) -> Callable[..., JarvisConfig]:
    """make_config(names, production=False, **extra) -> a fresh JarvisConfig seeing only those names."""
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(jarvis_config, "load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.setenv("JARVIS_SHARED_DIR", str(tmp_path / "shared"))

    def build(names=(), production: bool = False, **extra: str) -> JarvisConfig:
        for name in names:
            monkeypatch.setenv(name, _fake(name))
        for name, value in extra.items():
            monkeypatch.setenv(name, value)
        if production:
            monkeypatch.setenv("VORTEX_ENV", "production")
        return JarvisConfig()

    return build


@pytest.fixture
def config_log(caplog):
    caplog.set_level(logging.WARNING, logger="JARVIS-CONFIG")
    return caplog


def _critical(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "JARVIS-CONFIG" and r.levelno == logging.CRITICAL]


# ------------------------------------------------------------------ validate() without arguments


def test_no_arguments_keep_the_original_check(make_config, config_log):
    assert make_config(OLD_PC_ENV).validate() is True
    assert not _critical(config_log)


def test_no_arguments_ignore_role_and_mode(make_config, config_log):
    # ui_bridge.py calls validate() at import: the role/mode variables must not change that check.
    cfg = make_config(OLD_PC_ENV - {"GOOGLE_API_KEY"}, JARVIS_ROLE="bridge", JARVIS_DESKTOP_MODE="remote")
    assert cfg.validate() is False
    assert _critical(config_log) == ["❌ CRITICAL CONFIG MISSING: GOOGLE_API_KEY"]


def test_no_arguments_never_ask_for_the_new_keys(make_config):
    cfg = make_config({"GOOGLE_API_KEY", "VORTEX_SECURITY_TOKEN", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"},
                      production=True)
    assert cfg.validate() is True


def test_no_arguments_raise_in_production_as_before(make_config):
    cfg = make_config(OLD_PC_ENV - {"VORTEX_SECURITY_TOKEN"}, production=True)
    with pytest.raises(RuntimeError, match=r"Production safety check failed: Missing \['VORTEX_SECURITY_TOKEN'\]"):
        cfg.validate()


# ------------------------------------------------------------------ every (role, mode) pair


@pytest.mark.parametrize(("role", "mode"), PAIRS)
def test_each_pair_passes_with_exactly_its_keys(make_config, config_log, role, mode):
    cfg = make_config(NEEDS[(role, mode)], production=True)
    assert cfg.validate(role, mode) is True
    assert not _critical(config_log)


@pytest.mark.parametrize(("role", "mode", "name"),
                         [(role, mode, name) for role, mode in PAIRS for name in sorted(NEEDS[(role, mode)])])
def test_each_missing_key_is_named_and_raises_in_production(make_config, config_log, role, mode, name):
    keys = NEEDS[(role, mode)] - {name}
    if name in FATAL:
        with pytest.raises(RuntimeError, match="refusing to start"):
            make_config(keys).validate(role, mode)
    else:
        assert make_config(keys).validate(role, mode) is False
        with pytest.raises(RuntimeError, match="Production safety check failed"):
            make_config(keys, production=True).validate(role, mode)
    messages = _critical(config_log)
    assert messages and all(name in message for message in messages)


@pytest.mark.parametrize(("role", "mode"), PAIRS)
def test_no_value_is_ever_logged_or_raised(make_config, config_log, role, mode):
    names = set(ENV_NAMES) - {"LEGACY_TOKEN_AUTH", "VORTEX_ENV", "JARVIS_ROLE", "JARVIS_BRAIN",
                              "JARVIS_DESKTOP_MODE", "JARVIS_USER_ID"}
    cfg = make_config(names - NEEDS[(role, mode)], production=True)
    with pytest.raises(RuntimeError) as raised:
        cfg.validate(role, mode)
    text = str(raised.value) + "\n".join(r.getMessage() for r in config_log.records)
    assert not any(_fake(name) in text for name in names)


def test_role_and_mode_default_to_the_environment(make_config):
    cfg = make_config(AGENT_ENV, JARVIS_ROLE="agent", JARVIS_DESKTOP_MODE="remote")
    assert cfg.validate(role="agent") is True  # mode from JARVIS_DESKTOP_MODE: remote keys present
    assert cfg.validate(mode="remote") is True  # role from JARVIS_ROLE
    assert cfg.validate(mode="local") is False  # the PC brain needs a bridge credential


@pytest.mark.parametrize(("role", "mode"), [("brain", "local"), ("agent", "vps"), ("", "pc")])
def test_unknown_role_or_mode_is_a_programming_error(make_config, role, mode):
    with pytest.raises(ValueError):
        make_config(AGENT_ENV).validate(role, mode)


def test_role_and_mode_ignore_case_and_spaces(make_config):
    assert make_config(AGENT_ENV).validate(" Agent", "REMOTE ") is True


# ------------------------------------------------------------------ both env layouts


def test_pc_brain_accepts_the_old_and_the_new_pc_env(make_config):
    assert make_config(OLD_PC_ENV, production=True).validate("agent", "local") is True
    assert make_config(NEW_PC_ENV, production=True).validate("agent", "local") is True


def test_pc_brain_needs_a_whole_bridge_credential(make_config, config_log):
    cfg = make_config(NEW_PC_ENV - {"BRIDGE_HMAC_SECRET"})
    assert cfg.validate("agent", "local") is False
    assert _critical(config_log) == [
        "❌ CRITICAL CONFIG MISSING (agent/local): VORTEX_SECURITY_TOKEN or AGENT_TOKEN+BRIDGE_HMAC_SECRET"]


def test_vps_agent_needs_no_bridge_token(make_config):
    assert make_config(AGENT_ENV, production=True).validate("agent", "remote") is True
    assert make_config(AGENT_ENV, production=True).validate("svc", "remote") is True


def test_bridge_accepts_the_old_and_the_split_env(make_config):
    assert make_config(OLD_BRIDGE_ENV, production=True).validate("bridge") is True
    assert make_config(NEW_BRIDGE_ENV, production=True).validate("bridge") is True


def test_bridge_never_asks_for_agent_secrets(make_config):
    # After the split (C-127 V2b) bridge.env has no AI or encryption key.
    cfg = make_config({"LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "VORTEX_SECURITY_TOKEN"}, production=True)
    assert cfg.validate("bridge", "remote") is True


@pytest.mark.parametrize(("flag", "needs_token"), [("1", True), ("true", True), ("0", False), ("off", False)])
def test_bridge_needs_the_legacy_token_only_while_it_is_in_use(make_config, flag, needs_token):
    cfg = make_config({"LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"}, LEGACY_TOKEN_AUTH=flag)
    assert cfg.validate("bridge") is (not needs_token)


def test_node_key_held_only_in_memory_counts(make_config):
    cfg = make_config({"DESKTOP_AGENT_PUBLIC_KEY"})
    assert cfg.validate("desktop") is False
    cfg.desktop_pc_signing_key = _fake("DESKTOP_PC_SIGNING_KEY")  # unwrapped from DPAPI, never in the env
    assert cfg.validate("desktop") is True


def test_blank_values_count_as_missing(make_config):
    cfg = make_config(AGENT_ENV - {"DESKTOP_AGENT_SIGNING_KEY"}, DESKTOP_AGENT_SIGNING_KEY="   ")
    assert cfg.validate("agent", "remote") is False


# ------------------------------------------------------------------ runtime profile and user id


def test_profile_defaults_are_the_pc_brain(make_config):
    cfg = make_config()
    assert jarvis_config.runtime_profile() == RuntimeProfile("agent", "pc", "local")
    assert (cfg.role, cfg.brain, cfg.desktop_mode) == ("agent", "pc", "local")
    assert jarvis_config.is_local_mode() is True
    assert cfg.user_id == jarvis_config.DEFAULT_USER_ID == "matloob"  # C-52 (K-69): unset means the owner's id


def test_profile_is_read_from_the_environment(make_config, monkeypatch):
    cfg = make_config(JARVIS_ROLE=" SVC ", JARVIS_BRAIN="vps", JARVIS_DESKTOP_MODE="Remote", JARVIS_USER_ID="matloob")
    assert jarvis_config.runtime_profile() == RuntimeProfile("svc", "vps", "remote")
    assert jarvis_config.is_local_mode() is False
    assert cfg.user_id == "matloob"
    monkeypatch.setenv("JARVIS_DESKTOP_MODE", "local")  # read on every call, not cached
    assert cfg.desktop_mode == "local"


def test_an_unknown_value_falls_back_with_one_warning_naming_only_the_variable(make_config, monkeypatch,
                                                                                config_log):
    monkeypatch.setattr(jarvis_config, "_warned_choices", set())
    make_config(JARVIS_DESKTOP_MODE="laptop-secret-ish")
    assert jarvis_config.desktop_mode() == "local"
    assert jarvis_config.desktop_mode() == "local"
    warnings = [r.getMessage() for r in config_log.records
                if r.levelno == logging.WARNING and "JARVIS_DESKTOP_MODE" in r.getMessage()]
    assert warnings == ["JARVIS_DESKTOP_MODE is not one of local/remote; using local"]
