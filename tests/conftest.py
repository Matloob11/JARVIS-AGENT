"""
J.A.R.V.I.S Test Configuration
pytest configuration and shared fixtures

Test hygiene (C-138). Everything below runs when pytest imports this file, before any services module
(and its load_dotenv) is imported, so it holds for collection-time imports too:

1. Secrets. The real env files of this tree (the root .env, deploy/windows-local/.env, deploy/hostinger/.env
   and any other non-example '.env' file inside the project) never hand a secret to a test: every secret
   name (jarvis_security.SECRET_ENV_NAMES, plus any KEY/TOKEN/SECRET/PASS/HASH/CREDENTIAL/PRIVATE/COOKIE
   name and the private EMAIL_USER, SIM_LOOKUP_API_URL and WHATSAPP_TEST_NUMBERS, the pre-commit hook's
   rule) is dropped from what dotenv.load_dotenv/dotenv_values read from those files, and removed from
   os.environ. Non-secret settings of the real .env still load as before. A test that needs a secret sets a
   throwaway value itself (monkeypatch.setenv / patching config); the suite gives the same result with or
   without AGENT_TOKEN, BRIDGE_HMAC_SECRET or any other secret in the real .env. dotenv on a test's own file
   (for example under tmp_path) is untouched, and a bare load_dotenv() finds the .env nearest to the module
   that calls it, as dotenv itself does. Throwaway VORTEX_SECURITY_TOKEN and LiveKit keys (the names that
   src/core/ui_bridge.py's config.validate('bridge') and the legacy notify signer need) are set for the whole
   session: generated per run (secrets.token_hex) and named test-only-fake-..., never read from a file.
   LIVEKIT_URL and LIVEKIT_PUBLIC_URL point at a closed local port, so no test reaches the live LiveKit
   server.
   Test-session mode. Tests always run in a non-production environment (_TEST_MODE_SETTINGS): VORTEX_ENV is
   pinned to 'development' (its default without a .env; the real .env says production), and the mode
   switches JARVIS_ROLE, JARVIS_BRAIN, JARVIS_DESKTOP_MODE and LEGACY_TOKEN_AUTH are unset (defaults agent,
   pc, local, legacy token on). A real env file never hands these names to a test either (load_dotenv and
   dotenv_values drop them like the secret names, override=True included). A test that needs production
   mode or another role sets it itself with monkeypatch.
   Child processes. A test's child interpreter (cwd=ROOT, env={**os.environ} or a fresh env) imports
   jarvis_config, whose own load_dotenv reads the real .env outside this process. So the same filter runs
   inside every Python child: this file writes a generated sitecustomize.py into a per-run temp folder
   (_CHILD_SITE_DIR), and every child that subprocess.Popen starts from this process (subprocess.run,
   asyncio subprocesses and agent_ctl's helpers all go through it) gets that folder appended to its
   PYTHONPATH (at the end, so the caller's own PYTHONPATH entries keep their priority). Python imports the
   sitecustomize at start-up; it drops the secret names and the test-session mode names from what
   python-dotenv reads out of a real env file of this tree (DotEnv.dict, which load_dotenv and dotenv_values
   both use; put in place by an import hook when the child itself imports dotenv.main, so a child that never
   imports python-dotenv does not get it in sys.modules; a child with a fresh env so runs in development mode
   without real secrets too), does the same PYTHONPATH step for the child's own children (grandchildren),
   then runs any other sitecustomize it shadows. The
   env a caller builds is otherwise passed on exactly as built: no name is added or removed, so a
   production scrub of a child's env (net_guard's CHILD_ENV guard, the Desktop Node supervisor) is what
   the child sees. Popen.__init__ keeps its signature (functools.wraps), so code that inspects or wraps it
   (net_guard.install_child_env_guard) stacks on top of this wrapper and restores it on uninstall.
   Not filtered: code that reads a real env file directly with open()/read_text instead of python-dotenv
   (SecureConfigManager's plain ./.env fallback when .env.encrypted is missing, agent_ctl's and the
   watchdog's own env parsers). A test that reaches one of those must mock the read or point it at a
   tmp_path file; a test never gets a filtered copy of a real env file, so it can never write one back.
   Live probes. With JARVIS_LIVE_BRIDGE_URL set (the owner opts into the C-14 live probes of
   tests/infrastructure, which sign with the real credentials of the root .env), the secret isolation of
   this step is off for that run; the log redirection, the pinned local URLs and VORTEX_ENV=development stay
   on.
2. Logs. LOG_FILE and LOG_DIR point at a temp folder (jarvis_logger's bridge.log and runner.log honour
   LOG_DIR), any logging.FileHandler that still names a file under the live logs/ writes the same relative
   path under that temp folder instead (its baseFilename stays as it is, rotation is off), the
   telemetry singleton writes to the temp folder (_live_sinks_go_to_tmp), and open()/Path.open of a file
   under the live logs/ for writing (a module's own sink, such as control_log.txt) opens the same relative
   path under the temp folder (_test_open).
3. Guard. An audit hook records every write, rename, truncate or delete this pytest process makes under the
   live logs/; any such write fails the run at session end, with the test and code line that did it. The
   session end also compares the size of every file under logs/ with its size at the start, which also
   catches a child process: any growth fails the run when no live JARVIS worker is running. While the
   owner's worker or Desktop Node runs, the files they write themselves (_RUNTIME_LOG_NAMES, their rotations
   and the LOG_FILE of the real env files) are only reported; growth of any other file under logs/ still
   fails. JARVIS_TESTS_STRICT_LOGS=1 makes every growth fail.
"""

import atexit
import builtins
import functools
import inspect
import io
import logging
import logging.handlers
import os
import secrets
import subprocess
import sys
import pytest
import tempfile
import shutil
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

# Test isolation (2026-10-01): tests must never reach the live VPS UI bridge or write into the
# live agent logs. These env vars are set before any services module (and its load_dotenv) runs;
# load_dotenv never overrides variables that already exist.
os.environ["UI_BRIDGE_URL"] = "http://127.0.0.1:9"  # closed local port: bridge notifications fail fast
_TEST_LOG_DIR = os.path.join(tempfile.gettempdir(), "jarvis_tests")
os.makedirs(_TEST_LOG_DIR, exist_ok=True)
os.environ["LOG_FILE"] = os.path.join(_TEST_LOG_DIR, "jarvis_test.log")
os.environ["LOG_DIR"] = _TEST_LOG_DIR  # C-138: jarvis_logger's bridge.log and runner.log

# Add project root and service directories to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# ------------------------------------------------------------------ C-138 (1): no real secrets

import dotenv  # noqa: E402  # pylint: disable=wrong-import-position
import dotenv.main  # noqa: E402  # pylint: disable=wrong-import-position

from services.utils.jarvis_security import SECRET_ENV_NAMES  # noqa: E402  # stdlib only, reads no env file
from src.core.desktop.settings import EXTRA_SECRET_NAMES, SECRET_NAME_RE  # noqa: E402  # stdlib only

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ORIGINAL_LOAD_DOTENV = dotenv.main.load_dotenv
_ORIGINAL_DOTENV_VALUES = dotenv.main.dotenv_values
_ORIGINAL_FIND_DOTENV = dotenv.main.find_dotenv
_ORIGINAL_POPEN_INIT = subprocess.Popen.__init__
_POPEN_SIGNATURE = inspect.signature(_ORIGINAL_POPEN_INIT)

# The owner opted into the C-14 live probes, which sign with the real credentials: no secret isolation.
LIVE_PROBES = bool((os.environ.get("JARVIS_LIVE_BRIDGE_URL") or "").strip())
# Not secret by name, but private (phone numbers): hidden like a secret.
_PRIVATE_SETTING_NAMES = frozenset({"WHATSAPP_TEST_NUMBERS"})

# The test-session mode. Tests always run in a non-production environment, whatever the real .env (it says
# VORTEX_ENV=production) or the shell says: in production config.validate('bridge'), run when src/core/ui_bridge.py
# is imported, raises without its keys, and the notify signers refuse to sign without theirs. VORTEX_ENV is the
# only switch of config.is_production(); it is pinned to 'development', its default without a .env. The K-72
# switches JARVIS_ROLE, JARVIS_BRAIN and JARVIS_DESKTOP_MODE (which keys config.validate(role, mode) asks for)
# and LEGACY_TOKEN_AUTH (whether the bridge asks for VORTEX_SECURITY_TOKEN) are unset, so their defaults hold:
# agent, pc, local, legacy token on. These names are also dropped from what python-dotenv reads out of a real
# env file of this tree, here and in every child process, so neither a later load_dotenv (override=True
# included) nor a child started with a fresh env switches a test to production. A test that needs production
# or another role sets it itself (monkeypatch.setenv, or the env it builds for its child).
_TEST_MODE_SETTINGS = {"VORTEX_ENV": "development"}
_TEST_MODE_UNSET = frozenset({"JARVIS_ROLE", "JARVIS_BRAIN", "JARVIS_DESKTOP_MODE", "LEGACY_TOKEN_AUTH"})
_TEST_MODE_NAMES = frozenset({*_TEST_MODE_SETTINGS, *_TEST_MODE_UNSET})
# Secret names a module needs at import or validation time; each gets a generated throwaway value (_fake_secret).
_TEST_FAKE_SECRET_NAMES = ("VORTEX_SECURITY_TOKEN", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")


def _is_secret_name(name: str) -> bool:
    """The pre-commit hook's secret-name rule plus the K-29 list (redact_secrets masks those values) and the
    private settings above."""
    upper = name.upper()
    return (upper in SECRET_ENV_NAMES or upper in EXTRA_SECRET_NAMES or upper in _PRIVATE_SETTING_NAMES
            or bool(SECRET_NAME_RE.search(upper)))


def _is_hidden_file_setting(name: str) -> bool:
    """A name that a real env file of this tree never hands to a test: a secret or a test-session mode name."""
    return _is_secret_name(name) or name.upper() in _TEST_MODE_NAMES


def _is_real_env_file(path: Any) -> bool:
    """True for an env file of this working tree (.env, .env.<x>, <x>.env; never a *.example template)."""
    try:
        full = Path(os.fspath(path)).resolve()
        full.relative_to(_PROJECT_ROOT)
    except (TypeError, ValueError, OSError):
        return False
    name = full.name.lower()
    if name.endswith(".example"):
        return False
    return name == ".env" or name.startswith(".env.") or name.endswith(".env")


def _without_secrets(values: dict[str, str | None]) -> dict[str, str | None]:
    """A real env file's values without its secret names and the test-session mode names."""
    return {name: value for name, value in values.items() if not _is_hidden_file_setting(name)}


def _dotenv_runs_from_cwd() -> bool:
    """dotenv.find_dotenv's own rule for searching from the working directory (REPL, notebook, debugger or
    tracer, frozen app) instead of from the calling module's folder."""
    if hasattr(sys, "ps1") or hasattr(sys, "ps2") or sys.gettrace() is not None or getattr(sys, "frozen", False):
        return True
    return not hasattr(sys.modules.get("__main__"), "__file__")


def _find_dotenv_for_caller() -> str:
    """dotenv.find_dotenv() as the module that called load_dotenv()/dotenv_values() would run it: the nearest
    .env from that module's folder upwards. (dotenv's own frame walk would start at this file, in tests/.)"""
    if _dotenv_runs_from_cwd():
        return _ORIGINAL_FIND_DOTENV(usecwd=True)
    here = os.path.normcase(os.path.abspath(__file__))
    dotenv_dir = os.path.normcase(os.path.dirname(os.path.abspath(dotenv.main.__file__))) + os.sep
    frame = sys._getframe(1)  # pylint: disable=protected-access
    while frame is not None:
        filename = frame.f_code.co_filename
        full = os.path.normcase(os.path.abspath(filename))
        if full != here and not full.startswith(dotenv_dir) and os.path.exists(filename):
            break
        frame = frame.f_back
    if frame is None:
        return _ORIGINAL_FIND_DOTENV(usecwd=True)
    folder = Path(os.path.abspath(frame.f_code.co_filename)).parent
    for candidate in (folder, *folder.parents):
        check = candidate / ".env"
        if check.is_file():
            return str(check)
    return ""


def _dotenv_target(dotenv_path: Any, stream: Any) -> Any:
    """The file a dotenv call reads: its path, or the nearest .env as dotenv itself would find it."""
    if dotenv_path is None and stream is None:
        return _find_dotenv_for_caller() or None
    return dotenv_path


def _test_dotenv_values(dotenv_path: Any = None, stream: Any = None, verbose: bool = False,
                        interpolate: bool = True, encoding: str | None = "utf-8") -> dict[str, str | None]:
    """dotenv_values; a real env file of this tree comes back without its secret and test-session mode names."""
    target = _dotenv_target(dotenv_path, stream)
    if stream is None and target is not None and _is_real_env_file(target):
        return _without_secrets(dict(_ORIGINAL_DOTENV_VALUES(target, verbose=verbose, interpolate=interpolate,
                                                             encoding=encoding)))
    return _ORIGINAL_DOTENV_VALUES(dotenv_path=target if stream is None else dotenv_path, stream=stream,
                                   verbose=verbose, interpolate=interpolate, encoding=encoding)


def _test_load_dotenv(dotenv_path: Any = None, stream: Any = None, verbose: bool = False,
                      override: bool = False, interpolate: bool = True, encoding: str | None = "utf-8") -> bool:
    """load_dotenv; a real env file of this tree sets only its names that are neither secret nor a test-session
    mode name (override as asked)."""
    target = _dotenv_target(dotenv_path, stream)
    if stream is None and target is not None and _is_real_env_file(target):
        values = _without_secrets(dict(_ORIGINAL_DOTENV_VALUES(target, verbose=verbose, interpolate=interpolate,
                                                               encoding=encoding)))
        for name, value in values.items():
            if value is not None and (override or name not in os.environ):
                os.environ[name] = value
        return bool(values)
    return _ORIGINAL_LOAD_DOTENV(dotenv_path=target if stream is None else dotenv_path, stream=stream,
                                 verbose=verbose, override=override, interpolate=interpolate, encoding=encoding)


def _real_env_files() -> list[Path]:
    found = [_PROJECT_ROOT / ".env"]
    deploy = _PROJECT_ROOT / "deploy"
    if deploy.is_dir():
        found.extend(p for p in deploy.rglob("*env*") if p.is_file() and _is_real_env_file(p))
    return [p for p in found if p.is_file()]


def _hidden_secret_names() -> frozenset[str]:
    """Every secret name a real env file holds, plus the fixed K-29 and private lists (names only). The fixed
    part makes a child see the same thing whether or not the real .env has the name."""
    names = {name.upper() for name in (*SECRET_ENV_NAMES, *EXTRA_SECRET_NAMES, *_PRIVATE_SETTING_NAMES)}
    for env_file in _real_env_files():
        try:
            names.update(name.upper() for name in _ORIGINAL_DOTENV_VALUES(env_file) if _is_secret_name(name))
        except (OSError, UnicodeDecodeError, ValueError):
            continue
    return frozenset(names)


def _scrub_secret_environ(names: frozenset[str]) -> list[str]:
    """Removes these names from os.environ (names only)."""
    removed = sorted(name for name in os.environ if name.upper() in names)
    for name in removed:
        os.environ.pop(name, None)
    return removed


# The child processes' copy of the dotenv filter (see the module docstring). Written for any Python 3.8+
# (a child may be another interpreter); it reads no env file itself and holds no value, only names and paths.
_CHILD_SITECUSTOMIZE = '''\
"""Generated by tests/conftest.py (C-138) for the child processes of one pytest run; not project code.
python-dotenv in this process reads a real env file of the project without its secret names and the
test-session mode names, and children started from here get the folder of this file on their PYTHONPATH too.
python-dotenv is never imported for this: the filter is put in place when the process itself imports it."""
import os as _c138_os
import sys as _c138_sys

_C138_ROOT = {root!r}
_C138_NAMES = frozenset({names!r})
_C138_MODE_NAMES = frozenset({mode_names!r})
_C138_PATTERN = {pattern!r}
_C138_FLAGS = {flags!r}
_C138_SITE_DIR = _c138_os.path.dirname(_c138_os.path.abspath(__file__))


def _c138_is_secret(name, _re=[]):
    if not _re:
        import re
        _re.append(re.compile(_C138_PATTERN, _C138_FLAGS))
    upper = name.upper()
    return upper in _C138_NAMES or bool(_re[0].search(upper))


def _c138_is_real_env_file(path):
    from pathlib import Path
    try:
        full = Path(_c138_os.fspath(path)).resolve()
        full.relative_to(_C138_ROOT)
    except (TypeError, ValueError, OSError):
        return False
    name = full.name.lower()
    if name.endswith(".example"):
        return False
    return name == ".env" or name.startswith(".env.") or name.endswith(".env")


def _c138_is_hidden(name):
    return _c138_is_secret(name) or name.upper() in _C138_MODE_NAMES


def _c138_patch_dotenv(main):
    """DotEnv.dict (what load_dotenv and dotenv_values both read) of this dotenv.main, filtered once."""
    dotenv_class = getattr(main, "DotEnv", None)
    original = getattr(dotenv_class, "dict", None)
    if original is None or getattr(original, "_c138_filtered", False):
        return

    def dict_without_secrets(self):
        values = original(self)
        if self.stream is None and self.dotenv_path is not None and _c138_is_real_env_file(self.dotenv_path):
            return type(values)((k, v) for k, v in values.items() if not _c138_is_hidden(k))
        return values

    dict_without_secrets._c138_filtered = True
    dotenv_class.dict = dict_without_secrets


class _C138DotenvFinder(object):
    """A meta path finder that only watches for dotenv.main: the module loads as it would have (found by the
    other finders), then gets the filter. So a child that never imports python-dotenv never has it in
    sys.modules (what a test of a module's own imports checks), and one without python-dotenv is unaffected."""

    def find_spec(self, name, path=None, target=None):
        if name != "dotenv.main":
            return None
        spec = None
        for finder in list(_c138_sys.meta_path):
            if finder is self or not hasattr(finder, "find_spec"):
                continue
            spec = finder.find_spec(name, path, target)
            if spec is not None:
                break
        loader = getattr(spec, "loader", None)
        exec_module = getattr(loader, "exec_module", None)
        if exec_module is None or getattr(exec_module, "_c138_filtered", False):
            return spec

        def exec_and_filter(module):
            exec_module(module)
            _c138_patch_dotenv(module)

        exec_and_filter._c138_filtered = True
        try:
            loader.exec_module = exec_and_filter  # this spec's own loader instance
        except (AttributeError, TypeError):
            return spec
        return spec


def _c138_filter_dotenv():
    already = _c138_sys.modules.get("dotenv.main")
    if already is not None:
        _c138_patch_dotenv(already)
        return
    _c138_sys.meta_path.insert(0, _C138DotenvFinder())


def _c138_with_site_dir(env):
    base = dict(_c138_os.environ if env is None else env)
    if any(not isinstance(name, str) for name in base):
        return env
    key = next((n for n in base if n.upper() == "PYTHONPATH"), "PYTHONPATH") if _c138_os.name == "nt" \\
        else "PYTHONPATH"
    parts = [p for p in (base.get(key) or "").split(_c138_os.pathsep) if p]
    wanted = _c138_os.path.normcase(_C138_SITE_DIR)
    if not any(_c138_os.path.normcase(p) == wanted for p in parts):
        base[key] = _c138_os.pathsep.join(parts + [_C138_SITE_DIR])
    return base


def _c138_wrap_popen():
    import functools
    import inspect
    import subprocess
    original = subprocess.Popen.__init__
    signature = inspect.signature(original)

    @functools.wraps(original)
    def init(self, *args, **kwargs):
        try:
            bound = signature.bind(self, *args, **kwargs)
        except TypeError:
            original(self, *args, **kwargs)
            return
        bound.arguments["env"] = _c138_with_site_dir(bound.arguments.get("env"))
        original(*bound.args, **bound.kwargs)

    subprocess.Popen.__init__ = init


def _c138_chain():
    """Runs the sitecustomize this file shadows (one later on sys.path), as Python would have."""
    import importlib.machinery
    import importlib.util
    here = _c138_os.path.normcase(_C138_SITE_DIR)
    rest = [p for p in _c138_sys.path if _c138_os.path.normcase(_c138_os.path.abspath(p or ".")) != here]
    spec = importlib.machinery.PathFinder.find_spec("sitecustomize", rest)
    if spec is None or spec.loader is None:
        return
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as err:  # site.py's own report for a failing sitecustomize
        _c138_sys.stderr.write("Error in sitecustomize; set PYTHONVERBOSE for traceback:\\n%s: %s\\n"
                               % (type(err).__name__, err))


_c138_filter_dotenv()
_c138_wrap_popen()
_c138_chain()
'''

# One folder per pytest process, so two test runs at the same time never rewrite each other's file.
_CHILD_SITE_DIR = Path(_TEST_LOG_DIR) / "child_site" / str(os.getpid())


def _write_child_site() -> None:
    """Writes the children's sitecustomize.py (removed again when this process exits)."""
    _CHILD_SITE_DIR.mkdir(parents=True, exist_ok=True)
    source = _CHILD_SITECUSTOMIZE.format(
        root=str(_PROJECT_ROOT),
        names=sorted(name.upper() for name in (*SECRET_ENV_NAMES, *EXTRA_SECRET_NAMES, *_PRIVATE_SETTING_NAMES)),
        mode_names=sorted(_TEST_MODE_NAMES),
        pattern=SECRET_NAME_RE.pattern, flags=int(SECRET_NAME_RE.flags))
    (_CHILD_SITE_DIR / "sitecustomize.py").write_text(source, encoding="utf-8")
    atexit.register(shutil.rmtree, _CHILD_SITE_DIR, ignore_errors=True)


def _child_env(env: Any) -> Any:
    """The env a child process gets: env (a copy of os.environ when None) with _CHILD_SITE_DIR appended to
    its PYTHONPATH, so a Python child runs the dotenv filter. Nothing else is added, removed or changed."""
    base = dict(os.environ if env is None else env)
    if any(not isinstance(name, str) for name in base):
        return env  # a bytes environment (POSIX only): passed on as given
    key = "PYTHONPATH"
    if os.name == "nt":  # Windows names are case-insensitive: extend the caller's own spelling
        key = next((name for name in base if name.upper() == "PYTHONPATH"), key)
    site_dir = str(_CHILD_SITE_DIR)
    parts = [part for part in (base.get(key) or "").split(os.pathsep) if part]
    if not any(os.path.normcase(part) == os.path.normcase(site_dir) for part in parts):
        base[key] = os.pathsep.join([*parts, site_dir])
    return base


@functools.wraps(_ORIGINAL_POPEN_INIT)  # keeps the signature visible (__wrapped__) for net_guard and others
def _popen_init(self: subprocess.Popen, *args: Any, **kwargs: Any) -> None:
    """subprocess.Popen.__init__ with _child_env applied to its env (every subprocess of the test run)."""
    try:
        bound = _POPEN_SIGNATURE.bind(self, *args, **kwargs)
    except TypeError:
        _ORIGINAL_POPEN_INIT(self, *args, **kwargs)  # Popen raises its own error for a wrong call
        return
    bound.arguments["env"] = _child_env(bound.arguments.get("env"))
    _ORIGINAL_POPEN_INIT(*bound.args, **bound.kwargs)


def _fake_secret(name: str) -> str:
    """A throwaway value for a secret name: generated for this run, and named so nobody takes it for a key."""
    return f"test-only-fake-{name.lower().replace('_', '-')}-{secrets.token_hex(16)}"


def _pin_test_mode() -> list[str]:
    """Development mode for the whole session (see _TEST_MODE_SETTINGS); returns the unset names (names only)."""
    os.environ.update(_TEST_MODE_SETTINGS)
    removed = sorted(name for name in os.environ if name.upper() in _TEST_MODE_UNSET)
    for name in removed:
        os.environ.pop(name, None)
    return removed


HIDDEN_SECRET_NAMES: frozenset[str] = frozenset() if LIVE_PROBES else _hidden_secret_names()

if not LIVE_PROBES:
    for _dotenv_module in (dotenv, dotenv.main):
        _dotenv_module.load_dotenv = _test_load_dotenv  # type: ignore[assignment]
        _dotenv_module.dotenv_values = _test_dotenv_values  # type: ignore[assignment]
    _scrub_secret_environ(HIDDEN_SECRET_NAMES)
    _write_child_site()
    subprocess.Popen.__init__ = _popen_init  # type: ignore[method-assign]

    # Throwaway credentials for the names a module needs at import or validation time: the bridge
    # (config.validate('bridge') when src/core/ui_bridge.py is imported) asks for the LiveKit keys and, while
    # the legacy token is on, VORTEX_SECURITY_TOKEN, which also keys the legacy notify signature
    # (jarvis_security). Generated per run and named test-only-fake-..., so they match no real key and never
    # come from a real env file. A test that needs other values (or none) sets or deletes them with monkeypatch.
    os.environ.update({name: _fake_secret(name) for name in _TEST_FAKE_SECRET_NAMES})

# Pinned for every run, also with the live probes: the test-session mode (development; see _TEST_MODE_SETTINGS)
# and the LiveKit URLs at a closed local port (as UI_BRIDGE_URL above), so a LiveKit call that a test leaves
# unmocked fails fast instead of reaching the live server.
_pin_test_mode()
os.environ.update({
    "LIVEKIT_URL": "ws://127.0.0.1:9",
    "LIVEKIT_PUBLIC_URL": "ws://127.0.0.1:9",
})

# ------------------------------------------------------------------ C-138 (2) and (3): no live log writes

_LIVE_LOG_DIR = _PROJECT_ROOT / "logs"
_LIVE_LOG_PREFIX = os.path.normcase(str(_LIVE_LOG_DIR)) + os.sep
_REDIRECT_DIR = Path(_TEST_LOG_DIR) / "live_logs_redirect"
_STRICT_LOGS_ENV = "JARVIS_TESTS_STRICT_LOGS"
_live_log_writes: list[tuple[str, str, str, str]] = []  # (event, path, test, code line); no content
_guard_active = [False]


def _live_log_relpath(path: Any) -> str | None:
    """path relative to the live logs/ folder, or None when it is not inside it."""
    try:
        full = os.path.normcase(os.path.abspath(os.fsdecode(os.fspath(path))))
    except (TypeError, ValueError):
        return None
    if not full.startswith(_LIVE_LOG_PREFIX):
        return None
    return full[len(_LIVE_LOG_PREFIX):]


def _redirect_target(path: Any) -> str | None:
    relative = _live_log_relpath(path)
    if relative is None:
        return None
    target = _REDIRECT_DIR / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    return str(target)


_ORIGINAL_FILEHANDLER_OPEN = logging.FileHandler._open  # pylint: disable=protected-access


def _filehandler_open(self: logging.FileHandler) -> Any:
    """A FileHandler naming a live log opens the same relative path under the temp folder instead."""
    target = _redirect_target(self.baseFilename)
    if target is None:
        return _ORIGINAL_FILEHANDLER_OPEN(self)
    return open(target, self.mode, encoding=self.encoding, errors=self.errors)  # pylint: disable=consider-using-with


def _guard_rotation(cls: type) -> None:
    """Rotation of a redirected handler would rename the live files (baseFilename): it is switched off."""
    original_should = cls.shouldRollover
    original_do = cls.doRollover

    def should_rollover(self: Any, record: logging.LogRecord) -> bool:
        if _live_log_relpath(self.baseFilename) is not None:
            return False
        return original_should(self, record)

    def do_rollover(self: Any) -> None:
        if _live_log_relpath(self.baseFilename) is not None:
            return
        original_do(self)

    cls.shouldRollover = should_rollover
    cls.doRollover = do_rollover


logging.FileHandler._open = _filehandler_open  # type: ignore[method-assign]  # pylint: disable=protected-access
_guard_rotation(logging.handlers.RotatingFileHandler)
_guard_rotation(logging.handlers.TimedRotatingFileHandler)

_ORIGINAL_OPEN = builtins.open


def _test_open(file: Any, mode: str = "r", buffering: int = -1, encoding: str | None = None,
               errors: str | None = None, newline: str | None = None, closefd: bool = True,
               opener: Any = None) -> Any:
    """open() for the whole test process. A file under the live logs/ that code opens for writing (a module's
    own sink, such as keyboard_mouse_ctrl's control_log.txt) is the same relative path under the temp folder
    instead; reading it reads that copy once a test wrote it, else the live file. Everything else is open."""
    relative = None if isinstance(file, int) else _live_log_relpath(file)
    if relative is not None:
        target = _REDIRECT_DIR / relative
        if isinstance(mode, str) and any(c in mode for c in "wax+"):
            target.parent.mkdir(parents=True, exist_ok=True)
            file = str(target)
        elif target.is_file():
            file = str(target)
    return _ORIGINAL_OPEN(file, mode, buffering, encoding, errors, newline, closefd, opener)


# builtins.open serves open(); io.open serves pathlib's Path.open / write_text / read_text.
builtins.open = _test_open
io.open = _test_open

_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
_WATCHED_EVENTS = frozenset({"open", "os.rename", "os.remove", "os.truncate", "os.rmdir", "shutil.rmtree"})


def _writes(event: str, args: tuple[Any, ...]) -> list[Any]:
    """The paths an audited event writes, renames, truncates or removes."""
    if event == "open":
        path, mode, flags = (tuple(args) + (None, None, None))[:3]
        if isinstance(path, int):
            return []
        if isinstance(mode, str):
            return [path] if any(c in mode for c in "wax+") else []
        return [path] if isinstance(flags, int) and flags & _WRITE_FLAGS else []
    if event == "os.rename":
        return [arg for arg in args[:2] if not isinstance(arg, int)]
    return [args[0]] if args and not isinstance(args[0], int) else []


def _code_line() -> str:
    """The innermost project frame outside this file (file:line), never a value."""
    frame = sys._getframe(2)  # pylint: disable=protected-access
    root = os.path.normcase(str(_PROJECT_ROOT)) + os.sep
    here = os.path.normcase(os.path.abspath(__file__))
    while frame is not None:
        filename = os.path.normcase(os.path.abspath(frame.f_code.co_filename))
        if filename.startswith(root) and filename != here and ".venv" not in filename:
            return f"{os.path.relpath(filename, root)}:{frame.f_lineno}"
        frame = frame.f_back
    return "?"


def _live_log_audit_hook(event: str, args: tuple[Any, ...]) -> None:
    # Runs for every audited event of this process: the fast path returns at once, and nothing here may
    # raise (an exception would make the audited call itself fail).
    if event not in _WATCHED_EVENTS or not _guard_active[0]:
        return
    try:
        for path in _writes(event, args):
            relative = _live_log_relpath(path)
            if relative is not None:
                test = (os.environ.get("PYTEST_CURRENT_TEST") or "collection or session setup").split(" (")[0]
                _live_log_writes.append((event, "logs" + os.sep + relative, test, _code_line()))
    except Exception:  # pylint: disable=broad-exception-caught
        return


def _log_sizes() -> dict[str, int]:
    """Size of every file under the live logs/ (names only)."""
    sizes: dict[str, int] = {}
    if not _LIVE_LOG_DIR.is_dir():
        return sizes
    for dirpath, _dirs, files in os.walk(_LIVE_LOG_DIR):
        for name in files:
            full = os.path.join(dirpath, name)
            try:
                sizes[os.path.relpath(full, _LIVE_LOG_DIR)] = os.path.getsize(full)
            except OSError:
                continue
    return sizes


def _live_worker_running() -> bool:
    """True while the owner's JARVIS worker or Desktop Node (run/*.json pid, a python process) is alive.
    Reads the pid files only; never takes a lock."""
    try:
        import json  # pylint: disable=import-outside-toplevel
        import psutil  # pylint: disable=import-outside-toplevel
    except ImportError:
        return True  # cannot tell: report growth instead of failing on it
    for info_file in ("agent.pid.json", "desktop_actor.json"):
        try:
            pid = int(json.loads((_PROJECT_ROOT / "run" / info_file).read_text(encoding="utf-8"))["pid"])
            if pid != os.getpid() and "python" in psutil.Process(pid).name().lower():
                return True
        except Exception:  # pylint: disable=broad-exception-caught  # no file, stale pid, no access
            continue
    return False


# Files under logs/ that the owner's running worker (agent_ctl's stdout/stderr capture, jarvis_logger,
# telemetry, jarvis_audit, keyboard_mouse_ctrl, livekit_link) and the Desktop Node with its supervisor write
# themselves, each with its rotations (name.1, name.<date>). agent_ctl.log is not one of them: only an
# agent_ctl start/stop/restart writes it, which no test may run against the live tree.
_RUNTIME_LOG_NAMES = frozenset({
    "agent_stdout.log", "agent_stderr.log", "jarvis.log", "jarvis_default.log", "jarvis_production.log",
    "jarvis_hybrid_local.log", "bridge.log", "runner.log", "telemetry.jsonl", "security.log", "control_log.txt",
    "agent_recovery.log", "desktop_audit.jsonl", "desktop_node.log", "desktop_node_tools.log",
    "desktop_supervisor.log", "desktop_node.stdio.log",
})


def _runtime_log_files() -> frozenset[str]:
    """_RUNTIME_LOG_NAMES plus the LOG_FILE that the worker's env files name under logs/ (normcased)."""
    names = {os.path.normcase(name) for name in _RUNTIME_LOG_NAMES}
    for env_file in (_PROJECT_ROOT / ".env", _PROJECT_ROOT / "deploy" / "windows-local" / ".env"):
        try:
            value = (_ORIGINAL_DOTENV_VALUES(env_file).get("LOG_FILE") or "").strip() if env_file.is_file() else ""
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        if value:
            relative = _live_log_relpath(value if os.path.isabs(value) else _PROJECT_ROOT / value)
            if relative is not None:
                names.add(relative)
    return frozenset(names)


_RUNTIME_LOG_FILES = _runtime_log_files()


def _is_runtime_log(relative: str) -> bool:
    """True for a file (path relative to logs/) that a running worker or node writes itself."""
    name = os.path.normcase(relative)
    return any(name == base or name.startswith(base + ".") for base in _RUNTIME_LOG_FILES)


_LOG_SIZES_AT_START = _log_sizes()
sys.addaudithook(_live_log_audit_hook)
_guard_active[0] = True

# Add subdirectories to path to allow direct imports in tests
service_dirs = [
    "services/automation",
    "services/multimedia",
    "services/info",
    "services/system",
    "services/ai_core",
    "services/utils",
    "services/connectivity",
    "services/vision",
    "ui"  # In case there's a ui dir
]

for d in service_dirs:
    full_path = str(project_root / d)
    if full_path not in sys.path and os.path.exists(full_path):
        sys.path.append(full_path)

from services.utils.jarvis_logger import setup_logger
from services.utils.jarvis_secure_config import SecureConfigManager

# Test logger
logger = setup_logger("JARVIS-TESTS")


@pytest.fixture(scope="session")
def test_config():
    """Test configuration fixture"""
    return {
        "test_user_name": "Test User",
        "test_api_key": "test_api_key_12345",
        "test_livekit_url": "wss://test.livekit.cloud",
        "temp_dir": tempfile.mkdtemp(prefix="jarvis_test_")
    }


@pytest.fixture
def temp_dir():
    """Temporary directory fixture"""
    temp_path = tempfile.mkdtemp(prefix="jarvis_test_")
    yield temp_path
    shutil.rmtree(temp_path, ignore_errors=True)


@pytest.fixture
def mock_env_file(temp_dir):
    """Create a mock .env file for testing"""
    env_content = """
LIVEKIT_API_KEY=test_livekit_key
LIVEKIT_API_SECRET=test_livekit_secret
LIVEKIT_URL=wss://test.livekit.cloud
GOOGLE_API_KEY=test_google_key
WEATHER_API_KEY=test_weather_key
USER_NAME=Test User
CONTROLLER_TOKEN=test_token
TAVILY_API_KEY=test_tavily_key
"""
    env_file = os.path.join(temp_dir, ".env")
    with open(env_file, "w") as f:
        f.write(env_content.strip())
    return env_file


@pytest.fixture
def encrypted_env_file(mock_env_file):
    """Create encrypted .env file for testing"""
    from services.utils.jarvis_crypto import JarvisCrypto
    
    crypto = JarvisCrypto()
    encrypted_file = crypto.encrypt_env_file(mock_env_file)
    return encrypted_file


@pytest.fixture
def secure_config_manager(encrypted_env_file):
    """Secure config manager fixture with encrypted env"""
    return SecureConfigManager(encrypted_env_file)


@pytest.fixture
def mock_livekit_agent():
    """Mock LiveKit agent fixture"""
    agent = Mock()
    agent.session = Mock()
    agent.say = Mock()
    agent.listen = Mock()
    return agent


@pytest.fixture
def mock_google_llm():
    """Mock Google LLM fixture"""
    llm = Mock()
    llm.chat = Mock()
    llm.ainfer = Mock()
    return llm


@pytest.fixture
def sample_audio_file(temp_dir):
    """Create a sample audio file for testing"""
    import wave
    import struct
    
    audio_file = os.path.join(temp_dir, "test_audio.wav")
    
    # Create a simple WAV file
    with wave.open(audio_file, 'w') as wav_file:
        wav_file.setnchannels(1)  # Mono
        wav_file.setsampwidth(2)  # 16-bit
        wav_file.setframerate(16000)  # 16kHz
        
        # Generate simple sine wave
        duration = 1.0  # 1 second
        frequency = 440  # A4 note
        samples = int(duration * 16000)
        
        for i in range(samples):
            value = int(32767 * 0.5 * (i % 100 < 50))  # Simple square wave
            wav_file.writeframes(struct.pack('<h', value))
    
    return audio_file


@pytest.fixture
def mock_memory_store():
    """Mock memory store fixture"""
    memory_store = Mock()
    memory_store.add = Mock()
    memory_store.get = Mock()
    memory_store.search = Mock()
    memory_store.delete = Mock()
    return memory_store


@pytest.fixture
def mock_plugin_manager():
    """Mock plugin manager fixture"""
    plugin_manager = Mock()
    plugin_manager.load_plugins = Mock()
    plugin_manager.execute_tool = Mock()
    plugin_manager.get_available_tools = Mock(return_value=[])
    return plugin_manager


@pytest.fixture(autouse=True)
def mock_environment_variables(monkeypatch):
    """Mock environment variables for all tests"""
    test_env = {
        "JARVIS_ENCRYPTION_KEY": "MTIzNDU2Nzg5MDEyMzQ1Njc4OTAxMjM0NTY3ODkwMTI=",
        "PYTHONPATH": str(project_root),
        "JARVIS_TEST_MODE": "true"
    }
    
    for key, value in test_env.items():
        monkeypatch.setenv(key, value)


@pytest.fixture(autouse=True)
def _vector_memory_never_loads_for_real(monkeypatch):
    """
    C-137: the vector memory starts in a background thread that outlives a test's event loop. A test
    that reaches the global jarvis_vector_db unmocked (a conversation save, the agent entrypoint) would
    load the real sentence-transformers model (model hub requests included) and open the project's
    chroma_db; here that start fails at once instead, as when the memory is unavailable. Tests of
    VectorMemory itself build their own instances.
    """
    import concurrent.futures  # pylint: disable=import-outside-toplevel

    failed: concurrent.futures.Future[bool] = concurrent.futures.Future()
    failed.set_result(False)
    for name in ("services.ai_core.jarvis_vector_memory", "jarvis_vector_memory"):
        module = sys.modules.get(name)
        if module is not None and hasattr(module, "jarvis_vector_db"):
            # In the instance's own dict, so the undo leaves no attribute behind.
            monkeypatch.setitem(vars(module.jarvis_vector_db), "prewarm", lambda: failed)


@pytest.fixture(autouse=True)
def _fresh_ui_bridge_agent_status(monkeypatch):
    """
    C-23: the bridge emits agent_status only when it changed, so it remembers the last one it emitted, and it
    remembers when the last UI socket left. Every test starts with neither, whatever an earlier test emitted (a
    module imported for the first time inside a test starts fresh anyway).
    """
    module = sys.modules.get("src.core.ui_bridge")
    if module is not None and hasattr(module, "AgentStatusTracker"):
        monkeypatch.setattr(module, "_status_tracker", module.AgentStatusTracker())
        monkeypatch.setattr(module, "_ui_last_seen", None)


# Module singletons that write a fixed file under the live logs/ with open() (no logging handler).
_LIVE_SINKS: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("services.utils.jarvis_telemetry", "jarvis_telemetry"), "telemetry", "log_path"),
)


def _redirect_live_sinks() -> None:
    for module_names, singleton, attribute in _LIVE_SINKS:
        for module_name in module_names:
            sink = getattr(sys.modules.get(module_name), singleton, None)
            path = getattr(sink, attribute, None)
            if path is not None and _live_log_relpath(path) is not None:
                setattr(sink, attribute, Path(_redirect_target(path) or path))


def _telemetry_writes_to_tmp() -> None:
    """A JarvisTelemetry built on the default logs/telemetry.jsonl writes that name under the temp folder."""
    from services.utils import jarvis_telemetry  # pylint: disable=import-outside-toplevel

    original_init = jarvis_telemetry.JarvisTelemetry.__init__

    def init(self: Any, log_path: Any = "logs/telemetry.jsonl") -> None:
        original_init(self, _redirect_target(log_path) or log_path)

    jarvis_telemetry.JarvisTelemetry.__init__ = init  # type: ignore[method-assign]
    _redirect_live_sinks()


_telemetry_writes_to_tmp()


@pytest.fixture(autouse=True)
def _live_sinks_go_to_tmp():
    """
    C-138: the telemetry singleton appends every finished interaction to logs/telemetry.jsonl; in tests
    (also for a copy of the module imported under its bare name, or one a test reloads) it writes the same
    file name under the temp folder instead. Kept for the rest of the session: no test may write the live
    file, and a test that wants the telemetry file builds its own JarvisTelemetry(tmp_path / ...).
    """
    _redirect_live_sinks()
    yield


@pytest.fixture
def mock_http_client():
    """Mock HTTP client fixture"""
    import httpx
    
    # Create mock response
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"status": "success"}
    mock_response.text = "Success"
    
    # Mock client
    mock_client = Mock(spec=httpx.Client)
    mock_client.get.return_value = mock_response
    mock_client.post.return_value = mock_response
    
    return mock_client


# Test markers
pytest_plugins = []

def pytest_configure(config):
    """Configure pytest with custom markers"""
    config.addinivalue_line(
        "markers", "unit: mark test as a unit test"
    )
    config.addinivalue_line(
        "markers", "integration: mark test as an integration test"
    )
    config.addinivalue_line(
        "markers", "slow: mark test as slow running"
    )
    config.addinivalue_line(
        "markers", "security: mark test as security-related"
    )
    config.addinivalue_line(
        "markers", "audio: mark test as audio-related"
    )
    config.addinivalue_line(
        "markers", "llm: mark test as LLM-related"
    )


def pytest_collection_modifyitems(config, items):
    """Modify test collection to add markers automatically"""
    for item in items:
        # Add markers based on test location
        if "unit" in str(item.fspath):
            item.add_marker(pytest.mark.unit)
        elif "integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)
        
        # Add markers based on test name
        if "security" in item.name.lower():
            item.add_marker(pytest.mark.security)
        if "audio" in item.name.lower():
            item.add_marker(pytest.mark.audio)
        if "llm" in item.name.lower() or "google" in item.name.lower():
            item.add_marker(pytest.mark.llm)


# ------------------------------------------------------------------ C-138 (3): the session-end check

_live_log_report: list[str] = []


def _live_log_findings() -> tuple[list[str], list[tuple[str, str]]]:
    """(this process's writes under logs/, (name, change) of each file under logs/ whose size changed); names
    and sizes only, no content."""
    writes = [f"{event} {path} by {test} at {code}"
              for event, path, test, code in dict.fromkeys(_live_log_writes)]
    end = _log_sizes()
    changed = []
    for name in sorted(set(_LOG_SIZES_AT_START) | set(end)):
        before, after = _LOG_SIZES_AT_START.get(name), end.get(name)
        if before != after:
            changed.append((name, f"logs{os.sep}{name}: {'-' if before is None else before} -> "
                                  f"{'removed' if after is None else after} bytes"))
    return writes, changed


def _failing_growth(changed: list[tuple[str, str]], strict: bool, worker_running: bool) -> list[str]:
    """The changed files that fail the run: all of them without a running worker (or when strict), else only
    the files that the worker and node do not write themselves."""
    if strict or not worker_running:
        return [line for _name, line in changed]
    return [line for name, line in changed if not _is_runtime_log(name)]


def pytest_sessionfinish(session, exitstatus):
    """C-138: fails the run when this pytest process wrote under the live logs/, or when a file there changed
    size that no running JARVIS worker or node writes (any file while none runs; any file with
    JARVIS_TESTS_STRICT_LOGS=1)."""
    _guard_active[0] = False
    writes, changed = _live_log_findings()
    strict = os.environ.get(_STRICT_LOGS_ENV, "").strip() == "1"
    failing = _failing_growth(changed, strict, bool(changed) and not strict and _live_worker_running())
    reported = [line for _name, line in changed if line not in failing]
    _live_log_report.clear()
    if writes:
        _live_log_report.append(f"{len(writes)} write(s) by this test run under the live logs/ (FAILED):")
        _live_log_report.extend(f"  {line}" for line in writes[:50])
    if failing:
        _live_log_report.append(f"{len(failing)} file(s) under logs/ changed during the run (FAILED):")
        _live_log_report.extend(f"  {line}" for line in failing[:50])
    if reported:
        _live_log_report.append(f"{len(reported)} file(s) under logs/ changed during the run (not failed: the "
                                "live JARVIS worker or node is running and writes these itself):")
        _live_log_report.extend(f"  {line}" for line in reported[:50])
    if (writes or failing) and exitstatus == pytest.ExitCode.OK:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(terminalreporter, exitstatus, config):  # pylint: disable=unused-argument
    """Prints the C-138 live logs/ findings (file names and sizes only)."""
    if _live_log_report:
        terminalreporter.write_sep("=", "C-138 live logs/ check")
        for line in _live_log_report:
            terminalreporter.write_line(line)
