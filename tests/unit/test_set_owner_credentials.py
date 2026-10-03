"""Plan task C-20: scripts/ops/set_owner_credentials.py (owner enrolment, contract C-AUTH-1 / K-36).

Nothing real is touched: getpass, input, the clock, ssh and HTTP are replaced. A fake ssh runs the exact program and
JSON payload that would go to the VPS in this process against a temporary bridge.env (one test runs it through the
real loader in a fresh Python). Every value here is an obvious throwaway fake, bcrypt runs at cost 4 except in the
one test that checks cost 12, and the round-trip test proves the bridge's own parsers and verifiers accept what the
script writes.
"""
# pylint: disable=redefined-outer-name,protected-access
from __future__ import annotations

import email.message
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

import pyotp
import pytest
from dotenv import dotenv_values

from services.utils import jarvis_config
from src.core import bridge_auth
from src.core.bridge_security import check_origin

ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location("jarvis_ops_test_set_owner_credentials",
                                               ROOT / "scripts" / "ops" / "set_owner_credentials.py")
assert _SPEC is not None and _SPEC.loader is not None
soc = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = soc  # dataclasses look the module up
_SPEC.loader.exec_module(soc)

PASSWORD = "c20 fake owner passphrase"          # throwaway test value
SECRET = "CTWENTYFAKETESTSECRETAAAAAAAAAAA"      # throwaway base32 test secret
START = 1_790_000_000.0                           # fixed fake 'now'
FAKE_JWT = "c20-fake-session-jwt-secret-" + "x" * 40
FAKE_COOKIE = "c20.fake.cookie-value-never-printed"
BRIDGE_ENV = ("# JARVIS bridge (fake)\n"
              f"SESSION_JWT_SECRET={FAKE_JWT}\n"
              "OWNER_PASSWORD_HASH=old-fake-hash\n"
              "LEGACY_TOKEN_AUTH=1\n"
              "export OWNER_TOTP_SECRET = OLDFAKE\n"
              "# OWNER_RECOVERY_HASHES=commented-out-stays\n"
              "ALLOWED_ORIGINS=https://jarvis.srv1802669.hstgr.cloud")      # no final newline on purpose
CODE_RE = re.compile(r"[a-z2-9]{4}-[a-z2-9]{4}-[a-z2-9]{4}")


def code_at(offset_steps: int = 0) -> str:
    return pyotp.TOTP(SECRET).at(START + offset_steps * 30)


def to_arabic_indic(code: str) -> str:
    return "".join(chr(0x0660 + int(d)) for d in code)


def to_extended_arabic(code: str) -> str:   # what an Urdu keyboard types
    return "".join(chr(0x06F0 + int(d)) for d in code)


@pytest.fixture(autouse=True)
def fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(soc, "BCRYPT_COST", 4)
    monkeypatch.setattr(soc.bridge_auth, "new_totp_secret", lambda: SECRET)


class Script:
    """Answers for getpass / input in order; an exception instance in the list is raised (Ctrl+C)."""

    def __init__(self, answers: list[Any]) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    def __call__(self, prompt: str = "") -> str:
        self.prompts.append(prompt)
        if not self.answers:
            raise AssertionError(f"unexpected prompt {prompt!r}")
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


class FakeSSH:
    """Stands in for subprocess.run: records the argv and stdin, then runs the shipped program like the VPS loader
    does, with the VPS paths mapped into tmp_path."""

    def __init__(self, tmp: Path, *, returncode: int | None = None, raise_exc: BaseException | None = None,
                 stderr: str = "") -> None:
        self.tmp = tmp
        self.calls: list[dict[str, Any]] = []
        self.returncode = returncode
        self.raise_exc = raise_exc
        self.stderr = stderr
        self.target = tmp / "bridge.env"
        self.backups = tmp / "jarvis-backups"

    def __call__(self, command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append({"command": command, **kwargs})
        if self.raise_exc is not None:
            raise self.raise_exc
        if self.returncode is not None:
            return subprocess.CompletedProcess(command, self.returncode, "", self.stderr)
        data = json.loads(kwargs["input"])
        args = dict(data["args"])
        args["backup_dir"] = str(self.backups / Path(args["backup_dir"]).name)
        args["target"] = str(self.target)
        out = io.StringIO()
        code = 0
        with redirect_stdout(out):
            try:
                exec(compile(data["program"], "jarvis-c20", "exec"),  # pylint: disable=exec-used
                     {"__name__": "jarvis_c20", "ARGS": args})
            except SystemExit as stop:
                code = int(stop.code or 0)
        return subprocess.CompletedProcess(command, code, out.getvalue(), "")


def cp1252_stream() -> io.TextIOWrapper:
    """A stdout like a redirected Windows console: cp1252, strict."""
    return io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict", newline="\n")


def text_of(stream: io.TextIOWrapper) -> str:
    stream.flush()
    return stream.buffer.getvalue().decode(stream.encoding)  # type: ignore[attr-defined]


def make_deps(tmp: Path, secrets_: list[Any], inputs: list[Any], *, ssh: Any = None, http: Any = None,
              env: dict[str, str] | None = None, out: Any = None) -> Any:
    return soc.Deps(ask_secret=Script(secrets_), ask=Script(inputs), out=out or io.StringIO(),
                    clock=lambda: START, interactive=lambda: True, private_output=lambda: True,
                    ssh_run=ssh or FakeSSH(tmp),
                    http_open=http or (lambda *a, **k: (_ for _ in ()).throw(AssertionError("no HTTP here"))),
                    env=env or {})


def output(deps: Any) -> str:
    stream = deps.out
    return text_of(stream) if isinstance(stream, io.TextIOWrapper) else stream.getvalue()


@pytest.fixture
def chmods(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, int]]:
    seen: list[tuple[str, int]] = []
    real = os.chmod

    def record(path: Any, mode: int, *args: Any, **kwargs: Any) -> None:
        seen.append((str(path), mode))
        real(path, mode, *args, **kwargs)

    monkeypatch.setattr(os, "chmod", record)
    return seen


def enrol_ok(tmp: Path, **kw: Any) -> tuple[int, Any, FakeSSH]:
    tmp.joinpath("bridge.env").write_text(BRIDGE_ENV, encoding="utf-8", newline="")
    ssh = kw.pop("ssh", None) or FakeSSH(tmp)
    deps = make_deps(tmp, [PASSWORD, PASSWORD], [code_at(), ""], ssh=ssh, **kw)
    return soc.main([], deps), deps, ssh


# ---------------------------------------------------------------- password rules


@pytest.mark.parametrize("bad", ["short pass", "password1234", "Jarvis123456", "aaaaaaaaaaaaaaaa", "é" * 40])
def test_password_refused_then_accepted(tmp_path: Path, bad: str) -> None:
    deps = make_deps(tmp_path, [bad, PASSWORD, PASSWORD], [])
    assert soc.ask_password(deps) == PASSWORD
    assert "refused" in output(deps)
    assert bad not in output(deps)


def test_password_mismatch_then_three_strikes(tmp_path: Path) -> None:
    deps = make_deps(tmp_path, [PASSWORD, PASSWORD + "x", "short", PASSWORD, "different but long"], [])
    with pytest.raises(soc.EnrolError, match="nothing was written"):
        soc.ask_password(deps)
    assert "different" in output(deps) and PASSWORD not in output(deps)


def test_password_hash_cost_is_12_and_bridge_format(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.undo()  # the real cost
    assert soc.BCRYPT_COST == 12
    values = soc.make_values(PASSWORD, SECRET, ["abcd-efgh-jkmn"], cost=soc.BCRYPT_COST)
    raw = bridge_auth.decode_bcrypt_hash(values["OWNER_PASSWORD_HASH"])
    assert raw is not None and raw.startswith(b"$2b$12$")
    assert re.fullmatch(r"[A-Za-z0-9_-]+", values["OWNER_PASSWORD_HASH"])  # base64url, no padding


# ---------------------------------------------------------------- QR / URI on a Windows console


def test_qr_on_cp1252_console_falls_back_to_ascii(tmp_path: Path) -> None:
    stream = cp1252_stream()
    deps = make_deps(tmp_path, [], [], out=stream)
    soc.show_authenticator(deps, SECRET)          # must not raise UnicodeEncodeError
    text = text_of(stream)
    assert "##" in text
    assert bridge_auth.provisioning_uri(SECRET) in text
    assert "otpauth://totp/JARVIS:owner?secret=" + SECRET in text
    assert " ".join(SECRET[i:i + 4] for i in range(0, len(SECRET), 4)) in text


def test_qr_uses_block_characters_when_the_console_takes_unicode() -> None:
    stream = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    picture = soc.qr_text(bridge_auth.provisioning_uri(SECRET), stream)
    assert picture is not None and any(ch in picture for ch in "\u2580\u2584\u2588")


def test_make_console_utf8_reconfigures_cp1252() -> None:
    stream = cp1252_stream()
    soc.make_console_utf8(stream)
    assert stream.encoding.lower().replace("-", "") == "utf8"
    soc.make_console_utf8(io.StringIO())       # no reconfigure: ignored


def test_without_qrcode_the_uri_and_secret_still_show(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "qrcode", None)   # import fails
    stream = cp1252_stream()
    deps = make_deps(tmp_path, [], [], out=stream)
    soc.show_authenticator(deps, SECRET)
    text = text_of(stream)
    assert "cannot be shown" in text and bridge_auth.provisioning_uri(SECRET) in text


def test_full_enrol_on_cp1252_console(tmp_path: Path) -> None:
    code, deps, _ = enrol_ok(tmp_path, out=cp1252_stream())
    assert code == 0
    assert soc.DONE_LINE in output(deps)


# ---------------------------------------------------------------- TOTP check with a fake clock


@pytest.mark.parametrize("typed", [code_at(), code_at(-1), code_at(1), " ".join([code_at()[:3], code_at()[3:]]),
                                   to_arabic_indic(code_at()), to_extended_arabic(code_at())])
def test_code_accepted(tmp_path: Path, typed: str) -> None:
    deps = make_deps(tmp_path, [], [typed])
    soc.confirm_code(deps, SECRET)
    assert "Code OK" in output(deps)


def test_code_retry_then_ok(tmp_path: Path) -> None:
    wrong = f"{(int(code_at()) + 1) % 1_000_000:06d}"
    deps = make_deps(tmp_path, [], [wrong, code_at(-2), "12345", code_at()])
    soc.confirm_code(deps, SECRET)
    assert output(deps).count("does not match") == 3 and "Code OK" in output(deps)


def test_code_gives_up_after_five(tmp_path: Path) -> None:
    deps = make_deps(tmp_path, [], [code_at(2)] * soc.CODE_TRIES)
    with pytest.raises(soc.EnrolError, match="nothing was written"):
        soc.confirm_code(deps, SECRET)


def test_wrong_codes_write_nothing(tmp_path: Path) -> None:
    tmp_path.joinpath("bridge.env").write_text(BRIDGE_ENV, encoding="utf-8")
    ssh = FakeSSH(tmp_path)
    deps = make_deps(tmp_path, [PASSWORD, PASSWORD], ["000000"] * soc.CODE_TRIES, ssh=ssh)
    assert soc.main([], deps) == 1
    assert not ssh.calls and "nothing was written" in output(deps)
    assert tmp_path.joinpath("bridge.env").read_text(encoding="utf-8") == BRIDGE_ENV


# ---------------------------------------------------------------- recovery codes


def test_recovery_codes_format_and_hashes() -> None:
    codes = bridge_auth.new_recovery_codes()
    assert len(codes) == 10 and len(set(codes)) == 10
    assert all(CODE_RE.fullmatch(c) for c in codes)
    values = soc.make_values(PASSWORD, SECRET, codes, cost=4)
    hashes = values["OWNER_RECOVERY_HASHES"].split(",")
    assert len(hashes) == 10
    for code, stored in zip(codes, hashes):
        assert code not in values["OWNER_RECOVERY_HASHES"]
        assert re.fullmatch(r"[A-Za-z0-9_-]+", stored)
        assert bridge_auth.verify_recovery_code(code.upper().replace("-", " "), stored)
    assert not bridge_auth.verify_recovery_code("aaaa-bbbb-cccc", hashes[0])


def test_recovery_codes_printed_once_with_note(tmp_path: Path) -> None:
    code, deps, ssh = enrol_ok(tmp_path)
    assert code == 0
    text = output(deps)
    shown = CODE_RE.findall(text)
    assert len(shown) == 10 and len(set(shown)) == 10
    assert "SAVE THESE NOW" in text
    payload = json.loads(ssh.calls[0]["input"])
    assert not any(c in payload["args"]["values"]["OWNER_RECOVERY_HASHES"] for c in shown)


# ---------------------------------------------------------------- the ssh write


def test_ssh_command_and_stdin_payload(tmp_path: Path) -> None:
    code, deps, ssh = enrol_ok(tmp_path)
    assert code == 0 and len(ssh.calls) == 1
    call = ssh.calls[0]
    command = call["command"]
    key = os.path.expanduser("~/.ssh/id_ed25519_hostinger")
    assert command[:6] == ["ssh", "-i", key, "-o", "BatchMode=yes", "-o"]
    assert command[-2:] == ["root@187.127.123.235", soc.REMOTE_LOADER]
    payload = json.loads(call["input"])
    args = payload["args"]
    assert set(args["values"]) == set(soc.KEYS)
    assert args["target"] == "/opt/jarvis-agent/deploy/hostinger/bridge.env"
    assert args["backup_dir"] == "/root/jarvis-backups/2026-09-21_141320Z"
    assert args["backup_name"] == "bridge.env.pre-c20"
    assert args["values"]["OWNER_TOTP_SECRET"] == SECRET
    # no value on the command line, nothing secret in the shipped program text
    joined = " ".join(command)
    for value in args["values"].values():
        assert value not in joined
        assert value not in payload["program"]
    assert PASSWORD not in call["input"]
    assert soc.DONE_LINE in output(deps).splitlines()[-1]
    # the hashes never reach the screen
    assert args["values"]["OWNER_PASSWORD_HASH"] not in output(deps)


def test_vps_file_backup_replace_and_mode_600(tmp_path: Path, chmods: list[tuple[str, int]]) -> None:
    code, _, ssh = enrol_ok(tmp_path)
    assert code == 0
    values = json.loads(ssh.calls[0]["input"])["args"]["values"]
    backup = tmp_path / "jarvis-backups" / "2026-09-21_141320Z" / "bridge.env.pre-c20"
    assert backup.read_bytes() == BRIDGE_ENV.encode("utf-8")
    new = (tmp_path / "bridge.env").read_text(encoding="utf-8")
    assert new.splitlines() == [
        "# JARVIS bridge (fake)",
        f"SESSION_JWT_SECRET={FAKE_JWT}",
        f"OWNER_PASSWORD_HASH={values['OWNER_PASSWORD_HASH']}",
        "LEGACY_TOKEN_AUTH=1",
        f"OWNER_TOTP_SECRET={values['OWNER_TOTP_SECRET']}",
        "# OWNER_RECOVERY_HASHES=commented-out-stays",
        "ALLOWED_ORIGINS=https://jarvis.srv1802669.hstgr.cloud",
        f"OWNER_RECOVERY_HASHES={values['OWNER_RECOVERY_HASHES']}",
    ]
    assert new.endswith("\n")
    modes = {Path(p).name: m for p, m in chmods}
    assert modes["bridge.env.pre-c20"] == 0o600
    # The temp file is made mode 600 before os.replace, which keeps that mode for bridge.env (no chmod after the
    # replace, so a failure there can never be reported as 'nothing written').
    assert modes[".bridge.env.c20.tmp"] == 0o600 and "bridge.env" not in modes
    assert not list(tmp_path.glob(".bridge.env.c20.tmp"))


def test_remote_keeps_crlf_and_reports_no_values(tmp_path: Path) -> None:
    target = tmp_path / "bridge.env"
    target.write_bytes(b"A=1\r\nOWNER_TOTP_SECRET=OLD\r\nB=2\r\n")
    out = io.StringIO()
    with redirect_stdout(out):
        code = soc.remote_set_owner({"target": str(target), "backup_dir": str(tmp_path / "b"),
                                     "backup_name": "x.pre-c20", "values": {"OWNER_TOTP_SECRET": SECRET,
                                                                             "OWNER_PASSWORD_HASH": "ZmFrZQ"}})
    assert code == 0
    assert target.read_bytes() == f"A=1\r\nOWNER_TOTP_SECRET={SECRET}\r\nB=2\r\nOWNER_PASSWORD_HASH=ZmFrZQ\r\n".encode()
    row = json.loads(out.getvalue())
    assert row["keys"] == {"OWNER_TOTP_SECRET": "replaced", "OWNER_PASSWORD_HASH": "added"}
    assert row["session_jwt_secret"] is False
    assert SECRET not in out.getvalue() and "ZmFrZQ" not in out.getvalue()


def test_remote_refuses_existing_backup_missing_file_and_bad_value(tmp_path: Path) -> None:
    target = tmp_path / "bridge.env"
    target.write_text("A=1\n", encoding="utf-8")
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "x.pre-c20").write_text("older backup", encoding="utf-8")
    args = {"target": str(target), "backup_dir": str(tmp_path / "b"), "backup_name": "x.pre-c20",
            "values": {"OWNER_TOTP_SECRET": SECRET}}
    with redirect_stdout(io.StringIO()):
        assert soc.remote_set_owner(args) == 2                       # never over an existing backup
        assert soc.remote_set_owner({**args, "backup_name": "y", "values": {"OWNER_TOTP_SECRET": "a b\nC=1"}}) == 2
        assert soc.remote_set_owner({**args, "target": str(tmp_path / "missing.env")}) == 3
    assert target.read_text(encoding="utf-8") == "A=1\n"
    assert (tmp_path / "b" / "x.pre-c20").read_text(encoding="utf-8") == "older backup"
    assert not (tmp_path / "b" / "y").exists()


def test_remote_program_through_the_real_loader(tmp_path: Path) -> None:
    """The loader string really runs the shipped program (python3 on the VPS; this Python here)."""
    target = tmp_path / "bridge.env"
    target.write_text("A=1\n", encoding="utf-8")
    loader = re.fullmatch(r"python3 -c '(.*)'", soc.REMOTE_LOADER, re.S)
    assert loader is not None
    payload = json.dumps({"program": soc.remote_program(),
                          "args": {"target": str(target), "backup_dir": str(tmp_path / "bk"),
                                   "backup_name": "bridge.env.pre-c20", "values": {"OWNER_TOTP_SECRET": SECRET}}})
    proc = subprocess.run([sys.executable, "-c", loader.group(1)], input=payload, text=True, capture_output=True,
                          timeout=60, check=False)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["ok"] is True
    assert target.read_text(encoding="utf-8") == f"A=1\nOWNER_TOTP_SECRET={SECRET}\n"


def test_env_overrides_host_and_key(tmp_path: Path) -> None:
    code, _, ssh = enrol_ok(tmp_path, env={"JARVIS_VPS_HOST": "root@vps.example.invalid",
                                           "JARVIS_SSH_KEY": "C:/fake/key"})
    assert code == 0
    command = ssh.calls[0]["command"]
    assert command[2] == "C:/fake/key" and command[-2] == "root@vps.example.invalid"


@pytest.mark.parametrize("ssh_kw, words", [
    ({"returncode": 255, "stderr": "ssh: connect to host x port 22: Connection timed out"}, "could not connect"),
    ({"raise_exc": FileNotFoundError("ssh")}, "ssh was not found"),
    ({"returncode": 1}, "server refused"),
])
def test_failed_ssh_writes_nothing(tmp_path: Path, ssh_kw: dict[str, Any], words: str) -> None:
    code, deps, ssh = enrol_ok(tmp_path, ssh=FakeSSH(tmp_path, **ssh_kw))
    text = output(deps)
    assert code == 1 and len(ssh.calls) == 1
    assert words in text and "nothing was written" in text and "NOT active" in text
    assert soc.DONE_LINE not in text
    assert (tmp_path / "bridge.env").read_text(encoding="utf-8") == BRIDGE_ENV
    assert not (tmp_path / "jarvis-backups").exists()


def test_missing_bridge_env_on_vps(tmp_path: Path) -> None:
    ssh = FakeSSH(tmp_path)
    deps = make_deps(tmp_path, [PASSWORD, PASSWORD], [code_at(), ""], ssh=ssh)
    assert soc.main([], deps) == 1          # tmp bridge.env was never created
    assert "does not exist" in output(deps) and not (tmp_path / "jarvis-backups").exists()


# ---------------------------------------------------------------- Ctrl+C


@pytest.mark.parametrize("where", ["password", "repeat", "code", "enter"])
def test_ctrl_c_writes_nothing(tmp_path: Path, where: str) -> None:
    tmp_path.joinpath("bridge.env").write_text(BRIDGE_ENV, encoding="utf-8")
    stop = KeyboardInterrupt()
    secrets_ = {"password": [stop], "repeat": [PASSWORD, stop]}.get(where, [PASSWORD, PASSWORD])
    inputs = {"code": [stop], "enter": [code_at(), stop]}.get(where, [])
    ssh = FakeSSH(tmp_path)
    deps = make_deps(tmp_path, secrets_, inputs, ssh=ssh)
    assert soc.main([], deps) == 130
    assert not ssh.calls
    assert "nothing was written" in output(deps)
    assert tmp_path.joinpath("bridge.env").read_text(encoding="utf-8") == BRIDGE_ENV


def test_eof_counts_as_cancel(tmp_path: Path) -> None:
    deps = make_deps(tmp_path, [EOFError()], [])
    assert soc.main([], deps) == 130


def test_ctrl_c_during_ssh_says_to_check(tmp_path: Path) -> None:
    code, deps, _ = enrol_ok(tmp_path, ssh=FakeSSH(tmp_path, raise_exc=KeyboardInterrupt()))
    assert code == 1 and "ask Claude to check" in output(deps)


def test_unexpected_error_shows_no_secret(tmp_path: Path) -> None:
    code, deps, _ = enrol_ok(tmp_path, ssh=FakeSSH(tmp_path, raise_exc=RuntimeError(f"boom {PASSWORD} {SECRET}")))
    text = output(deps)
    assert code == 1 and "RuntimeError" in text and PASSWORD not in text and "boom" not in text


def test_non_interactive_refused(tmp_path: Path) -> None:
    deps = make_deps(tmp_path, [], [])
    deps.interactive = lambda: False
    assert soc.main([], deps) == 2 and "PowerShell" in output(deps)
    assert soc.main(["--test-login"], deps) == 2


def test_help_text(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as stop:
        soc.main(["--help"])
    assert stop.value.code == 0
    text = capsys.readouterr().out
    assert "--test-login" in text and "authenticator app" in text and "Ctrl+C" in text


# ---------------------------------------------------------------- round trip with the bridge's own code


def test_round_trip_bridge_accepts_written_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, deps, _ = enrol_ok(tmp_path)
    assert code == 0
    shown_codes = CODE_RE.findall(output(deps))
    parsed = dotenv_values(tmp_path / "bridge.env")                 # how compose / dotenv read the file
    for name in (*soc.KEYS, "SESSION_JWT_SECRET"):
        monkeypatch.setenv(name, parsed[name] or "")
    cfg = jarvis_config.JarvisConfig()                               # the bridge's own env parsing
    assert len(cfg.owner_recovery_hashes) == 10
    monkeypatch.setattr(bridge_auth, "config", cfg)
    creds = bridge_auth.OwnerCredentials.from_config()
    assert creds.enrolled and creds.problems() == []
    assert bridge_auth.verify_password(PASSWORD, creds.password_hash)
    assert not bridge_auth.verify_password(PASSWORD + "!", creds.password_hash)
    assert creds.totp_secret == SECRET
    assert bridge_auth.totp_match(creds.totp_secret, to_extended_arabic(code_at()), START) is not None
    assert bridge_auth.totp_match(creds.totp_secret, code_at(3), START) is None
    for index, recovery in enumerate(shown_codes):
        assert bridge_auth.recovery_match(recovery, creds.recovery_hashes) == index


# ---------------------------------------------------------------- --test-login


class FakeResponse:
    def __init__(self, status: int, body: bytes, cookies: list[str] | None = None,
                 extra: dict[str, str] | None = None) -> None:
        self.status = status
        self._body = body
        self.headers = email.message.Message()
        for cookie in cookies or []:
            self.headers["Set-Cookie"] = cookie
        for name, value in (extra or {}).items():
            self.headers[name] = value

    def read(self) -> bytes:
        return self._body

    def getcode(self) -> int:
        return self.status

    def close(self) -> None:
        pass


class FakeHTTP:
    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.requests: list[Any] = []

    def __call__(self, request: Any, timeout: float = 0) -> Any:
        self.requests.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


def http_error(status: int, body: dict[str, Any], headers: dict[str, str] | None = None) -> urllib.error.HTTPError:
    hdrs = email.message.Message()
    for name, value in (headers or {}).items():
        hdrs[name] = value
    return urllib.error.HTTPError("https://x.invalid", status, "err", hdrs, io.BytesIO(json.dumps(body).encode()))


def login_deps(tmp: Path, http: FakeHTTP, code: str = "123456", env: dict[str, str] | None = None) -> Any:
    return make_deps(tmp, [PASSWORD], [code], http=http, env=env)


def test_login_ok_never_prints_cookie(tmp_path: Path) -> None:
    ok = FakeResponse(200, json.dumps({"ok": True, "user": "owner", "session_id": "sidfake123456",
                                       "expires_at": 1_790_043_200}).encode(),
                      cookies=[f"jarvis_session={FAKE_COOKIE}; Path=/vortex; HttpOnly; Secure; SameSite=strict"])
    http = FakeHTTP(ok, FakeResponse(200, b'{"ok": true}'))
    deps = login_deps(tmp_path, http, code=to_extended_arabic("123456"))
    assert soc.main(["--test-login"], deps) == 0
    text = output(deps)
    assert "LOGIN OK (expires 2026-09-22 02:13 UTC)" in text
    assert FAKE_COOKIE not in text and "sidfake" not in text and PASSWORD not in text
    login, logout = http.requests
    assert login.full_url == "https://jarvis.srv1802669.hstgr.cloud/vortex/auth/login"
    assert login.get_method() == "POST"
    assert login.get_header("Content-type") == "application/json"
    origin = login.get_header("Origin")
    assert origin == "https://jarvis.srv1802669.hstgr.cloud"
    # the bridge's own CSRF rule with the deployed ALLOWED_ORIGINS list accepts it
    allowed = ["https://jarvis.srv1802669.hstgr.cloud", "http://127.0.0.1:5173", "http://localhost:5173"]
    assert check_origin({"origin": origin}, allowed)
    assert json.loads(login.data) == {"password": PASSWORD, "totp": "123456"}
    assert logout.full_url.endswith("/vortex/auth/logout")
    assert logout.get_header("Cookie") == f"jarvis_session={FAKE_COOKIE}"
    assert "closed" in text


@pytest.mark.parametrize("answer, words", [
    (http_error(401, {"ok": False, "error": "invalid_credentials"}), "LOGIN FAILED: invalid credentials"),
    (http_error(429, {"ok": False, "error": "rate_limited", "retry_after": 42}, {"Retry-After": "42"}),
     "rate_limited - retry after 42 s"),
    (http_error(503, {"ok": False, "error": "owner_not_enrolled"}), "owner_not_enrolled (HTTP 503)"),
    (http_error(403, {"ok": False, "error": "bad_origin"}), "bad_origin (HTTP 403)"),
    (urllib.error.HTTPError("https://x.invalid", 502, "bad", email.message.Message(),
                            io.BytesIO(b"<html> Bad   Gateway </html>")), "HTTP 502: <html> Bad Gateway </html>"),
    (urllib.error.URLError(OSError("getaddrinfo failed")), "could not reach"),
])
def test_login_errors(tmp_path: Path, answer: Any, words: str) -> None:
    http = FakeHTTP(answer)
    deps = login_deps(tmp_path, http)
    assert soc.main(["--test-login"], deps) == 1
    text = output(deps)
    assert words in text and PASSWORD not in text and "LOGIN OK" not in text
    assert len(http.requests) == 1


def test_login_bad_code_is_not_sent(tmp_path: Path) -> None:
    http = FakeHTTP()
    deps = login_deps(tmp_path, http, code="12ab56")
    assert soc.main(["--test-login"], deps) == 1
    assert not http.requests and "LOGIN FAILED" in output(deps)


def test_login_domain_override(tmp_path: Path) -> None:
    http = FakeHTTP(http_error(401, {"ok": False, "error": "invalid_credentials"}))
    deps = login_deps(tmp_path, http, env={"JARVIS_DOMAIN": "jarvis.example.invalid"})
    soc.main(["--test-login"], deps)
    assert http.requests[0].full_url == "https://jarvis.example.invalid/vortex/auth/login"
    assert http.requests[0].get_header("Origin") == "https://jarvis.example.invalid"


def test_login_ctrl_c(tmp_path: Path) -> None:
    deps = make_deps(tmp_path, [KeyboardInterrupt()], [], http=FakeHTTP())
    assert soc.main(["--test-login"], deps) == 130


# ---------------------------------------------------------------- review follow-ups (uncertain writes, output, codes)


@pytest.mark.parametrize("ssh_kw", [
    {"returncode": 255, "stderr": "Connection to vps closed by remote host."},
    {"raise_exc": subprocess.TimeoutExpired("ssh", 120)},
])
def test_an_uncertain_write_asks_claude_to_check_and_never_says_not_active(tmp_path: Path,
                                                                          ssh_kw: dict[str, Any]) -> None:
    code, deps, _ = enrol_ok(tmp_path, ssh=FakeSSH(tmp_path, **ssh_kw))
    text = output(deps)
    assert code == 1
    assert "either unchanged or fully updated" in text and "ask Claude to check" in text
    assert "NOT active" not in text and "nothing was written" not in text


def test_enrolment_refuses_a_redirected_output_before_asking_anything(tmp_path: Path) -> None:
    deps = make_deps(tmp_path, [], [])
    deps.private_output = lambda: False
    assert soc.main([], deps) == 2
    assert "without redirecting its output" in output(deps)


def test_the_test_login_code_loses_rtl_marks_and_urdu_digits() -> None:
    assert soc.normalise_code("‏۱۲۳ ۴۵۶‎") == "123456"


def test_ssh_output_is_decoded_as_utf8(tmp_path: Path) -> None:
    code, _, ssh = enrol_ok(tmp_path)
    assert code == 0
    assert ssh.calls[0]["encoding"] == "utf-8" and ssh.calls[0]["errors"] == "replace"
