"""JARVIS pre-commit guard: blocks secrets, private terms and forbidden files (plan task C-04), plus
commits without JARVIS_AGENT, staged paths outside the committer's lane and removed or changed lines
in append-only files such as done_highlight.md (plan task C-05).

Usage:
    JARVIS_AGENT=claude|cursor|user git commit -m "[agent] TASK-ID: ..." -- <paths>   # git runs this hook
    python scripts/coord/precommit.py              # check what is staged (run by .githooks/pre-commit)
    python scripts/coord/precommit.py --all-files  # check every tracked + untracked (not ignored) file

PowerShell: $env:JARVIS_AGENT='cursor'; git commit -m "[cursor] X-NN: ..." -- <paths>
Lanes come from HEAD's .coord/lanes.json (scripts/coord/check_lane.py); a staged or unstaged edit applies from the next commit.
Added lines are also checked for MOJIBAKE (UTF-8 text saved after being read as ANSI), and modified files
for ENCODING_LOSS (committed non-ASCII characters saved as '?'; scripts/coord/restore_unicode.py undoes it).
ENV_VALUE looks for the secret values of ENV_FILES and, in vps mode, for the PC fallback secrets that 'agent_ctl brain
vps' moved out of the env files into the DPAPI-wrapped run/fallback.env.dpapi (scripts/ops/fallback_env.py, plan task
C-127, D13): this process unwraps that store (it runs as the same Windows user) and keeps the values in memory only.
A store it cannot unwrap is named on stderr with the names it holds, and every other check still runs.
Prints only "path:line RULE" (never the secret value). Exit code 1 if anything is found.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys

# Lane rules live next to this file in scripts/coord/check_lane.py.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import check_lane  # noqa: E402  pylint: disable=wrong-import-position
import restore_unicode  # noqa: E402  pylint: disable=wrong-import-position

ROOT = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True).stdout.strip()
# Their secret values are looked for in every staged line (when the file exists here). .env.agent (C-126, K-75) is the
# VPS agent containers' env file; a copy on this PC is scanned the same way.
ENV_FILES = [".env", "deploy/windows-local/.env", "deploy/hostinger/.env", "deploy/hostinger/bridge.env",
             "deploy/hostinger/.env.agent"]
# vps mode (C-127 step 5, D13): the PC fallback secrets leave the first two ENV_FILES for this DPAPI-wrapped store
# (fallback_env.store_path(): JARVIS_RUN_DIR, else <repo>/run); fallback_values() reads it with fallback_env's own code.
FALLBACK_ENV_SCRIPT = os.path.join(os.path.dirname(_HERE), "ops", "fallback_env.py")
FALLBACK_STORE = "fallback.env.dpapi"
PRIVATE_TERMS_FILE = ".coord/private_terms.txt"
MAX_SCAN_BYTES = 2_000_000
AGENT_ENV = "JARVIS_AGENT"

FORBIDDEN_PATHS = [
    (re.compile(r"(^|/)\.env$"), "ENV_FILE"),
    # .env.<anything> (.env.local, the VPS agent's .env.agent and its .tmp, ...) unless it is a names-only template
    # ending in .example (.env.example, .env.production.example, .env.agent.example)
    (re.compile(r"(^|/)\.env\.(?!(?:[^/]*\.)?example$)[^/]+$"), "ENV_FILE"),
    (re.compile(r"\.htpasswd$"), "HTPASSWD"),
    (re.compile(r"(^|/)bridge\.env$"), "BRIDGE_ENV"),
    # Any other <name>.env (C-126): copies such as hostinger.env or root.env hold the same secrets
    (re.compile(r"(^|/)(?!bridge\.env$)[^/]+\.env$"), "ENV_FILE"),
    (re.compile(r"\.(pem|key)$"), "PRIVATE_KEY_FILE"),
    (re.compile(r"(^|/)claude-artifact-[^/]*\.png$"), "AUDIT_SCREENSHOT"),
]
SECRET_PATTERNS = [
    (re.compile(r"AIza[0-9A-Za-z_\-]{30,}"), "GOOGLE_API_KEY_PATTERN"),
    (re.compile(r"sk-or-v1-[0-9a-f]{20,}"), "OPENROUTER_KEY_PATTERN"),
    (re.compile(r"gsk_[0-9A-Za-z]{20,}"), "GROQ_KEY_PATTERN"),
    (re.compile(r"\bhf_[0-9A-Za-z]{20,}"), "HF_TOKEN_PATTERN"),
    (re.compile(r"tvly-[0-9A-Za-z\-]{16,}"), "TAVILY_KEY_PATTERN"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "PRIVATE_KEY_BLOCK"),
]


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, cwd=ROOT, check=False,
                          encoding="utf-8", errors="replace").stdout


SECRET_KEY_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASS|HASH|CREDENTIAL|PRIVATE)", re.I)
PRIVATE_VALUE_KEYS = {"EMAIL_USER", "WHATSAPP_TEST_NUMBERS", "SIM_LOOKUP_API_URL"}


def phone_variants(number: str) -> list[str]:
    digits = re.sub(r"\D", "", number)
    if len(digits) < 10:
        return []
    local = digits[-10:]
    return sorted({digits, "+" + digits, "0" + local, local})


def _value_entries(key: str, value: str) -> list[tuple[str, str]]:
    """The (KEY_NAME, value) pairs one assignment adds: a secret-like value (16+ chars), a private value (6+ chars),
    every form of each test number; none for anything else."""
    if not value:
        return []
    if key == "WHATSAPP_TEST_NUMBERS":
        return [(key, v) for num in value.split(",") for v in phone_variants(num)]
    if key in PRIVATE_VALUE_KEYS and len(value) >= 6:
        return [(key, value)]
    if SECRET_KEY_NAME.search(key) and len(value) >= 16:
        return [(key, value)]
    return []


def fallback_store() -> str:
    """The DPAPI store of the PC fallback secrets, where scripts/ops/fallback_env.py keeps it: JARVIS_RUN_DIR, else
    <repo>/run."""
    return os.path.join(os.environ.get("JARVIS_RUN_DIR") or os.path.join(ROOT, "run"), FALLBACK_STORE)


def _store_names(path: str) -> list[str]:
    """The names in the store's plaintext header (nothing is unwrapped); [] when it cannot be read."""
    try:
        with open(path, encoding="utf-8") as handle:
            groups = json.load(handle).get("names") or {}
        return sorted({str(name) for names in groups.values() for name in names})
    except (OSError, ValueError, AttributeError, TypeError):
        return []


def _load_fallback_env():
    """scripts/ops/fallback_env.py next to this folder, as a module (scripts/ops is not a package)."""
    spec = importlib.util.spec_from_file_location("jarvis_coord_fallback_env", FALLBACK_ENV_SCRIPT)
    if spec is None or spec.loader is None:
        raise ImportError("scripts/ops/fallback_env.py cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fallback_values(unprotect=None) -> list[tuple[str, str]]:
    """(name, value) of the PC fallback secrets in the DPAPI store (vps mode, C-127 step 5, D13), unwrapped in this
    process by fallback_env.load_store with its DPAPI call (`unprotect` replaces that call in the tests): the hook runs
    as the Windows user who wrapped them. The values stay in memory and are never printed. None without a store. A
    store that cannot be unwrapped (another Windows user, a damaged file, fallback_env.py or its packages missing) is
    reported on stderr with the names its plaintext header holds, never a value; the caller's other checks go on."""
    store = fallback_store()
    if not os.path.isfile(store):
        return []
    module = None
    try:
        module = _load_fallback_env()
        files = module.load_store(store, unprotect or module.dpapi_unprotect)
    except Exception as exc:  # pylint: disable=broad-exception-caught  # named on stderr; the commit check goes on
        # fallback_env's EnvError never carries a value; anything else is named by its type only
        why = str(exc) if module is not None and isinstance(exc, module.EnvError) else type(exc).__name__
        names = ", ".join(_store_names(store)) or "names unreadable"
        print(f"JARVIS pre-commit guard: WARNING: no line is checked for the values of the DPAPI-wrapped PC fallback "
              f"secrets ({names}): {why}. Every other check still runs.", file=sys.stderr)
        return []
    return [(str(name), str(value)) for values in files.values() for name, value in values.items()]


def env_values(unprotect=None) -> list[tuple[str, str]]:
    """(KEY_NAME, value) for secret-like .env values (16+ chars) and private values (email, test numbers): from
    ENV_FILES, and in vps mode from the DPAPI-wrapped PC fallback store (fallback_values)."""
    out = []
    for rel in ENV_FILES:
        path = os.path.join(ROOT, rel)
        if not os.path.isfile(path):
            continue
        for line in open(path, encoding="utf-8", errors="replace"):
            m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line.rstrip("\r\n"))
            if not m:
                continue
            out += _value_entries(m.group(1), m.group(2).strip().strip('"').strip("'"))
    for key, value in fallback_values(unprotect):
        out += _value_entries(key, value)
    return out


def private_terms() -> list[re.Pattern]:
    path = os.path.join(ROOT, PRIVATE_TERMS_FILE)
    if not os.path.isfile(path):
        return []
    terms = []
    for line in open(path, encoding="utf-8", errors="replace"):
        t = line.strip()
        if not t or t.startswith("#"):
            continue
        left = r"(?<![A-Za-z0-9])" if t[0].isalnum() else ""
        right = r"(?![A-Za-z0-9])" if t[-1].isalnum() else ""
        terms.append(re.compile(left + re.escape(t) + right, re.I))
    return terms


def current_agent() -> tuple[str, str]:
    """(agent, problem): agent is claude, cursor or user ('' when unusable); problem says what is wrong."""
    raw = os.environ.get(AGENT_ENV, "").strip()
    if raw.lower() in check_lane.AGENTS:
        return raw.lower(), ""
    if not raw:
        return "", f"{AGENT_ENV} is not set (who is committing?)"
    shown = re.sub(r"[^A-Za-z0-9_.-]", "?", raw)[:20]
    return "", f"{AGENT_ENV}={shown} is not valid (use claude, cursor or user)"


def git_blob(spec: str) -> str | None:
    """Text of 'HEAD:<path>' or ':<path>' (the staged copy); None when it does not exist."""
    proc = subprocess.run(["git", "cat-file", "blob", spec], capture_output=True, cwd=ROOT, check=False)
    return proc.stdout.decode("utf-8", errors="replace") if proc.returncode == 0 else None


def commit_lanes() -> tuple[check_lane.Lanes | None, str]:
    """Lanes for this commit: HEAD's .coord/lanes.json, so no commit can widen its own lane (not even by
    staging a new lanes.json; a lanes change applies from the next commit). Before that file is first
    committed: the staged copy, else the file on disk."""
    try:
        for spec in ("HEAD:" + check_lane.LANES_FILE, ":" + check_lane.LANES_FILE):
            text = git_blob(spec)
            if text is not None:
                return check_lane.parse_lanes(text), ""
        return check_lane.load_lanes(os.path.join(ROOT, check_lane.LANES_FILE)), ""
    except (OSError, ValueError) as exc:
        return None, f"{check_lane.LANES_FILE}:0 LANES_FILE ({type(exc).__name__})"


def staged_changes() -> list[str]:
    """Every staged path, including deletions and both sides of a rename."""
    return [p for p in git("diff", "--cached", "--name-only", "--no-renames", "-z").split("\0") if p]


def lane_check(paths: list[str], agent: str = "", lanes: check_lane.Lanes | None = None) -> list[str]:
    """Plan task C-05: 'path:0 OTHER_LANE (owner: x)' or 'path:0 NO_LANE' for paths outside the agent's lane.

    'user' has no lane limit. Without a valid agent or lanes file this finds nothing; main() reports those.
    """
    if agent not in check_lane.LANE_NAMES or lanes is None:
        return []
    return [f"{rel}:0 NO_LANE" if owner == check_lane.UNOWNED else f"{rel}:0 OTHER_LANE (owner: {owner})"
            for rel, owner in check_lane.outside_lane(agent, paths, lanes)]


def line_spans(numbers: list[int]) -> list[str]:
    """[3, 4, 5, 9] -> ['3-5', '9']"""
    spans: list[list[int]] = []
    for n in sorted(set(numbers)):
        if spans and n == spans[-1][1] + 1:
            spans[-1][1] = n
        else:
            spans.append([n, n])
    return [str(a) if a == b else f"{a}-{b}" for a, b in spans]


def append_only_check(changed: list[str], lanes: check_lane.Lanes | None) -> list[str]:
    """Plan task C-05: 'done_highlight.md:12-14 APPEND_ONLY' when the commit removes or changes committed lines."""
    if lanes is None:
        return []
    findings = []
    for rel in lanes.append_only:
        if rel in changed:
            old, new = git_blob("HEAD:" + rel) or "", git_blob(":" + rel) or ""
            findings += [f"{rel}:{span} APPEND_ONLY" for span in line_spans(check_lane.removed_lines(old, new))]
    return findings


# UTF-8 text that was read as ANSI (cp1252) and saved again: a middle dot becomes 'A-circumflex + middle dot',
# an em dash becomes 'a-circumflex + euro + quote', an arrow 'a-circumflex + dagger + quote'.
MOJIBAKE = re.compile("\u00c2[\u00a0-\u00bf]|\u00c3[\u0080-\u00bf]|\u00e2\u20ac|\u00e2\u2020")


def scan_line(rel: str, lineno: int, text: str, values, terms) -> list[str]:
    hits = []
    if MOJIBAKE.search(text):
        hits.append(f"{rel}:{lineno} MOJIBAKE")
    for rx, rule in SECRET_PATTERNS:
        if rx.search(text):
            hits.append(f"{rel}:{lineno} {rule}")
    for key, value in values:
        if value in text:
            hits.append(f"{rel}:{lineno} ENV_VALUE:{key}")
    for term in terms:
        if term.search(text):
            hits.append(f"{rel}:{lineno} PRIVATE_TERM")
            break
    return hits


def utf8_text_blob(spec: str) -> str | None:
    """Like git_blob, but None for binary content (NUL byte or not valid UTF-8) and for large files."""
    proc = subprocess.run(["git", "cat-file", "blob", spec], capture_output=True, cwd=ROOT, check=False)
    data = proc.stdout
    if proc.returncode != 0 or len(data) > MAX_SCAN_BYTES or b"\0" in data:
        return None
    try:
        return data.decode("utf-8").replace("\r\n", "\n")
    except UnicodeDecodeError:
        return None


def not_utf8_check() -> list[str]:
    """'done_highlight.md:2471 NOT_UTF8' for an added line that is not valid UTF-8 (a file saved as ANSI/cp1252: a
    middle dot is the raw byte 0xB7). git hands such lines on with replacement characters, and the next tool that
    reads the file as UTF-8 fails; only added lines count, so a file that already holds such bytes still commits."""
    proc = subprocess.run(["git", "diff", "--cached", "-U0", "--no-color", "--diff-filter=ACMR"], capture_output=True,
                          cwd=ROOT, check=False)
    findings, current, lineno = [], None, 0
    for raw in proc.stdout.split(b"\n"):
        if raw.startswith(b"+++ "):
            current = raw[6:].decode("utf-8", errors="replace").rstrip("\r") if raw.startswith(b"+++ b/") else None
            continue
        if raw.startswith(b"@@"):
            m = re.search(rb"\+(\d+)", raw)
            lineno = int(m.group(1)) if m else 0
            continue
        if current and raw.startswith(b"+") and not raw.startswith(b"+++"):
            try:
                raw[1:].decode("utf-8")
            except UnicodeDecodeError:
                findings.append(f"{current}:{lineno} NOT_UTF8")
            lineno += 1
    return findings


def encoding_loss_check(modified: list[str]) -> list[str]:
    """'plan.md:12-14 ENCODING_LOSS' when committed non-ASCII characters became '?' (file saved as ASCII)."""
    findings = []
    for rel in modified:
        old = utf8_text_blob("HEAD:" + rel)
        if old is None or old.isascii():
            continue
        new = utf8_text_blob(":" + rel)
        if new is None:
            continue
        findings += [f"{rel}:{span} ENCODING_LOSS"
                     for span in line_spans(restore_unicode.loss_lines(old, new))]
    return findings


def forbidden(rel: str) -> list[str]:
    rel = rel.replace("\\", "/")
    return [f"{rel}:0 {rule}" for rx, rule in FORBIDDEN_PATHS if rx.search(rel)]


def check_staged(values, terms, agent: str = "", lanes: check_lane.Lanes | None = None) -> list[str]:
    findings = []
    staged = [p for p in git("diff", "--cached", "--name-only", "--diff-filter=ACMR").splitlines() if p]
    for rel in staged:
        findings += forbidden(rel)
    changed = staged_changes()  # lanes and append-only also look at deletions and renames
    findings += lane_check(changed, agent, lanes)
    findings += append_only_check(changed, lanes)
    findings += encoding_loss_check(
        [p for p in git("diff", "--cached", "--name-only", "--diff-filter=M").splitlines() if p])
    findings += not_utf8_check()
    current, lineno = None, 0
    for line in git("diff", "--cached", "-U0", "--no-color", "--diff-filter=ACMR").splitlines():
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else None
            continue
        if line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            lineno = int(m.group(1)) if m else 0
            continue
        if current and line.startswith("+") and not line.startswith("+++"):
            if current != PRIVATE_TERMS_FILE:
                findings += scan_line(current, lineno, line[1:], values, terms)
            lineno += 1
    return findings


def check_all(values, terms) -> list[str]:
    findings = []
    files = [p for p in git("ls-files", "-co", "--exclude-standard").splitlines() if p]
    for rel in files:
        findings += forbidden(rel)
        path = os.path.join(ROOT, rel)
        if rel == PRIVATE_TERMS_FILE or not os.path.isfile(path) or os.path.getsize(path) > MAX_SCAN_BYTES:
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                for i, text in enumerate(fh, 1):
                    findings += scan_line(rel, i, text, values, terms)
        except (UnicodeDecodeError, OSError):
            continue  # binary or unreadable file
    return findings


def fix_hints(problems: list[str], findings: list[str], agent: str) -> list[str]:
    """What the committer should do about each kind of problem (never contains a value)."""
    rules = " ".join(findings)
    hints = []
    if problems:
        hints += [
            "  - JARVIS_AGENT: set JARVIS_AGENT=cursor (Cursor) or JARVIS_AGENT=claude (Claude) on the commit command"
            " and name your paths:",
            '        JARVIS_AGENT=cursor git commit -m "[cursor] X-NN: <summary>" -- <paths>',
            '        JARVIS_AGENT=claude git commit -m "[claude] C-NN: <summary>" -- <paths>',
            "      PowerShell: $env:JARVIS_AGENT='cursor'; git commit -m \"[cursor] X-NN: <summary>\" -- <paths>",
            "      New files need 'git add <your new files>' first. JARVIS_AGENT=user is only for the user.",
        ]
    if "OTHER_LANE" in rules or "NO_LANE" in rules:
        tag = "C-NN" if agent == "claude" else "X-NN"
        hints += [
            f"  - OTHER_LANE / NO_LANE: commit only files of the {agent} lane and name them explicitly:",
            f'        JARVIS_AGENT={agent} git commit -m "[{agent}] {tag}: <summary>" -- <your paths>',
            "      Another lane's file: ask its owner in plan.md section 11 (Handoff) instead of committing it.",
            "      Owner of a path: python scripts/coord/check_lane.py --whoowns <path>"
            "   (lanes: .coord/lanes.json = plan.md section 2.1)",
            "      A path in no lane (NO_LANE) is first added to .coord/lanes.json by Claude.",
        ]
    if "APPEND_ONLY" in rules:
        names = ", ".join(sorted({f.split(":", 1)[0] for f in findings if f.endswith(" APPEND_ONLY")}))
        hints += [
            f"  - APPEND_ONLY: {names} is append-only. Put the removed or changed lines back exactly as committed",
            f"      (compare: git diff HEAD -- {names}) and add your new entry only at the end of the file.",
        ]
    if "LANES_FILE" in rules:
        hints.append("  - LANES_FILE: .coord/lanes.json is missing or not valid; Claude restores it (plan task C-05).")
    if "MOJIBAKE" in rules:
        hints += [
            "  - MOJIBAKE: the text was read as ANSI and saved as UTF-8 (double encoding, e.g. a middle dot shows as"
            " 'A-circumflex + dot').",
            "      Edit the file with your editor's own UTF-8 edit tool, not PowerShell 5.1 Get-Content/Set-Content.",
            "      If PowerShell is needed: Get-Content -Encoding UTF8 ... | Set-Content -Encoding UTF8 ...",
            "      For plan.md / done_highlight.md, ask Claude in plan.md section 11 to repair it.",
            "      Never 'fix' it by saving the file as ASCII: that turns the characters into '?' (ENCODING_LOSS).",
        ]
    if "NOT_UTF8" in rules:
        hints += [
            "  - NOT_UTF8: the added lines were saved as ANSI (cp1252), not UTF-8 (a middle dot became the raw byte 0xB7).",
            "      Save the file as UTF-8 with your editor's own edit tool; in PowerShell use Set-Content -Encoding UTF8",
            "      (or Out-File -Encoding utf8), never the default ANSI encoding of PowerShell 5.1.",
        ]
    if "ENCODING_LOSS" in rules:
        names = sorted({f.split(":", 1)[0] for f in findings if f.endswith(" ENCODING_LOSS")})
        hints += [
            "  - ENCODING_LOSS: the file was saved as ASCII/ANSI, so committed characters (middle dot, arrows, Urdu,"
            " emoji) became '?'.",
            "      Put them back (keeps your own edits; dry run first, then --write), then stage the file again:",
        ]
        hints += [f"        python scripts/coord/restore_unicode.py {n} --write" for n in names]
        hints.append("      Edit text only with your editor's UTF-8 edit tool; never write files with PowerShell"
                     " Set-Content/Out-File or encoding='ascii'.")
    return hints


def main() -> int:
    values, terms = env_values(), private_terms()
    problems: list[str] = []
    agent = ""
    if "--all-files" in sys.argv:
        findings = check_all(values, terms)
    else:
        agent, agent_problem = current_agent()
        lanes, lanes_problem = commit_lanes()
        problems = [agent_problem] if agent_problem else []
        findings = check_staged(values, terms, agent, lanes)
        if lanes_problem:
            findings.append(lanes_problem)
    if findings or problems:
        print("JARVIS pre-commit guard: blocked. Fix these before committing (values are never printed):")
        for f in problems + sorted(set(findings)):
            print("  " + f)
        hints = fix_hints(problems, findings, agent)
        if hints:
            print("How to fix:")
            print("\n".join(hints))
        return 1
    print("JARVIS pre-commit guard: clean." + (f" ({AGENT_ENV}={agent})" if agent else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
