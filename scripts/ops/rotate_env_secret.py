"""Replace one secret (or another one-line value) in PC env files and a VPS env file without printing it
(plan task C-14, contract C-ENV-1 / K-47).

Usage (from the repo root, with the project venv):
    .venv_312/Scripts/python.exe scripts/ops/rotate_env_secret.py --name KEY [--alias KEY2]
        [--pc-files .env,deploy/windows-local/.env] [--vps-file /opt/jarvis-agent/deploy/hostinger/bridge.env]
        [--value-from-stdin] [--dry-run] [--host H] [--ssh-key K]

--name KEY          the key to replace. Its new value is generated (AGENT_TOKEN token_urlsafe(48),
                    BRIDGE_HMAC_SECRET token_hex(32), SESSION_JWT_SECRET token_urlsafe(64), any other key
                    token_urlsafe(32)) unless --value-from-stdin reads it: one line, piped in (from a file or a
                    password manager) or typed into a console that hides it (PowerShell, cmd, a POSIX terminal).
                    Git Bash's mintty shows typed text, so typing the value there is refused: pipe it in, or put
                    'winpty' in front of the command. Use stdin for a value that is not a random secret, e.g.
                    LIVEKIT_URL.
--alias KEY2        KEY2 gets the same value in every file that already has a KEY2 value.
--pc-files a,b      local env files, comma-separated. Every assignment of KEY is replaced in place (its 'KEY='
                    part and line ending stay; an inline comment on that line is dropped), or KEY is appended.
--vps-file PATH     an env file on the VPS, edited over ssh: the value travels on ssh stdin (never on a command
                    line) and the file is written mode 600 under umask 077.
Order: each PC file is first written to a temp file next to it (a read-only PC file stops the run on Windows), then
the VPS file is written, then the temp files are renamed over the PC files (atomic; mode 600 on POSIX). Every other
line stays byte for byte. Nothing is written anywhere when a temp file cannot be written, and nothing on this PC
when the VPS step fails. If a rename still fails after the VPS step, the error says that the VPS file already holds
the new value (with its fingerprint): fix the cause and run the same command again.
Prints 'KEY rotated in N files' with the value's length and fingerprint (sha256 of 'jarvis-fp:' + value, first
8 hex); never the value. Afterwards restart what reads the key (the bridge: deploy_from_pc.sh or
'docker compose -p jarvis up -d --no-deps ui-bridge'; the PC agent: agent_ctl restart).
Back up first: scripts/ops/backup_local.py --now (PC) and deploy/hostinger/backup_vps.sh (VPS).
Exit codes: 0 ok, 1 the VPS step failed (nothing written on this PC) or a PC file could not be replaced after the
VPS file was written, 2 usage or file error (nothing written).
"""
from __future__ import annotations

import argparse
import getpass
import importlib.util
import os
import re
import sys
from pathlib import Path
from typing import Any, TextIO

# scripts/ops is not a package: gen_secrets.py (which loads env_set.py) is loaded by its path.
_GEN_SECRETS_SPEC = importlib.util.spec_from_file_location("jarvis_ops_gen_secrets",
                                                           Path(__file__).with_name("gen_secrets.py"))
assert _GEN_SECRETS_SPEC is not None and _GEN_SECRETS_SPEC.loader is not None
gen_secrets = importlib.util.module_from_spec(_GEN_SECRETS_SPEC)
_GEN_SECRETS_SPEC.loader.exec_module(gen_secrets)
env_set = gen_secrets.env_set
EnvError = env_set.EnvError

# Git Bash's mintty (like Cygwin's) gives a Windows program named pipes instead of a console, so isatty() is False
# while the terminal shows what is typed. The pipes are named \msys-<key>-pty<N>-from-master (or cygwin-, -to-, -nat).
_MSYS_PTY = re.compile(r"(?:\\Device\\NamedPipe)?\\(?:msys|cygwin)-[^-\\]+-pty[^-\\]*-(?:from|to)-master(?:-[^\\]*)?")


def replace_in(env_file: Any, key: str, token: str) -> str:
    """Puts key=token into an env_set.EnvFile: over every assignment of key (keeping its 'KEY=' part and line
    ending), else appended. Returns 'replaced' or 'added'."""
    pattern = re.compile(rf"(\s*(?:export[ \t]+)?{re.escape(key)}[ \t]*=)[^\r\n]*(\r\n|\n|\r)?")
    hits = [i for i, name in enumerate(env_file.keys) if name == key]
    for i in hits:
        match = pattern.fullmatch(env_file.chunks[i])
        if not match:
            raise EnvError(f"{env_file.label}: {key} spans several lines; edit that file by hand")
        env_file.chunks[i] = match.group(1) + token + (match.group(2) or "")
    if hits:
        return "replaced"
    env_file.appended.append(f"{key}={token}")
    return "added"


def _pipe_name(stream: TextIO) -> str:
    """The name of the Windows pipe behind stream; '' when it is not a pipe. Windows only."""
    import ctypes  # pylint: disable=import-outside-toplevel
    import msvcrt  # pylint: disable=import-outside-toplevel,import-error
    from ctypes import wintypes  # pylint: disable=import-outside-toplevel

    class FileNameInfo(ctypes.Structure):  # FILE_NAME_INFO with room for the name
        _fields_ = [("length", wintypes.DWORD), ("name", wintypes.WCHAR * 512)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetFileType.argtypes = [wintypes.HANDLE]
    kernel32.GetFileType.restype = wintypes.DWORD
    kernel32.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL
    file_type_pipe, file_name_info = 3, 2
    handle = msvcrt.get_osfhandle(stream.fileno())
    info = FileNameInfo()
    if kernel32.GetFileType(handle) != file_type_pipe or not kernel32.GetFileInformationByHandleEx(
            handle, file_name_info, ctypes.byref(info), ctypes.sizeof(info)):
        return ""
    return info.name[:info.length // 2]


def echoing_terminal(stream: TextIO) -> bool:
    """True when stream is a Git Bash (mintty) or Cygwin terminal, which shows typed text although isatty() is
    False. False for anything else, and when the check itself fails."""
    if os.name != "nt":
        return False
    try:
        return bool(_MSYS_PTY.fullmatch(_pipe_name(stream)))
    except (AttributeError, OSError, ValueError):  # no OS file behind stream (io.UnsupportedOperation is both)
        return False


def read_value(name: str, stream: TextIO) -> str:
    """One line from stdin, without its line ending and surrounding spaces: hidden while typed into a console, else
    read as it arrives (piped in). Typing it into Git Bash's mintty, which would show it, is refused."""
    isatty = getattr(stream, "isatty", None)
    if callable(isatty) and isatty():
        return getpass.getpass(f"{name} (not shown): ").strip()
    if echoing_terminal(stream):
        raise EnvError(f"{name}: this terminal (Git Bash mintty) would show the value as it is typed; pipe it in "
                       "(from a file or a password manager) or put 'winpty' in front of the command")
    return stream.readline().strip()


class _Parser(env_set._Parser):  # pylint: disable=protected-access
    """argparse without echoing typed text (it could be a value)."""


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = _Parser(prog="rotate_env_secret.py",
                     description="Replace one key in PC env files and a VPS env file without printing its value.")
    parser.add_argument("--name", required=True, metavar="KEY", help="the key to replace")
    parser.add_argument("--alias", metavar="KEY2", help="also set KEY2 where a file already has it")
    parser.add_argument("--pc-files", metavar="A,B", help="local env files, comma-separated")
    parser.add_argument("--vps-file", metavar="PATH", help="env file on the VPS (edited over ssh)")
    parser.add_argument("--value-from-stdin", action="store_true",
                        help="read the new value from stdin: piped in, or typed into a console that hides it "
                             "(not Git Bash's mintty; use winpty there)")
    parser.add_argument("--dry-run", action="store_true", help="report what would change; write nothing")
    parser.add_argument("--host", default=gen_secrets.VPS_HOST, help=f"ssh target (default {gen_secrets.VPS_HOST})")
    parser.add_argument("--ssh-key", type=Path, default=gen_secrets.VPS_KEY, help="ssh identity file")
    args = parser.parse_args(argv)
    for option in ("name", "alias"):
        value = getattr(args, option)
        if value is not None and not env_set.KEY_RE.fullmatch(value):
            parser.error(f"--{option}: not a valid KEY name")
    if args.alias == args.name:
        parser.error("--alias must differ from --name")
    if not (args.pc_files or args.vps_file):
        parser.error("give --pc-files and/or --vps-file")
    return args


def _vps_rotate(args: argparse.Namespace, value: str, runner: Any) -> tuple[int, list[str], bool]:
    """(exit code, report lines, whether --name was set there) for the --vps-file step."""
    ops = [{"op": "replace", "key": args.name, "value": value}]
    if args.alias:
        ops.append({"op": "replace", "key": args.alias, "value": value, "only_present": True})
    remote = {"target": args.vps_file, "source": None, "dry_run": args.dry_run, "ops": ops}
    code, out, err = runner(gen_secrets.remote_program(), remote, args.host, args.ssh_key)
    report, status = gen_secrets.describe(out)
    if not status or code:
        last = (err.strip().splitlines() or ["no answer"])[-1]
        return 1, report + [f"{args.vps_file}: failed (exit {code}): {last[:200]}"], False
    return 0, report, True


def main(argv: list[str] | None = None, runner: Any = None, stdin: TextIO | None = None) -> int:
    args = parse_args(argv)
    runner = runner or gen_secrets.run_ssh
    staged: Any = []
    try:
        value = read_value(args.name, stdin or sys.stdin) if args.value_from_stdin else gen_secrets.generate(args.name)
        if not value:
            raise EnvError(f"{args.name}: empty value; nothing to set")
        paths = [Path(p.strip()) for p in (args.pc_files or "").split(",") if p.strip()]
        if len({os.path.normcase(str(p.resolve())) for p in paths}) != len(paths):
            raise EnvError("the same file is given twice")
        files = [env_set.EnvFile(path) for path in paths]
        token = env_set.render_value(args.name, value)
        alias_files = 0
        for f in files:
            replace_in(f, args.name, token)
            if args.alias and f.value(args.alias):
                replace_in(f, args.alias, env_set.render_value(args.alias, value))
                alias_files += 1
        fp = gen_secrets.fingerprint(value)
        # Temp files before the VPS step: a PC file that cannot be written stops the run while nothing has changed.
        staged = [] if args.dry_run else gen_secrets.stage_private(files)

        count = len(files)
        if args.vps_file:
            code, report, done = _vps_rotate(args, value, runner)
            for line in report:
                print(f"vps {line}")
            if code:
                print("nothing written on this PC", file=sys.stderr)
                return code
            count += int(done)
            alias_files += sum(1 for line in report if args.alias and line.startswith(f"{args.alias}: replaced"))

        if args.dry_run:
            print(f"dry run: {args.name} would be rotated in {count} files; nothing written on this PC")
        else:
            try:
                written = gen_secrets.commit_private(staged)
            except EnvError as exc:
                if not args.vps_file:
                    raise
                print(f"rotate_env_secret: the VPS file {args.vps_file} already holds the new {args.name} (fp {fp}), "
                      f"but this PC's files were not updated: {exc}. Fix that, then run the same command again.",
                      file=sys.stderr)
                return 1
            if written:
                print(f"written: {', '.join(written)}")
        verb = "would be rotated" if args.dry_run else "rotated"
        print(f"{args.name} {verb} in {count} files (len {len(value)}, fp {fp})")
        if args.alias:
            print(f"{args.alias} {verb} in {alias_files} files (same value)")
        return 0
    except EnvError as exc:
        print(f"rotate_env_secret: {exc}", file=sys.stderr)
        return 2
    finally:
        gen_secrets.discard_private(staged)


if __name__ == "__main__":
    sys.exit(main())
