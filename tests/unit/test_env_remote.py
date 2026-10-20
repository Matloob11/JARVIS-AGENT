"""Plan task C-127: scripts/ops/env_remote.py, the helpers the VPS env tools ship to the VPS over ssh stdin.

Only files under tmp_path are touched; ssh never runs. The shipped programs must stay standard-library python3.8
code (the VPS host runs them with its own python3), so every program is checked against the 3.8 grammar, against a
list of 3.9+ APIs and, when an older interpreter is installed (the Windows 'py' launcher or python3.8..3.11 on
PATH), compiled by it.
"""
# pylint: disable=redefined-outer-name,protected-access
from __future__ import annotations

import ast
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path, PurePosixPath

import pytest

ROOT = Path(__file__).resolve().parents[2]
OPS = ROOT / "scripts" / "ops"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"jarvis_ops_test_{name}", OPS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


er = _load("env_remote")
sbe = _load("split_bridge_env")
pae = _load("push_agent_env")
lpp = _load("livekit_pc_pair")

FAKE = "c127-remote-fake-secret-value"


# --------------------------------------------------------------------------- env text helpers

def test_env_entries_names_assignments_exports_and_bare_names() -> None:
    text = "# c\nA=1\r\n  export B = two words\n\nC\nnot a line\nD=\"q\" # x\nE=\n"
    entries = er.env_entries(text)
    assert [name for name, _ in entries] == [None, "A", "B", None, "C", None, "D", "E"]
    assert "".join(line for _, line in entries) == text   # every byte, line endings included
    values = er.env_values(text)
    assert values == {"A": "1", "B": "two words", "C": "", "D": "q", "E": ""}


@pytest.mark.parametrize(("line", "value"), [
    ("A=plain\n", "plain"), ("A = spaced  # comment\n", "spaced"), ("A='lit $X # no'\n", "lit $X # no"),
    ('A="dq"\n', "dq"), ("A=\n", ""), ("A\n", ""), ("A=x#y\n", "x#y"),
])
def test_env_value_reads_like_compose(line: str, value: str) -> None:
    assert er.env_value(line) == value


def test_env_multiline_and_render() -> None:
    assert er.env_multiline('A="starts\n') and not er.env_multiline('A="closed"\n') and not er.env_multiline("A=x\n")
    assert er.render_env_value("abc-_.:/@+=,~%9") == "abc-_.:/@+=,~%9"
    assert er.render_env_value("has space") == "'has space'"
    assert er.render_env_value("a'b") is None and er.render_env_value("a\nb") is None


def test_env_entries_reads_past_a_byte_order_mark_and_every_writer_keeps_it() -> None:
    """Review finding: a VPS .env saved with a UTF-8 BOM hid the name on its first line from every tool (an agent
    secret there was neither removed by the split nor seen by its check). The BOM is an entry of its own now."""
    text = "\ufeffGOOGLE_API_KEY=" + FAKE + "\r\nB=2\r\n"
    entries = er.env_entries(text)
    assert entries[0] == [None, "\ufeff"] and [name for name, _ in entries] == [None, "GOOGLE_API_KEY", "B"]
    assert "".join(line for _, line in entries) == text
    assert er.env_values(text) == {"GOOGLE_API_KEY": FAKE, "B": "2"}
    assert er.env_drop_lines(text, ["GOOGLE_API_KEY"]) == "\ufeffB=2\r\n"
    assert er.env_set_lines(text, {"GOOGLE_API_KEY": "new", "C": "3"}) == "\ufeffGOOGLE_API_KEY=new\r\nB=2\r\nC=3\r\n"
    assert er.env_set_lines("\ufeff", {"A": "1"}) == "\ufeffA=1\n"   # no blank line after a lone BOM
    assert er.env_entries("A=\ufeff1\n") == [["A", "A=\ufeff1\n"]]   # only a leading one is special


def test_env_set_and_drop_lines_keep_every_other_byte() -> None:
    text = "# keep\r\nA=old\r\nB=1\r\nA=older\r\n"
    out = er.env_set_lines(text, {"A": "new", "C": "c"})
    assert out == "# keep\r\nA=new\r\nB=1\r\nA=new\r\nC=c\r\n"
    assert er.env_drop_lines(out, ["A", "C"]) == "# keep\r\nB=1\r\n"
    assert er.env_set_lines("X=1", {"Y": "2"}) == "X=1\nY=2\n"   # a missing final newline is added first


# --------------------------------------------------------------------------- files

def test_atomic_write_private_replaces_and_leaves_no_temp(tmp_path: Path) -> None:
    target = tmp_path / "bridge.env"
    target.write_bytes(b"OLD=1\n")
    er.atomic_write_private(str(target), b"NEW=2\n")
    assert target.read_bytes() == b"NEW=2\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["bridge.env"]
    if os.name != "nt":
        assert target.stat().st_mode & 0o777 == 0o600


def test_atomic_write_private_failure_keeps_the_old_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / ".env"
    target.write_bytes(b"OLD=1\n")

    def boom(*_args, **_kwargs):
        raise OSError("rename failed")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        er.atomic_write_private(str(target), b"NEW=2\n")
    assert target.read_bytes() == b"OLD=1\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == [".env"]


def test_backup_dir_and_copies(tmp_path: Path) -> None:
    root = tmp_path / "backups"
    first = er.make_backup_dir(str(root), "20261014T120000Z", "pre-env-split")
    second = er.make_backup_dir(str(root), "20261014T120000Z", "pre-env-split")
    assert Path(first).name == "20261014T120000Z-pre-env-split" and Path(second).name.endswith("-pre-env-split-2")
    src = tmp_path / "x.env"
    src.write_bytes(b"A=" + FAKE.encode() + b"\n")
    assert er.backup_private(str(src), first, "hostinger.env") is True
    assert er.backup_private(str(tmp_path / "missing"), first, "bridge.env") is False
    assert (Path(first) / "hostinger.env").read_bytes() == src.read_bytes()
    assert er.file_mode(str(tmp_path / "missing")) is None and er.file_mode(str(src))["mode"]
    if os.name != "nt":
        assert (Path(first) / "hostinger.env").stat().st_mode & 0o777 == 0o600
        assert Path(first).stat().st_mode & 0o777 == 0o700


def test_env_tool_backups_stay_out_of_the_backup_vps_namespace() -> None:
    """C-127: the env tools' backups (env.agent, hostinger.env and bridge.env copies) live in a folder of their own
    under /root/jarvis-backups: never a '2*' name, which the RUNBOOK's PC mirror and its 'newest backup' commands
    would take for a backup_vps.sh folder, and never a name backup_vps.sh's prune or rollback.sh accepts."""
    rel = PurePosixPath(er.BACKUP_ROOT).relative_to("/root/jarvis-backups")
    assert rel.parts and not rel.parts[0].startswith("2"), er.BACKUP_ROOT
    hostinger = ROOT / "deploy" / "hostinger"
    for script in ("backup_vps.sh", "deploy.sh"):   # backup_vps.sh's prune, and rollback.sh through deploy.sh
        name_re = re.search(r"readonly NAME_RE='([^']+)'", (hostinger / script).read_text(encoding="utf-8"))
        assert name_re is not None and not re.fullmatch(name_re.group(1), rel.parts[0]), script
    # every tool takes the folder from er.BACKUP_ROOT when it runs (the tests move it into tmp_path that way)
    for module in (sbe, pae, lpp):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "er.BACKUP_ROOT" in source and '"/root/jarvis-backups' not in source, module.__name__


def test_value_digest_is_keyed_by_the_nonce() -> None:
    nonce, other = er.new_nonce(), er.new_nonce()
    digest = er.value_digest(nonce, "A", FAKE)
    assert FAKE not in digest and len(digest) == 64
    assert er.digests_equal(digest, er.value_digest(nonce, "A", FAKE))
    assert not er.digests_equal(digest, er.value_digest(other, "A", FAKE))
    assert not er.digests_equal(digest, er.value_digest(nonce, "B", FAKE))   # the name is part of it
    assert not er.digests_equal(None, None) and not er.digests_equal(digest, None)


def test_compose_interpolations_kinds() -> None:
    text = ("a: ${REQ:?set it}\nb: ${REQ2?x}\nc: ${PLAIN}\nd: $BARE and $$LITERAL\ne: ${DEF:-x} ${ALT+y}\n"
            "# f: ${COMMENTED}\ng: ${PLAIN:-weaker}\n")
    assert er.compose_interpolations(text) == {"REQ": "required", "REQ2": "required", "PLAIN": "plain",
                                               "BARE": "plain", "DEF": "default", "ALT": "default"}


def test_the_live_compose_file_interpolates_no_agent_secret() -> None:
    """The split keeps every variable docker-compose.yml interpolates in .env: none of them may be an agent secret."""
    interp = er.compose_interpolations((ROOT / "deploy" / "hostinger" / "docker-compose.yml").read_text("utf-8"))
    assert interp == {"JARVIS_BRAIN": "required", "LIVEKIT_API_KEY": "plain", "LIVEKIT_API_SECRET": "plain",
                      "VORTEX_SECURITY_TOKEN": "plain", "GIT_SHA": "default"}
    assert not set(interp) & set(sbe.AGENT_ONLY)


# --------------------------------------------------------------------------- the shipped programs

def _programs() -> dict[str, str]:
    return {"split": sbe.split_program(), "names": sbe.names_program(), "push": pae.push_program(),
            "keys": pae.keys_program(), "pair_write": lpp.write_program(), "pair_verify": lpp.verify_program()}


# 3.9+ (or 3.10+) APIs that would break python3.8 on the VPS
NEWER_APIS = (r"\.removeprefix\(", r"\.removesuffix\(", r"\bstrict\s*=", r"functools\.cache\b", r"\bmath\.lcm\b",
              r"\bzoneinfo\b", r"\bgraphlib\b", r"\.is_relative_to\(", r"\bmatch\s+\w+\s*:", r"\bcase\s+\w+\s*:",
              r"\bitertools\.pairwise\b", r"\.bit_count\(", r"\bstr \| None\b(?!.*->)")


@pytest.mark.parametrize("name", sorted(_programs()))
def test_shipped_programs_are_python38_standard_library_code(name: str) -> None:
    source = _programs()[name]
    tree = ast.parse(source, feature_version=(3, 8))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported <= {"__future__", "hashlib", "hmac", "json", "os", "re", "secrets", "subprocess", "sys",
                        "typing"}, imported
    body = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith(("def ", "#")))
    for pattern in NEWER_APIS:
        assert not re.search(pattern, body), f"{name}: {pattern}"
    # PEP 701 f-strings (3.12) reuse the outer quote inside a replacement field; 3.8 cannot parse them
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            segment = ast.get_source_segment(source, node) or ""
            quote = segment.lstrip("rRfFbB")[:1]
            inner = re.findall(r"\{[^{}]*\}", segment)
            assert not any(quote in part for part in inner), f"{name}: {segment}"


def _older_pythons() -> list[list[str]]:
    found: list[list[str]] = []
    for minor in (8, 9, 10, 11):
        exe = shutil.which(f"python3.{minor}")
        if exe:
            found.append([exe])
    launcher = shutil.which("py") if os.name == "nt" else None
    if launcher:
        for minor in (8, 9, 10, 11):
            probe = subprocess.run([launcher, f"-3.{minor}", "-c", "pass"], capture_output=True, check=False)
            if probe.returncode == 0:
                found.append([launcher, f"-3.{minor}"])
    return found


def test_shipped_programs_compile_on_an_older_python(tmp_path: Path) -> None:
    pythons = _older_pythons()
    if not pythons:
        pytest.skip("no python3.8..3.11 interpreter installed")
    for name, source in _programs().items():
        path = tmp_path / f"{name}.py"
        path.write_text(source, encoding="utf-8")
        for python in pythons:
            proc = subprocess.run([*python, "-c", "import sys; compile(open(sys.argv[1], encoding='utf-8').read(), "
                                   "sys.argv[1], 'exec')", str(path)], capture_output=True, text=True, check=False)
            assert proc.returncode == 0, f"{name} on {python}: {proc.stderr[-400:]}"


def test_program_entry_must_be_shipped_and_an_unexpected_error_shows_its_type_only() -> None:
    with pytest.raises(ValueError):
        er.program(er.read_text, entry="remote_split")

    def remote_boom(args):  # a function whose error message would quote a value
        raise RuntimeError("the secret is " + args["value"])

    namespace: dict = {"__name__": "jarvis_remote", "ARGS": {"value": FAKE}}
    source = er.program(remote_boom, entry="remote_boom")
    out = io.StringIO()
    with redirect_stdout(out), pytest.raises(SystemExit) as stop:
        exec(compile(source, "jarvis-remote", "exec"), namespace)  # pylint: disable=exec-used
    assert stop.value.code == 1
    assert json.loads(out.getvalue()) == {"result": "failed", "error": "unexpected RuntimeError"}
    assert FAKE not in out.getvalue()


def test_pc_side_helpers() -> None:
    assert er.json_rows('noise\n{"a": 1}\n[1]\n{"b": 2}\n') == [{"a": 1}, {"b": 2}]
    assert er.mode_text({"mode": "600", "uid": 0}) == "600 root" and er.mode_text(None) == "absent"
    assert er.failure_line("a\nb\n") == "b" and er.failure_line("") == "no answer"
    assert er.names_line(["b", "a", "a"]) == "a, b" and er.names_line([]) == "none"
    assert er.vps_path("C:/Program Files/Git/opt/jarvis-agent") == "/opt/jarvis-agent"
    assert re.fullmatch(r"\d{8}T\d{6}Z", er.utc_stamp())
    assert sys.version_info >= (3, 8)
