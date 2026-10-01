"""Read-only check of the JARVIS desktop prerequisites on this Windows PC (plan task C-06).

Usage (from the project root):
    .venv_312\\Scripts\\python.exe scripts/pc/check_desktop_prereqs.py
    .venv_312\\Scripts\\python.exe scripts/pc/check_desktop_prereqs.py --launch-test

Every check prints one line: STATUS  item  detail. STATUS is OK, MISSING, WARN (present but needs
attention) or INFO. --launch-test also starts Playwright's bundled Chromium headless; its line
contains "chromium ok" when the browser works. The script always exits with code 0.

Read-only: it reads files, folders and the registry, runs 'powercfg /a',
'netsh advfirewall firewall show rule', 'ffmpeg -version' and 'node --version', imports the
auto-loaded pytest plugins in a child Python (no bytecode written), and sends one GET to the local
PinchTab /health endpoint. It never creates, changes or deletes anything (pytest plugins switched off
with '-p no:<name>' are not even imported, because importing some of them creates folders). Secret values are never
printed: PINCHTAB_TOKEN is only used for the Authorization header and reported as set / not set.
"""
from __future__ import annotations

# Optional and Windows-only modules (winreg, psutil, dotenv, playwright) are imported where they are used.
# pylint: disable=import-outside-toplevel

import argparse
import configparser
import ctypes
import datetime as dt
import importlib
import importlib.metadata
import importlib.util
import json
import os
import shlex
import shutil
import string
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILES = (PROJECT_ROOT / ".env", PROJECT_ROOT / "deploy" / "windows-local" / ".env")
# The only keys read from the environment / .env files (contract K-15). PINCHTAB_TOKEN is secret.
ENV_KEYS = (
    "FFMPEG_DIR",
    "JARVIS_CHROME_PATH",
    "JARVIS_EDGE_PATH",
    "JARVIS_OUTPUTS_DIR",
    "JARVIS_SHARED_DIR",
    "PINCHTAB_BIN",
    "PINCHTAB_TOKEN",
    "PINCHTAB_URL",
    "WHATSAPP_APP_ID",
)
REQUIRED_DRIVES = ("C", "D")
WHATSAPP_APP_ID_DEFAULT = "5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App"
PINCHTAB_URL_DEFAULT = "http://127.0.0.1:9867"
PINCHTAB_TIMEOUT_S = 2.0
FFMPEG_FALLBACK_DIR = Path("C:/ffmpeg/bin")
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
DRIVE_TYPES = {2: "removable", 3: "fixed disk", 4: "network drive", 5: "CD/DVD", 6: "RAM disk"}
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SEM_FAILCRITICALERRORS = 0x0001
PLAYWRIGHT_INSTALL_HINT = r".venv_312\Scripts\python.exe -m playwright install chromium"
OLD_TERMINAL_NOTE = "registry PATH; terminals opened before the install will not see it"


class Report:
    """Prints one aligned line per check and counts the statuses."""

    STATUSES = ("OK", "MISSING", "WARN", "INFO")

    def __init__(self) -> None:
        self.counts = dict.fromkeys(self.STATUSES, 0)

    def line(self, status: str, item: str, detail: str = "") -> None:
        self.counts[status] = self.counts.get(status, 0) + 1
        print(f"{status:<8} {item:<26} {detail}".rstrip(), flush=True)

    def summary(self) -> str:
        return ", ".join(f"{self.counts[s]} {s}" for s in self.STATUSES if self.counts.get(s))


def _read_env_file(path: Path) -> dict[str, str]:
    """Parse one .env file with python-dotenv when available, else with a plain KEY=VALUE reader."""
    try:
        from dotenv import dotenv_values
    except ImportError:
        values: dict[str, str] = {}
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.removeprefix("export ").partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")
        return values
    return {key: value for key, value in dotenv_values(path).items() if value is not None}


class Settings:
    """ENV_KEYS values: process environment first, then .env, then deploy/windows-local/.env."""

    def __init__(self) -> None:
        self._values: dict[str, tuple[str, str]] = {}
        for key in ENV_KEYS:
            value = os.environ.get(key, "").strip()
            if value:
                self._values[key] = (value, "environment")
        for path in ENV_FILES:
            if not path.is_file():
                continue
            try:
                file_values = _read_env_file(path)
            except (OSError, ValueError):
                continue
            label = path.relative_to(PROJECT_ROOT).as_posix()
            for key in ENV_KEYS:
                value = (file_values.get(key) or "").strip()
                if value and key not in self._values:
                    self._values[key] = (value, label)
            # Everything else in the file (other secrets included) is dropped here, never kept or printed.

    def get(self, key: str) -> tuple[str | None, str]:
        """Return (value or None, where it came from)."""
        if key in self._values:
            return self._values[key]
        return None, "not set"


# ---------------------------------------------------------------- helpers


def _short(exc: BaseException, limit: int = 140) -> str:
    lines = str(exc).strip().splitlines()
    text = f"{type(exc).__name__}: {lines[0]}" if lines else type(exc).__name__
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _pretty(path: str | Path) -> str:
    """Shorten user folders to %LOCALAPPDATA%, %APPDATA% or %USERPROFILE% for readable lines."""
    text = str(path)
    for var in ("LOCALAPPDATA", "APPDATA", "USERPROFILE"):
        base = os.environ.get(var, "").rstrip("\\/")
        if base and os.path.normcase(text).startswith(os.path.normcase(base) + os.sep):
            return f"%{var}%" + text[len(base):]
    return text


def _dist_version(dist: str) -> str | None:
    try:
        return importlib.metadata.version(dist)
    except importlib.metadata.PackageNotFoundError:
        return None


def _decode(data: bytes) -> str:
    for encoding in ("oem", "mbcs", "utf-8"):
        try:
            return data.decode(encoding, errors="replace")
        except LookupError:
            continue
    return data.decode("latin-1")


def run_read_only(args: list[str], timeout: float = 15.0) -> tuple[int | None, str, str]:
    """Run a read-only command without a console window: (exit code or None, stdout, stderr)."""
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            timeout=timeout,
            check=False,
            stdin=subprocess.DEVNULL,
            creationflags=NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, "", _short(exc)
    return proc.returncode, _decode(proc.stdout), _decode(proc.stderr)


def _tool_version(args: list[str], prefix: str = "") -> str:
    code, out, _ = run_read_only(args, timeout=10)
    lines = out.strip().splitlines()
    if code != 0 or not lines:
        return ""
    first = lines[0].strip()
    if prefix and first.startswith(prefix):
        first = (first[len(prefix):].split() or ["?"])[0]
    return f", {first[:60]}"


def _registry_path_dirs() -> list[str]:
    """Machine + user PATH as stored in the registry (what a newly opened terminal gets)."""
    try:
        import winreg
    except ImportError:
        return []
    keys = (
        (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
        (winreg.HKEY_CURRENT_USER, "Environment"),
    )
    dirs: list[str] = []
    for hive, sub_key in keys:
        try:
            with winreg.OpenKey(hive, sub_key) as key:
                value = winreg.QueryValueEx(key, "Path")[0]
        except OSError:
            continue
        dirs += [os.path.expandvars(part.strip().strip('"')) for part in str(value).split(";") if part.strip()]
    return dirs


def _on_disk(path: str) -> str:
    """The path with its real on-disk spelling (shutil.which returns e.g. 'node.EXE')."""
    try:
        return os.path.realpath(path)
    except (OSError, ValueError):
        return path


def which_anywhere(name: str) -> tuple[str | None, str]:
    """Find an executable on this process's PATH, else on the registry PATH (new terminals)."""
    found = shutil.which(name)
    if found:
        return _on_disk(found), "PATH"
    dirs = _registry_path_dirs()
    if dirs:
        found = shutil.which(name, path=os.pathsep.join(dirs))
        if found:
            return _on_disk(found), OLD_TERMINAL_NOTE
    return None, ""


def _version_key(name: str) -> tuple[int, ...]:
    return tuple(int(part) if part.isdigit() else 0 for part in name.split("."))


# ---------------------------------------------------------------- checks


def check_drives(rep: Report, _settings: Settings) -> None:
    kernel32 = ctypes.windll.kernel32
    old_mode = kernel32.SetErrorMode(SEM_FAILCRITICALERRORS)  # no "insert a disk" pop-ups
    try:
        mask = kernel32.GetLogicalDrives()
        letters = [c for i, c in enumerate(string.ascii_uppercase) if mask >> i & 1]
        for letter in REQUIRED_DRIVES:
            root = f"{letter}:\\"
            kind = DRIVE_TYPES.get(kernel32.GetDriveTypeW(root), "drive")
            if os.path.isdir(root):
                try:
                    free_gb = shutil.disk_usage(root).free / 1024**3
                    rep.line("OK", f"drive {letter}:", f"{kind}, {free_gb:.0f} GB free")
                except OSError:
                    rep.line("OK", f"drive {letter}:", kind)
            elif letter in letters:
                rep.line("MISSING", f"drive {letter}:", f"letter exists ({kind}) but is not readable (no media?)")
            else:
                note = " (JARVIS then skips D:/ as media root, open root and file-server root)" if letter == "D" else ""
                rep.line("MISSING", f"drive {letter}:", "not present" + note)
        extra = [f"{c}:" for c in letters if c not in REQUIRED_DRIVES]
        if extra:
            rep.line("INFO", "other drives", ", ".join(extra))
    finally:
        kernel32.SetErrorMode(old_mode)


def check_notepad(rep: Report, _settings: Settings) -> None:
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    for path in (system_root / "System32" / "notepad.exe", system_root / "notepad.exe"):
        if path.is_file():
            rep.line("OK", "notepad.exe", str(path))
            return
    found, how = which_anywhere("notepad.exe")
    if found:
        rep.line("OK", "notepad.exe", f"{found} ({how})")
    else:
        rep.line("MISSING", "notepad.exe", f"not in {system_root} or on PATH")


def _app_paths_exe(exe: str) -> str | None:
    """Registered location of exe (the 'App Paths' key Windows uses for 'start <exe>')."""
    try:
        import winreg
    except ImportError:
        return None
    sub_key = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, sub_key) as key:
                value = winreg.QueryValueEx(key, "")[0]
        except OSError:
            continue
        path = os.path.expandvars(str(value).strip().strip('"'))
        if path and Path(path).is_file():
            return path
    return None


def _find_browser(exe: str, vendor_dir: tuple[str, ...]) -> str | None:
    registered = _app_paths_exe(exe)
    if registered:
        return registered
    for base_var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(base_var)
        if base:
            path = Path(base, *vendor_dir, "Application", exe)
            if path.is_file():
                return str(path)
    return None


def _check_browser(rep: Report, settings: Settings, *, item: str, exe: str, env_key: str,
                   vendor_dir: tuple[str, ...]) -> None:
    value, source = settings.get(env_key)
    auto = _find_browser(exe, vendor_dir)
    if value:
        path = Path(os.path.expandvars(value))
        if path.is_file():
            rep.line("OK", item, f"{path} ({env_key} from {source})")
        else:
            hint = f"; auto-detect would find {auto}" if auto else ""
            rep.line("MISSING", item, f"{env_key} from {source} points to a missing file: {path}{hint}")
        return
    if auto:
        rep.line("OK", item, auto)
    else:
        rep.line("MISSING", item, f"{exe} not found (App Paths registry, Program Files, LocalAppData)")


def check_chrome(rep: Report, settings: Settings) -> None:
    _check_browser(rep, settings, item="Chrome", exe="chrome.exe", env_key="JARVIS_CHROME_PATH",
                   vendor_dir=("Google", "Chrome"))


def check_edge(rep: Report, settings: Settings) -> None:
    _check_browser(rep, settings, item="Edge", exe="msedge.exe", env_key="JARVIS_EDGE_PATH",
                   vendor_dir=("Microsoft", "Edge"))


def check_whatsapp(rep: Report, settings: Settings) -> None:
    app_id = settings.get("WHATSAPP_APP_ID")[0]
    family = (app_id or WHATSAPP_APP_ID_DEFAULT).split("!", 1)[0]
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    package_dir = Path(local, "Packages", family)
    if package_dir.is_dir():
        rep.line("OK", "WhatsApp Desktop", f"package folder {_pretty(package_dir)}")
        return
    legacy = Path(local, "WhatsApp", "WhatsApp.exe")
    extra = f"; an old non-Store {_pretty(legacy)} exists, but JARVIS starts the Store app" if legacy.is_file() else ""
    rep.line("MISSING", "WhatsApp Desktop",
             f"package folder {_pretty(package_dir)} not found (install WhatsApp from the Microsoft Store){extra}")


def _npm_pinchtab_dirs() -> list[Path]:
    candidates: list[Path] = []
    shim = which_anywhere("pinchtab.cmd")[0]
    if shim:
        candidates.append(Path(shim).parent / "node_modules" / "pinchtab")
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata, "npm", "node_modules", "pinchtab"))
    unique: dict[str, Path] = {}
    for path in candidates:
        unique.setdefault(os.path.normcase(str(path)), path)
    return list(unique.values())


def _package_version(package_json: Path) -> str | None:
    try:
        version = json.loads(package_json.read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError, AttributeError):
        return None
    return str(version) if version else None


def check_pinchtab_binary(rep: Report, settings: Settings) -> None:
    value, source = settings.get("PINCHTAB_BIN")
    if value:
        path = Path(os.path.expandvars(value))
        if path.is_file():
            rep.line("OK", "PinchTab binary", f"{_pretty(path)} (PINCHTAB_BIN from {source})")
        else:
            rep.line("MISSING", "PinchTab binary", f"PINCHTAB_BIN from {source} points to a missing file: {path}")
        return
    for package_dir in _npm_pinchtab_dirs():
        managed = sorted(package_dir.glob(".managed-bin/*/pinchtab*.exe"), key=lambda p: _version_key(p.parent.name))
        if managed:
            exe = managed[-1]
            version = _package_version(package_dir / "package.json") or exe.parent.name
            rep.line("OK", "PinchTab binary", f"{_pretty(exe)} (npm package {version})")
            return
    standalone, how = which_anywhere("pinchtab.exe")
    if standalone:
        rep.line("OK", "PinchTab binary", f"{_pretty(standalone)} ({how})")
        return
    shim = which_anywhere("pinchtab.cmd")[0]
    if shim:
        rep.line("MISSING", "PinchTab binary",
                 f"npm shim {_pretty(shim)} exists but its managed binary is missing (npm rebuild -g pinchtab)")
    else:
        rep.line("MISSING", "PinchTab binary", "not installed (npm install -g pinchtab)")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow redirects, so the Bearer token cannot travel to another URL."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # pylint: disable=too-many-positional-arguments
        return None


def _display_url(parts: urllib.parse.SplitResult) -> str:
    """URL without user info, query or fragment."""
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    if parts.port:
        host = f"{host}:{parts.port}"
    return urllib.parse.urlunsplit((parts.scheme, host, parts.path, "", ""))


def _health_fields(body: bytes) -> str:
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except ValueError:
        return ""
    if not isinstance(data, dict):
        return ""
    fields = [f"{key}={str(data[key])[:40]}" for key in ("status", "version")
              if isinstance(data.get(key), (str, int, float, bool))]
    return f" ({', '.join(fields)})" if fields else ""


def _net_reason(reason: object) -> str:
    # Windows retries SYNs to a closed localhost port for about 2 s, so "not running" often shows as a timeout.
    if isinstance(reason, ConnectionRefusedError):
        return "connection refused (server not running)"
    if isinstance(reason, TimeoutError) or "timed out" in str(reason).lower():
        return f"no answer within {PINCHTAB_TIMEOUT_S:.0f} s (server not running or not responding)"
    return f"{str(reason)[:100]} (server not reachable)"


def check_pinchtab_health(rep: Report, settings: Settings) -> None:
    base, source = settings.get("PINCHTAB_URL")
    token = settings.get("PINCHTAB_TOKEN")[0]
    url = (base or PINCHTAB_URL_DEFAULT).rstrip("/") + "/health"
    try:
        parts = urllib.parse.urlsplit(url)
        shown = _display_url(parts)
    except ValueError:
        rep.line("WARN", "PinchTab /health", f"PINCHTAB_URL from {source} is not a valid URL")
        return
    if parts.scheme not in ("http", "https") or not parts.hostname:
        rep.line("WARN", "PinchTab /health", f"PINCHTAB_URL from {source} is not an http(s) URL")
        return
    send_token = bool(token) and (parts.scheme == "https" or parts.hostname in LOOPBACK_HOSTS)
    token_note = "token set" if token else "token not set"
    if token and not send_token:
        token_note = "token set but not sent to a non-local http URL"
    # Request the sanitized URL: user info in a URL is never used (the token goes in the Bearer header).
    request = urllib.request.Request(shown, headers={"Accept": "application/json"}, method="GET")
    if send_token:
        request.add_header("Authorization", f"Bearer {token}")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(request, timeout=PINCHTAB_TIMEOUT_S) as response:
            code = response.status
            body = response.read(4096)
    except urllib.error.HTTPError as exc:
        hint = " (check PINCHTAB_TOKEN)" if exc.code in (401, 403) else ""
        if 300 <= exc.code < 400:
            hint = " (redirect, not followed)"
        rep.line("WARN", "PinchTab /health", f"GET {shown} -> HTTP {exc.code}{hint}; {token_note}")
        return
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = _net_reason(getattr(exc, "reason", exc))
        rep.line("MISSING", "PinchTab /health", f"GET {shown}: {reason}; {token_note}")
        return
    rep.line("OK", "PinchTab /health", f"GET {shown} -> HTTP {code}{_health_fields(body)}; {token_note}")


def _playwright_registry(package_dir: Path) -> Path:
    custom = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    if custom == "0":
        return package_dir / "driver" / "package" / ".local-browsers"
    if custom:
        return Path(os.path.expandvars(os.path.expanduser(custom))).resolve()
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local) / "ms-playwright"


def check_playwright(rep: Report, _settings: Settings) -> None:
    spec = importlib.util.find_spec("playwright")
    if spec is None or not spec.submodule_search_locations:
        rep.line("MISSING", "Playwright chromium", "python package 'playwright' is not installed")
        return
    package_dir = Path(next(iter(spec.submodule_search_locations)))
    version = _dist_version("playwright") or "?"
    browsers_json = package_dir / "driver" / "package" / "browsers.json"
    try:
        entries = json.loads(browsers_json.read_text(encoding="utf-8"))["browsers"]
        browsers = {entry["name"]: entry for entry in entries}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        rep.line("WARN", "Playwright chromium", f"cannot read {_pretty(browsers_json)}: {_short(exc)}")
        return
    registry = _playwright_registry(package_dir)
    for name, item in (("chromium", "Playwright chromium"), ("chromium-headless-shell", "Playwright headless shell")):
        entry = browsers.get(name)
        if not entry:
            rep.line("WARN", item, f"'{name}' is not listed in {browsers_json.name}")
            continue
        revision = str((entry.get("revisionOverrides") or {}).get("win64", entry.get("revision", "?")))
        prefix = name.replace("-", "_")
        expected = registry / f"{prefix}-{revision}"
        others = sorted(p.name for p in registry.glob(f"{prefix}-*") if p.is_dir() and p.name != expected.name)
        wanted = f"playwright {version} expects revision {revision} (Chromium {entry.get('browserVersion', '?')})"
        also = f"; also present: {', '.join(others)}" if others else ""
        if (expected / "INSTALLATION_COMPLETE").is_file():
            rep.line("OK", item, f"{expected.name} installed; {wanted}{also}")
        elif expected.is_dir():
            rep.line("MISSING", item, f"{expected.name} is incomplete; {wanted}; run: {PLAYWRIGHT_INSTALL_HINT}")
        else:
            rep.line("MISSING", item,
                     f"{expected.name} not in {_pretty(registry)}; {wanted}{also}; run: {PLAYWRIGHT_INSTALL_HINT}")


def check_ffmpeg(rep: Report, settings: Settings) -> None:
    value, source = settings.get("FFMPEG_DIR")
    if value:
        exe = Path(os.path.expandvars(value)) / "ffmpeg.exe"
        if not exe.is_file():
            rep.line("MISSING", "ffmpeg", f"FFMPEG_DIR from {source} has no ffmpeg.exe ({_pretty(exe.parent)})")
            return
        found, how = str(exe), f"FFMPEG_DIR from {source}"
    else:
        found, how = which_anywhere("ffmpeg")
        if not found and (FFMPEG_FALLBACK_DIR / "ffmpeg.exe").is_file():
            found, how = str(FFMPEG_FALLBACK_DIR / "ffmpeg.exe"), "C:/ffmpeg/bin fallback"
    if not found:
        rep.line("MISSING", "ffmpeg", "not in FFMPEG_DIR, on PATH or in C:/ffmpeg/bin")
        return
    rep.line("OK", "ffmpeg", f"{_pretty(found)} ({how}){_tool_version([found, '-version'], 'ffmpeg version ')}")


def check_node(rep: Report, _settings: Settings) -> None:
    found, how = which_anywhere("node")
    if found:
        rep.line("OK", "node", f"{_pretty(found)} ({how}){_tool_version([found, '--version'])}")
    else:
        rep.line("MISSING", "node", "node is not on PATH (the PinchTab npm wrapper needs Node.js)")


def _check_import(rep: Report, module: str, dist: str, hint: str) -> bool:
    try:
        importlib.import_module(module)
    except ModuleNotFoundError as exc:
        detail = f"not installed ({hint})" if exc.name == module else f"import fails: {_short(exc)}"
        rep.line("MISSING", module, detail)
        return False
    except Exception as exc:  # pylint: disable=broad-exception-caught
        rep.line("MISSING", module, f"import fails: {_short(exc)}")
        return False
    rep.line("OK", module, f"importable ({dist} {_dist_version(dist) or '?'})")
    return True


def check_yt_dlp_ejs(rep: Report, _settings: Settings) -> None:
    _check_import(rep, "yt_dlp_ejs", "yt-dlp-ejs", 'pip install "yt-dlp[default]"')


def check_imageio_ffmpeg(rep: Report, _settings: Settings) -> None:
    _check_import(rep, "imageio_ffmpeg", "imageio-ffmpeg",
                  "optional fallback only; pip install imageio-ffmpeg if FFmpeg is not on PATH")


def check_pytest(rep: Report, _settings: Settings) -> None:
    try:
        import pytest
    except Exception as exc:  # pylint: disable=broad-exception-caught
        rep.line("MISSING", "pytest", f"not importable ({_short(exc)}); versions are pinned in requirements-dev.txt")
        return
    plugins = ", ".join(f"{dist} {_dist_version(dist) or 'missing'}"
                        for dist in ("pytest-asyncio", "pytest-mock", "pytest-timeout"))
    rep.line("OK", "pytest", f"importable (pytest {pytest.__version__}; {plugins})")


_PLUGIN_PROBE = """\
import json
import os
from importlib.metadata import entry_points
skip = {n for n in os.environ.get("JARVIS_PROBE_SKIP", "").split(",") if n}
rows = []
for ep in entry_points(group="pytest11"):
    dist = f"{ep.dist.name} {ep.dist.version}" if ep.dist else "?"
    if ep.name in skip:  # switched off with -p no:<name>; importing it could have side effects
        rows.append([ep.name, dist, "", "skipped"])
        continue
    try:
        ep.load()
        rows.append([ep.name, dist, ""])
    except BaseException as exc:
        rows.append([ep.name, dist, f"{type(exc).__name__}: {exc}"])
print("PLUGIN_PROBE:" + json.dumps(rows))
"""


def _disabled_plugins() -> dict[str, list[str]]:
    """Plugin name -> where it is switched off with '-p no:<name>' (pytest.ini addopts, PYTEST_ADDOPTS)."""
    sources = [("PYTEST_ADDOPTS", os.environ.get("PYTEST_ADDOPTS", ""))]
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(PROJECT_ROOT / "pytest.ini", encoding="utf-8")
        sources.append(("pytest.ini", parser.get("pytest", "addopts", fallback="")))
    except (configparser.Error, OSError, UnicodeDecodeError):
        pass
    disabled: dict[str, list[str]] = {}
    for label, text in sources:
        try:
            tokens = shlex.split(text, posix=False)
        except ValueError:
            tokens = text.split()
        names = {tok[3:] for tok in tokens if tok.startswith("no:")}
        names |= {tok[5:] for tok in tokens if tok.startswith("-pno:")}
        for name in names:
            disabled.setdefault(name, []).append(label)
    return disabled


def check_pytest_plugins(rep: Report, _settings: Settings) -> None:
    if importlib.util.find_spec("pytest") is None:
        rep.line("INFO", "pytest plugins", "skipped (pytest is not installed)")
        return
    if os.environ.get("PYTEST_DISABLE_PLUGIN_AUTOLOAD"):
        rep.line("INFO", "pytest plugins", "auto-loading is off (PYTEST_DISABLE_PLUGIN_AUTOLOAD is set)")
        return
    disabled = _disabled_plugins()
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", JARVIS_PROBE_SKIP=",".join(sorted(disabled)))
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _PLUGIN_PROBE],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180, check=False,
            stdin=subprocess.DEVNULL, creationflags=NO_WINDOW, env=env, cwd=str(PROJECT_ROOT),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        rep.line("WARN", "pytest plugins", f"probe failed: {_short(exc)}")
        return
    rows = None
    for out_line in reversed(proc.stdout.splitlines()):
        if out_line.startswith("PLUGIN_PROBE:"):
            rows = json.loads(out_line[len("PLUGIN_PROBE:"):])
            break
    if rows is None:
        rep.line("WARN", "pytest plugins", f"probe gave no result (exit {proc.returncode})")
        return
    skipped = [row[0] for row in rows if len(row) > 3 and row[3] == "skipped"]
    if skipped:
        where = " and ".join(sorted({label for name in skipped for label in disabled.get(name, [])}))
        rep.line("INFO", "pytest plugins off", f"{', '.join(skipped)} switched off via {where} (not imported)")
    rows = [row for row in rows if not (len(row) > 3 and row[3] == "skipped")]
    broken = [row for row in rows if row[2]]
    if not broken:
        rep.line("OK", "pytest plugins", f"all {len(rows)} active auto-loaded plugins import ({', '.join(r[0] for r in rows)})")
        return
    open_broken = [row for row in broken if row[0] not in disabled]
    if not open_broken:
        names = ", ".join(f"'{row[0]}'" for row in broken)
        where = " and ".join(sorted({label for row in broken for label in disabled[row[0]]}))
        rep.line("OK", "pytest plugins", f"broken plugin {names} is disabled via {where}")
        return
    text = "; ".join(f"'{name}' ({dist}) fails to import: {error[:110]}" for name, dist, error in open_broken)
    flags = " ".join(f"-p no:{row[0]}" for row in open_broken)
    rep.line("WARN", "pytest plugins", f"{text} -> every pytest run crashes at startup unless run with {flags}")


def check_battery(rep: Report, _settings: Settings) -> None:
    try:
        import psutil
    except ImportError:
        rep.line("MISSING", "battery", "psutil is not installed")
        return
    battery = psutil.sensors_battery()
    if battery is None:
        rep.line("MISSING", "battery", f"no battery reported (psutil {psutil.__version__})")
        return
    state = {True: "plugged in", False: "on battery"}.get(battery.power_plugged, "power source unknown")
    left = ""
    if battery.power_plugged is False and battery.secsleft not in (psutil.POWER_TIME_UNLIMITED,
                                                                  psutil.POWER_TIME_UNKNOWN):
        hours, rest = divmod(int(battery.secsleft), 3600)
        left = f", about {hours}h{rest // 60:02d}m left"
    rep.line("OK", "battery", f"{battery.percent:.0f}%, {state}{left} (psutil {psutil.__version__})")


def check_sleep_states(rep: Report, _settings: Settings) -> None:
    code, out, err = run_read_only(["powercfg", "/a"])
    if code is None or not out.strip():
        rep.line("WARN", "sleep states", f"'powercfg /a' failed (exit {code}): {(err or out).strip()[:100]}")
        return
    available: list[str] = []
    section, headers = "", 0
    for raw in out.splitlines():
        if not raw.strip():
            continue
        if not raw[0].isspace():  # section header
            low = raw.lower()
            if "not available" in low:
                section = "no"
            elif "available" in low:
                section = "yes"
            else:  # other display languages: the first header lists the available states
                section = "yes" if headers == 0 else "no"
            headers += 1
        elif section == "yes" and not raw.startswith("\t"):  # reasons are tab-indented
            available.append(raw.strip())
    if available:
        rep.line("OK", "sleep states", "; ".join(available))
    else:
        rep.line("MISSING", "sleep states", "'powercfg /a' lists no available sleep state")


def check_dirs(rep: Report, settings: Settings) -> None:
    for item, key, default in (
        ("outputs dir", "JARVIS_OUTPUTS_DIR", PROJECT_ROOT / "Jarvis_Outputs"),
        ("shared dir", "JARVIS_SHARED_DIR", Path.home() / "Jarvis_Shared"),
    ):
        value, source = settings.get(key)
        path = Path(os.path.expandvars(os.path.expanduser(value))) if value else default
        origin = f"{key} from {source}" if value else f"default, {key} not set"
        if path.is_dir():
            if os.access(path, os.W_OK):
                rep.line("OK", item, f"{_pretty(path)} ({origin})")
            else:
                rep.line("WARN", item, f"{_pretty(path)} is read-only ({origin})")
        elif path.exists():
            rep.line("MISSING", item, f"{_pretty(path)} exists but is not a folder ({origin})")
        else:
            rep.line("MISSING", item, f"{_pretty(path)} does not exist yet ({origin})")


def _norm_exe(path: str | Path) -> str:
    text = str(path).strip().strip('"')
    return os.path.normcase(os.path.abspath(os.path.expandvars(text))) if text else ""


def _jarvis_python() -> tuple[set[str], Path]:
    """Programs Windows Firewall sees for the JARVIS venv: its launcher and the base interpreter."""
    venv = PROJECT_ROOT / ".venv_312"
    base: Path | None = None
    try:
        for raw in (venv / "pyvenv.cfg").read_text(encoding="utf-8", errors="replace").splitlines():
            key, sep, value = raw.partition("=")
            if sep and key.strip().lower() == "executable":
                base = Path(value.strip())
                break
            if sep and key.strip().lower() == "home":
                base = Path(value.strip()) / "python.exe"
    except OSError:
        pass
    if base is None:
        base = Path(getattr(sys, "_base_executable", sys.executable))
    programs = {base, base.with_name("pythonw.exe"), venv / "Scripts" / "python.exe", venv / "Scripts" / "pythonw.exe"}
    return {_norm_exe(p) for p in programs}, base


def _parse_netsh_rules(text: str) -> list[dict[str, str]]:
    rules: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for raw in text.splitlines():
        if not raw.strip():
            if current:
                rules.append(current)
                current = {}
            continue
        key, sep, value = raw.partition(":")
        if sep and not raw.startswith("-"):
            current.setdefault(key.strip(), value.strip())
    if current:
        rules.append(current)
    return [rule for rule in rules if "Rule Name" in rule]


def _profiles(value: str) -> set[str]:
    names = {part.strip().capitalize() for part in value.split(",") if part.strip()}
    if names & {"Any", "All"}:
        return {"Domain", "Private", "Public"}
    return names


def check_firewall(rep: Report, _settings: Settings) -> None:
    item = "firewall (python)"
    programs, base = _jarvis_python()
    code, out, err = run_read_only(
        ["netsh", "advfirewall", "firewall", "show", "rule", "name=all", "dir=in", "verbose"], timeout=60)
    rules = _parse_netsh_rules(out)
    if code != 0 or not rules:
        rep.line("WARN", item, f"could not read inbound rules (netsh exit {code}) {(err or out).strip()[:80]}")
        return
    mine = [rule for rule in rules if _norm_exe(rule.get("Program", "")) in programs]
    active = [rule for rule in mine if rule.get("Enabled", "").lower() == "yes"]
    allow = [rule for rule in active if rule.get("Action", "").lower() == "allow"]
    block = [rule for rule in active if rule.get("Action", "").lower() == "block"]
    profiles: set[str] = set()
    protocols: set[str] = set()
    for rule in allow:
        profiles |= _profiles(rule.get("Profiles", ""))
        protocols.add(rule.get("Protocol", "?"))
    shown = _pretty(base)
    covered = f"{'+'.join(sorted(profiles))} {'/'.join(sorted(protocols))}"
    if block:
        rep.line("WARN", item, f"{len(block)} enabled inbound Block rule(s) for {shown} (Block wins over Allow)")
    elif "Private" in profiles:
        note = "; note: Public networks are allowed too" if "Public" in profiles else ""
        rep.line("OK", item, f"inbound Allow for {shown}: {covered} ({len(allow)} rules){note}")
    elif allow:
        rep.line("WARN", item, f"inbound Allow for {shown} only on {covered} (not Private)")
    elif mine:
        rep.line("WARN", item, f"{len(mine)} inbound rule(s) for {shown}, none enabled with Allow")
    else:
        rep.line("MISSING", item,
                 f"no inbound rule for {shown}; Windows asks the first time JARVIS listens on the LAN (allow Private only)")


def launch_test(rep: Report) -> None:
    """Start Playwright's bundled Chromium headless, render a tiny page, close it."""
    item = "Chromium launch test"
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pylint: disable=broad-exception-caught
        rep.line("MISSING", item, f"chromium FAILED: playwright not importable ({_short(exc)})")
        return
    started = time.perf_counter()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, timeout=60_000)
            try:
                page = browser.new_page()
                page.set_content("<title>jarvis-prereq</title><p>ok</p>")
                title = page.title()
                version = browser.version
            finally:
                browser.close()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        rep.line("MISSING", item, f"chromium FAILED: {_short(exc, 200)}; try: {PLAYWRIGHT_INSTALL_HINT}")
        return
    if title != "jarvis-prereq":
        rep.line("MISSING", item, "chromium FAILED: the test page did not render")
        return
    seconds = time.perf_counter() - started
    rep.line("OK", item, f"chromium ok (headless Chromium {version}, playwright "
                         f"{_dist_version('playwright') or '?'}, {seconds:.1f} s)")


CHECKS: tuple[tuple[str, Callable[[Report, Settings], None]], ...] = (
    ("drives", check_drives),
    ("notepad.exe", check_notepad),
    ("Chrome", check_chrome),
    ("Edge", check_edge),
    ("WhatsApp Desktop", check_whatsapp),
    ("PinchTab binary", check_pinchtab_binary),
    ("PinchTab /health", check_pinchtab_health),
    ("Playwright", check_playwright),
    ("ffmpeg", check_ffmpeg),
    ("node", check_node),
    ("yt_dlp_ejs", check_yt_dlp_ejs),
    ("imageio_ffmpeg", check_imageio_ffmpeg),
    ("pytest", check_pytest),
    ("pytest plugins", check_pytest_plugins),
    ("battery", check_battery),
    ("sleep states", check_sleep_states),
    ("outputs/shared dirs", check_dirs),
    ("firewall (python)", check_firewall),
)


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass
    parser = argparse.ArgumentParser(
        description="Read-only check of the JARVIS desktop prerequisites on this PC (always exits 0).")
    parser.add_argument("--launch-test", action="store_true",
                        help="also start Playwright's bundled Chromium headless and print 'chromium ok'")
    args, unknown = parser.parse_known_args(argv)
    rep = Report()
    now = dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %z")
    print(f"JARVIS desktop prerequisites (read-only) | {now} | python {sys.version.split()[0]} "
          f"at {_pretty(sys.executable)}", flush=True)
    if unknown:
        rep.line("WARN", "arguments", f"ignored unknown arguments: {' '.join(unknown)}")
    if os.name != "nt":
        rep.line("INFO", "platform", "not Windows; the desktop checks only run on the PC")
    else:
        settings = Settings()
        for name, check in CHECKS:
            try:
                check(rep, settings)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                rep.line("WARN", name, f"check crashed: {_short(exc)}")
        if args.launch_test:
            launch_test(rep)
    print(f"Summary: {rep.summary()} (read-only check, exit code 0)", flush=True)
    return 0


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        pass  # argparse --help / usage errors: still exit 0
    except KeyboardInterrupt:
        print("Interrupted.", flush=True)
    except Exception as error:  # pylint: disable=broad-exception-caught
        print(f"WARN     checker                    stopped early: {_short(error)}", flush=True)
    sys.exit(0)
