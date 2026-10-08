"""Set the owner's dashboard login (password + authenticator app + recovery codes) on the VPS bridge (plan task C-20,
contract C-AUTH-1 / K-36), or with --target local on the PC's local bridge (plan task C-36, K-09). Run by the OWNER in
his own Windows PowerShell or cmd window, never by an agent:

    .venv_312/Scripts/python.exe scripts/ops/set_owner_credentials.py              # enrol (about 10 minutes)
    .venv_312/Scripts/python.exe scripts/ops/set_owner_credentials.py --test-login # try one real login
    .venv_312/Scripts/python.exe scripts/ops/set_owner_credentials.py --target local [--test-login]

Enrolment: asks for a new password twice (12+ characters, not a common one), makes a new authenticator secret and
shows it as a QR code, the otpauth:// link and the plain secret, checks one 6-digit code from the app, shows 10
one-time recovery codes once, then writes OWNER_PASSWORD_HASH, OWNER_TOTP_SECRET and OWNER_RECOVERY_HASHES into
/opt/jarvis-agent/deploy/hostinger/bridge.env on the VPS. The values travel only on ssh stdin (never on a command
line, never in a file on this PC); the VPS first copies the file to /root/jarvis-backups/<UTC time>/bridge.env.pre-c20
(mode 600), replaces the OWNER_* lines (or appends them), keeps every other line and writes the file atomically with
mode 600.

--target local is the login of the bridge vortex.py starts on http://127.0.0.1:5001 (npm run dev, Electron local):
the same password rules and code check, its own authenticator entry 'JARVIS local' and no recovery codes. It writes
OWNER_PASSWORD_HASH, OWNER_TOTP_SECRET and SESSION_JWT_SECRET (a usable one already there is kept) into the root .env
of this repo and nowhere else: every assignment of those keys is replaced in place, a missing key is appended, every
other line stays byte for byte, a leftover OWNER_RECOVERY_HASHES is emptied (an older enrolment's codes stop working),
and the file is replaced atomically (a temp file next to it, fsynced, then os.replace; mode 600 on POSIX). No value is
printed. Restart the local bridge afterwards; --target local --test-login then tries one login against it.

The formats are the bridge's own: every hash, the secret, the code check and the digit handling come from
src/core/bridge_auth.py, so what this script writes is exactly what the bridge reads. Nothing secret is logged or put
in an error message. Ctrl+C before the VPS write (or the .env write) saves nothing.

Env overrides (as in deploy_from_pc.sh): JARVIS_VPS_HOST (default root@187.127.123.235), JARVIS_SSH_KEY (default
~/.ssh/id_ed25519_hostinger), JARVIS_DOMAIN (default jarvis.srv1802669.hstgr.cloud).
Exit codes: 0 done, 1 refused or failed (nothing written), 2 not an interactive window, 130 cancelled.
"""
from __future__ import annotations

import argparse
import getpass
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:  # run as a file: make 'src' and 'services' importable
    sys.path.insert(0, str(ROOT))

from services.utils import jarvis_config  # noqa: E402  pylint: disable=wrong-import-position
from src.core import bridge_auth  # noqa: E402  pylint: disable=wrong-import-position


def _load_sibling(name: str) -> ModuleType:
    """scripts/ops/<name>.py as a module (scripts/ops is not a package)."""
    spec = importlib.util.spec_from_file_location(f"jarvis_ops_{name}", Path(__file__).with_name(f"{name}.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# The PC env file tools (C-14, C-26) for --target local: byte-exact .env parsing (env_set), the in-place replacement
# of every assignment of a key (rotate_env_secret) and the private atomic write (gen_secrets).
rotate_env_secret = _load_sibling("rotate_env_secret")
gen_secrets = rotate_env_secret.gen_secrets
env_set = rotate_env_secret.env_set

DEFAULT_HOST = "root@187.127.123.235"
DEFAULT_KEY = "~/.ssh/id_ed25519_hostinger"
DEFAULT_DOMAIN = "jarvis.srv1802669.hstgr.cloud"
VPS_ENV_FILE = "/opt/jarvis-agent/deploy/hostinger/bridge.env"
VPS_BACKUP_BASE = "/root/jarvis-backups"
BACKUP_NAME = "bridge.env.pre-c20"
KEYS = ("OWNER_PASSWORD_HASH", "OWNER_TOTP_SECRET", "OWNER_RECOVERY_HASHES")
BCRYPT_COST = bridge_auth.BCRYPT_COST       # 12 (C-AUTH-1); the tests lower it to stay fast
PASSWORD_TRIES = 3
CODE_TRIES = 5
SSH_TIMEOUT_S = 120
HTTP_TIMEOUT_S = 20
DONE_LINE = "Owner enrolled - ask Claude to recreate the bridge"
# --target local (C-36, K-09): the bridge vortex.py starts on this PC, its root .env and its authenticator entry.
LOCAL_ENV_FILE = ROOT / ".env"
LOCAL_ISSUER = "JARVIS local"
LOCAL_BRIDGE = "http://127.0.0.1:5001"    # its routes have no /vortex prefix (the Vite dev proxy strips it)
LOCAL_ORIGIN = "http://localhost:5173"    # the Vite dev page, one of the local bridge's default ALLOWED_ORIGINS
JWT_KEY = "SESSION_JWT_SECRET"
RECOVERY_KEY = "OWNER_RECOVERY_HASHES"
LOCAL_DONE_LINE = "Owner enrolled for the local bridge - restart it (vortex.py / npm run dev) to use the login"
# The VPS runs this loader; the program and its arguments (the values too) arrive as JSON on stdin.
REMOTE_LOADER = ("python3 -c 'import json,sys; d=json.load(sys.stdin); "
                 "exec(compile(d[\"program\"], \"jarvis-c20\", \"exec\"), "
                 "{\"__name__\": \"jarvis_c20\", \"ARGS\": d[\"args\"]})'")

HELP = """\
What this does
  Sets your JARVIS dashboard login: a password, an authenticator app (Google Authenticator, Microsoft
  Authenticator, Authy, ...) and 10 one-time recovery codes. Only you see them. They are saved on the
  JARVIS server in a form nobody can read back.

How to run it
  1. Open Windows PowerShell (not Git Bash) in the JARVIS-AGENT folder.
  2. Run:  .venv_312/Scripts/python.exe scripts/ops/set_owner_credentials.py
  3. Type a new password twice (12 or more characters; nothing shows while you type).
  4. Scan the QR code with your authenticator app (or type the secret into the app by hand).
  5. Type the 6-digit code the app shows.
  6. Write the 10 recovery codes down on paper or in a password manager, then press Enter.
  7. When it says 'Owner enrolled', ask Claude to recreate the bridge.
  8. Then run it again with --test-login to check that the login works.

Ctrl+C stops it at any time; before the last step nothing is saved anywhere.

For the local bridge on this PC (npm run dev, Electron local)
  Run it with --target local. The same steps, but the login is saved only in this PC's .env file (not on
  the server), the app entry is called 'JARVIS local', and there are no recovery codes (if you lose the
  phone, run it again). The same password as the server login is fine. Then restart the local bridge
  and check it with --target local --test-login.
"""


class EnrolError(Exception):
    """A refusal with a message that holds no secret."""


class UncertainWrite(EnrolError):
    """The server write may or may not have happened (the connection dropped or timed out): Claude must check
    the file before the owner runs the script again."""


@dataclass
class Deps:  # pylint: disable=too-many-instance-attributes
    """Everything the script touches outside itself, so the tests can replace it."""

    ask_secret: Callable[[str], str] = getpass.getpass
    ask: Callable[[str], str] = input
    out: Any = None                                  # a text stream; None means sys.stdout at call time
    clock: Callable[[], float] = time.time
    interactive: Callable[[], bool] = lambda: sys.stdin.isatty()  # pylint: disable=unnecessary-lambda
    private_output: Callable[[], bool] = lambda: sys.stdout.isatty()  # pylint: disable=unnecessary-lambda
    ssh_run: Callable[..., Any] = subprocess.run
    http_open: Callable[..., Any] = urllib.request.urlopen
    env: dict[str, str] = field(default_factory=lambda: dict(os.environ))
    local_env: Path = LOCAL_ENV_FILE                 # --target local writes this file only

    def stream(self) -> Any:
        return self.out if self.out is not None else sys.stdout

    def say(self, text: str = "") -> None:
        stream = self.stream()
        stream.write(text + "\n")
        stream.flush()


# ---------------------------------------------------------------- console


def make_console_utf8(stream: Any) -> None:
    """Switch a redirected stdout to UTF-8 so the QR's block characters cannot crash it (a real Windows console
    already writes Unicode). Any failure is ignored: the QR printer has its own ASCII fallback."""
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        if (getattr(stream, "encoding", "") or "").lower().replace("-", "") != "utf8":
            reconfigure(encoding="utf-8", errors="replace")
    except (ValueError, OSError, io.UnsupportedOperation):
        pass


def _can_print(stream: Any, text: str) -> bool:
    encoding = getattr(stream, "encoding", None) or "ascii"
    try:
        text.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return False
    return True


def qr_text(uri: str, stream: Any) -> str | None:
    """The QR code of uri as text the stream can print: half-block characters (light on dark, like the bridge's own
    enrol command) when the stream can encode them, else plain ASCII '##' squares. None when no QR can be made."""
    try:
        import qrcode  # type: ignore[import-untyped]  # pylint: disable=import-outside-toplevel
    except ImportError:
        return None
    try:
        qr = qrcode.QRCode(border=2)
        qr.add_data(uri)
        qr.make(fit=True)
        buffer = io.StringIO()
        qr.print_ascii(out=buffer, invert=True)
        blocks = buffer.getvalue()
        if _can_print(stream, blocks):
            return blocks
        # Each module two characters wide; light modules (and the quiet zone) are drawn, so it reads right on a
        # dark console window like the half-block version.
        return "\n".join("".join("  " if dark else "##" for dark in row) for row in qr.get_matrix()) + "\n"
    except Exception:  # pylint: disable=broad-exception-caught  # the URI and the secret below still work
        return None


def show_authenticator(deps: Deps, secret: str, issuer: str = "JARVIS") -> None:
    uri = bridge_auth.provisioning_uri(secret, issuer=issuer)
    deps.say(f"\nStep 2: add {issuer} to your authenticator app.")
    deps.say("Scan this QR code with the app (make the window bigger if the code is cut off):\n")
    picture = qr_text(uri, deps.stream())
    if picture is None:
        deps.say("(The QR code cannot be shown here; use the link or the secret below.)")
    else:
        try:
            deps.stream().write(picture)
            deps.stream().flush()
        except (UnicodeEncodeError, OSError):
            deps.say("\n(The QR code cannot be shown here; use the link or the secret below.)")
    deps.say("\nOr open this link on the phone:")
    deps.say(f"  {uri}")
    grouped = " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))
    deps.say("Or type this secret into the app by hand (account 'owner', time based):")
    deps.say(f"  {grouped}")


# ---------------------------------------------------------------- the enrolment steps


def ask_password(deps: Deps) -> str:
    """A new owner password the bridge accepts (bridge_auth.password_problem), typed twice."""
    deps.say("Step 1: choose the dashboard password (12 or more characters; nothing shows while you type).")
    for _ in range(PASSWORD_TRIES):
        first = deps.ask_secret("New password: ")
        problem = bridge_auth.password_problem(first)
        if problem:
            deps.say(f"That password is refused: {problem}. Try another one.")
            continue
        if deps.ask_secret("Type it again: ") != first:
            deps.say("The two passwords are different. Try again.")
            continue
        return first
    raise EnrolError("no usable password after 3 tries; nothing was written")


def normalise_code(text: str) -> str:
    """A typed 6-digit code as the bridge reads it: no whitespace or invisible direction marks (an RTL keyboard
    or a paste adds them), Urdu/Arabic (or any other script's) digits as ASCII digits."""
    return bridge_auth._code_text(text, drop_dashes=False)  # pylint: disable=protected-access


def confirm_code(deps: Deps, secret: str) -> None:
    """Asks for the app's current code until it matches (window 1, as the bridge checks it)."""
    for attempt in range(1, CODE_TRIES + 1):
        code = deps.ask("\nType the 6-digit code the app shows now: ")
        if bridge_auth.totp_match(secret, code, deps.clock(), window=bridge_auth.TOTP_WINDOW) is not None:
            deps.say("Code OK.")
            return
        if attempt < CODE_TRIES:
            deps.say("That code does not match. Check that the phone sets its time automatically, wait for the "
                     "next code and try again.")
    raise EnrolError(f"no matching code after {CODE_TRIES} tries; nothing was written "
                     "(delete the new JARVIS entry from the app and run this again)")


def make_values(password: str, secret: str, codes: list[str], cost: int) -> dict[str, str]:
    """The owner's env values (OWNER_*) in the bridge's own formats; each one is checked with the bridge's verifier.
    Without codes (--target local) there is no OWNER_RECOVERY_HASHES."""
    password_hash = bridge_auth.hash_password(password, cost=cost)
    hashes = [bridge_auth.hash_recovery_code(code, cost=cost) for code in codes]
    values = {"OWNER_PASSWORD_HASH": password_hash, "OWNER_TOTP_SECRET": secret}
    if codes:
        values["OWNER_RECOVERY_HASHES"] = ",".join(hashes)
    # Self-check with the bridge's functions before anything is written or leaves this PC.
    recovery_ok = not codes or bridge_auth.verify_recovery_code(codes[0], hashes[0])
    if (bridge_auth.password_problem(password)
            or not bridge_auth.verify_password(password, bridge_auth.decode_bcrypt_hash(password_hash))
            or not bridge_auth.totp_usable(secret)
            or any(bridge_auth.decode_bcrypt_hash(h) is None for h in hashes)
            or not recovery_ok):
        raise EnrolError("internal check of the new values failed; nothing was written")
    return values


def show_recovery_codes(deps: Deps, codes: list[str]) -> None:
    deps.say("\nStep 3: your 10 recovery codes. SAVE THESE NOW - they are shown only this once.")
    deps.say("Each one works once, instead of the app code, if you lose your phone.")
    deps.say("Write them on paper or put them in a password manager; do not keep them in a file or a chat.\n")
    for number, code in enumerate(codes, 1):
        deps.say(f"  {number:2d}. {code}")
    deps.say("")


# ---------------------------------------------------------------- the VPS write


def remote_set_owner(args: dict[str, Any]) -> int:
    """
    Runs on the VPS (python3, standard library only; its source travels on ssh stdin, so it uses nothing from this
    module) and in the tests. Backs up args['target'] to args['backup_dir']/args['backup_name'] (mode 600, never over
    an existing file), sets every key of args['values'] (each assignment line replaced, a missing key appended),
    keeps every other line byte for byte and replaces the file atomically with mode 600. Prints one JSON line
    without any value; returns 0 ok, 2 refused (nothing written), 3 the file is missing.
    """
    import json  # pylint: disable=import-outside-toplevel,redefined-outer-name,reimported
    import os  # pylint: disable=import-outside-toplevel,redefined-outer-name,reimported
    import re  # pylint: disable=import-outside-toplevel

    def report(code: int, **row: Any) -> int:
        print(json.dumps({"ok": code == 0, **row}))
        return code

    target, values = args["target"], args["values"]
    safe = re.compile(r"[A-Za-z0-9_,=-]+")
    if not all(re.fullmatch(r"[A-Z][A-Z0-9_]*", k) and isinstance(v, str) and safe.fullmatch(v)
               for k, v in values.items()):
        return report(2, error="bad_value")
    try:
        with open(target, "rb") as handle:
            original = handle.read()
    except FileNotFoundError:
        return report(3, error="missing", target=target)
    text = original.decode("utf-8")
    old_umask = os.umask(0o077)
    tmp = ""
    try:
        os.makedirs(args["backup_dir"], mode=0o700, exist_ok=True)
        backup = os.path.join(args["backup_dir"], args["backup_name"])
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(original)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(backup, 0o600)

        lines = text.splitlines(keepends=True)
        eol = "\r\n" if "\r\n" in text else "\n"
        actions = {}
        for key, value in values.items():
            pattern = re.compile(r"^\s*(?:export\s+)?" + re.escape(key) + r"\s*=.*?(\r\n|\n|\r)?$", re.S)
            hits = 0
            for i, line in enumerate(lines):
                match = pattern.match(line)
                if match:
                    lines[i] = f"{key}={value}" + (match.group(1) or "")
                    hits += 1
            if not hits:
                if lines and not lines[-1].endswith(("\n", "\r")):
                    lines[-1] += eol
                lines.append(f"{key}={value}{eol}")
            actions[key] = "replaced" if hits else "added"
        jwt_set = any(re.match(r"^\s*(?:export\s+)?SESSION_JWT_SECRET\s*=\s*['\"]?[^\s'\"#]", line) for line in lines)

        folder = os.path.dirname(os.path.abspath(target))
        tmp = os.path.join(folder, f".{os.path.basename(target)}.c20.tmp")
        if os.path.lexists(tmp):  # a stale temp file from an interrupted run (never followed if it is a link)
            os.unlink(tmp)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write("".join(lines).encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        stat = os.stat(target)
        if hasattr(os, "chown"):
            try:
                os.chown(tmp, stat.st_uid, stat.st_gid)
            except OSError:
                pass
        os.replace(tmp, target)  # the temp file is already mode 600, so the replaced file is too
        tmp = ""
    except OSError as exc:
        return report(2, error=type(exc).__name__)
    finally:
        os.umask(old_umask)
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)
    return report(0, backup=backup, keys=actions, mode=oct(os.stat(target).st_mode & 0o777),
                  session_jwt_secret=jwt_set)


def remote_program() -> str:
    import inspect  # pylint: disable=import-outside-toplevel
    return ("from __future__ import annotations\nfrom typing import Any\n"
            + inspect.getsource(remote_set_owner) + "\nraise SystemExit(remote_set_owner(ARGS))\n")


def ssh_settings(env: dict[str, str]) -> tuple[str, str]:
    host = (env.get("JARVIS_VPS_HOST") or "").strip() or DEFAULT_HOST
    key = os.path.expanduser((env.get("JARVIS_SSH_KEY") or "").strip() or DEFAULT_KEY)
    return host, key


def backup_dir(now: float) -> str:
    return f"{VPS_BACKUP_BASE}/{time.strftime('%Y-%m-%d_%H%M%SZ', time.gmtime(now))}"


def write_to_vps(deps: Deps, values: dict[str, str]) -> dict[str, Any]:
    """Sends the values to the VPS on ssh stdin. Returns the remote status row; raises EnrolError (no secret in it)
    when the write did not happen."""
    host, key = ssh_settings(deps.env)
    payload = json.dumps({"program": remote_program(),
                          "args": {"target": VPS_ENV_FILE, "backup_dir": backup_dir(deps.clock()),
                                   "backup_name": BACKUP_NAME, "values": values}})
    command = ["ssh", "-i", key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", host, REMOTE_LOADER]
    try:
        proc = deps.ssh_run(command, input=payload, text=True, encoding="utf-8", errors="replace",
                            capture_output=True, timeout=SSH_TIMEOUT_S, check=False)
    except FileNotFoundError as exc:
        raise EnrolError("ssh was not found on this PC (install the Windows OpenSSH client); nothing was "
                         "written") from exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UncertainWrite(f"ssh did not finish ({type(exc).__name__}); the server file is either unchanged "
                             "or fully updated - ask Claude to check it before running this again") from exc
    status: dict[str, Any] = {}
    for line in (proc.stdout or "").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and "ok" in row:
            status = row
    if proc.returncode == 0 and status.get("ok"):
        return status
    if status.get("error") == "missing":
        raise EnrolError(f"{VPS_ENV_FILE} does not exist on the server; nothing was written - ask Claude")
    if proc.returncode == 255 and not status:
        last = ((proc.stderr or "").strip().splitlines() or ["no answer"])[-1][:160]
        if re.search(r"refused|timed out|could not resolve|no route|permission denied|host key", last, re.I):
            raise EnrolError(f"could not connect to the server with ssh ({last}); nothing was written")
        raise UncertainWrite(f"the ssh connection ended without an answer ({last}); the server file is either "
                             "unchanged or fully updated - ask Claude to check it before running this again")
    reason = status.get("error") or f"exit {proc.returncode}"
    raise EnrolError(f"the server refused the write ({reason}); nothing was written")


# ---------------------------------------------------------------- the local write (--target local, C-36)


def _env_problem(exc: Exception) -> str:
    """An env_set.EnvError message (it never holds a value) without its own 'no file was changed' ending."""
    return str(exc).removesuffix("; no file was changed")


def check_local_env(path: Path) -> None:
    """Stops before the first question when the root .env cannot be read exactly or is read-only."""
    try:
        env_set.EnvFile(path)
    except env_set.EnvError as exc:
        raise EnrolError(f"the root .env cannot be used ({_env_problem(exc)}); nothing was written") from None
    if not os.access(path, os.W_OK):
        raise EnrolError("the root .env is read-only; nothing was written")


def write_local_env(path: Path, values: dict[str, str]) -> dict[str, str]:
    """Puts values into the root .env at path; returns {key: 'replaced'|'added'|'kept'|'cleared'} (never a value).
    Every assignment of a key is replaced in place (its 'KEY=' part and line ending stay), a missing key is appended
    with the file's own line ending, and every other line stays byte for byte. SESSION_JWT_SECRET: a usable one (32+
    characters) is kept, else a new token_urlsafe(64) is written. A non-empty OWNER_RECOVERY_HASHES is emptied: the
    local login takes no recovery codes, and an older enrolment's codes must not outlive it. The file is written once
    through a temp file next to it (fsynced; mode 600 on POSIX) and os.replace, so it is either unchanged or fully
    updated, and no temp copy of it is left behind. Raises EnrolError (no value in it) when nothing was written."""
    temp = path.with_name(path.name + gen_secrets.TEMP_SUFFIX)
    try:
        env_file = env_set.EnvFile(path)  # read again: the file may have changed while the owner typed
        todo = dict(values)
        jwt_usable = len(env_file.value(JWT_KEY).strip()) >= bridge_auth.JWT_SECRET_MIN_CHARS
        if not jwt_usable:
            todo[JWT_KEY] = gen_secrets.generate(JWT_KEY)
        actions = {key: rotate_env_secret.replace_in(env_file, key, env_set.render_value(key, value))
                   for key, value in todo.items()}
        if jwt_usable:
            actions[JWT_KEY] = "kept"
        if env_file.value(RECOVERY_KEY).strip():
            rotate_env_secret.replace_in(env_file, RECOVERY_KEY, "")
            actions[RECOVERY_KEY] = "cleared"
        gen_secrets.write_private([env_file])
    except env_set.EnvError as exc:
        raise EnrolError(f"the root .env could not be updated ({_env_problem(exc)}); nothing was written") from None
    finally:
        # gen_secrets removes its temp file after an OSError; this also covers Ctrl+C in the middle of the write.
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
    return actions


def local_origin_note(path: Path) -> str | None:
    """A note when the root .env's ALLOWED_ORIGINS leaves out a Vite dev origin, so the local bridge would answer the
    dev page's login 403 bad_origin (K-49: the list comes only from the environment). None when it lists both or is
    unset or empty there (then the dev origins are the default). Names the missing dev origins, never the value."""
    try:
        raw = env_set.EnvFile(path).values.get("ALLOWED_ORIGINS")
    except env_set.EnvError:
        return None
    listed = {part.strip().rstrip("/") for part in (raw or "").split(",") if part.strip()}
    dev = jarvis_config._DEV_ORIGINS  # pylint: disable=protected-access  # the bridge's own default list
    missing = [origin for origin in dev if origin not in listed] if listed else []
    if not missing:
        return None
    return (f"Note: ALLOWED_ORIGINS in the root .env does not list {' and '.join(missing)}, so the local bridge "
            f"refuses a login from the dev page (bad_origin). Add {'it' if len(missing) == 1 else 'them'} to that "
            "line (comma-separated, keep the entries already there), then restart the local bridge.")


# ---------------------------------------------------------------- the commands


def enrol(deps: Deps) -> int:
    """The enrolment flow. Every secret stays in memory and on this window; only the VPS write sends values."""
    password = ask_password(deps)
    secret = bridge_auth.new_totp_secret()
    show_authenticator(deps, secret)
    confirm_code(deps, secret)
    codes = bridge_auth.new_recovery_codes()
    show_recovery_codes(deps, codes)
    deps.ask("Press Enter once the 10 codes are saved (Ctrl+C cancels; nothing is saved yet): ")
    deps.say("Making the hashes (a few seconds)...")
    values = make_values(password, secret, codes, BCRYPT_COST)
    deps.say("Saving on the JARVIS server...")
    try:
        status = write_to_vps(deps, values)
    except KeyboardInterrupt:
        raise EnrolError("cancelled during the server write: the file there is either unchanged or fully "
                         "updated - ask Claude to check it before running this again") from None
    except UncertainWrite:
        raise
    except EnrolError as exc:
        raise EnrolError(f"{exc}. The QR entry and the recovery codes above are NOT active; delete the JARVIS "
                         "entry from the app and run this again") from None
    deps.say(f"Saved. Backup of the old file on the server: {status.get('backup', '?')}")
    if status.get("session_jwt_secret") is False:
        deps.say("Note: the server file has no SESSION_JWT_SECRET yet; tell Claude (the login needs it).")
    deps.say("Clear this window now (type cls) so the codes do not stay on screen.")
    deps.say(DONE_LINE)
    return 0


def enrol_local(deps: Deps) -> int:
    """--target local (C-36, K-09): the login of the PC's local bridge. The same password rules and code check as the
    server enrolment, its own authenticator entry and no recovery codes; the values go only into the root .env of
    this PC (write_local_env), and none of them is printed."""
    check_local_env(deps.local_env)
    deps.say("This sets the login of the local bridge on this PC (npm run dev, Electron local); the same password "
             "as the server login is fine.")
    password = ask_password(deps)
    secret = bridge_auth.new_totp_secret()
    show_authenticator(deps, secret, issuer=LOCAL_ISSUER)
    confirm_code(deps, secret)
    deps.say("Making the hash (a few seconds)...")
    values = make_values(password, secret, [], BCRYPT_COST)
    deps.say("Saving in the .env file of this PC...")
    try:
        actions = write_local_env(deps.local_env, values)
    except KeyboardInterrupt:
        raise EnrolError("cancelled during the .env write: the file is either unchanged or fully updated - run "
                         "this again") from None
    except EnrolError as exc:
        raise EnrolError(f"{exc}. The new {LOCAL_ISSUER} entry in the app is NOT active; delete it from the app "
                         "and run this again") from None
    deps.say("Saved: " + ", ".join(f"{key} {action}" for key, action in actions.items()) + ".")
    note = local_origin_note(deps.local_env)
    if note:
        deps.say(note)
    deps.say("Clear this window now (type cls) so the secret does not stay on screen.")
    deps.say(LOCAL_DONE_LINE)
    return 0


def _post_json(deps: Deps, url: str, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, dict[str, Any],
                                                                                              str, Any]:
    """(status, JSON body or {}, a short text of a non-JSON body, the response headers). Network errors raise
    EnrolError without any request detail."""
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json", "Accept": "application/json",
                                              "User-Agent": "jarvis-set-owner-credentials", **headers})
    try:
        response = deps.http_open(request, timeout=HTTP_TIMEOUT_S)
    except urllib.error.HTTPError as exc:  # 4xx/5xx still carry the server's answer
        response = exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", None) or type(exc).__name__
        raise EnrolError(f"could not reach {url}: {str(reason)[:160]}") from None
    try:
        status = int(getattr(response, "status", None) or response.getcode())
        raw = response.read() or b""
        response_headers = response.headers
    finally:
        close = getattr(response, "close", None)
        if close is not None:
            close()
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    text = " ".join(raw.decode("utf-8", "replace").split())[:120]
    return status, data if isinstance(data, dict) else {}, text, response_headers


def _session_cookie(headers: Any) -> str | None:
    """The jarvis_session cookie value from Set-Cookie (kept in memory only, never printed)."""
    getter = getattr(headers, "get_all", None)
    values = (getter("Set-Cookie") if getter else None) or []
    prefix = f"{bridge_auth.COOKIE_NAME}="
    for value in values:
        first = str(value).split(";", 1)[0].strip()
        if first.startswith(prefix) and len(first) > len(prefix):
            return first[len(prefix):]
    return None


def run_test_login(deps: Deps, local: bool = False) -> int:
    """One real login against the live bridge (with local, the PC's local bridge as the Vite dev page reaches it);
    prints LOGIN OK or the server's error, never the cookie."""
    if local:
        origin, base = LOCAL_ORIGIN, LOCAL_BRIDGE
    else:
        domain = (deps.env.get("JARVIS_DOMAIN") or "").strip() or DEFAULT_DOMAIN
        origin = f"https://{domain}"
        base = f"{origin}/vortex"
    url = f"{base}/auth/login"
    password = deps.ask_secret("Dashboard password: ")
    code = normalise_code(deps.ask("6-digit code from the app: "))
    if not password or len(code) != bridge_auth.TOTP_DIGITS or not code.isdigit() or not code.isascii():
        deps.say("LOGIN FAILED: type the password and the 6 digits the app shows")
        return 1
    status, data, text, headers = _post_json(deps, url, {"password": password, "totp": code}, {"Origin": origin})
    if status == 200 and data.get("ok"):
        expires = data.get("expires_at")
        when = (time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(int(expires)))
                if isinstance(expires, (int, float)) else "unknown")
        deps.say(f"LOGIN OK (expires {when})")
        cookie = _session_cookie(headers)
        if cookie:  # end the test session again so it does not stay open for 12 hours
            try:
                out_status, _, _, _ = _post_json(deps, f"{base}/auth/logout", {},
                                                 {"Origin": origin, "Cookie": f"{bridge_auth.COOKIE_NAME}={cookie}"})
                deps.say("Test session closed (logged out)." if out_status == 200 else
                         f"Note: the test session could not be closed (HTTP {out_status}); it ends by itself.")
            except EnrolError:
                deps.say("Note: the test session could not be closed; it ends by itself.")
        return 0
    error = str(data.get("error") or "")
    if error == "invalid_credentials":
        deps.say("LOGIN FAILED: invalid credentials (wrong password or code)")
    elif error == "rate_limited":
        retry = data.get("retry_after")
        if retry is None and headers is not None:
            retry = headers.get("Retry-After")
        deps.say(f"LOGIN FAILED: rate_limited - retry after {retry if retry is not None else '?'} s")
    elif error:
        deps.say(f"LOGIN FAILED: {error[:80]} (HTTP {status})")
        if local and error == "bad_origin":
            deps.say(f"The local bridge does not accept {LOCAL_ORIGIN}: add it to ALLOWED_ORIGINS in the root .env.")
        elif local and error == "owner_not_enrolled":
            deps.say("Run this with --target local first, then restart the local bridge.")
    else:
        deps.say(f"LOGIN FAILED: HTTP {status}" + (f": {text}" if text else ""))
    return 1


# ---------------------------------------------------------------- entry point


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="set_owner_credentials.py", formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Set (or test) your JARVIS dashboard login: password + authenticator app + recovery codes.",
        epilog=HELP + "\nAdvanced: JARVIS_VPS_HOST, JARVIS_SSH_KEY and JARVIS_DOMAIN change the server, the ssh key "
                      "and the web address.")
    parser.add_argument("--test-login", action="store_true",
                        help="do not change anything; try one real login with your password and a code")
    parser.add_argument("--target", choices=("vps", "local"), default="vps",
                        help="vps (default): the JARVIS server's login, saved there over ssh; local: the login of "
                             "the local bridge on this PC (npm run dev, Electron local), saved in this PC's .env")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, deps: Deps | None = None) -> int:
    args = parse_args(argv)
    deps = deps or Deps()
    make_console_utf8(deps.stream())
    if not deps.interactive():
        deps.say("Run this in your own PowerShell or cmd window (not Git Bash, not piped): it asks for secrets.")
        return 2
    if not args.test_login and not deps.private_output():
        deps.say("Run this in your own PowerShell or cmd window without redirecting its output (no '>' or "
                 "Tee-Object): it shows the authenticator secret and the recovery codes once.")
        return 2
    local = args.target == "local"
    enrol_command = enrol_local if local else enrol
    try:
        return run_test_login(deps, local=local) if args.test_login else enrol_command(deps)
    except (KeyboardInterrupt, EOFError):
        deps.say("\nCancelled - nothing was written." if not args.test_login else "\nCancelled.")
        return 130
    except EnrolError as exc:
        deps.say(f"\nStopped: {exc}.")
        return 1
    except Exception as exc:  # pylint: disable=broad-exception-caught  # never show values from a traceback
        deps.say(f"\nStopped by an unexpected error ({type(exc).__name__}); nothing was written. Tell Claude the "
                 "error name (not your password or codes).")
        return 1


if __name__ == "__main__":
    sys.exit(main())
