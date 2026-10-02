"""C-112: the agent imports on a headless Linux host in remote mode (the VPS brain, K-72).

Linux is simulated on this Windows PC: a fresh interpreter blocks every GUI and Windows desktop module the
way a headless Linux host fails them (pyautogui and friends raise KeyError 'DISPLAY', the rest
NotImplementedError; winreg is missing), removes DISPLAY and imports src.core.agent_runner with
JARVIS_DESKTOP_MODE=remote. The import must succeed without touching any of them and start no PC sensor.

Also here, because the Docker image build runs them on Linux: the capture tools answer 'unavailable'
without a backend, and the voice-model download binds to the installed huggingface_hub.
"""
# pylint: disable=protected-access,redefined-outer-name
from __future__ import annotations

import importlib.util
import inspect
import json
import os
import subprocess
import sys
import textwrap
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from services.ai_core import jarvis_vision
from services.ai_core import voice_fingerprint as vf

PROJECT_ROOT = Path(__file__).resolve().parents[2]

PROBE = textwrap.dedent(
    """
    import asyncio, importlib.abc, importlib.machinery, json, os, sys, threading, traceback
    from unittest.mock import AsyncMock, MagicMock

    X11 = {"pyautogui", "mouseinfo", "pymsgbox", "pyscreeze", "pytweening", "Xlib", "pynput", "pyperclip"}
    DESKTOP = {"pygetwindow", "pywinauto", "pyrect", "win32api", "win32gui", "win32con", "win32clipboard",
               "win32process", "win32com", "win32ui", "win32event", "win32file", "pythoncom", "pywintypes",
               "comtypes", "pycaw", "cv2", "wmi", "screen_brightness_control", "pystray", "win10toast",
               "winotify", "uiautomation", "pygame"}
    MISSING = {"winreg"}  # stdlib on Windows only; the code already handles its absence
    attempts = []

    class Raise(importlib.abc.Loader):
        def __init__(self, exc):
            self.exc = exc
        def create_module(self, spec):
            return None
        def exec_module(self, module):
            raise self.exc

    class HeadlessLinux(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path, target=None):
            top = name.split(".")[0]
            if top in X11:
                exc = KeyError("DISPLAY")
            elif top in DESKTOP:
                exc = NotImplementedError(top + " is not supported on this platform")
            elif top in MISSING:
                exc = ModuleNotFoundError("No module named " + repr(top))
            else:
                return None
            if top not in MISSING:
                attempts.append(name)
            return importlib.machinery.ModuleSpec(name, Raise(exc))

    for name in list(sys.modules):
        if name.split(".")[0] in X11 | DESKTOP | MISSING:
            del sys.modules[name]
    sys.meta_path.insert(0, HeadlessLinux())
    threads_before = {thread.ident for thread in threading.enumerate()}
    result = {"ok": False}
    try:
        from src.core import agent_runner
        from src.core.vision_handler import VisionHandler
        result["ok"] = True
    except BaseException:
        result["error"] = traceback.format_exc()[-4000:]
        print("RESULT " + json.dumps(result))
        raise SystemExit(0)

    result["new_threads"] = [t.name for t in threading.enumerate() if t.ident not in threads_before]
    result["clipboard_monitor_is_none"] = agent_runner.ClipboardMonitor is None
    desktop_modules = ("services.automation.jarvis_clipboard", "services.system.jarvis_window_ctrl",
                       "services.system.jarvis_desktop_common", "services.automation.keyboard_mouse_ctrl",
                       "services.automation.jarvis_whatsapp_automation", "services.utils.jarvis_win32")
    result["desktop_modules_loaded"] = [name for name in desktop_modules if name in sys.modules]

    async def session_start():
        handler = VisionHandler(MagicMock())
        handler.start_loop()
        result["window_poller"] = handler._proactive_vision_task is not None
        names = []
        agent_runner.autonomous_protector.run_protected = AsyncMock(side_effect=lambda name, *a: names.append(name))
        tasks = await agent_runner._start_background_tasks(MagicMock(), MagicMock())
        result["protected"] = names
        result["extra_tasks"] = len(tasks)

    asyncio.run(session_start())
    result["gui_attempts"] = attempts
    print("RESULT " + json.dumps(result))
    """
)


@pytest.fixture(scope="module")
def headless_import() -> dict:
    env = {name: value for name, value in os.environ.items() if name not in ("DISPLAY", "PYTEST_CURRENT_TEST")}
    env.update({
        "JARVIS_ROLE": "agent", "JARVIS_DESKTOP_MODE": "remote", "JARVIS_NO_LOCK": "1",
        "UI_BRIDGE_URL": "http://127.0.0.1:9", "PYTHONIOENCODING": "utf-8", "PYTHONPATH": str(PROJECT_ROOT),
    })
    result = subprocess.run(
        [sys.executable, "-c", PROBE], cwd=PROJECT_ROOT, env=env, capture_output=True, encoding="utf-8",
        errors="replace", timeout=600, check=False,
    )
    lines = [line for line in result.stdout.splitlines() if line.startswith("RESULT ")]
    assert result.returncode == 0 and lines, (result.stdout[-2000:], result.stderr[-4000:])
    return json.loads(lines[-1][len("RESULT "):])


def test_agent_runner_imports_on_a_headless_linux_host_in_remote_mode(headless_import):
    assert headless_import["ok"], headless_import.get("error")


def test_the_import_tries_no_gui_or_desktop_module(headless_import):
    assert headless_import["gui_attempts"] == []
    assert headless_import["desktop_modules_loaded"] == []
    assert headless_import["clipboard_monitor_is_none"] is True


def test_the_import_and_a_session_start_no_pc_sensor(headless_import):
    assert headless_import["new_threads"] == []  # no clipboard, window or audio thread at import
    assert headless_import["window_poller"] is False
    assert headless_import["protected"] == ["memory_loop", "heartbeat_loop", "ui_command_listener",
                                            "bug_hunter_loop", "memory_storage_loop", "adaptation_loop",
                                            "healing_loop"]  # no reminder loop: agent-svc owns it
    assert headless_import["extra_tasks"] == 0  # no clipboard monitor task


# ---------------------------------------------------------------- capture tools without a backend


@pytest.fixture
def no_backend(monkeypatch):
    """jarvis_vision with no cached backend and an import that fails like a headless host."""
    failures = {"pyautogui": KeyError("DISPLAY"), "cv2": NotImplementedError("no OpenCV build")}

    def import_module(name):
        raise failures[name]

    monkeypatch.setattr(jarvis_vision, "pyautogui", None)
    monkeypatch.setattr(jarvis_vision, "cv2", None)
    monkeypatch.setattr(jarvis_vision, "importlib", types.SimpleNamespace(import_module=import_module))
    monkeypatch.setattr(jarvis_vision.vision_system, "latest_frame", None)


def test_importing_jarvis_vision_loads_no_capture_backend():
    probe = ("import sys; import services.ai_core.jarvis_vision as v; "
             "print(sorted(m for m in ('pyautogui', 'cv2') if m in sys.modules), v.pyautogui, v.cv2)")
    env = {**os.environ, "UI_BRIDGE_URL": "http://127.0.0.1:9", "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run([sys.executable, "-c", probe], cwd=PROJECT_ROOT, env=env, capture_output=True,
                            encoding="utf-8", errors="replace", timeout=300, check=False)
    assert result.returncode == 0, result.stderr[-3000:]
    assert result.stdout.strip().splitlines()[-1] == "[] None None"


@pytest.mark.asyncio
async def test_screen_analysis_is_unavailable_without_pyautogui(no_backend):
    result = await jarvis_vision.analyze_screen("what is on my screen?")
    assert result["status"] == "unavailable"
    assert "KeyError" in result["error"] and "pyautogui" in result["error"]


@pytest.mark.asyncio
async def test_camera_analysis_is_unavailable_without_a_frame_or_opencv(no_backend):
    result = await jarvis_vision.analyze_camera("what do you see?")
    assert result["status"] == "unavailable"
    assert "NotImplementedError" in result["error"]


@pytest.mark.asyncio
async def test_a_ui_frame_needs_no_opencv(no_backend, monkeypatch):
    monkeypatch.setattr(jarvis_vision.vision_system, "latest_frame", b"jpeg")
    assert await jarvis_vision.vision_system.capture_webcam() == b"jpeg"


# ---------------------------------------------------------------- huggingface_hub (installed 1.16.x)


def test_the_voice_model_download_binds_to_the_installed_huggingface_hub(monkeypatch, tmp_path):
    import huggingface_hub  # pylint: disable=import-outside-toplevel

    spec = importlib.util.spec_from_file_location("jarvis_download_voice_model",
                                                  PROJECT_ROOT / "scripts" / "models" / "download_voice_model.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    real_signature = inspect.signature(huggingface_hub.snapshot_download)
    calls = []

    def snapshot_download(*args, **kwargs):
        calls.append(real_signature.bind(*args, **kwargs))  # TypeError on any argument it would reject
        return str(tmp_path)

    monkeypatch.setattr(module, "snapshot_download", snapshot_download)
    monkeypatch.setattr(module, "TARGET_DIR", tmp_path / "spkrec-ecapa-voxceleb")
    module.main()
    assert len(calls) == 1
    bound = calls[0]
    assert bound.arguments["repo_id"] == "speechbrain/spkrec-ecapa-voxceleb"
    assert bound.arguments["local_dir"] == str(tmp_path / "spkrec-ecapa-voxceleb")


def test_voice_id_hub_patches_pass_only_arguments_the_hub_accepts(monkeypatch):
    def hf_hub_download(repo_id, filename, *, revision=None, cache_dir=None, token=None):
        return f"{repo_id}/{filename}:{token}"

    def snapshot_download(repo_id, *, local_dir=None, token=None):
        return f"{repo_id}->{local_dir}:{token}"

    fake_hub = types.ModuleType("huggingface_hub")  # like 1.16: no local_dir_use_symlinks, no **kwargs
    fake_hub.hf_hub_download = hf_hub_download
    fake_hub.snapshot_download = snapshot_download
    fake_torchaudio = types.ModuleType("torchaudio")
    fake_transforms = types.ModuleType("torchaudio.transforms")
    fake_torchaudio.transforms = fake_transforms
    fake_torchaudio.load = MagicMock()
    fake_speaker = types.ModuleType("speechbrain.inference.speaker")
    fake_speaker.SpeakerRecognition = type("SpeakerRecognition", (), {})
    fake_modules = {
        "huggingface_hub": fake_hub, "numpy": types.ModuleType("numpy"), "soundfile": types.ModuleType("soundfile"),
        "torch": types.ModuleType("torch"), "torchaudio": fake_torchaudio, "torchaudio.transforms": fake_transforms,
        "speechbrain": types.ModuleType("speechbrain"), "speechbrain.inference": types.ModuleType("speechbrain.inference"),
        "speechbrain.inference.speaker": fake_speaker,
    }
    module_globals = {"_AUDIO_DEPS_READY": False, "np": None, "sf": None, "torch": None, "torchaudio": None,
                      "huggingface_hub": None, "SpeakerRecognition": None}
    monkeypatch.setattr(os, "symlink", os.symlink)  # _load_audio_deps replaces it on Windows; restored after
    with patch.dict(sys.modules, fake_modules), patch.multiple(vf, **module_globals):
        vf._ensure_audio_deps()
        # SpeechBrain 1.0 passes use_auth_token; older callers may pass local_dir_use_symlinks.
        # pylint: disable=no-member  # the fake module's functions are set at runtime
        assert fake_hub.hf_hub_download(repo_id="r", filename="f", use_auth_token=False) == "r/f:False"
        assert fake_hub.hf_hub_download(repo_id="r", filename="f", local_dir_use_symlinks=False) == "r/f:None"
        assert fake_hub.snapshot_download(repo_id="r", local_dir="d", local_dir_use_symlinks=False) == "r->d:None"
