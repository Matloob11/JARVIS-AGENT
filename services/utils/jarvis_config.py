import logging
import os
import shutil

from dotenv import load_dotenv

try:
    import winreg
except ImportError:  # not Windows: the VPS bridge container imports this module too
    winreg = None  # type: ignore[assignment]

logger = logging.getLogger("JARVIS-CONFIG")

_TRUE_WORDS = frozenset({"1", "true", "yes", "on"})
_FALSE_WORDS = frozenset({"0", "false", "no", "off"})
# Optional second drive: used for media, open roots and the file server only when it exists (this PC has only C:).
_D_DRIVE = "D:/"
_FFMPEG_FALLBACK_DIR = "C:/ffmpeg/bin"
_PINCHTAB_URL = "http://127.0.0.1:9867"
_WHATSAPP_APP_ID = "5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App"
# Windows long-path prefixes \\?\ and \\?\UNC\ (os.path.realpath keeps them when its input had one).
_LONG_PREFIX = "\\\\?\\"
_LONG_UNC_PREFIX = "\\\\?\\UNC\\"
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# The root .env is the single settings source (K-47); deploy/windows-local/.env only fills keys it lacks.
_ENV_FILES = (
    os.path.join(_PROJECT_ROOT, ".env"),
    os.path.join(_PROJECT_ROOT, "deploy", "windows-local", ".env"),
)


def _load_env_files() -> None:
    """Root .env, then the hybrid file; never overrides a variable that is already set."""
    for path in _ENV_FILES:
        if os.path.isfile(path):
            load_dotenv(path, override=False)


def _expand(path: str) -> str:
    """Expands '~' and %VARS% (or $VARS)."""
    return os.path.expanduser(os.path.expandvars(path))


def _env_str(name: str) -> str | None:
    """Stripped value of an environment variable; None when it is unset or empty."""
    return (os.getenv(name) or "").strip() or None


def _env_path(name: str) -> str | None:
    value = _env_str(name)
    return _expand(value) if value else None


def _env_bool(name: str, default: bool) -> bool:
    """1/true/yes/on or 0/false/no/off in any case; unset, empty or anything else gives the default."""
    value = (os.getenv(name) or "").strip().lower()
    if not value:
        return default
    if value in _TRUE_WORDS:
        return True
    if value in _FALSE_WORDS:
        return False
    logger.warning("%s is not a yes/no value (1/0, true/false, yes/no, on/off); using the default %s", name, default)
    return default


def _env_int(name: str, default: int, minimum: int = 1, maximum: int | None = None) -> int:
    """Whole number between minimum and maximum; unset, empty, invalid or out of range gives the default."""
    value = (os.getenv(name) or "").strip()
    if not value:
        return default
    try:
        number = int(value)
    except ValueError:
        logger.warning("%s is not a whole number; using the default %s", name, default)
        return default
    if number < minimum or (maximum is not None and number > maximum):
        logger.warning("%s is out of range; using the default %s", name, default)
        return default
    return number


def _env_list(name: str, default: list[str], paths: bool = True) -> list[str]:
    """';'-separated list ('~' and %VARS% expanded when paths=True, ',' also accepted when paths=False);
    unset or empty gives the default."""
    raw = os.getenv(name) or ""
    if not paths:
        raw = raw.replace(",", ";")
    items = [part.strip().strip('"').strip("'").strip() for part in raw.split(";")]
    items = [_expand(item) if paths else item for item in items if item]
    return items or list(default)


def _unique(paths: list[str]) -> list[str]:
    """Drops repeated folders (compared case-insensitively on Windows); keeps the first spelling."""
    seen: set[str] = set()
    result = []
    for path in paths:
        key = os.path.normcase(os.path.normpath(path))
        if key not in seen:
            seen.add(key)
            result.append(path)
    return result


def _real_folder(path: str) -> str:
    r"""Case-folded real path without a '\\?\' prefix, so a junction, symlink, 8.3 name or '\\?\' / '\\.\'
    spelling of a folder compares equal to its plain path."""
    try:
        path = os.path.realpath(path)
    except (OSError, ValueError):  # non-strict realpath still raises some Windows errors (e.g. an unreachable share)
        path = os.path.abspath(path)
    if path.startswith(_LONG_UNC_PREFIX):
        path = "\\\\" + path[len(_LONG_UNC_PREFIX):]
    elif path.startswith(_LONG_PREFIX):
        path = path[len(_LONG_PREFIX):]
    return os.path.normcase(path)


def _is_home_or_parent(path: str, home: str) -> bool:
    r"""True for the home folder itself or a folder above it, however it is spelled (a junction such as
    'C:\Documents and Settings', a '\\?\' path): either would expose ~/.ssh and AppData."""
    path, home = _real_folder(path), _real_folder(home)
    try:
        return os.path.commonpath([path, home]) == path
    except ValueError:  # another drive
        return False


def _registry_path_dirs() -> list[str]:
    """Machine + user PATH as stored in the registry, so programs installed after this process
    started (winget, npm -g) are still found."""
    if winreg is None:
        return []
    dirs: list[str] = []
    for hive, sub_key in (
        (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
        (winreg.HKEY_CURRENT_USER, "Environment"),
    ):
        try:
            with winreg.OpenKey(hive, sub_key) as key:
                value = winreg.QueryValueEx(key, "Path")[0]
        except OSError:
            continue
        dirs += [os.path.expandvars(part.strip().strip('"')) for part in str(value).split(";") if part.strip()]
    return dirs


def _which(name: str) -> str | None:
    """shutil.which on this process's PATH, then on the registry PATH."""
    found = shutil.which(name)
    if not found:
        dirs = _registry_path_dirs()
        if dirs:
            found = shutil.which(name, path=os.pathsep.join(dirs))
    return found


def _app_paths_exe(exe: str) -> str | None:
    """Location registered under the Windows 'App Paths' key (what 'start chrome' uses)."""
    if winreg is None:
        return None
    sub_key = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, sub_key) as key:
                value = winreg.QueryValueEx(key, "")[0]
        except OSError:
            continue
        path = os.path.expandvars(str(value).strip().strip('"'))
        if path and os.path.isfile(path):
            return path
    return None


def _find_browser(exe: str, vendor: str, product: str) -> str | None:
    """Known install folders first, then the registry App Paths entry; None when it is not installed."""
    for base_var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.getenv(base_var)
        if base:
            path = os.path.join(base, vendor, product, "Application", exe)
            if os.path.isfile(path):
                return path
    return _app_paths_exe(exe)


def _find_pinchtab() -> str | None:
    """which('pinchtab') (the npm shim), else %APPDATA%/npm/pinchtab.cmd; None when it is not installed."""
    found = _which("pinchtab")
    if found:
        return found
    appdata = os.getenv("APPDATA")
    if appdata:
        shim = os.path.join(appdata, "npm", "pinchtab.cmd")
        if os.path.isfile(shim):
            return shim
    return None


def _find_ffmpeg_dir() -> str | None:
    """Folder of which('ffmpeg'), else C:/ffmpeg/bin when it holds ffmpeg.exe, else None."""
    found = _which("ffmpeg")
    if found:
        return os.path.dirname(found)
    if os.path.isfile(os.path.join(_FFMPEG_FALLBACK_DIR, "ffmpeg.exe")):
        return _FFMPEG_FALLBACK_DIR
    return None


def _env_file(name: str) -> str | None:
    """Existing file named by an environment variable; a wrong path is logged and auto-detection takes over."""
    path = _env_path(name)
    if path and not os.path.isfile(path):
        logger.warning("%s=%s is not a file; auto-detecting instead", name, path)
        return None
    return path


def _env_ffmpeg_dir() -> str | None:
    path = _env_path("FFMPEG_DIR")
    if path and not shutil.which("ffmpeg", path=path):
        logger.warning("FFMPEG_DIR=%s has no ffmpeg; auto-detecting instead", path)
        return None
    return path


# pylint: disable=too-many-instance-attributes
class JarvisConfig:
    """
    Centralized configuration loader for JARVIS-AGENT.
    Supports .env with production/development toggles.
    """

    env: str
    security_token: str | None
    bridge_url: str
    ui_bridge_url: str
    google_api_key: str | None
    groq_api_key: str | None
    google_search_key: str | None
    search_engine_id: str | None
    tavily_api_key: str | None
    openrouter_key: str | None
    hf_token: str | None
    livekit_url: str | None
    livekit_key: str | None
    livekit_secret: str | None
    email_user: str | None
    email_password: str | None
    weather_api_key: str | None
    user_name: str
    user_city: str
    log_level: str
    log_file: str
    allowed_origins: list[str]
    shared_dir: str
    # Desktop tools (contract K-15)
    controller_token: str | None
    outputs_dir: str
    media_dirs: list[str]
    open_roots: list[str]
    file_server_root: str
    file_server_port: int
    file_server_ttl_min: int
    desktop_control_enabled: bool
    clipboard_helper_enabled: bool
    clipboard_speak: bool
    confirm_timeout_s: int
    voice_confirm_high: bool
    chrome_path: str | None
    edge_path: str | None
    playwright_channel: str
    pinchtab_url: str
    pinchtab_token: str | None
    pinchtab_bin: str | None
    pinchtab_autostart: bool
    pinchtab_allowed_domains: list[str]
    ffmpeg_dir: str | None
    whatsapp_app_id: str
    whatsapp_contacts_file: str
    cv_path: str | None

    def __init__(self) -> None:
        """Loads environment variables and sets up configuration attributes."""
        _load_env_files()
        self.env = os.getenv("VORTEX_ENV", "development").lower()
        self.security_token = os.getenv("VORTEX_SECURITY_TOKEN")
        self.bridge_url = os.getenv("UI_BRIDGE_URL", "http://127.0.0.1:5001")
        self.ui_bridge_url = self.bridge_url  # alias for compatibility

        # LLM & Search API Keys
        self.google_api_key = os.getenv("GOOGLE_API_KEY")
        self.groq_api_key = os.getenv("GROQ_API_KEY")
        self.google_search_key = os.getenv("GOOGLE_SEARCH_API_KEY")
        self.search_engine_id = os.getenv("SEARCH_ENGINE_ID")
        self.tavily_api_key = os.getenv("TAVILY_API_KEY")
        self.openrouter_key = os.getenv("OPENROUTER_API_KEY")
        self.hf_token = os.getenv("HF_TOKEN")

        # LiveKit
        self.livekit_url = os.getenv("LIVEKIT_URL")
        self.livekit_key = os.getenv("LIVEKIT_API_KEY")
        self.livekit_secret = os.getenv("LIVEKIT_API_SECRET")

        # Email
        self.email_user = os.getenv("EMAIL_USER")
        self.email_password = os.getenv("EMAIL_APP_PASSWORD")

        # Weather
        self.weather_api_key = os.getenv("WEATHER_API_KEY")

        # User Info
        self.user_name = os.getenv("USER_NAME", "Sir Matloob")
        self.user_city = os.getenv("USER_CITY", "Lahore")

        # Root Directory
        self.project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        home = os.path.expanduser("~")

        # Logging
        self.log_level = os.getenv("LOG_LEVEL", "INFO")
        log_rel_path = os.getenv("LOG_FILE", "logs/jarvis.log")
        self.log_file = os.path.join(self.project_root, log_rel_path)

        # Standard Directories (Absolute Paths)
        self.conversations_dir = os.path.join(self.project_root, "conversations")
        self.chroma_db_dir = os.path.join(self.project_root, "chroma_db")
        self.pretrained_models_dir = os.path.join(self.project_root, "pretrained_models")
        self.backups_dir = os.path.join(self.project_root, "backups")

        # Network
        origins: str = os.getenv(
            "ALLOWED_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000",
        )
        self.allowed_origins = origins.split(",")

        # Shared Assets: JARVIS_SHARED_DIR wins; the default is ~/Jarvis_Shared because this PC has no D: drive
        self.shared_dir = _env_path("JARVIS_SHARED_DIR") or os.path.join(home, "Jarvis_Shared")
        try:
            # Use exist_ok=True and wrap in try-except to prevent startup blocks
            if not os.path.exists(self.shared_dir):
                os.makedirs(self.shared_dir, exist_ok=True)
                logger.info("📁 Created shared directory: %s", self.shared_dir)
        except (OSError, PermissionError) as e:
            logger.warning("⚠️ Could not access or create shared directory %s: %s", self.shared_dir, e)

        # Desktop tools (contract K-15). Cursor code reads each one via getattr(config, name, <same default>).
        self.controller_token = _env_str("CONTROLLER_TOKEN")
        extra_drive = [_D_DRIVE] if os.path.exists(_D_DRIVE) else []
        # A relative JARVIS_OUTPUTS_DIR is taken from the project root.
        self.outputs_dir = os.path.join(self.project_root, _env_path("JARVIS_OUTPUTS_DIR") or "Jarvis_Outputs")

        env_media = _env_list("JARVIS_MEDIA_DIRS", [])
        if env_media:
            media_dirs = [d for d in env_media if os.path.isdir(d)]
        else:
            media_dirs = [os.path.join(home, name) for name in ("Music", "Videos", "Downloads", "Desktop", "Documents")]
            media_dirs = [d for d in media_dirs + [self.shared_dir] if os.path.isdir(d)] + extra_drive
        self.media_dirs = _unique(media_dirs)

        open_roots = _env_list("JARVIS_OPEN_ROOTS", [])
        if not open_roots:
            open_roots = [os.path.join(home, name) for name in ("Desktop", "Documents", "Downloads", "Music", "Videos")]
            open_roots += [self.outputs_dir, self.shared_dir] + extra_drive
        too_wide = [root for root in open_roots if _is_home_or_parent(root, home)]
        if too_wide:
            logger.warning("Open roots: left out %s (the whole home folder is never an open root)", ", ".join(too_wide))
        self.open_roots = _unique([root for root in open_roots if root not in too_wide])

        self.file_server_root = _env_path("JARVIS_FILE_SERVER_ROOT") or (_D_DRIVE if extra_drive else self.shared_dir)
        self.file_server_port = _env_int("JARVIS_FILE_SERVER_PORT", 8000, maximum=65535)
        self.file_server_ttl_min = _env_int("JARVIS_FILE_SERVER_TTL_MIN", 30)

        self.desktop_control_enabled = _env_bool("JARVIS_DESKTOP_CONTROL", True)
        self.clipboard_helper_enabled = _env_bool("JARVIS_CLIPBOARD_HELPER", True)
        self.clipboard_speak = _env_bool("JARVIS_CLIPBOARD_SPEAK", False)
        self.confirm_timeout_s = _env_int("JARVIS_CONFIRM_TIMEOUT_S", 60)
        self.voice_confirm_high = _env_bool("JARVIS_VOICE_CONFIRM_HIGH", False)

        self.chrome_path = _env_file("JARVIS_CHROME_PATH") or _find_browser("chrome.exe", "Google", "Chrome")
        self.edge_path = _env_file("JARVIS_EDGE_PATH") or _find_browser("msedge.exe", "Microsoft", "Edge")
        self.playwright_channel = _env_str("JARVIS_PLAYWRIGHT_CHANNEL") or "chrome"

        self.pinchtab_url = (_env_str("PINCHTAB_URL") or _PINCHTAB_URL).rstrip("/")
        self.pinchtab_token = _env_str("PINCHTAB_TOKEN")
        self.pinchtab_bin = _env_file("PINCHTAB_BIN") or _find_pinchtab()
        self.pinchtab_autostart = _env_bool("PINCHTAB_AUTOSTART", True)
        self.pinchtab_allowed_domains = _env_list("PINCHTAB_ALLOWED_DOMAINS", ["*"], paths=False)

        self.ffmpeg_dir = _env_ffmpeg_dir() or _find_ffmpeg_dir()
        self.whatsapp_app_id = _env_str("WHATSAPP_APP_ID") or _WHATSAPP_APP_ID
        self.whatsapp_contacts_file = _env_path("WHATSAPP_CONTACTS_FILE") or os.path.join(
            self.project_root, "data", "whatsapp_contacts.json")
        self.cv_path = _env_path("JARVIS_CV_PATH")

    def is_production(self) -> bool:
        """Returns True if the environment is set to production."""
        return self.env == "production"

    def validate(self) -> bool:
        """Mandatory validation for critical keys."""
        critical_keys: dict[str, str | None] = {
            "GOOGLE_API_KEY": self.google_api_key,
            "VORTEX_SECURITY_TOKEN": self.security_token,
            "LIVEKIT_API_KEY": self.livekit_key,
            "LIVEKIT_API_SECRET": self.livekit_secret,
        }

        missing: list[str] = [k for k, v in critical_keys.items() if not v]

        if missing:
            logger.critical("❌ CRITICAL CONFIG MISSING: %s",
                            ', '.join(missing))
            if self.is_production():
                raise RuntimeError(
                    f"Production safety check failed: Missing {missing}")

        return not missing


# Global singleton instance
config: JarvisConfig = JarvisConfig()

# Standardized Directory Ensuring (Self-Healing during startup)
for d in [config.conversations_dir, config.chroma_db_dir, 
          config.pretrained_models_dir, config.backups_dir, 
          os.path.dirname(config.log_file)]:
    os.makedirs(d, exist_ok=True)

# 📱 Application Mappings (App Name -> Path or URL)
APP_MAPPINGS: dict[str, str] = {
    "notepad": "notepad.exe",
    "chrome": "https://www.google.com",
    "edge": "msedge",
    "calculator": "calc",
    "youtube": "https://www.youtube.com",
    "whatsapp": "https://web.whatsapp.com",
    "google": "https://www.google.com",
    "vlc": "vlc",
    "excel": "excel",
    "word": "winword",
    "powerpoint": "powerpnt",
}

# 🎯 Focus Titles (App Name -> Window Title Fragment)
FOCUS_TITLES: dict[str, str] = {
    "notepad": " - Notepad",
    "chrome": "Google Chrome",
    "edge": "Microsoft Edge",
    "whatsapp": "WhatsApp",
    "youtube": "YouTube",
    "vlc": "VLC media player",
    "calculator": "Calculator",
}
