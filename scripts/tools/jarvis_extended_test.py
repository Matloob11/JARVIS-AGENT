"""
==========================================================
  JARVIS Extended Automation Test Suite
  Tested Tools: Screen Vision, Camera Vision, Email, RAG
  Run: .venv_312/Scripts/python.exe scripts/tools/jarvis_extended_test.py
==========================================================
"""

import asyncio
import os
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime

# ── Bootstrap ────────────────────────────────────────────────────────────────
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "../../.env"))

# ── Colors (ANSI) ─────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"

# ── Result Store ──────────────────────────────────────────────────────────────
@dataclass
class TestResult:
    tool_name: str
    category: str
    status: str          # "PASS" | "FAIL" | "SKIP"
    duration_ms: int
    output_preview: str
    error: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().strftime("%H:%M:%S"))

results: list[TestResult] = []

# ── Logging Helpers ───────────────────────────────────────────────────────────
def banner(title: str) -> None:
    width = 60
    print(f"\n{BOLD}{CYAN}{'=' * width}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{BOLD}{CYAN}{'=' * width}{RESET}\n")

def step(msg: str) -> None:
    print(f"  {YELLOW}>>>{RESET} {msg}")

def log_pass(tool: str, preview: str, ms: int) -> None:
    print(f"  {GREEN}[PASS]{RESET} {BOLD}{tool}{RESET} ({ms}ms)")
    print(f"        {DIM}{preview[:120].strip()}{RESET}\n")

def log_fail(tool: str, error: str, ms: int) -> None:
    print(f"  {RED}[FAIL]{RESET} {BOLD}{tool}{RESET} ({ms}ms)")
    print(f"        {RED}{error[:160].strip()}{RESET}\n")

def log_skip(tool: str, reason: str) -> None:
    print(f"  {YELLOW}[SKIP]{RESET} {BOLD}{tool}{RESET}")
    print(f"        {DIM}{reason}{RESET}\n")

# ── Runner Helper ─────────────────────────────────────────────────────────────
async def run_test(
    category: str,
    tool_name: str,
    coro,
    skip_reason: str = "",
    expect_key: str | None = "status",
    expect_val: str | None = "success",
) -> None:
    if skip_reason:
        log_skip(tool_name, skip_reason)
        results.append(TestResult(tool_name, category, "SKIP", 0, skip_reason))
        return

    step(f"Running: {tool_name} ...")
    t0 = time.monotonic()
    try:
        result = await asyncio.wait_for(coro, timeout=60)
        ms = int((time.monotonic() - t0) * 1000)

        # Determine pass/fail
        if isinstance(result, dict):
            if expect_key is None:
                passed = True
            else:
                status_val = result.get(expect_key, "")
                if expect_val is None:
                    passed = bool(status_val)
                else:
                    passed = str(status_val).lower() == str(expect_val).lower()
            preview = result.get("message", str(result))[:300]
        elif isinstance(result, str):
            passed = len(result.strip()) > 3
            preview = result[:300]
        else:
            passed = result is not None
            preview = str(result)[:300]

        if passed:
            log_pass(tool_name, preview, ms)
            results.append(TestResult(tool_name, category, "PASS", ms, preview))
        else:
            err = f"Unexpected result: {str(result)[:200]}"
            log_fail(tool_name, err, ms)
            results.append(TestResult(tool_name, category, "FAIL", ms, preview, err))

    except asyncio.TimeoutError:
        ms = int((time.monotonic() - t0) * 1000)
        log_fail(tool_name, "TIMEOUT (60s exceeded)", ms)
        results.append(TestResult(tool_name, category, "FAIL", ms, "", "Timeout"))
    except Exception as e:
        ms = int((time.monotonic() - t0) * 1000)
        tb = traceback.format_exc().strip().split("\n")[-1]
        log_fail(tool_name, f"{type(e).__name__}: {e} | {tb}", ms)
        results.append(TestResult(tool_name, category, "FAIL", ms, "", str(e)))

# ── Tests ───────────────────────────────────────────────────────────────────

async def test_vision_tools():
    banner("VISION TOOLS")
    
    from services.ai_core.jarvis_vision import analyze_screen, analyze_camera
    
    # Screen Analysis (might need a real display/PyAutoGUI)
    # This might fail in a headless environments or VM
    await run_test("Vision", "analyze_screen", analyze_screen("What is on my screen? (Short summary)"))
    
    # Camera Analysis (might fail if no webcam hardware present)
    await run_test("Vision", "analyze_camera", analyze_camera("What do you see in the camera?"))

async def test_rag_tools():
    banner("RAG TOOLS (PDF / DOCX)")
    
    from services.ai_core.jarvis_rag import ask_about_document, rag_system
    
    # Register common test directory for RAG
    rag_system.search_dirs = [os.path.abspath("Jarvis_Outputs"), "D:/Personal-Assistant-main/Jarvis_Outputs"]
    
    # Test Word Document RAG (using the file we just created)
    await run_test("RAG", "ask_about_document [DOCX]", 
                   ask_about_document(doc_name="test_doc.docx", question="Who is the creator?"))

    # If I could create a PDF, I would test it too.
    # But let's check for any existing PDF first.
    # actually I already searched and found none.

async def test_advanced_tools():
    banner("ADVANCED TOOLS (EMAIL)")
    
    from services.multimedia.jarvis_advanced_tools import send_email, download_images
    
    # Test Image Download
    await run_test("Advanced", "download_images [Python logo]", 
                   download_images(query="python_programming_logo", count=1))
    
    # Test Email
    # Sending a test email to a dummy address (or the same address)
    email_user = os.getenv("EMAIL_USER")
    if email_user:
        await run_test("Advanced", "send_email [Test]", 
                       send_email(recipient=email_user, subject="JARVIS TEST", body="Auto-generated test email from JARVIS."))
    else:
        log_skip("send_email", "EMAIL_USER not set in .env")

# ── Summary & Main ────────────────────────────────────────────────────────────

def print_summary():
    total   = len(results)
    passed  = sum(1 for r in results if r.status == "PASS")
    failed  = sum(1 for r in results if r.status == "FAIL")
    skipped = sum(1 for r in results if r.status == "SKIP")

    print(f"\n{BOLD}{'=' * 60}{RESET}")
    print(f"{BOLD}  EXTENDED TEST SUMMARY{RESET}")
    print(f"{'=' * 60}")
    print(f"  Total  : {total}")
    print(f"  Passed : {passed}")
    print(f"  Failed : {failed}")
    print(f"  Skipped: {skipped}")
    print(f"{'=' * 60}\n")

async def main():
    banner("JARVIS Extended Test Suite")
    
    await test_vision_tools()
    await test_rag_tools()
    await test_advanced_tools()
    
    print_summary()

if __name__ == "__main__":
    asyncio.run(main())
