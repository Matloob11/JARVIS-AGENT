"""JARVIS pre-commit guard: blocks secrets, private terms and forbidden files (plan task C-04).

Usage:
    python scripts/coord/precommit.py              # check what is staged (run by .githooks/pre-commit)
    python scripts/coord/precommit.py --all-files  # check every tracked + untracked (not ignored) file

Prints only "path:line RULE" (never the secret value). Exit code 1 if anything is found.
"""
from __future__ import annotations

import fnmatch
import os
import re
import subprocess
import sys

ROOT = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True).stdout.strip()
ENV_FILES = [".env", "deploy/windows-local/.env", "deploy/hostinger/.env", "deploy/hostinger/bridge.env"]
PRIVATE_TERMS_FILE = ".coord/private_terms.txt"
MAX_SCAN_BYTES = 2_000_000

FORBIDDEN_PATHS = [
    (re.compile(r"(^|/)\.env$"), "ENV_FILE"),
    (re.compile(r"(^|/)\.env\.(?!example$|production\.example$)[^/]+$"), "ENV_FILE"),
    (re.compile(r"\.htpasswd$"), "HTPASSWD"),
    (re.compile(r"(^|/)bridge\.env$"), "BRIDGE_ENV"),
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


def env_values() -> list[tuple[str, str]]:
    """(KEY_NAME, value) for secret-like .env values (16+ chars) and private values (email, test numbers)."""
    out = []
    for rel in ENV_FILES:
        path = os.path.join(ROOT, rel)
        if not os.path.isfile(path):
            continue
        for line in open(path, encoding="utf-8", errors="replace"):
            m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line.rstrip("\r\n"))
            if not m:
                continue
            key, value = m.group(1), m.group(2).strip().strip('"').strip("'")
            if not value:
                continue
            if key == "WHATSAPP_TEST_NUMBERS":
                for num in value.split(","):
                    out += [(key, v) for v in phone_variants(num)]
            elif key in PRIVATE_VALUE_KEYS and len(value) >= 6:
                out.append((key, value))
            elif SECRET_KEY_NAME.search(key) and len(value) >= 16:
                out.append((key, value))
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


def lane_check(paths: list[str]) -> list[str]:
    """Hook point for plan task C-05 (lane enforcement). Returns findings like 'path LANE_VIOLATION'."""
    return []


def scan_line(rel: str, lineno: int, text: str, values, terms) -> list[str]:
    hits = []
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


def forbidden(rel: str) -> list[str]:
    rel = rel.replace("\\", "/")
    return [f"{rel}:0 {rule}" for rx, rule in FORBIDDEN_PATHS if rx.search(rel)]


def check_staged(values, terms) -> list[str]:
    findings = []
    staged = [p for p in git("diff", "--cached", "--name-only", "--diff-filter=ACMR").splitlines() if p]
    for rel in staged:
        findings += forbidden(rel)
    findings += lane_check(staged)
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


def main() -> int:
    values, terms = env_values(), private_terms()
    findings = check_all(values, terms) if "--all-files" in sys.argv else check_staged(values, terms)
    if findings:
        print("JARVIS pre-commit guard: blocked. Remove these before committing (values are never printed):")
        for f in sorted(set(findings)):
            print("  " + f)
        return 1
    print("JARVIS pre-commit guard: clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
