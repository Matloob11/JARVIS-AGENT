"""Desktop Node policy: modes, Windows-lock pause, class floors, launch surfaces, paths, the ConfirmFlow
approval check, origins, stops and abuse caps (plan task C-121; K-16, K-45, K-70, K-74, K-76).

The node enforces its own rules without trusting the bridge or the model, and everyday actions run with no
prompt and no manual step on the laptop (user decision D3). install(node) replaces the C-120 BaselinePolicy
and MemoryState with the real, persisted, presence-aware policy and wires presence, the tray and the panic
hotkey. The policy itself is pure and synchronous: NodeRuntime calls check(call) for every verified exec, in
the K-76 order, and the first non-None result is the K-70 refusal ack {accepted:false, code} the agent gets.

Check order (K-76): kill switch, panic, mode and Windows lock first, then class and approval, then origin,
then paths, then the abuse caps. A canary or probe call therefore gets a state or approval refusal first and
nothing actuates. Presence never leaves the node.

Run time (guard_input_runs): every K-16 desktop_input call (executor.K16_DESKTOP_INPUT) holds the desktop_input
lock for its whole run, so two input calls never overlap. Right before it runs, the foreground is judged again
(recheck_foreground) and remembered (window handle and protection class). Then:
- type_text_tool typed key by key (not pasted) and type_code_animation_tool are sent in chunks of at most
  INPUT_CHUNK_CHARS characters; before each chunk the node state, the K-76 foreground rules and that foreground
  identity are verified again (verify_input_run). The animation's part after its first 1500 characters is one
  paste (one Ctrl+V through type_text_tool), as the tool itself does.
- While type_text_tool or type_code_animation_tool runs, a watchdog looks at the foreground every
  _WATCH_INTERVAL_S (watch_input_run): the state codes, a protected window, and a launch surface or messaging app
  the call's approval does not cover (the window the call started from is exempt until the focus first leaves
  it). On a refusal the call's keys are blocked at once (keygate) and its typing is cancelled.
- The tools that move the focus to their own window first (SELF_FOCUS_TOOLS: the Notepad tools, save_notepad,
  automate_whatsapp, an open_app that types) are judged at key time instead (judge_input_key): every PyAutoGUI
  and pynput key they send is judged, through the keystroke gate, against the window that has the focus at that
  moment: the state codes, a protected window (also the one they started from, when the focus never moved), a
  launch surface or messaging app their approval does not cover, and a browser while their text holds a
  javascript: URL. A refused key never reaches the OS and every later key of the call is refused too. Where the
  focus goes when no key is sent (Notepad closing back to Explorer or the desktop) refuses nothing, and a
  protected window in front when they start does not refuse them (check and recheck_foreground skip it).
On any refusal nothing more is sent and the call ends 'failed' (effect 'possible' once anything was sent) with the
run-time refusal code: the state code (DESKTOP_LOCKED, DESKTOP_PANIC, DESKTOP_CONTROL_DISABLED, DESKTOP_READ_ONLY),
PROTECTED_WINDOW for a shell, terminal, IDE or JARVIS window, else APPROVAL_REQUIRED (a launch surface or messaging
app the approval does not cover, a javascript: URL bound for a browser, or, between chunks, simply another window
than the one judged). A self-focusing call that sent all its keys ends 'completed' whatever has the focus after.
"""
# pylint: disable=too-many-lines
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable, Iterable, Mapping
from pathlib import Path
from typing import Any, Protocol

from services.ai_core import tool_placement
from services.utils import desktop_protocol as dp
from src.core.desktop import keygate
from src.core.desktop.executor import ACTUATION_CLASSES
from src.core.desktop.node import MODES, DesktopState, PendingCall
from src.core.desktop.presence import ManualPresence, Presence, PresenceMonitor

logger = logging.getLogger("JARVIS-DESKTOP.policy")

STATE_FILE = "desktop_state.json"
STATE_VERSION = 1

# K-76 codes this module returns (every one is a refusal before the call starts).
DESKTOP_CONTROL_DISABLED = "DESKTOP_CONTROL_DISABLED"
DESKTOP_PANIC = "DESKTOP_PANIC"
DESKTOP_READ_ONLY = "DESKTOP_READ_ONLY"
DESKTOP_LOCKED = "DESKTOP_LOCKED"
APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
APPROVAL_INVALID = "APPROVAL_INVALID"
PRESENCE_REQUIRED = "PRESENCE_REQUIRED"
SENSITIVE_SCREEN = "SENSITIVE_SCREEN"
PROTECTED_WINDOW = "PROTECTED_WINDOW"
PATH_NOT_ALLOWED = "PATH_NOT_ALLOWED"
DESKTOP_RATE_LIMITED = "DESKTOP_RATE_LIMITED"
DESKTOP_REJECTED = "DESKTOP_REJECTED"

_CODE_EXEC = tool_placement.CODE_EXEC
_MESSAGING = tool_placement.MESSAGING
_EXPOSURE = tool_placement.EXPOSURE
_AVAILABILITY = tool_placement.AVAILABILITY
_CAPTURE = tool_placement.CAPTURE
_INPUT = tool_placement.INPUT
_DE_ESCALATION = tool_placement.DE_ESCALATION
_CONFIRM_CLASSES = tool_placement.CONFIRM_CLASSES

_ISOLATED_ROOM_PREFIXES = ("jarvis-probe-", "jarvis-canary-")
_RISK_RANK = {"low": 0, "medium": 1, "high": 2}
_RECHECK_ATTEMPTS = 3      # looks at the foreground before an input call runs; the last one decides
_RECHECK_SETTLE_S = 0.2    # between looks, while a window that was just opened or closed takes the focus
# foreground codes that may still change while a window that was just opened or closed takes the focus
_RECHECK_SETTLE_CODES = frozenset({PROTECTED_WINDOW, APPROVAL_REQUIRED})
_ACCEPTED_MAX = 512  # accepted input calls remembered for the run-time re-check (oldest dropped first)
# Typing is sent in chunks of at most this many characters; the foreground is verified before each one, so a
# focus change mid-typing lets at most one chunk (about 0.3 s of typing) reach the window that was judged.
INPUT_CHUNK_CHARS = 16
# type_text_tool pastes (one Ctrl+V) instead of typing when not forced and the text is longer than this or not
# ASCII (services/automation/keyboard_mouse_ctrl.py); a paste is one action and is sent whole.
_PASTE_OVER_CHARS = 50
_CODE_ANIMATION_MAX = 6000   # type_code_animation_tool types at most this many characters
_CODE_ANIMATION_HEAD = 1500  # ... and unescapes its first part and the rest separately
_WINDOWS_PATH_RE = re.compile(r"(?:[A-Za-z]:\\|\\\\)")  # as the typing tool: no unescape with a path
_CHARS_RE = re.compile(r"\((\d+) characters\)")
# The recent typed text per foreground window, for the javascript: URL check across calls (defence in depth).
_TYPED_TAIL = 16
_TYPED_TTL_S = 120.0
_TYPED_WINDOWS_MAX = 64
# The run-time watchdog of an input call that is not cut into chunks (Notepad, WhatsApp, a paste): it looks at the
# foreground this often, and an unreadable foreground (nothing has the focus during a window switch) refuses only
# after this many looks in a row.
_WATCH_INTERVAL_S = 0.05
_WATCH_UNKNOWN_TICKS = 3
# Input-lock tools that are one action (a key, a hotkey, a click, a move, a scroll): the foreground was judged
# right before it, and a focus change it causes itself (Alt+Tab, a click on the taskbar) comes after its only
# input, so no watchdog runs for them.
_ATOMIC_INPUT_TOOLS = frozenset({"move_cursor_tool", "mouse_click_tool", "scroll_cursor_tool", "swipe_gesture_tool",
                                 "press_key_tool", "press_hotkey_tool"})
# open_app types its text after the launch only when the command asks for it (the tool's 'write'/'likh' parse);
# a plain launch sends no keystroke to the foreground window.
_OPEN_APP_TYPES_RE = re.compile(r"\b(?:write|likh)\b", re.IGNORECASE)
# Input-lock tools that move the focus to their own window (Notepad, WhatsApp, the app open_app launched) before
# they send a key, and never type into the window that was in front when they started. They are judged at key
# time (judge_input_key, through the keystroke gate): every key they send is judged against the window it really
# reaches. So a protected window in front at the start (the owner's IDE, terminal or the JARVIS window) does not
# refuse them, but a key that would still reach it because the focus never moved is refused; and the focus going
# back to Explorer or the desktop after their last key (Notepad closing) refuses nothing.
SELF_FOCUS_TOOLS: frozenset[str] = frozenset({
    "open_app", "save_notepad", "create_template_code", "write_custom_code", "edit_notepad_file_tool",
    "append_notepad_file_tool", "automate_whatsapp"})
# The text arguments of those tools (what they type): a javascript: URL in them is code_exec once a key would reach
# a browser (its address bar runs it in the signed-in page).
_SELF_FOCUS_TEXT_ARGS = ("append_text", "new_content", "content", "message", "full_command", "contact_name")
# At key time, nothing having the focus (a window switch) is looked at again this many times, this far apart.
_KEY_SETTLE_LOOKS = 3
_KEY_SETTLE_S = 0.03

# --- extensions ------------------------------------------------------------------------------------------------

# Scripts an installed interpreter runs when the file is opened (Python associates .py/.pyw with py.exe, Git for
# Windows associates .sh with bash): opening one, or writing one with auto_run, is code_exec (K-76).
SCRIPT_EXTS: frozenset[str] = frozenset({
    ".py", ".pyw", ".pyz", ".pyzw", ".pyc", ".pyo", ".sh", ".bash", ".zsh", ".ksh", ".bat", ".cmd", ".ps1",
    ".psm1", ".psd1", ".ps1xml", ".psc1", ".vbs", ".vbe", ".vb", ".js", ".jse", ".mjs", ".cjs", ".ws", ".wsf",
    ".wsh", ".wsc", ".sct", ".msh", ".msh1", ".msh2", ".mshxml", ".pl", ".rb", ".php", ".lua", ".tcl", ".ahk",
    ".au3", ".r"})
# Executables, installers, shortcuts and the Windows file types that run code or launch something when opened
# (LOLBins: mmc snap-ins, compiled help, ClickOnce, setting shortcuts, Explorer commands, setup scripts, Office
# add-ins, troubleshooters, macro-enabled Office files, disk images that mount and autorun).
EXEC_EXTS: frozenset[str] = SCRIPT_EXTS | frozenset({
    ".exe", ".com", ".scr", ".pif", ".cpl", ".msi", ".msp", ".mst", ".msix", ".msixbundle", ".appx",
    ".appxbundle", ".appinstaller", ".lnk", ".url", ".hta", ".jar", ".jnlp", ".reg", ".gadget", ".msc", ".chm",
    ".hlp", ".appref-ms", ".application", ".xbap", ".settingcontent-ms", ".scf", ".inf", ".xll", ".xlam", ".ppam",
    ".dotm", ".docm", ".xlsm", ".xltm", ".pptm", ".potm", ".ppsm", ".sldm", ".diagcab", ".diagpkg", ".library-ms",
    ".searchconnector-ms", ".searchms", ".rdp", ".iso", ".img", ".vhd", ".vhdx", ".dll", ".ocx", ".sys", ".drv",
    ".theme", ".themepack", ".deskthemepack", ".website", ".mof", ".ins", ".isp", ".crt", ".cer", ".der", ".p7b",
    ".csh", ".elf", ".run", ".bin", ".apk", ".deb", ".rpm", ".pkg", ".dmg"})
ARCHIVE_EXTS: frozenset[str] = frozenset({".zip", ".7z", ".rar", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".cab",
                                          ".zst", ".lzh", ".arj", ".ace", ".wim"})
_IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".heic", ".heif",
                         ".avif", ".ico"})
_AUDIO_EXTS = frozenset({".mp3", ".wav", ".flac", ".aac", ".ogg", ".oga", ".opus", ".m4a", ".wma", ".aiff",
                         ".mid", ".midi"})
_VIDEO_EXTS = frozenset({".mp4", ".m4v", ".mkv", ".avi", ".mov", ".wmv", ".webm", ".flv", ".mpg", ".mpeg",
                         ".3gp", ".m2ts"})
# The ONLY types folder_file and play_file may open without an approval (K-76): plain documents and media.
# Anything else, including no extension or an unknown one, is code_exec, because Windows runs whatever handler
# is registered for it (an allow-list, never a deny-list). The legacy Office types (.doc, .xls, .ppt, .pps) and
# .rtf are left out on purpose: they can carry macros or OLE objects, so opening one needs the approval.
OPEN_SAFE_EXTS: frozenset[str] = _IMAGE_EXTS | _AUDIO_EXTS | _VIDEO_EXTS | frozenset({
    ".txt", ".md", ".log", ".csv", ".tsv", ".json", ".pdf", ".docx", ".odt", ".xlsx", ".ods", ".pptx", ".odp",
    ".ppsx", ".epub", ".xps", ".oxps", ".html", ".htm", ".srt", ".vtt", ".svg"})
# The K-73 auto-open list, exactly: audio, video, image, pdf, txt, csv, json, xlsx, docx, pptx and html. Only
# a pc.fetch that opens one of these is non-actuation (K-76); any other open is code_exec.
K73_AUTO_OPEN_EXTS: frozenset[str] = _IMAGE_EXTS | _AUDIO_EXTS | _VIDEO_EXTS | frozenset({
    ".pdf", ".txt", ".csv", ".json", ".xlsx", ".docx", ".pptx", ".html", ".htm"})
# Kept for callers that import the old name; a script filename is any SCRIPT_EXTS or EXEC_EXTS type.
SCRIPT_FILENAME_EXTS: frozenset[str] = SCRIPT_EXTS
PASSWORD_MANAGER_EXTS: frozenset[str] = frozenset({".kdbx", ".kdb", ".psafe3", ".opvault", ".agilekeychain",
                                                   ".1pif", ".1pux"})
KEY_MATERIAL_EXTS: frozenset[str] = frozenset({".pem", ".key", ".ppk", ".pfx", ".p12", ".asc", ".gpg"})

# --- K-45 risk lists (laptop-relevant names) -------------------------------------------------------------------
# Only HIGH makes a call confirm-class on top of the class/K-16 rule; MEDIUM input tools keep running (K-76).
K45_HIGH: frozenset[str] = frozenset({
    "shutdown_system", "restart_system", "empty_recycle_bin", "run_cmd_command", "write_custom_code",
    "autonomous_self_repair", "send_email", "autonomous_research_and_email", "automate_whatsapp",
    "start_file_access_server", "zip_files", "lookup_sim_data", "guard_selftest"})
K45_MEDIUM: frozenset[str] = frozenset({
    "type_text_tool", "press_hotkey_tool", "press_key_tool", "mouse_click_tool",
    "chrome_browser_perform_action", "automate_chrome_browser", "download_youtube_media", "download_images",
    "create_folder", "sleep_system"})

# --- path argument hooks (K-76 path policy) --------------------------------------------------------------------
# resolve_in_roots(open_roots) + the extended is_sensitive_path.
PATH_ARG: Mapping[str, str] = {
    "open_notepad_file": "file_path", "folder_file": "path", "play_file": "name",
    "play_video": "file_path", "play_music": "file_path", "zip_files": "folder_path", "pc.read_file": "path"}
# a basename only, inside Jarvis_Outputs.
OUTPUTS_BASENAME_ARG: Mapping[str, str] = {
    "edit_notepad_file_tool": "filename", "append_notepad_file_tool": "filename"}
# opening anything but a folder or a plain document/media file through one of these is code_exec (K-76).
EXEC_ESCALATE_ARG: Mapping[str, str] = {"folder_file": "path", "play_file": "name"}

# --- launch surfaces -------------------------------------------------------------------------------------------
_WIN_TOKENS = frozenset({"win", "winleft", "winright", "lwin", "rwin", "super", "cmd", "command", "windows",
                         "win_l", "win_r", "start", "winkey"})
_CTRL_TOKENS = frozenset({"ctrl", "control", "lctrl", "rctrl", "ctrlleft", "ctrlright", "left ctrl", "right ctrl"})
_ALT_TOKENS = frozenset({"alt", "menu", "altleft", "altright", "left alt", "right alt"})
_SHIFT_TOKENS = frozenset({"shift", "lshift", "rshift", "shiftleft", "shiftright", "left shift", "right shift"})
_ESC_TOKENS = frozenset({"esc", "escape"})
_CONTEXT_MENU_TOKENS = frozenset({"apps", "application", "contextmenu", "context_menu"})
# Ctrl+Shift+<letter> that opens DevTools in any Chromium or Firefox browser (and in Electron apps): I toolbox,
# J console, C inspect element, K the Firefox Web Console.
_DEVTOOLS_LETTERS = frozenset({"i", "j", "c", "k"})
# Firefox-only DevTools panels; escalated only while a browser has the focus (Ctrl+Shift+Z is redo elsewhere).
_FIREFOX_DEVTOOLS_LETTERS = frozenset({"e", "m", "z"})

# Mapped, launch-safe GUI apps for open_app; anything else (a path, an executable, an unknown name) is code_exec.
MAPPED_APPS: frozenset[str] = frozenset({
    "notepad", "wordpad", "calc", "calculator", "mspaint", "paint", "snippingtool", "snip", "magnify",
    "charmap", "osk", "write", "settings", "chrome", "msedge", "edge", "firefox", "winword", "excel",
    "powerpnt", "outlook", "spotify", "vlc"})

# The foreground of a keystroke tool is judged by three signals, each guarded and each enough on its own: the
# process image name (the most reliable), the window class and the title. A match on any of them makes the
# keystrokes a launch surface (code_exec, K-76: dashboard Allow only); when a window could be a launch surface
# but we cannot tell which control has the focus (Explorer, the desktop, Start/Search) we fail conservative.
LAUNCH_PROCESSES: frozenset[str] = frozenset({
    # consoles and shells (incl. mintty, bash, wsl, python, node, conhost)
    "cmd.exe", "powershell.exe", "powershell_ise.exe", "pwsh.exe", "windowsterminal.exe", "wt.exe",
    "openconsole.exe", "conhost.exe", "mintty.exe", "bash.exe", "sh.exe", "wsl.exe", "wslhost.exe", "ubuntu.exe",
    "debian.exe", "python.exe", "pythonw.exe", "py.exe", "pyw.exe", "ipython.exe", "node.exe", "cscript.exe",
    "wscript.exe", "mshta.exe",
    # system tools that launch or change things
    "taskmgr.exe", "regedit.exe", "regedt32.exe", "mmc.exe", "rundll32.exe",
    # the Explorer shell (Run dialog, address bar, desktop, taskbar) and the Start/Search hosts
    "explorer.exe", "searchhost.exe", "searchui.exe", "searchapp.exe", "startmenuexperiencehost.exe",
    "shellexperiencehost.exe"})
# Window classes that are themselves a launch surface (K-76, spec step 3). The Explorer address bar and the Start
# menu / Search host have short, ambiguous or empty titles, so the class is the reliable signal there.
_LAUNCH_WINDOW_CLASSES = frozenset({
    "cabinetwclass", "explorewclass",             # File Explorer (the address bar launches programs)
    "progman", "workerw",                         # the desktop (Enter on a selected icon launches it)
    "windows.ui.core.corewindow",                 # Start menu / Search / modern shell host
    "searchhost", "searchui", "searchapp",        # Start-menu Search host (Win10/11)
    "startmenusizingframe", "immersivelauncher",  # Start menu frame
    "shell_traywnd", "shell_secondarytraywnd",    # taskbar / taskbar search box
    "consolewindowclass", "pseudoconsolewindow",  # conhost consoles (cmd, PowerShell, python, node, wsl)
    "cascadia_hosting_window_class",              # Windows Terminal
    "mintty", "virtualconsoleclass",              # Git Bash / MSYS mintty, ConEmu
    "taskmanagerwindow",                          # Task Manager
    "regedit_regedit",                            # Registry Editor
    "mmcmainframe",                               # Microsoft Management Console (every snap-in)
    "wndclass_desked_gsk",                        # Office VBA editor
    "devtools"})                                  # an undocked DevTools window
# Titles that are a launch surface on their own (exact match): the Run dialog and the Start/Search hosts, whose
# titles are short and otherwise ambiguous.
_LAUNCH_TITLE_EXACT = frozenset({"run", "search", "start", "start menu", "task switching", "task view",
                                 "program manager"})
# Title substrings of the launch surfaces, used only while the foreground process is unknown: a known process
# (chrome.exe showing 'Ubuntu - Wikipedia') is judged by its process and class, so an ordinary page title never
# turns typing into code_exec. Console titles usually carry the image path ('C:\...\cmd.exe').
_LAUNCH_TITLE_MARKERS = (
    "task manager", "registry editor", "command prompt", "windows powershell", "powershell", "windows terminal",
    "administrator:", "mintty", "git bash", "mingw64", "mingw32", "msys2", "ubuntu", "debian", "wsl",
    "cmd.exe", "conhost.exe", "bash.exe", "python.exe", "pythonw.exe", "py.exe", "node.exe", "python 3",
    "node.js command prompt", "visual basic for applications", "developer tools", "devtools",
    "microsoft management console", "console root", "computer management", "task scheduler", "event viewer",
    "group policy", "local security policy", "services (local)")
# Title prefixes that are a launch surface whatever the process: an undocked DevTools window is a browser
# process with the browser's window class, so its title is the only signal.
_LAUNCH_TITLE_PREFIXES = ("devtools -", "developer tools", "browser toolbox",
                          "microsoft visual basic for applications")
# Web browsers: a docked DevTools is invisible to the foreground signals, so with a browser in front the menu
# keys that lead to DevTools are escalated as well (is_browser_devtools_key).
BROWSER_PROCESSES: frozenset[str] = frozenset({
    "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "opera_gx.exe", "vivaldi.exe",
    "chromium.exe", "iexplore.exe", "arc.exe", "librewolf.exe", "waterfox.exe", "floorp.exe", "browser.exe",
    "thorium.exe", "yandex.exe", "zen.exe", "tor.exe", "palemoon.exe", "seamonkey.exe"})
_BROWSER_WINDOW_CLASSES = frozenset({"chrome_widgetwin_1", "chrome_widgetwin_0", "mozillawindowclass",
                                     "ieframe"})
_BROWSER_TITLE_SUFFIXES = ("google chrome", "microsoft edge", "mozilla firefox", "brave", "opera", "vivaldi",
                           "chromium", "firefox")
_MESSAGING_PROCESSES: frozenset[str] = frozenset({
    "whatsapp.exe", "whatsapp.root.exe", "telegram.exe", "signal.exe", "slack.exe", "discord.exe", "teams.exe",
    "ms-teams.exe", "skype.exe", "wechat.exe", "messenger.exe", "viber.exe", "element.exe"})
_MESSAGING_TITLE_MARKERS = (
    "whatsapp", "messenger", "telegram", "signal", "slack", "discord", "microsoft teams", "skype", "wechat",
    "viber")
# Keys that are safe whatever is in the foreground (they act on the system mixer or the media session). Compared
# with underscores removed on both sides, so the tool's real pynput names (media_volume_up, media_play_pause,
# media_next, media_previous ...) and the pyautogui spellings (volumeup, playpause, nexttrack ...) both match.
_SURFACE_SAFE_KEYS = frozenset({
    "mediavolumeup", "mediavolumedown", "mediavolumemute", "mediaplaypause", "medianext", "mediaprevious",
    "mediastop", "volumeup", "volumedown", "volumemute", "playpause", "nexttrack", "prevtrack"})
# Tools that send keystrokes to whatever has the focus.
KEYSTROKE_TOOLS: frozenset[str] = frozenset({
    "type_text_tool", "type_code_animation_tool", "press_key_tool", "press_hotkey_tool"})
# Protected windows refuse input/keys/typing outright (K-76). Prefixes match by prefix, always; the substring
# markers only while the foreground process is unknown (a browser tab titled 'CSS cursor property' is not Cursor).
PROTECTED_TITLE_PREFIXES = ("JARVIS & ANNA", "JARVIS-Agent-Worker", "JARVIS-AGENT")
_PROTECTED_TITLE_MARKERS = (
    "visual studio code", "cursor", "windows powershell", "command prompt", "windows terminal",
    "mintty", "wsl", "git bash")
# K-76 'shells, terminals, IDEs' by process image name and window class, so a console whose title was changed
# (a WSL prompt, a python REPL) is still protected.
_PROTECTED_PROCESSES: frozenset[str] = frozenset({
    "cmd.exe", "powershell.exe", "powershell_ise.exe", "pwsh.exe", "windowsterminal.exe", "wt.exe",
    "openconsole.exe", "conhost.exe", "mintty.exe", "bash.exe", "wsl.exe", "wslhost.exe", "code.exe",
    "cursor.exe", "devenv.exe", "pycharm64.exe", "idea64.exe", "webstorm64.exe", "phpstorm64.exe"})
_PROTECTED_WINDOW_CLASSES: frozenset[str] = frozenset({
    "consolewindowclass", "pseudoconsolewindow", "cascadia_hosting_window_class", "mintty",
    "virtualconsoleclass"})
# Foreground titles that refuse a capture (K-76): a sensitive path or a password manager.
_SENSITIVE_TITLE_MARKERS = (
    ".ssh", ".env", ".coord", "chroma_db", "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa", ".kdbx", ".pth",
    "desktop_state.json", "keepass", "1password", "bitwarden", "lastpass", "dashlane", "keeper", "nordpass",
    "enpass", "roboform", "proton pass", "password manager")
_PASSWORD_MANAGER_PROCESSES: frozenset[str] = frozenset({
    "keepass.exe", "keepassxc.exe", "1password.exe", "bitwarden.exe", "lastpass.exe", "dashlane.exe",
    "keeperpasswordmanager.exe", "keeper.exe", "nordpass.exe", "enpass.exe", "roboform.exe", "proton pass.exe"})
# Capture tools that use the webcam: they always need presence; a session opt-in never stands in for it (K-76).
WEBCAM_TOOLS: frozenset[str] = frozenset({"pc.capture_webcam"})

# the read-only allow-list first words for run_cmd_command (low risk); anything else is high.
_CMD_READONLY = frozenset({"dir", "echo", "type", "whoami", "hostname", "ver", "date", "time", "ipconfig",
                           "systeminfo", "tasklist", "where", "ping", "ping6", "netstat", "getmac", "vol",
                           "set", "path", "tree"})
_CMD_METACHARS = frozenset("%!^&|<>()")


# --- path helpers ----------------------------------------------------------------------------------------------

def _under(path: Path, parent: Path | None) -> bool:
    if parent is None:
        return False
    try:
        path.resolve(strict=False).relative_to(parent.resolve(strict=False))
    except (ValueError, OSError):
        return False
    return True


def is_sensitive_path(path: str | os.PathLike[str], *, data_root: Path | None, outputs_dir: Path | None,
                      jarvis_appdata: Path | None = None) -> bool:
    """The extended K-76 sensitive set. A True keeps the path out of reach of every laptop path tool.

    Covers: the repo (JARVIS_DATA_ROOT, never the snapshot) except Jarvis_Outputs; .venv*; any *.pth; the
    Startup folders; %LOCALAPPDATA%\\JARVIS; ~/.ssh; *.env; id_*; .coord, chroma_db, conversations and logs;
    key material; and password-manager data. Anything unresolvable is treated as sensitive (fails closed).
    """
    try:
        p = Path(path).expanduser()
    except (TypeError, ValueError):
        return True
    try:
        resolved = p.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return True
    parts_lower = [seg.lower() for seg in resolved.parts]
    name = resolved.name.lower()
    suffix = resolved.suffix.lower()
    if _under(resolved, data_root) and not _under(resolved, outputs_dir):
        return True
    if jarvis_appdata is not None and _under(resolved, jarvis_appdata):
        return True
    if any(seg.startswith(".venv") for seg in parts_lower):
        return True
    if suffix == ".pth":
        return True
    if "startup" in parts_lower and ("programs" in parts_lower or "start menu" in parts_lower):
        return True
    if ".ssh" in parts_lower:
        return True
    if name.startswith(".env") or name.endswith(".env") or ".env" in parts_lower:
        return True
    if name.startswith("id_"):
        return True
    if any(seg in {".coord", "chroma_db", "conversations", "logs"} for seg in parts_lower):
        return True
    if suffix in KEY_MATERIAL_EXTS or suffix in PASSWORD_MANAGER_EXTS:
        return True
    if any(seg in {"keepass", "1password", "bitwarden", "lastpass", "dashlane", "keeper"} for seg in parts_lower):
        return True
    return False


def resolve_in_roots(value: Any, roots: Iterable[str | os.PathLike[str]], *, data_root: Path | None,
                     outputs_dir: Path | None, jarvis_appdata: Path | None = None) -> Path | None:
    """The resolved path when it stays inside one of the open roots and is not sensitive, else None."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.startswith(("\\\\", "//")) or "\x00" in text:  # UNC / network / embedded NUL
        return None
    try:
        candidate = Path(text).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return None
    if is_sensitive_path(candidate, data_root=data_root, outputs_dir=outputs_dir, jarvis_appdata=jarvis_appdata):
        return None
    for root in roots:
        try:
            base = Path(root).expanduser().resolve(strict=False)
        except (OSError, RuntimeError, ValueError):
            continue
        try:
            candidate.relative_to(base)
            return candidate
        except ValueError:
            continue
    return None


def is_basename(value: Any) -> bool:
    """True for a plain file name: no separators, no drive, no '..', not absolute and not empty."""
    if not isinstance(value, str) or not value.strip():
        return False
    text = value.strip()
    if text in (".", "..") or "\x00" in text:
        return False
    if os.path.basename(text) != text:
        return False
    if "/" in text or "\\" in text or (len(text) >= 2 and text[1] == ":"):
        return False
    return ".." not in Path(text).parts


# --- launch-surface helpers ------------------------------------------------------------------------------------

def _norm_key(key: Any) -> str:
    return str(key).strip().lower().strip("{}<>").replace(" ", "")


def _key_tokens(keys: Any) -> list[str]:
    """The normalised key names of a hotkey: a list of names, or one 'ctrl+shift+esc' style string."""
    if isinstance(keys, str):
        keys = [keys]
    if not isinstance(keys, (list, tuple)):
        return []
    tokens: list[str] = []
    for key in keys:
        text = str(key)
        parts = [text] if text.strip() == "+" else [part for part in text.split("+") if part.strip()]
        tokens.extend(_norm_key(part) for part in parts)
    return [token for token in tokens if token]


def is_launch_hotkey(keys: Any) -> bool:
    """A press_hotkey_tool combo that reaches a launch surface (K-76): any Win combo (the Win key alone opens
    Start), Ctrl+Esc (Start), Ctrl+Shift+Esc, Alt+F11, anything containing F12, and the DevTools combos
    Ctrl+Shift+I/J/C/K (C opens DevTools in inspect mode in Chrome, Edge and Firefox, K the Firefox Web
    Console). A docked DevTools has the browser's process, class and title, so these keys are its only guard."""
    tokens = set(_key_tokens(keys))
    if not tokens:
        return False
    has_ctrl = bool(tokens & _CTRL_TOKENS)
    has_alt = bool(tokens & _ALT_TOKENS)
    has_shift = bool(tokens & _SHIFT_TOKENS)
    if tokens & _WIN_TOKENS:
        return True
    if "f12" in tokens:
        return True
    if has_ctrl and (tokens & _ESC_TOKENS):
        return True  # Ctrl+Esc opens Start, Ctrl+Shift+Esc opens Task Manager
    if has_alt and "f11" in tokens:
        return True
    if has_ctrl and has_shift and (tokens & _DEVTOOLS_LETTERS):
        return True
    return False


def is_browser_foreground(title: str, win_class: str = "", process: str = "") -> bool:
    """A web browser (or an Electron/WebView app, which has the same DevTools) has the focus: by process image
    name or by the Chromium/Firefox window class, and while the process is unknown also by a browser title
    suffix. Guarded the same way as the launch-surface signals."""
    proc = (process or "").strip().lower()
    if proc in BROWSER_PROCESSES:
        return True
    cls = (win_class or "").strip().lower()
    if cls and cls in _BROWSER_WINDOW_CLASSES:
        return True
    low = (title or "").strip().lower()
    return not proc and bool(low) and low.endswith(_BROWSER_TITLE_SUFFIXES)


def is_browser_devtools_key(keys: Any) -> bool:
    """Keys that, with a browser in front, reach DevTools through its menus or the browser-only DevTools combos
    (conservative, K-76): Alt alone or Alt+F / Alt+E (the browser menu; '... > Developer tools'), F10 and
    Shift+F10 / the context-menu key (the menu button and the page menu with 'Inspect'), and the Firefox tool
    combos Ctrl+Shift+E (Network), Ctrl+Shift+M (Responsive design) and Ctrl+Shift+Z (Debugger), whose split
    console opens with Esc."""
    tokens = set(_key_tokens(keys))
    if not tokens:
        return False
    has_ctrl = bool(tokens & _CTRL_TOKENS)
    has_alt = bool(tokens & _ALT_TOKENS)
    has_shift = bool(tokens & _SHIFT_TOKENS)
    if has_alt and (tokens <= _ALT_TOKENS | _SHIFT_TOKENS or tokens & {"f", "e"}):
        return True
    if "f10" in tokens or tokens & _CONTEXT_MENU_TOKENS:
        return True
    return has_ctrl and has_shift and bool(tokens & _FIREFOX_DEVTOOLS_LETTERS)


def is_launch_key(key: Any) -> bool:
    """A press_key_tool key that is itself a launch surface: F12 (DevTools), the Win key (Start), or a combo
    written as one key ('win+r')."""
    return is_launch_hotkey(key)


def _squash_typed(text: str) -> str:
    """Typed text as an address bar reads it for the javascript: check: lower case, no whitespace or control."""
    return "".join(ch for ch in text.lower() if not ch.isspace() and ch.isprintable())


def _types_javascript_url(text: Any) -> bool:
    """The typed text holds a javascript: URL (whitespace and control characters ignored, any case), which an
    address bar runs as script in the signed-in page."""
    if not isinstance(text, str):
        return False
    return "javascript:" in _squash_typed(text)


def _unescape_typed(text: str) -> str:
    """The text the typing tool really types: it turns a literal backslash-n / backslash-t into a newline / tab
    unless the text already has a real line break or holds a Windows path (keyboard_mouse_ctrl's
    _maybe_unescape_literals, mirrored so the chunks type exactly what one call would have typed)."""
    if "\n" in text or "\r" in text or _WINDOWS_PATH_RE.search(text):
        return text
    if "\\n" in text or "\\t" in text:
        return text.replace("\\n", "\n").replace("\\t", "\t")
    return text


def split_typed(text: str, size: int = INPUT_CHUNK_CHARS) -> list[str]:
    """Already-unescaped text cut into pieces of at most size characters. A piece never holds a backslash
    followed by 'n' or 't' (the cut falls between them), so the tool's unescape cannot change a piece: each one
    types exactly its characters."""
    size = max(1, int(size))
    pieces: list[str] = []
    current = ""
    for ch in text:
        if current and (len(current) >= size or (current.endswith("\\") and ch in "nt")):
            pieces.append(current)
            current = ""
        current += ch
    if current:
        pieces.append(current)
    return pieces


def _keys_all_safe(keys: Any) -> bool:
    tokens = _key_tokens(keys)
    return bool(tokens) and all(token.replace("_", "") in _SURFACE_SAFE_KEYS for token in tokens)


def _foreground_surface(  # pylint: disable=too-many-return-statements
        title: str, win_class: str = "", process: str = "") -> str:
    """'code_exec' when the foreground is a launch surface, 'messaging' for a messaging app, else ''.

    The launch check comes first because it is the stricter class (code_exec is dashboard Allow only). The
    process image name and the window class are the reliable signals. The title substring markers apply only
    while the process is unknown, so a known non-shell process (a browser page titled 'Ubuntu') stays input;
    the exact titles and the DevTools/VBA prefixes apply always. A foreground with no title, class and process
    at all (a focus change, Start opening, a failed query) is unknown and treated as a launch surface.
    """
    low = (title or "").strip().lower()
    cls = (win_class or "").strip().lower()
    proc = (process or "").strip().lower()
    if not (low or cls or proc):
        return _CODE_EXEC  # cannot tell what has the focus: escalate (K-76: when unsure, approval)
    if proc and proc in LAUNCH_PROCESSES:
        return _CODE_EXEC
    if cls and any(cls == c or cls.startswith(c) for c in _LAUNCH_WINDOW_CLASSES):
        return _CODE_EXEC
    if low and (low in _LAUNCH_TITLE_EXACT or low.startswith(_LAUNCH_TITLE_PREFIXES)):
        return _CODE_EXEC
    if low and not proc and any(marker in low for marker in _LAUNCH_TITLE_MARKERS):
        return _CODE_EXEC
    if proc and proc in _MESSAGING_PROCESSES:
        return _MESSAGING
    if low and any(marker in low for marker in _MESSAGING_TITLE_MARKERS):
        return _MESSAGING
    return ""


def is_protected_title(title: str, process: str = "") -> bool:
    """A protected window by title: the JARVIS prefixes always; the shell/IDE substrings only while the
    foreground process is unknown (a known process is judged by _PROTECTED_PROCESSES and the class)."""
    low = (title or "").strip().lower()
    if not low:
        return False
    if any((title or "").startswith(prefix) for prefix in PROTECTED_TITLE_PREFIXES):
        return True
    if (process or "").strip():
        return False
    return any(marker in low for marker in _PROTECTED_TITLE_MARKERS)


def windows_target_name(value: Any) -> str:
    """The final name Windows will actually open for a path argument, lower-cased: the last component with any
    ':' stream suffix cut ('x.exe::$DATA', 'x.exe:s') and the trailing dots and spaces stripped ('x.exe.',
    'x.exe. .'), as GetFullPathNameW / CreateFileW do. '' for an empty or non-text value."""
    if not isinstance(value, str):
        return ""
    text = value.strip().replace("\\", "/")
    name = text.rsplit("/", 1)[-1]
    if len(name) >= 2 and name[1] == ":" and "/" not in text:
        name = name[2:]  # 'C:x.exe' (drive-relative): the drive is not part of the name
    name = name.split(":", 1)[0]
    return name.rstrip(" .").lower()


def _suffix_of(name: str) -> str:
    """The last extension of a normalised name ('' for none; a leading-dot name like '.bashrc' has none)."""
    stem, dot, ext = name.rpartition(".")
    return f".{ext}" if dot and stem and ext else ""


def _normalised_target(value: str) -> Path | None:
    """The absolute path Windows would open: abspath, with the final name normalised as windows_target_name."""
    try:
        absolute = os.path.abspath(os.path.expanduser(value.strip()))
    except (OSError, ValueError, TypeError):
        return None
    name = windows_target_name(value)
    parent = os.path.dirname(absolute.rstrip("/\\")) if absolute.rstrip("/\\") else absolute
    return Path(parent) / name if name else Path(absolute)


def opens_as_code(value: Any, *, allow_directory: bool = True,
                  safe_exts: frozenset[str] = OPEN_SAFE_EXTS) -> bool:
    """True when opening value with the shell may run code (K-76 code_exec): anything whose normalised type is
    not in safe_exts (by default the plain document and media types, OPEN_SAFE_EXTS), including no extension and
    an unknown one. An existing directory stays input (Explorer opens it) unless its name carries an executable
    or archive type. When the target exists, its long name (an 8.3 short name hides the real type) must pass as
    well."""
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        return True
    text = value.strip().replace("\\", "/")
    final = text.rsplit("/", 1)[-1]
    if "/" not in text and len(final) >= 2 and final[1] == ":":
        final = final[2:]  # 'C:x.exe' (drive-relative)
    if ":" in final:
        return True  # an NTFS stream ('a.txt:x.exe'): the shell may pick the handler from the stream's type
    names = [windows_target_name(value)]
    target = _normalised_target(value)
    is_dir = False
    if target is not None:
        try:
            is_dir = target.is_dir()
            if target.exists():
                names.append(target.resolve(strict=False).name.rstrip(" .").lower())
        except (OSError, RuntimeError, ValueError):
            return True
    for name in names:
        suffix = _suffix_of(name)
        if suffix in EXEC_EXTS or suffix in ARCHIVE_EXTS:
            return True
        if is_dir and allow_directory:
            continue
        if suffix not in safe_exts:
            return True
    return False


def _is_mapped_app(full_command: Any) -> bool:
    if not isinstance(full_command, str) or not full_command.strip():
        return False
    first = full_command.strip().split()[0].strip('"')
    if "/" in first or "\\" in first or (len(first) >= 2 and first[1] == ":"):
        return False  # a path is never a mapped app
    base = Path(first).name.lower()
    if base.endswith(".exe"):
        base = base[:-4]
    return base in MAPPED_APPS


def sends_keys_to_foreground(tool: str, args: Mapping[str, Any]) -> bool:
    """False only for a call that holds the input lock but types nothing into the window in front: an open_app
    whose command asks for no text ('write'/'likh'). Its launch sends no keystroke there, so a shell, terminal or
    IDE in front does not refuse it (it still holds the lock and pauses while Windows is locked)."""
    if tool == "open_app":
        command = args.get("full_command")
        return not isinstance(command, str) or bool(_OPEN_APP_TYPES_RE.search(command))
    return True


def refuses_protected_start(tool: str, args: Mapping[str, Any]) -> bool:
    """Whether a protected window in front (shell, terminal, IDE, JARVIS) refuses this input-lock call before it
    runs (check, recheck_foreground): every call that sends input to the window in front (typing, keys, clicks).
    Not an open_app that types nothing, and not the SELF_FOCUS_TOOLS, which move the focus to their own window
    first: their keys are judged at key time instead (judge_input_key), so a key that would still reach the
    protected window is refused there."""
    return sends_keys_to_foreground(tool, args) and tool not in SELF_FOCUS_TOOLS


def _args_type_javascript_url(args: Mapping[str, Any]) -> bool:
    """A text argument of a SELF_FOCUS_TOOLS call holds a javascript: URL."""
    return any(_types_javascript_url(args.get(name)) for name in _SELF_FOCUS_TEXT_ARGS)


class ToolChunk(dict):
    """One chunk of an input call that is sent through another laptop tool (tool) with these arguments; fallback
    holds the call's own chunks that type the same text when that tool cannot be reached."""

    def __init__(self, tool: str, args: Mapping[str, Any], fallback: Iterable[Mapping[str, Any]] = ()) -> None:
        super().__init__(args)
        self.tool = tool
        self.fallback = [dict(chunk) for chunk in fallback]


def _is_script_filename(filename: Any) -> bool:
    if not isinstance(filename, str) or not filename.strip():
        return True  # unknown: treat as a script so auto_run is confirmed
    # a script is anything but a plain document/media type (normalised as Windows will name the file)
    return _suffix_of(windows_target_name(filename)) not in OPEN_SAFE_EXTS


# --- persisted state -------------------------------------------------------------------------------------------

class PersistentState:
    """run/desktop_state.json: the node's mode, panic latch and remote-stop flag, survived across reboots.

    The node starts in full unless the file holds a panic latch, a remote stop or a tray Off/Read choice
    (K-76). Remote callers only tighten (tighten); the laptop tray loosens (tray_full clears the panic latch
    and the remote stop). The Windows lock is runtime only and never persisted. Thread-safe; writes are
    atomic and a write failure never crashes the node.
    """

    def __init__(self, path: str | os.PathLike[str], *, default_mode: str = "full") -> None:
        if default_mode not in MODES:
            raise ValueError(f"default_mode must be one of {MODES}")
        self.path = Path(path)
        self._lock = threading.RLock()
        self._listeners: list[Callable[[DesktopState], None]] = []
        self._locked = False
        self._mode, self._panic, self._remote = self._load(default_mode)

    def _load(self, default_mode: str) -> tuple[str, bool, bool]:
        """Only a missing file starts in default_mode. A file that exists but cannot be read, or that is empty,
        torn (a power-off right after a panic) or not a valid state document, starts fail-closed: mode off with
        the panic latch set, so a lost latch can never come back as full (K-76, spec step 9). The bad file is
        kept as <name>.bad and only the tray Full choice clears the latch."""
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            self._write(default_mode, False, False)
            return default_mode, False, False
        except UnicodeDecodeError:  # read, but not text: torn or overwritten
            text = None
        except OSError as exc:  # unreadable (an AV scan, a permission problem)
            logger.warning("Desktop state file unreadable (%s); starting latched off", type(exc).__name__)
            return "off", True, False  # the file is left as it is: it may still hold the real state
        try:
            doc = json.loads(text) if text is not None else None
        except ValueError:
            doc = None
        valid = (isinstance(doc, dict) and doc.get("mode") in MODES
                 and isinstance(doc.get("panic", False), bool) and isinstance(doc.get("remote_stop", False), bool))
        if not valid:
            logger.warning("Desktop state file is corrupt; starting latched off (kept as %s.bad)", self.path.name)
            self._keep_bad_file()
            self._write("off", True, False)
            return "off", True, False
        mode = doc["mode"]
        panic = bool(doc.get("panic", False))
        remote = bool(doc.get("remote_stop", False))
        if panic:
            mode = "off"
        return mode, panic, remote

    def _keep_bad_file(self) -> None:
        try:
            os.replace(self.path, self.path.with_name(self.path.name + ".bad"))
        except OSError as exc:
            logger.warning("Could not keep the corrupt desktop state file (%s)", type(exc).__name__)

    def _write(self, mode: str, panic: bool, remote: bool) -> None:
        """Atomic and durable: the temp file is flushed and fsynced before the rename, so a power-off right
        after a panic leaves either the old state or the new one, never an empty file."""
        doc = {"version": STATE_VERSION, "mode": mode, "panic": panic, "remote_stop": remote}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            with open(tmp, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(doc, indent=2))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
        except OSError as exc:
            logger.warning("Could not persist desktop state (%s)", type(exc).__name__)

    # --- StateSource protocol --------------------------------------------------------------------------------

    def current(self) -> DesktopState:
        with self._lock:
            return DesktopState(mode=self._mode, locked=self._locked, panic=self._panic)

    def add_listener(self, callback: Callable[[DesktopState], None]) -> None:
        self._listeners.append(callback)

    def tighten(self, mode: str, *, source: str = "") -> bool:
        """A remote stop (UI, phone, chat, voice, agent or bridge): lowers the mode, never raises it, and
        records that a remote stop is in force so only the tray can loosen it."""
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        with self._lock:
            if _rank(mode) >= _rank(self._mode):
                return False
            logger.warning("Desktop mode %s -> %s (remote stop from %s)", self._mode, mode, source or "?")
            self._mode, self._remote = mode, True
            self._persist_locked()
        self._notify()
        return True

    # --- node/tray/panic drivers -----------------------------------------------------------------------------

    def set_locked(self, locked: bool) -> None:
        with self._lock:
            if self._locked == bool(locked):
                return
            self._locked = bool(locked)
        self._notify()

    def trip_panic(self) -> None:
        with self._lock:
            if self._panic and self._mode == "off":
                return
            self._panic, self._mode = True, "off"
            self._persist_locked()
        self._notify()

    def tray_off(self) -> None:
        self._set_tray_mode("off")

    def tray_read(self) -> None:
        self._set_tray_mode("read")

    def tray_full(self) -> None:
        """The only place loosening happens (K-76): mode full, panic latch and remote stop cleared."""
        with self._lock:
            if self._mode == "full" and not self._panic and not self._remote:
                return
            self._mode, self._panic, self._remote = "full", False, False
            self._persist_locked()
        self._notify()

    def _set_tray_mode(self, mode: str) -> None:
        with self._lock:
            # The panic latch and a remote stop are cleared solely by tray Full (K-76, spec step 9: 'latched,
            # cleared only at the laptop (the tray Full choice)'): Off and Read keep both flags. While the panic
            # latch holds the mode stays off, so a Read choice cannot show (or persist) a mode above off.
            if self._panic:
                mode = "off"
            if self._mode == mode:
                return
            self._mode = mode
            self._persist_locked()
        self._notify()

    # --- internals -------------------------------------------------------------------------------------------

    def _persist_locked(self) -> None:
        self._write(self._mode, self._panic, self._remote)

    def _notify(self) -> None:
        state = self.current()
        for callback in list(self._listeners):
            try:
                callback(state)
            except Exception:  # pylint: disable=broad-exception-caught
                logger.exception("State listener raised")


def _rank(mode: str) -> int:
    return {"off": 0, "read": 1, "full": 2}[mode]


def _isolated_room(room: Any) -> bool:
    """A probe or canary room (K-57): actuation is refused from it."""
    return isinstance(room, str) and room.startswith(_ISOLATED_ROOM_PREFIXES)


# --- abuse caps ------------------------------------------------------------------------------------------------

class RateLimiter:
    """The K-76 message-rate caps (not CPU/RAM). A trip returns DESKTOP_RATE_LIMITED; it is counted only on
    an otherwise-accepted call, so a refusal never consumes a slot. Byte caps (uploads 200 MB/h, deliveries
    5 GB/day) go through note_bytes, which C-122 calls with the size of each upload or delivery: it returns
    DESKTOP_RATE_LIMITED (and records nothing) when the size would cross the cap, else None."""

    COUNT_LIMITS: Mapping[str, tuple[int, float]] = {
        "input": (600, 60.0), "messaging": (60, 3600.0), "approvals": (30, 3600.0), "uploads": (60, 3600.0)}
    BYTE_LIMITS: Mapping[str, tuple[int, float]] = {
        "uploads": (200 * 1024 * 1024, 3600.0), "deliveries": (5 * 1024 * 1024 * 1024, 86400.0)}

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._counts: dict[str, deque[float]] = {name: deque() for name in self.COUNT_LIMITS}
        self._bytes: dict[str, deque[tuple[float, int]]] = {name: deque() for name in self.BYTE_LIMITS}

    def allow(self, bucket: str) -> bool:
        return self.allow_all((bucket,))

    def allow_all(self, buckets: Iterable[str]) -> bool:
        """True and one slot taken in every bucket when all of them have room; else False and none taken."""
        return self.reserve(buckets) is not None

    def reserve(self, buckets: Iterable[str]) -> tuple[tuple[str, ...], float] | None:
        """One slot taken in every bucket when all of them have room, returned as a token release() can give
        back; None (nothing taken) when one is full."""
        names = tuple(dict.fromkeys(buckets))
        with self._lock:
            now = self._clock()
            for name in names:
                limit, window = self.COUNT_LIMITS[name]
                events = self._counts[name]
                while events and now - events[0] > window:
                    events.popleft()
                if len(events) >= limit:
                    return None
            for name in names:
                self._counts[name].append(now)
            return names, now

    def release(self, token: tuple[tuple[str, ...], float] | None) -> None:
        """Gives back the slots of a reserve() token (a call refused or never started after it was judged)."""
        if not token:
            return
        names, stamp = token
        with self._lock:
            for name in names:
                try:
                    self._counts[name].remove(stamp)
                except (KeyError, ValueError):
                    pass  # already expired out of the window

    def note_bytes(self, bucket: str, size: int) -> str | None:
        """Records size bytes in a byte bucket ('uploads' 200 MB/h, 'deliveries' 5 GB/day). DESKTOP_RATE_LIMITED
        when they would cross the cap (nothing is recorded then), else None."""
        limit, window = self.BYTE_LIMITS[bucket]
        amount = max(0, int(size))
        with self._lock:
            now = self._clock()
            events = self._bytes[bucket]
            while events and now - events[0][0] > window:
                events.popleft()
            total = sum(n for _, n in events)
            if total + amount > limit:
                return DESKTOP_RATE_LIMITED
            events.append((now, amount))
            return None


# --- the policy ------------------------------------------------------------------------------------------------

class _Settings(Protocol):
    desktop_control: bool
    presence_window_s: int
    capture_presence_s: int
    origin_presence_gate: bool


class DesktopPolicy:
    """The K-76 policy NodeRuntime installs in place of BaselinePolicy. check(call) is synchronous and returns
    the refusal code, or None to run the call; the node turns a code into the K-70 refusal ack.

    check() never raises a K-74 alert itself: NodeRuntime._refuse raises exactly one per refused exec for the
    alert codes (APPROVAL_INVALID critical, DESKTOP_RATE_LIMITED warning), so one event is one notice. Its only
    side effect is the local toast (an approval verify failure, a cap trip). note_bytes() is the one path that
    raises the alert here, because a byte-cap trip is found while a call runs, not by a refusal.

    The cap slots an accepted call takes are only reserved by check(): the node calls call_started(call_id) when
    the call really starts (that commits them and shows the capture toast) and release_call(call_id) when it is
    refused later or ends without starting (that gives them back; no toast)."""

    def __init__(self, *, state: PersistentState, settings: Callable[[], _Settings], presence: Presence,
                 open_roots: Callable[[], Iterable[str]], data_root: Path, outputs_dir: Path,
                 jarvis_appdata: Path | None = None, active_job: Callable[[], Mapping[str, Any] | None] | None = None,
                 rate_limiter: RateLimiter | None = None,
                 alert: Callable[..., None] | None = None, toast: Callable[[str], None] | None = None,
                 audit: Callable[..., None] | None = None, capture_opt_in: bool = False) -> None:
        self._state = state
        self._settings = settings
        self._presence = presence
        self._open_roots = open_roots
        self._data_root = Path(data_root)
        self._outputs_dir = Path(outputs_dir)
        self._appdata = jarvis_appdata
        self._active_job = active_job or (lambda: None)
        self._caps = rate_limiter or RateLimiter()
        self._alert = alert
        self._toast = toast
        self._audit = audit
        self.capture_opt_in = capture_opt_in
        # Accepted input-lock calls, kept until recheck_foreground judges them again right before they run.
        self._accepted: OrderedDict[str, PendingCall] = OrderedDict()
        # Calls whose final re-check passed, handed to begin_input_run with their approval (the run-time checks
        # judge the call as it was accepted, approval included).
        self._passed: OrderedDict[str, PendingCall] = OrderedDict()
        self._accepted_lock = threading.Lock()
        # Input-lock calls running now: the call and the foreground (handle, protection class) judged for it.
        self._running: OrderedDict[str, tuple[PendingCall, tuple[int, str]]] = OrderedDict()
        # The watchdog's state per running call: whether the focus has left the window the call started from,
        # and how many looks in a row found no readable foreground.
        self._watching: dict[str, dict[str, Any]] = {}
        # Accepted calls not started yet: their reserved cap slots and the toast to show when they start.
        self._starting: OrderedDict[str, tuple[Any, str | None]] = OrderedDict()
        # Recent typed text per foreground window: key -> (monotonic time, squashed tail).
        self._typed: OrderedDict[Any, tuple[float, str]] = OrderedDict()

    # --- the K-76 check --------------------------------------------------------------------------------------

    def check(self, call: PendingCall) -> str | None:  # pylint: disable=too-many-return-statements,too-many-branches
        cls = call.cls
        if cls == _DE_ESCALATION:
            return None  # de-escalation always runs, even during panic, off or the kill switch
        settings = self._settings()
        state = self._state.current()
        # The dynamic class is only computed here (it refuses nothing): a call that escalates into an actuation
        # class (e.g. a pc.fetch that would open an executable) meets the state checks as an actuation.
        effective_cls = self._effective_class(call)
        actuation = call.actuation or effective_cls in ACTUATION_CLASSES

        # 1. kill switch, 2. panic, 3. mode, 4. Windows lock
        if actuation and not bool(settings.desktop_control):
            return DESKTOP_CONTROL_DISABLED
        if state.panic:
            return DESKTOP_PANIC
        if state.mode == "off":
            return DESKTOP_CONTROL_DISABLED
        if actuation and state.mode != "full":
            return DESKTOP_READ_ONLY
        if state.locked and (call.needs_input_lock or cls == _CAPTURE):
            return DESKTOP_LOCKED

        # 5. class and approval
        if call.needs_input_lock and refuses_protected_start(call.tool, call.args) and self._protected_foreground():
            return PROTECTED_WINDOW
        if cls == _CAPTURE:
            code = self._capture_problem(call, settings)
            if code is not None:
                return code
        confirm, eff_risk = self._confirm_requirements(call, effective_cls)
        if confirm:
            code = self._approval_problem(call, effective_cls, eff_risk)
            if code is not None:
                return code

        # 6. origin
        if actuation:
            if call.origin == "canary" or _isolated_room(call.room):
                return DESKTOP_READ_ONLY
            if not self._active_job_allows(call):
                return DESKTOP_REJECTED
            if bool(settings.origin_presence_gate) and call.origin in ("scheduler", "job"):
                if not self._presence.present_within(settings.presence_window_s):
                    return PRESENCE_REQUIRED

        # 7. paths
        code = self._path_problem(call)
        if code is not None:
            return code

        # 8. abuse caps (reserved now, committed when the call starts, given back if it never does)
        code, token = self._cap_problem(call, effective_cls, confirm)
        if code is not None:
            return code
        # a toast on every capture that runs (K-76 / D15), shown by call_started: never for a refused one
        toast = None
        if cls == _CAPTURE:
            toast = ("Sir, laptop ka camera istemal hua." if call.tool in WEBCAM_TOOLS
                     else "Sir, laptop ki screen li gayi.")
        if token is not None or toast is not None:
            with self._accepted_lock:
                self._starting[call.call_id] = (token, toast)
                self._starting.move_to_end(call.call_id)
                while len(self._starting) > _ACCEPTED_MAX:
                    self._starting.popitem(last=False)
        if call.needs_input_lock:
            self._remember(call)
        return None

    # --- the start or the later refusal of an accepted call (NodeRuntime) ------------------------------------

    def call_started(self, call_id: str) -> None:
        """The accepted call really starts (the node's 'running' record is written): its cap slots stay taken and
        a capture shows its toast now."""
        with self._accepted_lock:
            entry = self._starting.pop(str(call_id), None)
        if entry is not None and entry[1] is not None:
            self._show_toast(entry[1])

    def release_call(self, call_id: str) -> None:
        """The call was refused after check() accepted it (an early cancel or stop, DESKTOP_DEGRADED, a WAL
        conflict) or ended without starting: its cap slots are given back and no toast is shown."""
        call_id = str(call_id)
        with self._accepted_lock:
            entry = self._starting.pop(call_id, None)
            self._accepted.pop(call_id, None)
            self._passed.pop(call_id, None)
            self._running.pop(call_id, None)
            self._watching.pop(call_id, None)
        if entry is not None:
            self._caps.release(entry[0])

    # --- the run-time foreground re-check --------------------------------------------------------------------

    def recheck_foreground(self, call: Any, *, final: bool = True) -> str | None:
        """The K-76 foreground rules again, right before an input-lock call runs (the executor calls this while
        it holds the desktop_input lock, through install(node)). check() runs when a call is accepted, and the
        calls of one reply are accepted before the first of them runs, so an earlier call (Alt+Tab, a click on
        the taskbar search box) can change the foreground after a later call was judged. Returns None to run, or:
        the state codes (kill switch, panic, off/read, the Windows lock) when they changed meanwhile;
        PROTECTED_WINDOW when the foreground is now a shell, terminal, IDE or JARVIS window and the call sends its
        input to the window in front (refuses_protected_start; the SELF_FOCUS_TOOLS are judged per key instead);
        APPROVAL_REQUIRED
        when the keystrokes now reach a launch surface or a messaging app the call's approval does not cover.
        final=False is a look while the foreground may still be settling: a PROTECTED_WINDOW or APPROVAL_REQUIRED
        there keeps the call remembered and writes nothing. The final look, and a non-final look that returns any
        other code (the caller stops at it), forget the call and write one audit line for a refusal (no
        arguments, no title); a passed call goes on to begin_input_run with its approval. No toast or alert."""
        call_id = str(getattr(call, "call_id", "") or "")
        with self._accepted_lock:
            pending = self._accepted.get(call_id)
        if pending is None:
            pending = self._pending_from(call)  # not accepted by this policy: judged with no approval
        code = None if pending.cls == _DE_ESCALATION else self._recheck(pending)
        # A state code (lock, panic, kill switch, mode) decides at once, even on a non-final look, because the
        # caller stops looking; only the foreground codes may still settle on a later look.
        if final or (code is not None and code not in _RECHECK_SETTLE_CODES):
            with self._accepted_lock:
                accepted = self._accepted.pop(call_id, None)
                if code is None and final and accepted is not None:
                    self._passed[call_id] = accepted
                    self._passed.move_to_end(call_id)
                    while len(self._passed) > _ACCEPTED_MAX:
                        self._passed.popitem(last=False)
            if code is not None:
                logger.warning("Desktop call %s stopped before it ran: %s", pending.tool, code)
                self._write_audit("desktop_recheck_refused", call_id=pending.call_id, tool=pending.tool, code=code)
        return code

    def _state_code(self, pending: PendingCall, effective_cls: str) -> str | None:
        """The state part of the K-76 order for a call about to send input: kill switch, panic, mode, lock."""
        settings = self._settings()
        state = self._state.current()
        actuation = pending.actuation or effective_cls in ACTUATION_CLASSES
        if actuation and not bool(settings.desktop_control):
            return DESKTOP_CONTROL_DISABLED
        if state.panic:
            return DESKTOP_PANIC
        if state.mode == "off":
            return DESKTOP_CONTROL_DISABLED
        if actuation and state.mode != "full":
            return DESKTOP_READ_ONLY
        if state.locked:
            return DESKTOP_LOCKED
        return None

    def _recheck(self, pending: PendingCall) -> str | None:
        effective_cls = self._effective_class(pending)
        code = self._state_code(pending, effective_cls)
        if code is not None:
            return code
        if refuses_protected_start(pending.tool, pending.args) and self._protected_foreground():
            return PROTECTED_WINDOW
        confirm, eff_risk = self._confirm_requirements(pending, effective_cls)
        if confirm and self._approval_verdict(pending, effective_cls, eff_risk) is not None:
            return APPROVAL_REQUIRED  # the approval (if any) does not cover what the keystrokes now reach
        return None

    def _remember(self, call: PendingCall) -> None:
        with self._accepted_lock:
            self._accepted[call.call_id] = call
            self._accepted.move_to_end(call.call_id)
            while len(self._accepted) > _ACCEPTED_MAX:
                self._accepted.popitem(last=False)

    # --- the run of an input-lock call: chunks, each after the foreground is verified again --------------------

    def foreground_snapshot(self) -> tuple[int, str]:
        """The foreground identity a run is pinned to: (window handle, protection class). The class is
        'protected', 'code_exec', 'messaging' or 'input', so a title that changes while typing (Notepad's '*')
        is no change, but a tab that turns into a messaging app or a new window is."""
        if self._protected_foreground():
            label = "protected"
        else:
            label = _foreground_surface(self._foreground("foreground_title"), self._foreground("foreground_class"),
                                        self._foreground("foreground_process")) or _INPUT
        return self._foreground_handle(), label

    def begin_input_run(self, call: Any) -> None:
        """The executor holds the input lock and the re-check passed: the call and the foreground judged for it
        are pinned for verify_input_run."""
        call_id = str(getattr(call, "call_id", "") or "")
        with self._accepted_lock:
            pending = self._passed.pop(call_id, None) or self._accepted.pop(call_id, None)
        if pending is None:
            pending = self._pending_from(call)
        snapshot = self.foreground_snapshot()
        with self._accepted_lock:
            self._running[call_id] = (pending, snapshot)
            self._running.move_to_end(call_id)
            while len(self._running) > _ACCEPTED_MAX:
                self._running.popitem(last=False)
            self._watching[call_id] = {"left": False, "unknown": 0}
            while len(self._watching) > _ACCEPTED_MAX:
                self._watching.pop(next(iter(self._watching)))

    def verify_input_run(self, call: Any) -> str | None:
        """Before each chunk of a running input-lock call: None to send it, else the run-time refusal code (the
        state codes, PROTECTED_WINDOW, or APPROVAL_REQUIRED for a launch surface or messaging app the approval
        does not cover, or for any other window than the one judged: another handle or protection class). A
        refusal writes one audit line (no arguments, no title); the run stays pinned until end_input_run, which
        files what was sent under the window judged for it."""
        call_id = str(getattr(call, "call_id", "") or "")
        with self._accepted_lock:
            entry = self._running.get(call_id)
        pending, baseline = entry if entry is not None else (self._pending_from(call), None)
        if pending.cls == _DE_ESCALATION:
            return None  # de-escalation always runs
        code = self._recheck(pending)
        if code is None and baseline is not None and self.foreground_snapshot() != baseline:
            code = APPROVAL_REQUIRED  # the keystrokes would now reach another window than the one judged
        if code is not None:
            logger.warning("Desktop call %s stopped while it ran: %s", pending.tool, code)
            self._write_audit("desktop_input_interrupted", call_id=pending.call_id, tool=pending.tool, code=code)
        return code

    @staticmethod
    def watches_input_run(call: Any) -> bool:
        """Whether a run-time guard runs while this input-lock call runs: every call that types or keys a
        sequence, not a single key, hotkey, click, move or scroll, and not an open_app that types nothing. The
        SELF_FOCUS_TOOLS among them are judged at key time (judges_input_keys); the others (type_text_tool,
        type_code_animation_tool) by the watchdog's looks (watch_input_run)."""
        tool = str(getattr(call, "tool", ""))
        if tool in _ATOMIC_INPUT_TOOLS:
            return False
        return sends_keys_to_foreground(tool, dict(getattr(call, "args", None) or {}))

    def watch_input_run(self, call: Any) -> str | None:
        """One look of the run-time watchdog while a typing call runs (type_text_tool, type_code_animation_tool;
        every _WATCH_INTERVAL_S): None to let it go on, else the run-time refusal code: the state codes;
        PROTECTED_WINDOW when a shell, terminal, IDE or JARVIS window has the focus; APPROVAL_REQUIRED when a
        launch surface or a messaging app the call's approval does not cover has it. The window the call started
        from (judged right before the run) is exempt only until the focus first leaves it (between chunks
        verify_input_run also compares the window identity). An unreadable foreground refuses only after
        _WATCH_UNKNOWN_TICKS looks in a row. A refusal writes one audit line (no arguments, no title). The
        SELF_FOCUS_TOOLS are judged per key instead (judge_input_key); this look is their fallback only when no
        gated input library could be loaded (then no key of theirs can be sent either)."""
        call_id = str(getattr(call, "call_id", "") or "")
        with self._accepted_lock:
            entry = self._running.get(call_id)
            watch = self._watching.setdefault(call_id, {"left": False, "unknown": 0})
        pending, baseline = entry if entry is not None else (self._pending_from(call), None)
        if pending.cls == _DE_ESCALATION:
            return None
        code = self._state_code(pending, self._effective_class(pending))
        if code is None:
            code = self._watch_foreground(pending, baseline, watch)
        if code is not None:
            logger.warning("Desktop call %s stopped while it ran: %s", pending.tool, code)
            self._write_audit("desktop_input_interrupted", call_id=pending.call_id, tool=pending.tool, code=code)
        return code

    def _watch_foreground(self, pending: PendingCall, baseline: tuple[int, str] | None,
                          watch: dict[str, Any]) -> str | None:  # pylint: disable=too-many-return-statements
        title, win_class, process = (self._foreground(name) for name in
                                     ("foreground_title", "foreground_class", "foreground_process"))
        protected = self._protected_foreground()
        surface = "" if protected else _foreground_surface(title, win_class, process)
        snapshot = (self._foreground_handle(), "protected" if protected else (surface or _INPUT))
        if baseline is not None and not watch["left"]:
            if snapshot == baseline:
                return None  # still the window the call started from: it was judged right before the run
            watch["left"] = True
        if protected:
            return PROTECTED_WINDOW
        if not surface:
            watch["unknown"] = 0
            return None
        if not (title.strip() or win_class.strip() or process.strip()):
            watch["unknown"] += 1
            if watch["unknown"] < _WATCH_UNKNOWN_TICKS:
                return None  # nothing has the focus for a moment (a window switch): looked at again
        else:
            watch["unknown"] = 0
        effective = self._effective_class(pending)
        if effective != _CODE_EXEC:
            effective = surface  # the stricter class: the keystrokes now reach a launch surface or messaging app
        confirm, eff_risk = self._confirm_requirements(pending, effective)
        if confirm and self._approval_verdict(pending, effective, eff_risk) is not None:
            return APPROVAL_REQUIRED
        return None

    @staticmethod
    def judges_input_keys(call: Any) -> bool:
        """Whether this input-lock call is judged at key time (judge_input_key, through the keystroke gate)
        instead of by the watchdog's looks: the SELF_FOCUS_TOOLS that type something."""
        tool = str(getattr(call, "tool", ""))
        return tool in SELF_FOCUS_TOOLS and sends_keys_to_foreground(tool, dict(getattr(call, "args", None) or {}))

    def judge_input_key(self, call: Any) -> str | None:
        """Key time (K-76 run time): one key of a running SELF_FOCUS_TOOLS call is about to be sent to the window
        that has the focus now. None sends it, else the run-time refusal code: the state codes; PROTECTED_WINDOW
        when that window is a shell, terminal, IDE or JARVIS window (also the window the call started from: the
        focus never moved); APPROVAL_REQUIRED when it is a launch surface or a messaging app the call's approval
        does not cover, or a browser while the call's text holds a javascript: URL (code_exec). Nothing having
        the focus is looked at again briefly (a window switch) before it is judged as unknown (code_exec). Only
        keys are judged, so where the focus goes after the tool's last key (Notepad closing back to Explorer or
        the desktop) refuses nothing. The first refusal writes one audit line (no arguments, no title)."""
        call_id = str(getattr(call, "call_id", "") or "")
        with self._accepted_lock:
            entry = self._running.get(call_id)
            watch = self._watching.setdefault(call_id, {"left": False, "unknown": 0})
        pending = entry[0] if entry is not None else self._pending_from(call)
        if pending.cls == _DE_ESCALATION:
            return None
        code = self._state_code(pending, self._effective_class(pending))
        if code is None:
            code = self._key_foreground(pending)
        if code is not None and not watch.get("refused"):
            watch["refused"] = True
            logger.warning("Desktop call %s stopped while it ran: %s", pending.tool, code)
            self._write_audit("desktop_input_interrupted", call_id=pending.call_id, tool=pending.tool, code=code)
        return code

    def _key_foreground(self, pending: PendingCall) -> str | None:
        title = win_class = process = ""
        for look in range(_KEY_SETTLE_LOOKS):
            title, win_class, process = (self._foreground(name) for name in
                                         ("foreground_title", "foreground_class", "foreground_process"))
            if title.strip() or win_class.strip() or process.strip():
                break
            if look < _KEY_SETTLE_LOOKS - 1:
                time.sleep(_KEY_SETTLE_S)  # nothing has the focus for a moment: the key waits for the switch
        if self._protected_foreground():
            return PROTECTED_WINDOW
        effective = self._effective_class(pending)
        if effective != _CODE_EXEC:
            if is_browser_foreground(title, win_class, process) and _args_type_javascript_url(pending.args):
                effective = _CODE_EXEC  # its text would reach a browser, whose address bar runs javascript:
            else:
                effective = _foreground_surface(title, win_class, process) or effective
        confirm, eff_risk = self._confirm_requirements(pending, effective)
        if confirm and self._approval_verdict(pending, effective, eff_risk) is not None:
            return APPROVAL_REQUIRED
        return None

    def end_input_run(self, call: Any, sent: Iterable[Mapping[str, Any]] = ()) -> None:
        """The run ended (completed, stopped, cancelled or failed): the run is forgotten and the text its chunks
        sent (typed text, a printable key) is added to the recent typed text of the window judged for the run (the
        window it was pinned to, not the one in front when it ended)."""
        call_id = str(getattr(call, "call_id", "") or "")
        with self._accepted_lock:
            entry = self._running.pop(call_id, None)
            self._watching.pop(call_id, None)
        tool = str(getattr(call, "tool", ""))
        if tool not in ("type_text_tool", "type_code_animation_tool", "press_key_tool"):
            return
        typed = ""
        for chunk in sent:
            if tool == "press_key_tool":
                key = chunk.get("key")
                typed += key if isinstance(key, str) and len(key) == 1 and key.isprintable() else ""
                continue
            text = chunk.get("text", chunk.get("code"))
            typed += text if isinstance(text, str) else ""
        if typed:
            handle = entry[1][0] if entry is not None else 0
            self.note_typed(typed, handle=handle or None)

    @staticmethod
    def input_chunks(call: Any) -> list[dict[str, Any]]:
        """The argument sets one input-lock call is sent as. Typing tools whose text is typed key by key are cut
        into pieces of INPUT_CHUNK_CHARS that type exactly what the one call would have typed; a paste (one
        Ctrl+V), a key, a hotkey or a click is one piece. A text that fits one piece keeps its arguments. The
        animation's part after its first _CODE_ANIMATION_HEAD characters is one paste through type_text_tool (a
        ToolChunk; its fallback types it in pieces), exactly as the tool itself pastes it."""
        args = dict(getattr(call, "args", None) or {})
        tool = str(getattr(call, "tool", ""))
        if tool == "type_text_tool":
            text = args.get("text")
            if not isinstance(text, str) or not text:
                return [args]
            typed = _unescape_typed(text)
            if not args.get("force_typing") and (len(typed) > _PASTE_OVER_CHARS
                                                 or any(ord(ch) > 127 for ch in typed)):
                return [args]  # pasted at once
            pieces = split_typed(typed)
            return [args] if len(pieces) <= 1 else [dict(args, text=piece) for piece in pieces]
        if tool == "type_code_animation_tool":
            code = args.get("code")
            if not isinstance(code, str) or not code:
                return [args]
            code = code[:_CODE_ANIMATION_MAX]
            head = split_typed(_unescape_typed(code[:_CODE_ANIMATION_HEAD]))
            tail_raw = code[_CODE_ANIMATION_HEAD:]
            tail_typed = _unescape_typed(tail_raw)
            tail = [dict(args, code=piece) for piece in split_typed(tail_typed)]
            if len(head) + len(tail) <= 1:
                return [args]
            chunks: list[dict[str, Any]] = [dict(args, code=piece) for piece in head]
            if tail_typed and (len(tail_typed) > _PASTE_OVER_CHARS or any(ord(ch) > 127 for ch in tail_typed)):
                # the tool pastes this part in one Ctrl+V (type_text with force_typing False): one action
                chunks.append(ToolChunk("type_text_tool", {"text": tail_raw}, fallback=tail))
            else:
                chunks.extend(tail)
            return chunks
        return [args]

    # --- the recent typed text per window (javascript: across calls) -----------------------------------------

    def _foreground_handle(self) -> int:
        getter = getattr(self._presence, "foreground_handle", None)
        if getter is None:
            return 0
        try:
            value = getter()
        except Exception:  # pylint: disable=broad-exception-caught
            return 0
        return value if isinstance(value, int) and not isinstance(value, bool) else 0

    def _typed_key(self) -> tuple[Any, ...]:
        handle = self._foreground_handle()
        if handle:
            return ("hwnd", handle)
        return ("window", self._foreground("foreground_process").strip().lower(),
                self._foreground("foreground_class").strip().lower())

    def note_typed(self, text: str, *, handle: int | None = None) -> None:
        """Keeps the last characters typed into a window (squashed, never logged), so a javascript: URL typed over
        several calls is still seen. handle names the window the text went to (the run's pinned window); without
        one it is the foreground window now."""
        squashed = _squash_typed(text)
        if not squashed:
            return
        key = ("hwnd", handle) if handle else self._typed_key()
        now = time.monotonic()
        with self._accepted_lock:
            _stamp, tail = self._typed.get(key, (now, ""))
            if now - _stamp > _TYPED_TTL_S:
                tail = ""
            self._typed[key] = (now, (tail + squashed)[-_TYPED_TAIL:])
            self._typed.move_to_end(key)
            while len(self._typed) > _TYPED_WINDOWS_MAX:
                self._typed.popitem(last=False)

    def _recent_typed(self) -> str:
        key = self._typed_key()
        with self._accepted_lock:
            entry = self._typed.get(key)
        if entry is None or time.monotonic() - entry[0] > _TYPED_TTL_S:
            return ""
        return entry[1]

    @staticmethod
    def _pending_from(call: Any) -> PendingCall:
        spec = call.spec
        args = getattr(call, "args", None) or {}
        return PendingCall(call_id=str(getattr(call, "call_id", "") or ""), tool=str(getattr(call, "tool", "")),
                           cls=str(getattr(call, "cls", "") or spec.cls), origin=str(getattr(call, "origin", "")),
                           room=str(getattr(call, "room", "")), peer=str(getattr(call, "peer", "")), args=args,
                           args_sha256="", approval=None, risk=spec.call_risk(args), spec=spec)

    # --- class / dynamic risk --------------------------------------------------------------------------------

    def _effective_class(self, call: PendingCall) -> str:  # pylint: disable=too-many-return-statements
        """The class after the K-76 launch-surface and open-target escalation (dynamic risk)."""
        tool, args = call.tool, call.args
        if tool == "press_hotkey_tool" and is_launch_hotkey(args.get("keys")):
            return _CODE_EXEC
        if tool == "press_key_tool" and is_launch_key(args.get("key")):
            return _CODE_EXEC
        if tool in ("type_text_tool", "type_code_animation_tool"):
            typed = args.get("text") if tool == "type_text_tool" else args.get("code")
            if _types_javascript_url(typed) or (isinstance(typed, str) and _types_javascript_url(
                    self._recent_typed() + typed)):
                # a javascript: URL typed into an address bar runs in the signed-in page, also when it was
                # typed over several calls into the same window
                return _CODE_EXEC
        if tool == "press_key_tool":
            key = args.get("key")
            if isinstance(key, str) and len(key) == 1 and key.isprintable() and _types_javascript_url(
                    self._recent_typed() + key):
                return _CODE_EXEC  # the same, one key at a time
        if tool in ("press_key_tool", "press_hotkey_tool") and is_browser_devtools_key(
                args.get("keys") if tool == "press_hotkey_tool" else args.get("key")) and is_browser_foreground(
                    self._foreground("foreground_title"), self._foreground("foreground_class"),
                    self._foreground("foreground_process")):
            return _CODE_EXEC  # the browser menu / context menu leads to DevTools (K-76)
        if tool in KEYSTROKE_TOOLS:
            keys = args.get("keys") if tool == "press_hotkey_tool" else args.get("key")
            if tool in ("type_text_tool", "type_code_animation_tool") or not _keys_all_safe(keys):
                # an unreadable foreground (no title, class or process) comes back as code_exec
                surface = _foreground_surface(self._foreground("foreground_title"),
                                              self._foreground("foreground_class"),
                                              self._foreground("foreground_process"))
                if surface:
                    return surface
        if tool == "open_app" and not _is_mapped_app(args.get("full_command")):
            return _CODE_EXEC
        arg = EXEC_ESCALATE_ARG.get(tool)
        if arg is not None and opens_as_code(args.get(arg)):
            return _CODE_EXEC  # only a folder or a plain document/media file opens without approval (K-76)
        if tool == "pc.fetch" and bool(args.get("open")) and opens_as_code(
                args.get("relpath"), allow_directory=False, safe_exts=K73_AUTO_OPEN_EXTS):
            return _CODE_EXEC  # only a K-73 auto-open type may open without approval (K-73, K-76)
        if tool == "write_custom_code" and bool(args.get("auto_run", True)) and _is_script_filename(
                args.get("filename")):
            return _CODE_EXEC
        if tool == "run_cmd_command" and not self._cmd_is_readonly(args.get("command")):
            return _CODE_EXEC  # already code_exec; keeps it confirm-class for a command outside the allow-list
        return call.cls

    @staticmethod
    def _cmd_is_readonly(command: Any) -> bool:
        if not isinstance(command, str) or not command.strip():
            return False
        if any(ch in command for ch in _CMD_METACHARS):
            return False
        return command.strip().split()[0].lower() in _CMD_READONLY

    def _confirm_requirements(self, call: PendingCall, effective_cls: str) -> tuple[bool, str]:
        risk = _RISK_RANK.get(call.risk, 2)
        if call.tool in K45_HIGH:
            risk = max(risk, 2)
        elif call.tool in K45_MEDIUM:
            risk = max(risk, 1)
        if effective_cls == _CODE_EXEC:
            risk = max(risk, 2)
        elif effective_cls in (_MESSAGING, _EXPOSURE, _AVAILABILITY):
            risk = max(risk, 1)
        eff_risk = ("low", "medium", "high")[risk]
        confirm = (effective_cls in _CONFIRM_CLASSES or call.risk in ("medium", "high")
                   or call.tool in K45_HIGH)
        return confirm, eff_risk

    def _approval_problem(self, call: PendingCall, effective_cls: str, eff_risk: str) -> str | None:
        """The ConfirmFlow approval check (K-76) with its local toast on a verify failure. The critical
        APPROVAL_INVALID alert is raised once by NodeRuntime._refuse; here only the toast."""
        code = self._approval_verdict(call, effective_cls, eff_risk)
        if code == APPROVAL_INVALID:
            self._show_toast("Sir, laptop par ek ijazat theek nahi thi; woh kaam nahi chala.")
        return code

    @staticmethod
    def _approval_verdict(  # pylint: disable=too-many-return-statements
            call: PendingCall, effective_cls: str, eff_risk: str) -> str | None:
        """None when the approval covers the call. APPROVAL_REQUIRED without one, and for a valid approval asked
        at a lower risk than the call's effective risk now (the node raised it dynamically: code_exec is high, so
        only a 'high' dashboard Allow covers it; a spoken yes covers medium only): the agent asks again through
        ConfirmFlow at the higher risk. APPROVAL_INVALID (the critical K-74 alert and a toast) only when the
        approval does not verify: an args_sha256 mismatch, or a malformed approval (no confirm_id, a risk that is
        missing or unknown, a channel that can never approve such as the bridge, a voice approval not at medium
        risk or not from a voice-ID-verified turn). The approval sits inside the agent-signed, encrypted exec
        body (a bad signature never reaches the policy), so only the agent can have put it there. No side
        effects."""
        approval = call.approval
        if not isinstance(approval, Mapping):
            return APPROVAL_REQUIRED
        if not dp.digest_equal(approval.get("args_sha256"), call.args_sha256):
            return APPROVAL_INVALID
        confirm_id = approval.get("confirm_id")
        if not isinstance(confirm_id, str) or not confirm_id.strip():
            return APPROVAL_INVALID  # no ConfirmFlow item behind it
        approved_risk = approval.get("risk")
        if approved_risk not in _RISK_RANK:
            return APPROVAL_INVALID  # missing or unknown risk: malformed
        channel = approval.get("channel")
        if channel not in ("ui", "voice"):
            return APPROVAL_INVALID  # bridge, missing or unknown channel: the bridge never approves
        if channel == "voice":
            # A spoken yes is only ever asked at medium risk, and for a laptop action only on a voice-ID-verified
            # turn (D20): the agent marks that turn with voice_id_verified true inside the signed body.
            if approved_risk != "medium" or approval.get("voice_id_verified") is not True:
                return APPROVAL_INVALID
        if _RISK_RANK[approved_risk] < _RISK_RANK.get(eff_risk, 2):
            # a valid approval asked at a lower risk than the call has now (e.g. a medium WhatsApp message whose
            # keystrokes would reach the Run dialog): it does not cover the call, so the agent asks again
            return APPROVAL_REQUIRED
        if channel == "voice" and (effective_cls == _CODE_EXEC or eff_risk != "medium"):
            return APPROVAL_REQUIRED  # a spoken yes never covers code_exec: the dashboard Allow is needed
        return None

    # --- capture / windows -----------------------------------------------------------------------------------

    def _capture_problem(self, call: PendingCall, settings: _Settings) -> str | None:
        """Captures (K-76): refused on a sensitive foreground; presence within JARVIS_CAPTURE_PRESENCE_S, or a
        session opt-in for a screen capture only. The webcam always needs presence (the opt-in never counts)."""
        if self._sensitive_foreground():
            return SENSITIVE_SCREEN
        present = self._presence.present_within(settings.capture_presence_s)
        if call.tool in WEBCAM_TOOLS or "webcam" in call.tool or "camera" in call.tool:
            return None if present else PRESENCE_REQUIRED
        if not (present or self.capture_opt_in):
            return PRESENCE_REQUIRED
        return None

    def _sensitive_foreground(self) -> bool:
        if self._foreground("foreground_process").strip().lower() in _PASSWORD_MANAGER_PROCESSES:
            return True
        low = self._foreground("foreground_title").lower()
        return bool(low) and any(marker in low for marker in _SENSITIVE_TITLE_MARKERS)

    def _protected_foreground(self) -> bool:
        process = self._foreground("foreground_process").strip().lower()
        if is_protected_title(self._foreground("foreground_title"), process):
            return True
        if process in _PROTECTED_PROCESSES:
            return True
        win_class = self._foreground("foreground_class").lower()
        return bool(win_class) and win_class in _PROTECTED_WINDOW_CLASSES

    def _foreground(self, name: str) -> str:
        """One foreground signal (title, class or process) from presence; '' when the source lacks it or fails."""
        getter = getattr(self._presence, name, None)
        if getter is None:
            return ""
        try:
            value = getter()
        except Exception:  # pylint: disable=broad-exception-caught
            return ""
        return value if isinstance(value, str) else ""

    # --- origin / active_job ---------------------------------------------------------------------------------

    def _active_job_allows(self, call: PendingCall) -> bool:
        """The bridge's tighten-only active_job control (K-76): actuation only from that agent job's peer.

        The bridge sends {kind: 'active_job', job_id} (C-116). Calls from another LiveKit job peer are refused;
        the always-on svc peer (job_id 'svc', K-77) is not a LiveKit job and keeps its scheduler/job actuation
        under the origin rules. A peer-named form ({peer} / {peer_id}) is honoured as well."""
        job = self._active_job()
        if not isinstance(job, Mapping):
            return True
        job_id = job.get("job_id")
        if isinstance(job_id, str) and job_id.strip():
            if call.job_id == "svc":
                return True
            return call.job_id == job_id.strip()
        wanted = job.get("peer") or job.get("agent") or job.get("peer_id")
        if not isinstance(wanted, str) or not wanted:
            return True
        return call.peer in (wanted, f"agent:{wanted}")

    # --- paths -----------------------------------------------------------------------------------------------

    def _path_problem(self, call: PendingCall) -> str | None:
        tool, args = call.tool, call.args
        basename_arg = OUTPUTS_BASENAME_ARG.get(tool)
        if basename_arg is not None:
            value = args.get(basename_arg)
            if not is_basename(value):
                return PATH_NOT_ALLOWED
            full = self._outputs_dir / str(value).strip()
            if is_sensitive_path(full, data_root=self._data_root, outputs_dir=self._outputs_dir,
                                 jarvis_appdata=self._appdata):
                return PATH_NOT_ALLOWED
            return None
        path_arg = PATH_ARG.get(tool)
        if path_arg is not None:
            resolved = resolve_in_roots(args.get(path_arg), self._roots(), data_root=self._data_root,
                                        outputs_dir=self._outputs_dir, jarvis_appdata=self._appdata)
            if resolved is None:
                return PATH_NOT_ALLOWED
        return None

    def _roots(self) -> list[str]:
        try:
            roots = [str(root) for root in self._open_roots() if str(root).strip()]
        except Exception:  # pylint: disable=broad-exception-caught
            roots = []
        return roots or [str(self._outputs_dir)]

    # --- caps ------------------------------------------------------------------------------------------------

    def _cap_problem(self, call: PendingCall, effective_cls: str, confirm: bool) -> tuple[str | None, Any]:
        """The K-76 count caps: (None, token) with a slot reserved in every bucket the call belongs to (token None
        when it belongs to none), or (DESKTOP_RATE_LIMITED, None) with none taken when one is full. The
        DESKTOP_RATE_LIMITED alert comes from NodeRuntime._refuse; here only the local toast."""
        buckets: list[str] = []
        if effective_cls == _MESSAGING:
            buckets.append("messaging")
        if effective_cls in (_EXPOSURE, _CAPTURE):
            buckets.append("uploads")
        if confirm:
            buckets.append("approvals")
        if call.cls == _INPUT or effective_cls == _INPUT:
            buckets.append("input")
        if not buckets:
            return None, None
        token = self._caps.reserve(buckets)
        if token is not None:
            return None, token
        self._show_toast("Sir, laptop par kaam ki raftaar had se barh gayi; thori der rukein.")
        return DESKTOP_RATE_LIMITED, None

    def note_bytes(self, bucket: str, size: int, *, call_id: str | None = None,
                   tool: str | None = None) -> str | None:
        """The byte caps (uploads 200 MB/h, deliveries 5 GB/day) for C-122, which calls this with the size of
        each upload or delivery before it sends. A trip returns DESKTOP_RATE_LIMITED, shows the toast, raises
        the warning alert and writes an audit line (no arguments, no path); else None."""
        code = self._caps.note_bytes(bucket, size)
        if code is None:
            return None
        self._raise_alert(DESKTOP_RATE_LIMITED, call_id=call_id, tool=tool)
        self._show_toast("Sir, laptop se data bhejne ki had poori ho gayi; thori der rukein.")
        self._write_audit("desktop_cap_tripped", call_id=call_id, tool=tool, code=code, kind=bucket)
        return code

    # --- side effects ----------------------------------------------------------------------------------------

    def _raise_alert(self, code: str, *, call_id: str | None = None, tool: str | None = None) -> None:
        if self._alert is None:
            return
        try:
            self._alert(code, call_id=call_id, tool=tool)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Alert callback raised")

    def _write_audit(self, event: str, **fields: Any) -> None:
        if self._audit is None:
            return
        try:
            self._audit(event, **fields)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Audit callback raised")

    def _show_toast(self, text: str) -> None:
        if self._toast is None:
            return
        try:
            self._toast(text)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Toast callback raised")


# --- installation ----------------------------------------------------------------------------------------------

def _toast_windows(text: str) -> None:
    """A best-effort Windows toast; falls back to a beep and the node log. Never raises."""
    try:  # pragma: no cover - requires a Windows session
        import ctypes  # pylint: disable=import-outside-toplevel

        ctypes.windll.user32.MessageBeep(0x40)
    except Exception:  # pylint: disable=broad-exception-caught
        pass
    logger.info("Desktop toast: %s", text)


def install(node: Any) -> None:
    """NodeRuntime plugin hook (C-120 install_plugins): replace BaselinePolicy and MemoryState with the real
    policy, persisted state, presence, the tray and the panic hotkey. A failure leaves nothing installed
    (install_plugins restores the snapshot), so the node never runs full-mode behind a half-installed policy."""
    state = PersistentState(Path(node.run_dir) / STATE_FILE, default_mode="full")
    node.set_state_source(state)

    presence: Presence
    monitor: PresenceMonitor | None = None
    try:
        monitor = PresenceMonitor()
        monitor.add_lock_listener(state.set_locked)
        presence = monitor
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("PresenceMonitor unavailable; presence reads as absent")
        presence = ManualPresence()

    appdata = None
    home = getattr(node, "node_home", None)
    try:
        from src.core.desktop import settings as node_settings  # pylint: disable=import-outside-toplevel

        appdata = node_settings.node_home().parent  # %LOCALAPPDATA%\JARVIS
    except Exception:  # pylint: disable=broad-exception-caught
        appdata = Path(home).parent if home else None

    outputs_dir = _outputs_dir(node)
    caps = RateLimiter()
    policy = DesktopPolicy(
        state=state, settings=lambda: node.settings, presence=presence,
        open_roots=lambda: _open_roots(node), data_root=Path(node.data_root), outputs_dir=outputs_dir,
        jarvis_appdata=appdata, active_job=lambda: getattr(node, "active_job", None), rate_limiter=caps,
        alert=node.raise_alert, toast=_toast_windows,
        audit=getattr(getattr(node, "audit", None), "record", None))
    node.set_policy(policy)
    executor = getattr(node, "executor", None)
    if executor is not None:  # the run-time foreground re-check of every input-lock call (K-76)
        provider = getattr(node, "provider", None)
        guard_input_runs(executor, lambda: getattr(node, "policy", None),
                         describe=getattr(provider, "describe", None))

    # Panic hotkey: a genuine Ctrl+Alt+Shift+F12 -> off + cancel all, latched. A lock or unlock forgets the held
    # keys: the secure desktop swallows their key-ups, which must never leave a half-pressed combo armed.
    panic = _make_panic(node, state)
    if monitor is not None:
        monitor.add_key_listener(panic.on_key)
        monitor.add_lock_listener(lambda _locked: panic.reset())
        monitor.start()

    from src.core.desktop.tray import Tray  # pylint: disable=import-outside-toplevel

    tray = Tray(state).start()

    node.add_shutdown_hook(lambda: monitor.stop() if monitor is not None else None)
    node.add_shutdown_hook(tray.stop)
    logger.info("Desktop policy installed (mode=%s, tray=%s)", state.current().mode, Tray.available())


_RECHECK_MESSAGES = {
    PROTECTED_WINDOW: "Sir, saamne wali window mehfooz hai (terminal, editor ya JARVIS); laptop par typing nahi ki.",
    APPROVAL_REQUIRED: "Sir, saamne wali window badal gayi thi; is ke liye ijazat chahiye, is liye kaam nahi chala.",
}
_INTERRUPT_MESSAGES = {
    PROTECTED_WINDOW: "Sir, typing ke dauran saamne mehfooz window (terminal, editor ya JARVIS) aa gayi; baaqi typing "
                      "rok di gayi.",
    APPROVAL_REQUIRED: "Sir, typing ke dauran saamne wali window badal gayi, is liye baaqi typing rok di gayi.",
}


def _is_error_result(result: Any) -> bool:
    return isinstance(result, Mapping) and (result.get("status") not in (None, "success", "ok")
                                            or bool(result.get("error_code")))


def _whole_result(result: Any, typed: int) -> Any:
    """The last chunk's K-12 result, speaking for the whole text: its '(N characters)' count is the total."""
    if not isinstance(result, Mapping) or not isinstance(result.get("message"), str):
        return result
    return {**result, "message": _CHARS_RE.sub(f"({typed} characters)", result["message"], count=1)}


def guard_input_runs(executor: Any, policy_of: Callable[[], Any], *,
                     describe: Callable[[str], Awaitable[Any]] | None = None,
                     gate: keygate.KeystrokeGate | None = None) -> None:
    """Guards every input-lock call of the executor (K-16, K-76). The call holds the desktop_input lock for its
    whole run (two input calls never overlap). Right before it runs, while the lock is held, the policy's
    recheck_foreground judges the foreground again; a refused call ends NOT_STARTED with the code (effect none)
    and the lock is released. A call that passes is pinned to that foreground (begin_input_run) and its tool is
    sent in chunks (input_chunks; a ToolChunk goes through the tool describe() names, else its fallback chunks);
    before each chunk verify_input_run checks the state, the K-76 foreground rules and the foreground identity
    again. While a chunk of a typing call the policy watches (watches_input_run) runs, a watchdog task calls
    watch_input_run every _WATCH_INTERVAL_S; on a refusal it blocks the call's keys (gate) and cancels the chunk's
    task. A call the policy judges per key (judges_input_keys: the tools that focus their own window) runs with
    judge_input_key on the gate: each PyAutoGUI or pynput key it sends is judged right before it reaches the OS;
    a refused key blocks the call and cancels its task, and a call none of whose keys was refused completes,
    wherever the focus went after its last key. On any refusal nothing more is sent: the call ends 'failed' with
    the code (effect 'possible' once a chunk was sent), also when the tool swallowed the cancel or caught the
    refused key. A chunk that returns an error
    result ends the run with it. A failing re-check, verify or watch fails closed (DESKTOP_REJECTED). policy_of
    returns the node's current policy: a policy without these steps (the C-120 BaselinePolicy after a failed
    install) adds nothing. Raises when the executor has no input-lock step, so install() fails and leaves nothing
    half-wired. Guarding twice is a no-op."""
    # pylint: disable=protected-access,too-many-statements
    take = getattr(executor, "_take_input_lock", None)
    if getattr(take, "_jarvis_rechecks", False):
        return
    if not callable(take):
        raise RuntimeError("executor has no input-lock step to guard")
    from src.core.desktop import executor as executor_mod  # pylint: disable=import-outside-toplevel

    gate = gate if gate is not None else keygate.GATE
    gate.install_loaded()

    async def take_and_recheck(handle: Any) -> tuple[bool, Any]:
        held, outcome = await take(handle)
        if not held or outcome is not None:
            return held, outcome
        code: str | None = None
        try:
            recheck = getattr(policy_of(), "recheck_foreground", None)
            for attempt in range(_RECHECK_ATTEMPTS):
                final = attempt == _RECHECK_ATTEMPTS - 1
                code = recheck(handle.call, final=final) if recheck is not None else None
                if code not in _RECHECK_SETTLE_CODES or final:
                    break
                # a window that was just opened or closed may not have the focus yet: the last look decides
                await asyncio.sleep(_RECHECK_SETTLE_S)
        except asyncio.CancelledError:
            executor._release_later(handle)
            raise
        except Exception:  # pylint: disable=broad-exception-caught  # a broken re-check fails closed
            logger.exception("Desktop foreground re-check failed for %s", handle.call.tool)
            code = DESKTOP_REJECTED
        if code is None:
            begin = getattr(policy_of(), "begin_input_run", None)
            if begin is not None:
                try:
                    begin(handle.call)
                except Exception:  # pylint: disable=broad-exception-caught  # verify then fails closed
                    logger.exception("Desktop input run of %s could not be pinned", handle.call.tool)
            return True, None
        executor._release_later(handle)
        result = executor_mod.k12_error(handle.call.tool, code, _RECHECK_MESSAGES.get(code))
        return False, executor._outcome(handle, executor_mod.NOT_STARTED, result, executor_mod.EFFECT_NONE, code)

    def interrupted(code: str, *, sent: bool) -> Exception:
        return executor_mod.InputInterrupted(code, _INTERRUPT_MESSAGES.get(code) or _RECHECK_MESSAGES.get(code),
                                             sent=sent)

    def outer_cancelled() -> bool:
        """The call's own task was cancelled (a cancel, a stop, its timeout), not just the chunk by the watchdog."""
        cancelling = getattr(asyncio.current_task(), "cancelling", None)
        return bool(cancelling()) if callable(cancelling) else False

    async def run_watched(call: Any, invoke: Callable[[Mapping[str, Any]], Awaitable[Any]],
                          chunk: Mapping[str, Any], watch: Callable[[Any], str | None]) -> Any:
        gate.install_loaded()
        task = asyncio.ensure_future(invoke(chunk))  # a copy of this context: the chunk's work is the call's
        refused: list[str] = []

        async def watchdog() -> None:
            while not task.done():
                await asyncio.sleep(_WATCH_INTERVAL_S)
                if task.done():
                    return
                try:
                    code = watch(call)
                except Exception:  # pylint: disable=broad-exception-caught  # a broken watch fails closed
                    logger.exception("Desktop foreground watch failed for %s", call.tool)
                    code = DESKTOP_REJECTED
                if code is not None:
                    refused.append(code)
                    gate.block(call.call_id)  # no further PyAutoGUI key or click of this call reaches the OS
                    task.cancel()  # run_cancellable sets the tool's cancel_event; its awaits stop at once
                    return

        dog = asyncio.ensure_future(watchdog())
        try:
            try:
                result = await task
            except asyncio.CancelledError:
                if refused and not outer_cancelled():
                    raise interrupted(refused[0], sent=True) from None
                raise
            except Exception:  # e.g. keygate.InputBlocked once the call is blocked
                if refused:
                    raise interrupted(refused[0], sent=True) from None
                raise
        finally:
            dog.cancel()
        if refused:
            raise interrupted(refused[0], sent=True)  # the tool swallowed the cancel: refused all the same
        return result

    async def run_judged(call: Any, invoke: Callable[[Mapping[str, Any]], Awaitable[Any]],
                         chunk: Mapping[str, Any], judge: Callable[[Any], str | None],
                         watch: Callable[[Any], str | None] | None) -> Any:
        """A SELF_FOCUS_TOOLS call: each of its keys is judged when it is sent (the gate asks judge), so the
        call fails only when a key was really refused; a focus change after its last key fails nothing."""
        call_id = str(call.call_id)
        if not await asyncio.to_thread(gate.load):
            # no gated input library here, so no key can be judged (and the tool can send none either): the
            # watchdog's looks are the fallback
            if watch is None:
                return await invoke(chunk)
            return await run_watched(call, invoke, chunk, watch)

        def judge_key() -> str | None:
            try:
                return judge(call)
            except Exception:  # pylint: disable=broad-exception-caught  # a broken judge fails closed
                logger.exception("Desktop key judge failed for %s", call.tool)
                return DESKTOP_REJECTED

        gate.judge(call_id, judge_key)
        task = asyncio.ensure_future(invoke(chunk))  # a copy of this context: the chunk's work is the call's
        completed = False

        async def watchdog() -> None:
            while not task.done():
                await asyncio.sleep(_WATCH_INTERVAL_S)
                if task.done():
                    return
                gate.install_loaded()  # a gated library the tool imported meanwhile is judged from now on
                if gate.tripped(call_id) is not None:
                    task.cancel()  # a key was refused: the tool stops at its next await (its keys stay refused)
                    return

        dog = asyncio.ensure_future(watchdog())
        try:
            try:
                result = await task
            except asyncio.CancelledError:
                code = gate.tripped(call_id)
                if code is not None and not outer_cancelled():
                    raise interrupted(code, sent=True) from None
                raise
            except Exception:  # e.g. keygate.InputBlocked from the refused key
                code = gate.tripped(call_id)
                if code is not None:
                    raise interrupted(code, sent=True) from None
                raise
            code = gate.tripped(call_id)
            if code is not None:
                raise interrupted(code, sent=True)  # the tool caught the refused key: refused all the same
            completed = True
            return result
        finally:
            dog.cancel()
            if completed:
                gate.forget(call_id)
            else:
                gate.block(call_id)  # thread work a stopped call left behind never sends another key

    async def plan_of(call: Any, chunks: list[Mapping[str, Any]]) -> list[tuple[Any, Mapping[str, Any], bool]]:
        """(invoke, arguments, own tool?) per chunk; a ToolChunk is sent through its tool when describe() has it,
        else as its fallback chunks of the call's own tool."""
        plan: list[tuple[Any, Mapping[str, Any], bool]] = []
        for chunk in chunks:
            if isinstance(chunk, ToolChunk) and chunk.tool != call.tool:
                spec = None
                if describe is not None:
                    try:
                        spec = await describe(chunk.tool)
                    except Exception:  # pylint: disable=broad-exception-caught  # the fallback types it instead
                        logger.exception("Desktop tool %s could not be described", chunk.tool)
                if spec is not None:
                    plan.append((spec.invoke, dict(chunk), False))
                else:
                    plan.extend((call.spec.invoke, piece, True) for piece in chunk.fallback)
                continue
            plan.append((call.spec.invoke, chunk, True))
        return plan

    async def run_in_chunks(handle: Any) -> Any:  # pylint: disable=too-many-branches,too-many-locals
        call = handle.call
        policy = policy_of()
        chunks_of = getattr(policy, "input_chunks", None)
        verify = getattr(policy, "verify_input_run", None)
        if chunks_of is None or verify is None:
            return await call.spec.invoke(call.args)
        watches = getattr(policy, "watches_input_run", None)
        watch = getattr(policy, "watch_input_run", None)
        judges = getattr(policy, "judges_input_keys", None)
        judge = getattr(policy, "judge_input_key", None)
        sent: list[Mapping[str, Any]] = []  # every chunk handed to a tool (also one cut short by a cancel)
        result: Any = None
        own_result: Any = None
        try:
            try:
                chunks = list(chunks_of(call)) or [dict(call.args)]
                watched = watches is not None and watch is not None and bool(watches(call))
                judged = judges is not None and judge is not None and bool(judges(call))
            except Exception:  # pylint: disable=broad-exception-caught  # a broken planner fails closed
                logger.exception("Desktop input chunks of %s could not be planned", call.tool)
                raise executor_mod.InputInterrupted(DESKTOP_REJECTED, sent=False) from None
            plan = await plan_of(call, chunks)
            for invoke, chunk, own in plan:
                try:
                    code = verify(call)
                except Exception:  # pylint: disable=broad-exception-caught  # a broken verify fails closed
                    logger.exception("Desktop foreground verify failed for %s", call.tool)
                    code = DESKTOP_REJECTED
                if code is not None:
                    raise interrupted(code, sent=bool(sent))
                sent.append(chunk)
                if judged and judge is not None:
                    result = await run_judged(call, invoke, chunk, judge, watch if watched else None)
                elif watched and watch is not None:
                    result = await run_watched(call, invoke, chunk, watch)
                else:
                    result = await invoke(chunk)
                if own:
                    own_result = result
                if _is_error_result(result):
                    return result  # the tool refused or failed this chunk: nothing more is sent
            if len(plan) > 1:
                base = own_result if own_result is not None else result
                result = _whole_result(base, sum(len(str(chunk.get("text", chunk.get("code", ""))))
                                                 for chunk in sent))
            return result
        finally:
            end = getattr(policy, "end_input_run", None)
            if end is not None:
                try:
                    end(call, sent)
                except Exception:  # pylint: disable=broad-exception-caught
                    logger.exception("Desktop input run of %s could not be closed", call.tool)

    take_and_recheck._jarvis_rechecks = True  # type: ignore[attr-defined]
    executor._take_input_lock = take_and_recheck
    executor.input_runner = run_in_chunks


def _make_panic(node: Any, state: PersistentState) -> Any:
    from src.core.desktop.panic import PanicHotkey  # pylint: disable=import-outside-toplevel

    def on_panic() -> None:
        state.trip_panic()
        loop = getattr(node, "_loop", None)
        cancel = getattr(node, "cancel_all", None)
        if loop is not None and cancel is not None:
            try:
                loop.call_soon_threadsafe(lambda: loop.create_task(cancel("STOPPED_BY_USER")))
            except Exception:  # pylint: disable=broad-exception-caught
                logger.exception("Panic cancel-all could not be scheduled")

    return PanicHotkey(on_panic)


def _outputs_dir(node: Any) -> Path:
    config = getattr(node, "config", None)
    outputs = getattr(config, "outputs_dir", None)
    if isinstance(outputs, (str, os.PathLike)) and str(outputs).strip():
        return Path(outputs)
    env = os.environ.get("JARVIS_OUTPUTS_DIR")
    if env:
        return Path(env)
    return Path(node.data_root) / "Jarvis_Outputs"


def _open_roots(node: Any) -> list[str]:
    config = getattr(node, "config", None)
    roots = getattr(config, "open_roots", None)
    if isinstance(roots, (list, tuple)):
        return [str(root) for root in roots if str(root).strip()]
    return []
