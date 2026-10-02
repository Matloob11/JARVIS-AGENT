import logging
import os
import shutil
from typing import NamedTuple

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
# Browser origins the bridge accepts when ALLOWED_ORIGINS is unset or empty: the Vite dev server (K-49).
_DEV_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")
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

# Runtime profile (K-72): the role this process plays, the active brain and where the desktop is.
ROLE_AGENT, ROLE_SVC, ROLE_BRIDGE, ROLE_DESKTOP = "agent", "svc", "bridge", "desktop"
ROLES = (ROLE_AGENT, ROLE_SVC, ROLE_BRIDGE, ROLE_DESKTOP)
BRAIN_PC, BRAIN_VPS = "pc", "vps"
BRAINS = (BRAIN_PC, BRAIN_VPS)
DESKTOP_MODE_LOCAL, DESKTOP_MODE_REMOTE = "local", "remote"
DESKTOP_MODES = (DESKTOP_MODE_LOCAL, DESKTOP_MODE_REMOTE)
_warned_choices: set[tuple[str, str]] = set()


class RuntimeProfile(NamedTuple):
    """JARVIS_ROLE, JARVIS_BRAIN and JARVIS_DESKTOP_MODE as this process sees them (K-72)."""

    role: str
    brain: str
    mode: str


def _env_choice(name: str, choices: tuple[str, ...], default: str) -> str:
    """Lower-cased value when it is one of choices; unset or empty gives the default. Anything else gives
    the default plus one WARNING per value, naming the variable only (K-69: values are never logged)."""
    value = (os.getenv(name) or "").strip().lower()
    if not value:
        return default
    if value in choices:
        return value
    if (name, value) not in _warned_choices:
        _warned_choices.add((name, value))
        logger.warning("%s is not one of %s; using %s", name, "/".join(choices), default)
    return default


def runtime_role() -> str:
    """JARVIS_ROLE: agent (default) | svc | bridge | desktop."""
    return _env_choice("JARVIS_ROLE", ROLES, ROLE_AGENT)


def active_brain() -> str:
    """JARVIS_BRAIN: pc (default) | vps. The VPS compose .env holds the real switch (K-72)."""
    return _env_choice("JARVIS_BRAIN", BRAINS, BRAIN_PC)


def desktop_mode() -> str:
    """JARVIS_DESKTOP_MODE: local (default: the PC brain, every desktop sensor runs in this process) or
    remote (the VPS brain: the laptop's Desktop Node owns the clipboard, the active window, the device
    location and the startup sound, and agent-svc owns reminders and tasks)."""
    return _env_choice("JARVIS_DESKTOP_MODE", DESKTOP_MODES, DESKTOP_MODE_LOCAL)


def is_local_mode() -> bool:
    """True for the PC brain (JARVIS_DESKTOP_MODE unset or local)."""
    return desktop_mode() == DESKTOP_MODE_LOCAL


def runtime_profile() -> RuntimeProfile:
    """The current (role, brain, mode), read from the environment on every call."""
    return RuntimeProfile(runtime_role(), active_brain(), desktop_mode())


class _Needs(NamedTuple):
    """Keys one (role, mode) pair needs: required (raise only in production), fatal (always raise) and
    any_of (at least one group must be complete)."""

    required: tuple[str, ...]
    fatal: tuple[str, ...] = ()
    any_of: tuple[tuple[str, ...], ...] = ()


_LIVEKIT_KEYS = ("LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")
# Without it jarvis_crypto encrypts memory with a throw-away session key, so nothing written survives a restart.
_ENCRYPTION_KEY = "JARVIS_ENCRYPTION_KEY"
_AGENT_LINK_KEY = "DESKTOP_AGENT_SIGNING_KEY"


def _role_needs(role: str, mode: str) -> _Needs:
    if role == ROLE_AGENT and mode == DESKTOP_MODE_LOCAL:
        # The legacy shared token, or the C-14 agent credentials once the PC env moves to them.
        return _Needs(("GOOGLE_API_KEY", *_LIVEKIT_KEYS),
                      any_of=(("VORTEX_SECURITY_TOKEN",), ("AGENT_TOKEN", "BRIDGE_HMAC_SECRET")))
    if role == ROLE_AGENT:
        return _Needs(("GOOGLE_API_KEY", *_LIVEKIT_KEYS, _AGENT_LINK_KEY), fatal=(_ENCRYPTION_KEY,))
    if role == ROLE_SVC:
        return _Needs((_AGENT_LINK_KEY,), fatal=(_ENCRYPTION_KEY,))
    if role == ROLE_BRIDGE:
        legacy = ("VORTEX_SECURITY_TOKEN",) if _env_bool("LEGACY_TOKEN_AUTH", True) else ()
        return _Needs((*_LIVEKIT_KEYS, *legacy))
    return _Needs(("DESKTOP_PC_SIGNING_KEY", "DESKTOP_AGENT_PUBLIC_KEY"))


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


def _env_origins(name: str) -> list[str]:
    """','-separated browser origins, each without spaces or a trailing '/'; unset or empty gives the Vite dev
    origins. Nothing is appended in code: the deployed list comes only from the environment (K-49)."""
    origins = [part.strip().rstrip("/") for part in (os.getenv(name) or "").split(",")]
    return list(dict.fromkeys(origin for origin in origins if origin)) or list(_DEV_ORIGINS)


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
    agent_token: str | None
    bridge_hmac_secret: str | None
    legacy_token_auth: bool
    bridge_db_path: str
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
    user_id: str | None
    desktop_pc_signing_key: str | None

    # validate(role, mode) reads these names from their attribute, so a key a role holds only in memory
    # (the Desktop Node's unwrapped DESKTOP_PC_SIGNING_KEY) counts; other names come from the environment.
    _SETTING_ATTRS = {
        "GOOGLE_API_KEY": "google_api_key",
        "VORTEX_SECURITY_TOKEN": "security_token",
        "AGENT_TOKEN": "agent_token",
        "BRIDGE_HMAC_SECRET": "bridge_hmac_secret",
        "LIVEKIT_API_KEY": "livekit_key",
        "LIVEKIT_API_SECRET": "livekit_secret",
        "DESKTOP_PC_SIGNING_KEY": "desktop_pc_signing_key",
    }

    def __init__(self) -> None:
        """Loads environment variables and sets up configuration attributes."""
        _load_env_files()
        self.env = os.getenv("VORTEX_ENV", "development").lower()
        # Legacy shared token (UI and agent alike); accepted by the bridge only while LEGACY_TOKEN_AUTH is on.
        self.security_token = os.getenv("VORTEX_SECURITY_TOKEN")
        # Agent link v2 (C-SOCK-1, C-NOTIFY-1): the agent's own bridge token and the notify HMAC key.
        self.agent_token = _env_str("AGENT_TOKEN")
        self.bridge_hmac_secret = _env_str("BRIDGE_HMAC_SECRET")
        self.legacy_token_auth = _env_bool("LEGACY_TOKEN_AUTH", True)
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
        # Memory identity (K-69 v4): the same in every mode, so the PC and the VPS share one memory file.
        self.user_id = _env_str("JARVIS_USER_ID")
        self.desktop_pc_signing_key = _env_str("DESKTOP_PC_SIGNING_KEY")

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
        self.allowed_origins = _env_origins("ALLOWED_ORIGINS")
        # Bridge SQLite store (C-STATE-1); the same rule as src/core/bridge_store.bridge_db_path().
        self.bridge_db_path = _env_str("BRIDGE_DB_PATH") or os.path.join(self.project_root, "data", "bridge.db")

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

    @property
    def role(self) -> str:
        """JARVIS_ROLE (K-72), read on every access."""
        return runtime_role()

    @property
    def brain(self) -> str:
        """JARVIS_BRAIN (K-72), read on every access."""
        return active_brain()

    @property
    def desktop_mode(self) -> str:
        """JARVIS_DESKTOP_MODE (K-72), read on every access."""
        return desktop_mode()

    def _setting(self, name: str) -> str | None:
        attr = self._SETTING_ATTRS.get(name)
        if attr:
            return (getattr(self, attr, None) or "").strip() or None
        return _env_str(name)

    def validate(self, role: str | None = None, mode: str | None = None) -> bool:
        """Mandatory validation for critical keys; names are logged, values never.

        validate() keeps the original check: GOOGLE_API_KEY, VORTEX_SECURITY_TOKEN, LIVEKIT_API_KEY and
        LIVEKIT_API_SECRET, each missing one logged CRITICAL and a RuntimeError in production.

        validate(role, mode) (K-72) asks only for what that pair uses, so the old single env file and the
        split ones (bridge.env, .env.agent) both pass:
        - agent + local (the PC brain): GOOGLE_API_KEY, LIVEKIT_API_KEY/SECRET and VORTEX_SECURITY_TOKEN,
          or AGENT_TOKEN with BRIDGE_HMAC_SECRET in its place;
        - agent + remote (the VPS brain): GOOGLE_API_KEY, LIVEKIT_API_KEY/SECRET, DESKTOP_AGENT_SIGNING_KEY
          and JARVIS_ENCRYPTION_KEY;
        - svc: JARVIS_ENCRYPTION_KEY and DESKTOP_AGENT_SIGNING_KEY;
        - bridge: LIVEKIT_API_KEY/SECRET, plus VORTEX_SECURITY_TOKEN while the legacy token is in use
          (LEGACY_TOKEN_AUTH unset or true). AGENT_TOKEN and BRIDGE_HMAC_SECRET stay optional: without them
          the public agent link v2 (C-14) is off and src/core/ui_bridge.py logs which names are missing;
          the VPS brain's agents use the internal :5002 listener instead (K-38 v4);
        - desktop: DESKTOP_PC_SIGNING_KEY and DESKTOP_AGENT_PUBLIC_KEY.
        role and mode default to JARVIS_ROLE and JARVIS_DESKTOP_MODE; the mode matters only for the agent.
        A missing JARVIS_ENCRYPTION_KEY raises in every environment; any other missing key raises only in
        production, as before. Returns True when nothing is missing.
        """
        if role is not None or mode is not None:
            return self._validate_role(role, mode)
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

    def _validate_role(self, role: str | None, mode: str | None) -> bool:
        role = (role or runtime_role()).strip().lower()
        mode = (mode or desktop_mode()).strip().lower()
        if role not in ROLES:
            raise ValueError(f"Unknown role {role!r}; expected one of {', '.join(ROLES)}")
        if mode not in DESKTOP_MODES:
            raise ValueError(f"Unknown desktop mode {mode!r}; expected one of {', '.join(DESKTOP_MODES)}")
        needs = _role_needs(role, mode)
        label = f"{role}/{mode}" if role == ROLE_AGENT else role
        fatal = [name for name in needs.fatal if not self._setting(name)]
        missing = [name for name in needs.required if not self._setting(name)]
        if needs.any_of and not any(all(self._setting(name) for name in group) for group in needs.any_of):
            missing.append(" or ".join("+".join(group) for group in needs.any_of))

        if fatal or missing:
            logger.critical("❌ CRITICAL CONFIG MISSING (%s): %s", label, ", ".join(fatal + missing))
        if fatal:
            raise RuntimeError(f"Missing {fatal} for {label}: refusing to start")
        if missing and self.is_production():
            raise RuntimeError(f"Production safety check failed for {label}: Missing {missing}")
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
