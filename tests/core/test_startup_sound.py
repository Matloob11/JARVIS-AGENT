"""C-91: the startup sound (src/core/agent.py) tries JARVIS_STARTUP_SOUND, the same name as .wav,
then the bundled chime, and plays the first file pygame can load from a daemon thread. pygame is
always a fake here and every real thread is joined: tests never play audio."""
# pylint: disable=redefined-outer-name, too-many-positional-arguments
import importlib
import io
import os
import runpy
import sys
import threading
import types
from unittest.mock import MagicMock, call, patch

import pytest


def _fake_entry_modules():
    # Same light import as test_agent_entry.py: no real livekit, no real agent_runner.
    fake_agents = types.SimpleNamespace(cli=types.SimpleNamespace(run_app=MagicMock()))
    fake_livekit = types.ModuleType("livekit")
    fake_livekit.agents = fake_agents
    fake_runner = types.ModuleType("src.core.agent_runner")
    fake_runner.entrypoint = MagicMock(name="entrypoint")
    fake_runner.build_worker_options = MagicMock(name="build_worker_options")
    return {"livekit": fake_livekit, "livekit.agents": fake_agents, "src.core.agent_runner": fake_runner}


@pytest.fixture(scope="module")
def agent():
    with patch.dict(sys.modules, _fake_entry_modules()):
        sys.modules.pop("src.core.agent", None)  # patch.dict puts any earlier import back
        return importlib.import_module("src.core.agent")


@pytest.fixture
def sounds(agent, monkeypatch, tmp_path):
    """JARVIS_STARTUP_SOUND -> tmp/mine.mp3 and the bundled chime -> tmp/chime.wav (none exist yet)."""
    monkeypatch.setenv("JARVIS_STARTUP_SOUND", str(tmp_path / "mine.mp3"))
    monkeypatch.setattr(agent, "STARTUP_CHIME", str(tmp_path / "chime.wav"))
    return tmp_path


@pytest.fixture
def pygame(monkeypatch):
    fake = MagicMock(name="pygame")
    fake.error = type("error", (RuntimeError,), {})  # like pygame.error
    fake.mixer.music.get_busy.return_value = False
    monkeypatch.setitem(sys.modules, "pygame", fake)
    return fake


def _stdout_like_main(monkeypatch):
    """Like __main__: sys.stdout becomes a TextIOWrapper without line buffering over the log file."""
    log = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(log, encoding="utf-8"))
    return log


def _play(agent, monkeypatch):
    """The real play_startup_voice (daemon thread, no delay); returns once the thread is done."""
    monkeypatch.setattr(agent, "running_under_pytest", False)
    thread = agent.play_startup_voice(delay_s=0)
    thread.join(timeout=10)
    assert thread.daemon and not thread.is_alive()


@pytest.mark.parametrize("value", [None, "", "   "])
def test_default_order_is_the_mp3_then_its_wav_then_the_bundled_chime(agent, monkeypatch, value):
    if value is None:
        monkeypatch.delenv("JARVIS_STARTUP_SOUND", raising=False)
    else:
        monkeypatch.setenv("JARVIS_STARTUP_SOUND", value)
    audio = os.path.join(agent.root_dir, "assets", "audio")

    candidates = agent.startup_sound_candidates()

    expected = [
        os.path.join(audio, "Installing... Ironman Jarvis Ai.mp3"),
        os.path.join(audio, "Installing... Ironman Jarvis Ai.wav"),
        os.path.join(audio, "jarvis_startup_chime.wav"),
    ]
    assert list(candidates.items()) == [(path, path) for path in expected]  # default paths are logged
    assert os.path.isfile(expected[-1])  # the X-02 chime ships with the repo


def test_a_relative_env_path_starts_at_the_repo_root_not_the_cwd(agent, monkeypatch, tmp_path):
    (tmp_path / "elsewhere").mkdir()
    monkeypatch.chdir(tmp_path / "elsewhere")
    monkeypatch.setattr(agent, "root_dir", str(tmp_path / "repo"))
    monkeypatch.setenv("JARVIS_STARTUP_SOUND", os.path.join("sounds", "boot.mp3"))

    first, twin = list(agent.startup_sound_candidates())[:2]

    assert first == str(tmp_path / "repo" / "sounds" / "boot.mp3")
    assert twin == str(tmp_path / "repo" / "sounds" / "boot.wav")


def test_a_home_relative_env_path_is_expanded(agent, monkeypatch, tmp_path):
    for var in ("HOME", "USERPROFILE"):  # expanduser reads USERPROFILE on Windows, HOME elsewhere
        monkeypatch.setenv(var, str(tmp_path / "home"))
    monkeypatch.setenv("JARVIS_STARTUP_SOUND", "~/boot.mp3")

    assert list(agent.startup_sound_candidates())[0] == str(tmp_path / "home" / "boot.mp3")


def test_a_configured_wav_is_listed_once(agent, sounds, monkeypatch):
    monkeypatch.setenv("JARVIS_STARTUP_SOUND", str(sounds / "mine.wav"))

    assert list(agent.startup_sound_candidates().items()) == [
        (str(sounds / "mine.wav"), "JARVIS_STARTUP_SOUND"),
        (str(sounds / "chime.wav"), str(sounds / "chime.wav")),
    ]


@pytest.mark.parametrize("present, expected, logged", [
    (("mine.mp3", "mine.wav", "chime.wav"), "mine.mp3", "JARVIS_STARTUP_SOUND"),
    (("mine.wav", "chime.wav"), "mine.wav", "JARVIS_STARTUP_SOUND as .wav"),
    (("chime.wav",), "chime.wav", "chime.wav"),
])
def test_the_first_existing_file_plays(agent, monkeypatch, sounds, pygame, capsys, present, expected,
                                       logged):
    for name in present:
        (sounds / name).write_bytes(b"RIFF")

    _play(agent, monkeypatch)

    pygame.mixer.music.load.assert_called_once_with(str(sounds / expected))
    pygame.mixer.music.play.assert_called_once_with()
    # K-69: a JARVIS_STARTUP_SOUND file is logged by the variable's name, never by its path.
    assert capsys.readouterr().out.splitlines() == [f"[JARVIS-SYSTEM] initializing neural voice: {logged}"]


def test_a_directory_is_not_a_sound(agent, monkeypatch, sounds, pygame):
    (sounds / "mine.mp3").mkdir()
    (sounds / "chime.wav").write_bytes(b"RIFF")

    _play(agent, monkeypatch)

    pygame.mixer.music.load.assert_called_once_with(str(sounds / "chime.wav"))


def test_the_log_line_follows_play_and_is_written_at_once(agent, monkeypatch, pygame, tmp_path):
    # As on this PC: JARVIS_STARTUP_SOUND unset, no MP3 or .wav, only the bundled chime.
    monkeypatch.delenv("JARVIS_STARTUP_SOUND", raising=False)
    monkeypatch.setattr(agent, "root_dir", str(tmp_path))
    chime = tmp_path / agent.STARTUP_CHIME
    chime.parent.mkdir(parents=True)
    chime.write_bytes(b"RIFF")
    log = _stdout_like_main(monkeypatch)
    logged_at_play = []
    pygame.mixer.music.play.side_effect = lambda: logged_at_play.append(log.getvalue())

    _play(agent, monkeypatch)

    assert logged_at_play == [b""]  # the line never claims a sound that did not start
    # Nobody flushed the wrapper: the line must already be in the file (agent_stdout.log).
    assert log.getvalue().decode().splitlines() == [
        "[JARVIS-SYSTEM] initializing neural voice: jarvis_startup_chime.wav"]
    pygame.mixer.init.assert_called_once_with()
    pygame.mixer.music.load.assert_called_once_with(str(chime))
    pygame.mixer.music.set_volume.assert_called_once_with(0.85)
    pygame.mixer.music.play.assert_called_once_with()


def test_an_unplayable_file_falls_through_to_the_next_one(agent, monkeypatch, sounds, pygame, capsys):
    (sounds / "mine.mp3").write_bytes(b"")  # e.g. an interrupted copy
    (sounds / "chime.wav").write_bytes(b"RIFF")
    broken = str(sounds / "mine.mp3")

    def load(path):
        if path == broken:
            raise pygame.error("music_drmp3: corrupt mp3 file (bad tags).")

    pygame.mixer.music.load.side_effect = load

    _play(agent, monkeypatch)

    assert pygame.mixer.music.load.call_args_list == [call(broken), call(str(sounds / "chime.wav"))]
    pygame.mixer.music.play.assert_called_once_with()
    assert capsys.readouterr().out.splitlines() == [
        "[JARVIS-WARN] Startup voice JARVIS_STARTUP_SOUND unplayable: "
        "music_drmp3: corrupt mp3 file (bad tags).",
        "[JARVIS-SYSTEM] initializing neural voice: chime.wav",
    ]


def test_when_no_file_loads_nothing_claims_to_play(agent, monkeypatch, sounds, pygame, capsys):
    (sounds / "mine.mp3").write_bytes(b"")
    (sounds / "chime.wav").write_bytes(b"")
    pygame.mixer.music.load.side_effect = pygame.error("Unknown WAVE format")

    _play(agent, monkeypatch)

    assert capsys.readouterr().out.splitlines() == [
        "[JARVIS-WARN] Startup voice JARVIS_STARTUP_SOUND unplayable: Unknown WAVE format",
        "[JARVIS-WARN] Startup voice chime.wav unplayable: Unknown WAVE format",
    ]
    pygame.mixer.music.play.assert_not_called()


def test_a_configured_path_is_never_logged_even_in_pygame_errors(agent, monkeypatch, sounds, pygame,
                                                                  capsys):
    (sounds / "mine.mp3").write_bytes(b"ID3")
    (sounds / "mine.wav").write_bytes(b"RIFF")
    mp3 = str(sounds / "mine.mp3")

    def load(path):
        if path == mp3:  # the wording pygame uses when a file vanished after the check
            raise pygame.error(f"No file '{path}' found in working directory 'C:\\work'.")

    pygame.mixer.music.load.side_effect = load

    _play(agent, monkeypatch)

    out = capsys.readouterr().out
    assert str(sounds) not in out and "mine" not in out
    assert out.splitlines() == [
        "[JARVIS-WARN] Startup voice JARVIS_STARTUP_SOUND unplayable: "
        "No file 'JARVIS_STARTUP_SOUND' found in working directory 'C:\\work'.",
        "[JARVIS-SYSTEM] initializing neural voice: JARVIS_STARTUP_SOUND as .wav",
    ]


def test_no_file_logs_one_info_line_at_once_and_never_loads_the_player(agent, monkeypatch, sounds,
                                                                        pygame):
    log = _stdout_like_main(monkeypatch)

    _play(agent, monkeypatch)

    # Nobody flushed the wrapper. K-69: the configured path appears as the variable's name only.
    tried = f"JARVIS_STARTUP_SOUND | JARVIS_STARTUP_SOUND as .wav | {sounds / 'chime.wav'}"
    assert log.getvalue().decode().splitlines() == [
        f"[JARVIS-INFO] Startup voice not found; tried: {tried}"]
    pygame.mixer.init.assert_not_called()


def test_missing_pygame_only_warns(agent, monkeypatch, sounds, capsys):
    (sounds / "mine.mp3").write_bytes(b"ID3")
    monkeypatch.setitem(sys.modules, "pygame", None)  # 'import pygame' raises ImportError

    _play(agent, monkeypatch)

    assert capsys.readouterr().out.splitlines() == [
        "[JARVIS-WARN] pygame not installed; skipping startup voice"]


@pytest.mark.parametrize("step", ["init", "play"])
def test_player_errors_never_leave_the_thread(agent, monkeypatch, sounds, pygame, capsys, step):
    (sounds / "mine.mp3").write_bytes(b"ID3")
    failing = pygame.mixer.init if step == "init" else pygame.mixer.music.play
    failing.side_effect = RuntimeError("no audio device")

    _play(agent, monkeypatch)

    assert capsys.readouterr().out.splitlines() == ["[JARVIS-ERROR] System voice fail: no audio device"]


def test_the_worker_starts_one_daemon_thread_with_the_10_s_delay(agent, monkeypatch):
    started = []

    class FakeThread:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def start(self):
            started.append(self.kwargs)

    monkeypatch.setattr(agent, "running_under_pytest", False)
    monkeypatch.setattr(threading, "Thread", FakeThread)

    thread = agent.play_startup_voice()

    assert started == [thread.kwargs]
    assert thread.kwargs["daemon"] is True and thread.kwargs["args"] == (10,)


def test_under_pytest_no_thread_starts(agent, monkeypatch):
    monkeypatch.setattr(threading, "Thread", MagicMock(side_effect=AssertionError("thread started")))

    assert agent.running_under_pytest  # computed at import, pytest is loaded
    assert agent.play_startup_voice() is None


def test_the_main_block_under_pytest_starts_no_player(monkeypatch):
    # The real chime exists now, so a player thread here would sound about 10 s into the test run.
    monkeypatch.setattr(threading, "Thread", MagicMock(side_effect=AssertionError("thread started")))
    with patch.dict(sys.modules, _fake_entry_modules()):
        with patch("sys.stdout", new=io.TextIOWrapper(io.BytesIO(), encoding="utf-8")):
            runpy.run_module("src.core.agent", run_name="__main__")
