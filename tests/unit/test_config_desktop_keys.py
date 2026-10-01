"""Desktop config keys (contract K-15) and scripts/ops/env_set.py (plan task C-26).

The config tests build a fresh JarvisConfig with every K-15 variable removed, load_dotenv switched off (the
real .env files are never read) and the home folder inside tmp_path. The env_set tests only edit files under
tmp_path and check that no value ever reaches the output.
"""
# pylint: disable=protected-access,redefined-outer-name
from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from dotenv import dotenv_values

from services.utils import jarvis_config
from services.utils.jarvis_config import APP_MAPPINGS, JarvisConfig

ROOT = Path(__file__).resolve().parents[2]
ENV_SET_PY = ROOT / "scripts" / "ops" / "env_set.py"

K15_ENV = (
    "JARVIS_OUTPUTS_DIR", "JARVIS_SHARED_DIR", "JARVIS_MEDIA_DIRS", "JARVIS_OPEN_ROOTS",
    "JARVIS_FILE_SERVER_ROOT", "JARVIS_FILE_SERVER_PORT", "JARVIS_FILE_SERVER_TTL_MIN",
    "JARVIS_DESKTOP_CONTROL", "JARVIS_CLIPBOARD_HELPER", "JARVIS_CLIPBOARD_SPEAK",
    "JARVIS_CONFIRM_TIMEOUT_S", "JARVIS_VOICE_CONFIRM_HIGH", "JARVIS_CHROME_PATH", "JARVIS_EDGE_PATH",
    "JARVIS_PLAYWRIGHT_CHANNEL", "PINCHTAB_URL", "PINCHTAB_TOKEN", "PINCHTAB_BIN", "PINCHTAB_AUTOSTART",
    "PINCHTAB_ALLOWED_DOMAINS", "FFMPEG_DIR", "WHATSAPP_APP_ID", "WHATSAPP_CONTACTS_FILE",
    "CONTROLLER_TOKEN", "JARVIS_CV_PATH",
)
K15_ATTRS = (
    "outputs_dir", "shared_dir", "media_dirs", "open_roots", "file_server_root", "file_server_port",
    "file_server_ttl_min", "desktop_control_enabled", "clipboard_helper_enabled", "clipboard_speak",
    "confirm_timeout_s", "voice_confirm_high", "chrome_path", "edge_path", "playwright_channel",
    "pinchtab_url", "pinchtab_token", "pinchtab_bin", "pinchtab_autostart", "pinchtab_allowed_domains",
    "ffmpeg_dir", "whatsapp_app_id", "whatsapp_contacts_file", "controller_token", "cv_path",
)
USER_DIRS = ("Desktop", "Documents", "Downloads", "Music", "Videos")


def _set_d_drive(monkeypatch, present: bool) -> None:
    """Pretend a D: drive exists or not; every other path goes to the real os.path.exists."""
    real_exists = os.path.exists

    def exists(path) -> bool:
        if str(path).replace("\\", "/").rstrip("/").upper() == "D:":
            return present
        return real_exists(path)

    monkeypatch.setattr(os.path, "exists", exists)


@pytest.fixture
def home(monkeypatch, tmp_path) -> str:
    """K-15 variables removed, .env loading off, a fake home with the usual user folders, no D: drive."""
    for name in K15_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(jarvis_config, "load_dotenv", lambda *args, **kwargs: False)
    home_dir = tmp_path / "home"
    for name in USER_DIRS:
        (home_dir / name).mkdir(parents=True)
    monkeypatch.setenv("USERPROFILE", str(home_dir))
    monkeypatch.setenv("HOME", str(home_dir))
    _set_d_drive(monkeypatch, present=False)
    return str(home_dir)


@pytest.fixture
def no_tools(monkeypatch, tmp_path):
    """A machine without Chrome, Edge, PinchTab or ffmpeg."""
    empty = tmp_path / "empty"
    empty.mkdir()
    for var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA", "APPDATA"):
        monkeypatch.setenv(var, str(empty))
    monkeypatch.setattr(jarvis_config, "_which", lambda name: None)
    monkeypatch.setattr(jarvis_config, "_app_paths_exe", lambda exe: None)
    monkeypatch.setattr(jarvis_config, "_FFMPEG_FALLBACK_DIR", str(tmp_path / "no-ffmpeg" / "bin"))
    return tmp_path


# ---------------------------------------------------------------- defaults


def test_defaults_with_env_cleared(home, no_tools):
    cfg = JarvisConfig()
    shared = os.path.join(home, "Jarvis_Shared")
    outputs = os.path.join(cfg.project_root, "Jarvis_Outputs")

    assert cfg.shared_dir == shared and os.path.isdir(shared)
    assert cfg.outputs_dir == outputs
    assert cfg.media_dirs == [os.path.join(home, n) for n in ("Music", "Videos", "Downloads", "Desktop", "Documents")] \
        + [shared]
    assert cfg.open_roots == [os.path.join(home, n) for n in USER_DIRS] + [outputs, shared]
    assert cfg.file_server_root == shared
    assert (cfg.file_server_port, cfg.file_server_ttl_min, cfg.confirm_timeout_s) == (8000, 30, 60)
    assert cfg.desktop_control_enabled is True
    assert cfg.clipboard_helper_enabled is True
    assert cfg.clipboard_speak is False
    assert cfg.voice_confirm_high is False
    assert cfg.playwright_channel == "chrome"
    assert cfg.pinchtab_url == "http://127.0.0.1:9867"
    assert cfg.pinchtab_token is None
    assert cfg.pinchtab_autostart is True
    assert cfg.pinchtab_allowed_domains == ["*"]
    assert cfg.whatsapp_app_id == "5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App"
    assert cfg.whatsapp_contacts_file == os.path.join(cfg.project_root, "data", "whatsapp_contacts.json")
    assert cfg.controller_token is None
    assert cfg.cv_path is None
    assert (cfg.chrome_path, cfg.edge_path, cfg.pinchtab_bin, cfg.ffmpeg_dir) == (None, None, None, None)


def test_every_k15_attribute_exists_on_the_singleton():
    missing = [name for name in K15_ATTRS if not hasattr(jarvis_config.config, name)]
    assert not missing


def test_missing_media_folders_are_skipped_but_open_roots_keep_them(home, no_tools):
    os.rmdir(os.path.join(home, "Music"))
    cfg = JarvisConfig()
    assert os.path.join(home, "Music") not in cfg.media_dirs
    assert os.path.join(home, "Music") in cfg.open_roots


def test_shared_dir_default_is_created_without_a_warning(home, no_tools, caplog):
    with caplog.at_level("WARNING", logger="JARVIS-CONFIG"):
        cfg = JarvisConfig()
    assert os.path.isdir(cfg.shared_dir)
    assert "Could not access or create shared directory" not in caplog.text


def test_youtube_mapping_has_no_trailing_dot():
    assert APP_MAPPINGS["youtube"] == "https://www.youtube.com"


# ---------------------------------------------------------------- parsing


@pytest.mark.parametrize(("raw", "default", "expected"), [
    ("1", False, True), ("true", False, True), ("YES", False, True), (" on ", False, True),
    ("0", True, False), ("False", True, False), ("no", True, False), ("OFF", True, False),
    ("", True, True), ("", False, False), ("maybe", True, True), ("maybe", False, False),
])
def test_env_bool(monkeypatch, raw, default, expected):
    monkeypatch.setenv("JARVIS_TEST_BOOL", raw)
    assert jarvis_config._env_bool("JARVIS_TEST_BOOL", default) is expected


def test_env_bool_unset_gives_the_default(monkeypatch):
    monkeypatch.delenv("JARVIS_TEST_BOOL", raising=False)
    assert jarvis_config._env_bool("JARVIS_TEST_BOOL", True) is True
    assert jarvis_config._env_bool("JARVIS_TEST_BOOL", False) is False


@pytest.mark.parametrize(("raw", "expected"), [
    ("90", 90), (" 45 ", 45), ("abc", 60), ("6.5", 60), ("0", 60), ("-5", 60), ("", 60), ("70000", 60),
])
def test_env_int(monkeypatch, raw, expected):
    monkeypatch.setenv("JARVIS_TEST_INT", raw)
    assert jarvis_config._env_int("JARVIS_TEST_INT", 60, maximum=65535) == expected


def test_env_list_splits_strips_and_expands(monkeypatch, tmp_path):
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("JARVIS_TEST_ROOT", str(tmp_path))
    spaced = tmp_path / "with space"
    monkeypatch.setenv("JARVIS_TEST_LIST", f" $JARVIS_TEST_ROOT/a ; ~/b;; \"{spaced}\" ;")
    assert jarvis_config._env_list("JARVIS_TEST_LIST", ["unused"]) == [
        f"{tmp_path}/a", os.path.expanduser("~/b"), str(spaced)]


@pytest.mark.skipif(sys.platform != "win32", reason="%VARS% are a Windows form")
def test_env_list_expands_percent_vars(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_TEST_ROOT", str(tmp_path))
    monkeypatch.setenv("JARVIS_TEST_LIST", "%JARVIS_TEST_ROOT%\\Music")
    assert jarvis_config._env_list("JARVIS_TEST_LIST", []) == [f"{tmp_path}\\Music"]


@pytest.mark.parametrize("raw", [None, "", " ; ;"])
def test_env_list_unset_or_empty_gives_the_default(monkeypatch, raw):
    if raw is None:
        monkeypatch.delenv("JARVIS_TEST_LIST", raising=False)
    else:
        monkeypatch.setenv("JARVIS_TEST_LIST", raw)
    assert jarvis_config._env_list("JARVIS_TEST_LIST", ["*"]) == ["*"]


def test_env_list_of_domains_takes_commas_and_no_expansion(monkeypatch):
    monkeypatch.setenv("JARVIS_TEST_LIST", "example.com, *.google.com;~;foo.org")
    assert jarvis_config._env_list("JARVIS_TEST_LIST", ["*"], paths=False) == [
        "example.com", "*.google.com", "~", "foo.org"]


def test_env_values_override_the_defaults(home, no_tools, monkeypatch, tmp_path):
    shared = tmp_path / "shared"
    media = tmp_path / "media"
    media.mkdir()
    serve = tmp_path / "serve"
    monkeypatch.setenv("JARVIS_SHARED_DIR", f" {shared} ")
    monkeypatch.setenv("JARVIS_OUTPUTS_DIR", "outputs_elsewhere")
    monkeypatch.setenv("JARVIS_MEDIA_DIRS", f"{media};{tmp_path / 'not-there'}")
    monkeypatch.setenv("JARVIS_OPEN_ROOTS", f"{media};~/Desktop")
    monkeypatch.setenv("JARVIS_FILE_SERVER_ROOT", str(serve))
    monkeypatch.setenv("JARVIS_FILE_SERVER_PORT", "8123")
    monkeypatch.setenv("JARVIS_FILE_SERVER_TTL_MIN", "5")
    monkeypatch.setenv("JARVIS_DESKTOP_CONTROL", "0")
    monkeypatch.setenv("JARVIS_CLIPBOARD_HELPER", "off")
    monkeypatch.setenv("JARVIS_CLIPBOARD_SPEAK", "yes")
    monkeypatch.setenv("JARVIS_CONFIRM_TIMEOUT_S", "45")
    monkeypatch.setenv("JARVIS_VOICE_CONFIRM_HIGH", "true")
    monkeypatch.setenv("JARVIS_PLAYWRIGHT_CHANNEL", "msedge")
    monkeypatch.setenv("PINCHTAB_URL", "http://127.0.0.1:9999/")
    monkeypatch.setenv("PINCHTAB_TOKEN", "test-pinchtab-token")
    monkeypatch.setenv("PINCHTAB_AUTOSTART", "0")
    monkeypatch.setenv("PINCHTAB_ALLOWED_DOMAINS", "example.com,example.org")
    monkeypatch.setenv("WHATSAPP_APP_ID", "Test.App!App")
    monkeypatch.setenv("WHATSAPP_CONTACTS_FILE", "~/contacts.json")
    monkeypatch.setenv("CONTROLLER_TOKEN", "test-controller-token")
    monkeypatch.setenv("JARVIS_CV_PATH", "~/cv.pdf")

    cfg = JarvisConfig()

    assert cfg.shared_dir == str(shared) and shared.is_dir()
    assert cfg.outputs_dir == os.path.join(cfg.project_root, "outputs_elsewhere")
    assert cfg.media_dirs == [str(media)]
    assert cfg.open_roots == [str(media), os.path.expanduser("~/Desktop")]
    assert cfg.file_server_root == str(serve)
    assert (cfg.file_server_port, cfg.file_server_ttl_min, cfg.confirm_timeout_s) == (8123, 5, 45)
    assert (cfg.desktop_control_enabled, cfg.clipboard_helper_enabled) == (False, False)
    assert (cfg.clipboard_speak, cfg.voice_confirm_high) == (True, True)
    assert cfg.playwright_channel == "msedge"
    assert cfg.pinchtab_url == "http://127.0.0.1:9999"
    assert cfg.pinchtab_token == "test-pinchtab-token"
    assert cfg.pinchtab_autostart is False
    assert cfg.pinchtab_allowed_domains == ["example.com", "example.org"]
    assert cfg.whatsapp_app_id == "Test.App!App"
    assert cfg.whatsapp_contacts_file == os.path.expanduser("~/contacts.json")
    assert cfg.controller_token == "test-controller-token"
    assert cfg.cv_path == os.path.expanduser("~/cv.pdf")


def test_open_roots_never_include_the_home_folder(home, no_tools, monkeypatch, caplog):
    drive_root = os.path.splitdrive(home)[0] + os.sep if os.path.splitdrive(home)[0] else os.sep
    monkeypatch.setenv("JARVIS_OPEN_ROOTS", f"~;{home}{os.sep}Desktop;{drive_root}")
    with caplog.at_level("WARNING", logger="JARVIS-CONFIG"):
        cfg = JarvisConfig()
    assert cfg.open_roots == [f"{home}{os.sep}Desktop"]
    assert "never an open root" in caplog.text


def _folder_alias(target: Path, alias: Path) -> Path:
    """A second name for a folder: a junction on Windows (needs no admin rights), else a symlink."""
    if sys.platform == "win32":
        import _winapi  # pylint: disable=import-outside-toplevel
        _winapi.CreateJunction(str(target), str(alias))
    else:
        alias.symlink_to(target, target_is_directory=True)
    return alias


def test_open_roots_drop_a_link_to_the_home_folder_or_above(home, no_tools, monkeypatch, tmp_path_factory, caplog):
    links = tmp_path_factory.mktemp("links")  # not under home's parent, so no link sits inside the folder it points to
    home_link = _folder_alias(Path(home), links / "home")  # like 'C:\Documents and Settings\<user>'
    above_link = _folder_alias(Path(home).parent, links / "above-home")  # like 'C:\Documents and Settings'
    desktop = str(home_link / "Desktop")  # a folder inside home stays allowed, whatever the spelling
    monkeypatch.setenv("JARVIS_OPEN_ROOTS", f"{home_link};{above_link};{desktop}")
    with caplog.at_level("WARNING", logger="JARVIS-CONFIG"):
        cfg = JarvisConfig()
    assert cfg.open_roots == [desktop]
    assert "never an open root" in caplog.text


@pytest.mark.skipif(sys.platform != "win32", reason="'\\\\?\\' and '\\\\.\\' paths are Windows forms")
@pytest.mark.parametrize("prefix", ["\\\\?\\", "\\\\.\\"])
def test_open_roots_drop_a_device_path_to_the_home_folder_or_above(home, no_tools, monkeypatch, prefix):
    drive_root = os.path.splitdrive(home)[0] + os.sep
    desktop = prefix + os.path.join(home, "Desktop")
    monkeypatch.setenv("JARVIS_OPEN_ROOTS", ";".join([prefix + home, prefix + home + os.sep, prefix + drive_root,
                                                      desktop]))
    assert JarvisConfig().open_roots == [desktop]


def test_home_check_falls_back_when_realpath_fails(monkeypatch, tmp_path):
    def unreachable(*_args, **_kwargs):
        raise OSError("realpath failed")

    monkeypatch.setattr(os.path, "realpath", unreachable)
    home = str(tmp_path / "home")
    assert jarvis_config._is_home_or_parent(home, home) is True
    assert jarvis_config._is_home_or_parent(str(tmp_path), home) is True
    assert jarvis_config._is_home_or_parent(os.path.join(home, "Desktop"), home) is False


@pytest.mark.parametrize(("real", "expected"), [
    ("\\\\?\\UNC\\server\\share\\Music", "\\\\server\\share\\Music"),
    ("\\\\?\\C:\\Users\\test", "C:\\Users\\test"),
    ("C:\\Users\\test", "C:\\Users\\test"),
])
def test_real_folder_drops_the_long_path_prefix(monkeypatch, real, expected):
    monkeypatch.setattr(os.path, "realpath", lambda path: real)
    assert jarvis_config._real_folder("any spelling") == os.path.normcase(expected)


# ---------------------------------------------------------------- D: drive


def test_d_drive_is_added_only_when_it_exists(home, no_tools, monkeypatch):
    _set_d_drive(monkeypatch, present=True)
    with_d = JarvisConfig()
    assert with_d.media_dirs[-1] == "D:/"
    assert with_d.open_roots[-1] == "D:/"
    assert with_d.file_server_root == "D:/"

    _set_d_drive(monkeypatch, present=False)
    without_d = JarvisConfig()
    assert "D:/" not in without_d.media_dirs
    assert "D:/" not in without_d.open_roots
    assert without_d.file_server_root == without_d.shared_dir


PATH_ATTRS = (
    "project_root", "conversations_dir", "chroma_db_dir", "pretrained_models_dir", "backups_dir", "outputs_dir",
    "shared_dir", "file_server_root", "whatsapp_contacts_file", "chrome_path", "edge_path", "pinchtab_bin",
    "ffmpeg_dir", "cv_path", "media_dirs", "open_roots",
)


def test_no_default_path_starts_with_d_on_a_c_only_machine(home, no_tools):
    cfg = JarvisConfig()
    if cfg.project_root.upper().startswith("D:") or home.upper().startswith("D:"):
        pytest.skip("this checkout or the temp folder lives on D:")
    paths: list[str] = []
    for name in PATH_ATTRS:
        value = getattr(cfg, name)
        paths += value if isinstance(value, list) else [value] if value else []
    assert len(paths) > len(PATH_ATTRS) // 2
    assert not [path for path in paths if path.upper().startswith("D:")]


# ---------------------------------------------------------------- tool detection


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def test_tools_are_auto_detected(home, no_tools, monkeypatch, tmp_path):
    program_files = tmp_path / "pf"
    chrome = _touch(program_files / "Google" / "Chrome" / "Application" / "chrome.exe")
    monkeypatch.setenv("ProgramFiles", str(program_files))
    edge = _touch(tmp_path / "registered" / "msedge.exe")
    monkeypatch.setattr(jarvis_config, "_app_paths_exe", lambda exe: str(edge) if exe == "msedge.exe" else None)
    appdata = tmp_path / "appdata"
    shim = _touch(appdata / "npm" / "pinchtab.cmd")
    monkeypatch.setenv("APPDATA", str(appdata))
    ffmpeg = _touch(tmp_path / "ffbin" / "ffmpeg.exe")
    monkeypatch.setattr(jarvis_config, "_which", lambda name: str(ffmpeg) if name == "ffmpeg" else None)

    cfg = JarvisConfig()

    assert cfg.chrome_path == str(chrome)
    assert cfg.edge_path == str(edge)
    assert cfg.pinchtab_bin == str(shim)
    assert cfg.ffmpeg_dir == str(ffmpeg.parent)


def test_explicit_tool_paths_win_and_wrong_ones_fall_back(home, no_tools, monkeypatch, tmp_path, caplog):
    chrome = _touch(tmp_path / "pf" / "Google" / "Chrome" / "Application" / "chrome.exe")
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "pf"))
    edge = _touch(tmp_path / "custom" / "msedge.exe")
    pinchtab = _touch(tmp_path / "custom" / "pinchtab.exe")
    ffmpeg_dir = _touch(tmp_path / "custom-ffmpeg" / "ffmpeg.exe").parent
    monkeypatch.setenv("JARVIS_CHROME_PATH", str(tmp_path / "missing" / "chrome.exe"))
    monkeypatch.setenv("JARVIS_EDGE_PATH", str(edge))
    monkeypatch.setenv("PINCHTAB_BIN", str(pinchtab))
    monkeypatch.setenv("FFMPEG_DIR", str(ffmpeg_dir))

    with caplog.at_level("WARNING", logger="JARVIS-CONFIG"):
        cfg = JarvisConfig()

    assert cfg.chrome_path == str(chrome)
    assert "JARVIS_CHROME_PATH" in caplog.text
    assert cfg.edge_path == str(edge)
    assert cfg.pinchtab_bin == str(pinchtab)
    assert cfg.ffmpeg_dir == str(ffmpeg_dir)


def test_ffmpeg_dir_without_ffmpeg_falls_back(home, no_tools, monkeypatch, tmp_path, caplog):
    empty_dir = tmp_path / "no-ffmpeg-here"
    empty_dir.mkdir()
    monkeypatch.setenv("FFMPEG_DIR", str(empty_dir))
    with caplog.at_level("WARNING", logger="JARVIS-CONFIG"):
        cfg = JarvisConfig()
    assert cfg.ffmpeg_dir is None
    assert "FFMPEG_DIR" in caplog.text


def test_c_ffmpeg_folder_is_used_only_when_it_holds_ffmpeg(home, no_tools, monkeypatch, tmp_path):
    fallback = tmp_path / "ffmpeg" / "bin"
    monkeypatch.setattr(jarvis_config, "_FFMPEG_FALLBACK_DIR", str(fallback))
    fallback.mkdir(parents=True)
    assert JarvisConfig().ffmpeg_dir is None
    _touch(fallback / "ffmpeg.exe")
    assert JarvisConfig().ffmpeg_dir == str(fallback)


@pytest.mark.skipif(sys.platform != "win32", reason="PATHEXT lookup of an .exe")
def test_which_falls_back_to_the_registry_path(monkeypatch, tmp_path):
    tool = _touch(tmp_path / "bin" / "jarvis-c26-fake-tool.exe")
    monkeypatch.setattr(jarvis_config, "_registry_path_dirs", lambda: [str(tool.parent)])
    found = jarvis_config._which("jarvis-c26-fake-tool")
    assert found is not None and os.path.normcase(found) == os.path.normcase(str(tool))


# ---------------------------------------------------------------- scripts/ops/env_set.py


def _load_env_set():
    spec = importlib.util.spec_from_file_location("jarvis_ops_env_set", ENV_SET_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


env_set = _load_env_set()

OLD_SECRET = "existing-dummy-secret-0123456789"
BASE_LINES = [
    "# JARVIS env (test copy)",
    "VORTEX_ENV=development",
    "",
    f"CONTROLLER_TOKEN={OLD_SECRET}",
    "JARVIS_SHARED_DIR=C:/Users/test/Jarvis_Shared",
]


def _files(tmp_path: Path, first: bytes, second: bytes) -> tuple[Path, Path]:
    a = tmp_path / "windows-local.env"
    b = tmp_path / "root.env"
    a.write_bytes(first)
    b.write_bytes(second)
    return a, b


def _run(capsys, a: Path, b: Path, *args: str) -> tuple[int, str]:
    code = env_set.main(["--env-file", str(a), "--env-file", str(b), *args])
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def test_env_set_default_files_are_the_two_pc_env_files():
    assert [env_set._label(p) for p in env_set.DEFAULT_FILES] == ["deploy/windows-local/.env", ".env"]


def test_env_set_appends_missing_keys_and_keeps_every_other_byte(tmp_path, capsys):
    lf = ("\n".join(BASE_LINES) + "\n").encode()
    crlf_no_final_eol = "\r\n".join(BASE_LINES).encode()
    a, b = _files(tmp_path, lf, crlf_no_final_eol)

    code, out = _run(capsys, a, b, "JARVIS_DESKTOP_CONTROL=1", "PINCHTAB_URL=http://127.0.0.1:9867",
                     "PINCHTAB_ALLOWED_DOMAINS=*", "--secret", "PINCHTAB_TOKEN", "--comment", "Desktop tools")

    assert code == 0
    new_a, new_b = a.read_bytes(), b.read_bytes()
    assert new_a.startswith(lf) and new_b.startswith(crlf_no_final_eol)
    token = dotenv_values(a)["PINCHTAB_TOKEN"]
    assert token and re.fullmatch(r"[A-Za-z0-9_-]{43}", token)
    added = ["# Desktop tools", "JARVIS_DESKTOP_CONTROL=1", "PINCHTAB_URL=http://127.0.0.1:9867",
             "PINCHTAB_ALLOWED_DOMAINS=*", f"PINCHTAB_TOKEN={token}"]
    assert new_a[len(lf):] == ("\n".join(added) + "\n").encode()
    assert new_b[len(crlf_no_final_eol):] == ("\r\n" + "\r\n".join(added) + "\r\n").encode()
    for path in (a, b):
        values = dotenv_values(path)
        assert values["CONTROLLER_TOKEN"] == OLD_SECRET
        assert values["PINCHTAB_ALLOWED_DOMAINS"] == "*"
        assert values["PINCHTAB_TOKEN"] == token
    assert token not in out and OLD_SECRET not in out
    assert "PINCHTAB_TOKEN: new secret added in" in out
    assert "JARVIS_DESKTOP_CONTROL: added in" in out
    assert not list(tmp_path.glob("*.tmp"))


def test_env_set_leaves_present_keys_alone(tmp_path, capsys):
    text = ("\n".join(BASE_LINES) + "\n").encode()
    a, b = _files(tmp_path, text, text)
    code, out = _run(capsys, a, b, "CONTROLLER_TOKEN=some-other-value", "--secret", "VORTEX_ENV")
    assert code == 0
    assert a.read_bytes() == text and b.read_bytes() == text
    assert "CONTROLLER_TOKEN: present" in out and "VORTEX_ENV: present" in out and "no change" in out
    assert OLD_SECRET not in out and "some-other-value" not in out


def test_env_set_copies_a_value_that_one_file_already_has(tmp_path, capsys):
    a, b = _files(tmp_path, f"PINCHTAB_TOKEN={OLD_SECRET}\n".encode(), b"VORTEX_ENV=development\n")
    code, out = _run(capsys, a, b, "--secret", "PINCHTAB_TOKEN")
    assert code == 0
    assert a.read_bytes() == f"PINCHTAB_TOKEN={OLD_SECRET}\n".encode()
    assert b.read_bytes() == f"VORTEX_ENV=development\nPINCHTAB_TOKEN={OLD_SECRET}\n".encode()
    assert f"PINCHTAB_TOKEN: value from {a} added in {b}" in out
    assert OLD_SECRET not in out


def test_env_set_fills_an_empty_line_in_place(tmp_path, capsys):
    first = b"A=1\r\nPINCHTAB_TOKEN=\r\nexport PINCHTAB_URL=''\r\nB=2\r\n"
    second = b"PINCHTAB_TOKEN=\nPINCHTAB_URL=\n"
    a, b = _files(tmp_path, first, second)
    code, out = _run(capsys, a, b, "PINCHTAB_URL=http://127.0.0.1:9867", "--secret", "PINCHTAB_TOKEN")
    assert code == 0
    token = dotenv_values(a)["PINCHTAB_TOKEN"]
    assert a.read_bytes() == (f"A=1\r\nPINCHTAB_TOKEN={token}\r\n"
                              "export PINCHTAB_URL=http://127.0.0.1:9867\r\nB=2\r\n").encode()
    assert b.read_bytes() == f"PINCHTAB_TOKEN={token}\nPINCHTAB_URL=http://127.0.0.1:9867\n".encode()
    assert "PINCHTAB_TOKEN: new secret filled in" in out
    assert token not in out


def test_env_set_keeps_a_utf8_bom(tmp_path, capsys):
    with_bom = b"\xef\xbb\xbfA=1\n"
    a, b = _files(tmp_path, with_bom, b"A=1\n")
    assert _run(capsys, a, b, "B=2")[0] == 0
    assert a.read_bytes() == b"\xef\xbb\xbfA=1\nB=2\n"
    assert b.read_bytes() == b"A=1\nB=2\n"


def test_env_set_dry_run_writes_nothing(tmp_path, capsys):
    text = b"VORTEX_ENV=development\n"
    a, b = _files(tmp_path, text, text)
    code, out = _run(capsys, a, b, "JARVIS_DESKTOP_CONTROL=1", "--secret", "PINCHTAB_TOKEN", "--dry-run")
    assert code == 0
    assert a.read_bytes() == text and b.read_bytes() == text
    assert "dry run: nothing written" in out


def test_env_set_check_reports_names_never_values(tmp_path, capsys):
    a, b = _files(tmp_path, f"A=1\nB={OLD_SECRET}\nC=\nD=x\n".encode(), b"A=1\nC=\nD=y\n")
    code, out = _run(capsys, a, b, "--check")
    assert code == 1
    assert re.search(r"^OK\s+A$", out, re.M)
    assert re.search(r"^MISSING\s+B\s+\(not in .*root\.env\)$", out, re.M)
    assert re.search(r"^EMPTY\s+C\s", out, re.M)
    assert re.search(r"^DIFFERS\s+D\s", out, re.M)
    assert OLD_SECRET not in out

    assert _run(capsys, a, b, "--check", "A", "D")[0] == 0
    assert _run(capsys, a, b, "--check", "C")[0] == 1


def test_env_set_check_passes_when_the_key_sets_match(tmp_path, capsys):
    text = ("\n".join(BASE_LINES) + "\n").encode()
    a, b = _files(tmp_path, text, text)
    code, out = _run(capsys, a, b, "--check")
    assert code == 0
    assert "summary: 3 OK, 0 MISSING, 0 EMPTY, 0 DIFFERS" in out
    assert OLD_SECRET not in out


@pytest.mark.parametrize(("args", "hidden"), [
    (["1BAD=value-one"], "value-one"),
    (["KEY"], None),
    (["KEY="], None),
    (["KEY=a${HOME}b"], "a${HOME}b"),
    (["KEY=x-value", "KEY=y-value"], "x-value"),
    (["KEY=first-line\nsecond-line"], "first-line"),
    (["KEY='quoted\" #tail"], "#tail"),
    (["--secret", "BAD-NAME"], None),
    (["--secret", "PINCHTAB_TOKEN", "PlainValue123"], "PlainValue123"),  # a value typed after the key
    (["PlainValue123", "KEY=1"], "PlainValue123"),
])
def test_env_set_refuses_bad_input_without_echoing_values(tmp_path, capsys, args, hidden):
    text = b"VORTEX_ENV=development\n"
    a, b = _files(tmp_path, text, text)
    code, out = _run(capsys, a, b, *args)
    assert code == 2
    assert a.read_bytes() == text and b.read_bytes() == text
    if hidden:
        assert hidden not in out


def test_env_set_accepts_items_between_options(tmp_path, capsys):
    text = b"VORTEX_ENV=development\n"
    a, b = _files(tmp_path, text, text)
    assert _run(capsys, a, b, "A_KEY=1", "--secret", "PINCHTAB_TOKEN", "B_KEY=2")[0] == 0
    for path in (a, b):
        values = dotenv_values(path)
        assert (values["A_KEY"], values["B_KEY"]) == ("1", "2") and values["PINCHTAB_TOKEN"]


@pytest.mark.parametrize("args", [
    ["A=1", "-looks-like-a-secret-value"],  # unrecognized arguments
    ["--check=looks-like-a-secret-value"],  # ignored explicit argument
    ["A=1", "--dry-run=looks-like-a-secret-value"],
    ["A=1", "--c=looks-like-a-secret-value"],  # ambiguous option (--check or --comment)
])
def test_env_set_argparse_errors_hide_the_typed_text(tmp_path, capsys, args):
    text = b"VORTEX_ENV=development\n"
    a, b = _files(tmp_path, text, text)
    with pytest.raises(SystemExit) as exc:
        env_set.main(["--env-file", str(a), "--env-file", str(b), *args])
    captured = capsys.readouterr()
    assert exc.value.code == 2
    assert "not shown" in captured.err
    assert "looks-like-a-secret-value" not in captured.out + captured.err
    assert a.read_bytes() == text and b.read_bytes() == text


def test_env_set_refuses_a_missing_file(tmp_path, capsys):
    a = tmp_path / "exists.env"
    a.write_bytes(b"A=1\n")
    code, out = _run(capsys, a, tmp_path / "missing.env", "B=2")
    assert code == 2 and "file not found" in out
    assert a.read_bytes() == b"A=1\n"


def test_env_set_puts_files_back_when_a_write_fails(tmp_path, capsys, monkeypatch):
    text = b"VORTEX_ENV=development\n"
    a, b = _files(tmp_path, text, text)
    real_replace = os.replace
    calls = []

    def flaky_replace(src, dst):
        calls.append(dst)
        if len(calls) == 2:
            raise PermissionError(13, "Access is denied")
        return real_replace(src, dst)

    monkeypatch.setattr(env_set.os, "replace", flaky_replace)
    code, out = _run(capsys, a, b, "--secret", "PINCHTAB_TOKEN")
    assert code == 2 and "writing failed" in out
    assert a.read_bytes() == text and b.read_bytes() == text
    assert not list(tmp_path.glob("*.tmp"))


def test_env_set_render_value_quotes_only_when_needed():
    assert env_set.render_value("K", "*") == "*"
    assert env_set.render_value("K", "http://127.0.0.1:9867") == "http://127.0.0.1:9867"
    assert env_set.render_value("K", "a #b") == "'a #b'"
    assert env_set.render_value("K", " lead") == "' lead'"
    assert env_set.render_value("K", "x'y\"z") == "x'y\"z"  # python-dotenv keeps inner quotes unquoted
    with pytest.raises(env_set.EnvError):
        env_set.render_value("K", "'quoted\" #tail")  # no quoting reads back unchanged


def test_env_set_runs_as_a_script(tmp_path):
    text = ("\n".join(BASE_LINES) + "\n").encode()
    a, b = _files(tmp_path, text, text)
    proc = subprocess.run([sys.executable, str(ENV_SET_PY), "--check", "--env-file", str(a), "--env-file", str(b)],
                          capture_output=True, text=True, cwd=str(ROOT), check=False, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert "CONTROLLER_TOKEN" in proc.stdout
    assert OLD_SECRET not in proc.stdout + proc.stderr
