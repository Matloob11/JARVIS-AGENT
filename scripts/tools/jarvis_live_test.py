"""
==========================================================
  JARVIS Full Automation Test Suite
  Real-time live logging + Final HTML Report
  Run: .venv_312/Scripts/python.exe scripts/tools/jarvis_live_test.py
==========================================================
EXCLUDED TOOLS (truly destructive only):
  - system shutdown / sleep / lock / restart
  - close Antigravity / IDE / any critical window
"""

import asyncio
import os
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

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
        result = await asyncio.wait_for(coro, timeout=30)
        ms = int((time.monotonic() - t0) * 1000)

        # Determine pass/fail
        if isinstance(result, dict):
            if expect_key is None:
                # Any dict response is a pass
                passed = True
            else:
                status_val = result.get(expect_key, "")
                if expect_val is None:
                    passed = bool(status_val)   # just needs to have the key
                else:
                    passed = status_val == expect_val
            preview = result.get("message", str(result))[:200]
        elif isinstance(result, str):
            passed = len(result.strip()) > 3
            preview = result[:200]
        else:
            passed = result is not None
            preview = str(result)[:200]

        if passed:
            log_pass(tool_name, preview, ms)
            results.append(TestResult(tool_name, category, "PASS", ms, preview))
        else:
            err = f"Unexpected result: {str(result)[:200]}"
            log_fail(tool_name, err, ms)
            results.append(TestResult(tool_name, category, "FAIL", ms, preview, err))

    except asyncio.TimeoutError:
        ms = int((time.monotonic() - t0) * 1000)
        log_fail(tool_name, "TIMEOUT (30s exceeded)", ms)
        results.append(TestResult(tool_name, category, "FAIL", ms, "", "Timeout"))
    except Exception as e:  # pylint: disable=broad-exception-caught
        ms = int((time.monotonic() - t0) * 1000)
        tb = traceback.format_exc().strip().split("\n")[-1]
        log_fail(tool_name, f"{type(e).__name__}: {e} | {tb}", ms)
        results.append(TestResult(tool_name, category, "FAIL", ms, "", str(e)))


# ═══════════════════════════════════════════════════════════════
#  TEST CATEGORIES
# ═══════════════════════════════════════════════════════════════

async def test_info_tools() -> None:
    banner("INFO TOOLS")

    from services.info.jarvis_search import search_internet, get_formatted_datetime
    from services.info.jarvis_get_weather import get_weather
    from services.info.jarvis_researcher import perform_web_research

    await run_test("Info", "get_formatted_datetime", get_formatted_datetime(),
                   expect_key="formatted", expect_val=None)

    await run_test("Info", "search_internet [Tavily/Google/DDG]",
                   search_internet("Pakistan latest news today"))

    await run_test("Info", "get_weather [Lahore]",
                   get_weather("Lahore"))

    await run_test("Info", "perform_web_research",
                   perform_web_research("What is asyncio in Python?"),
                   expect_key=None)


async def test_system_tools() -> None:
    banner("SYSTEM TOOLS")

    from services.system.jarvis_system_info import get_laptop_info
    from services.system.jarvis_file_server import start_file_access_server, stop_file_access_server

    # Battery info (returns plain string)
    await run_test("System", "get_laptop_info", get_laptop_info(),
                   expect_key=None)

    # File server — start on port 8765 (test port)
    await run_test("System", "start_file_access_server",
                   start_file_access_server(port=8765))

    # Stop it right after
    await run_test("System", "stop_file_access_server",
                   stop_file_access_server())

    # SKIPPED (destructive)
    log_skip("system_shutdown", "Destructive — EXCLUDED from test suite")
    log_skip("system_sleep", "Destructive — EXCLUDED from test suite")
    log_skip("system_lock", "Destructive — EXCLUDED from test suite")
    log_skip("system_restart", "Destructive — EXCLUDED from test suite")
    results.extend([
        TestResult("system_shutdown", "System", "SKIP", 0, "Destructive"),
        TestResult("system_sleep", "System", "SKIP", 0, "Destructive"),
        TestResult("system_lock", "System", "SKIP", 0, "Destructive"),
        TestResult("system_restart", "System", "SKIP", 0, "Destructive"),
    ])


async def test_multimedia_tools() -> None:
    banner("MULTIMEDIA TOOLS")

    from services.multimedia.jarvis_qr_gen import generate_qr_code
    from services.multimedia.jarvis_file_opener import play_file
    from services.multimedia.jarvis_image_gen import generate_image
    from services.multimedia.jarvis_youtube_downloader import download_youtube_media

    # QR Code generation
    await run_test("Multimedia", "generate_qr_code",
                   generate_qr_code("https://github.com/Matloob11", "test_jarvis_qr.png"))

    # play_file — open a known file we already created in this test run
    step("Running: play_file [jarvis_test_output.py] ...")
    print(f"  {YELLOW}  NOTE: Will open jarvis_test_output.py briefly.{RESET}")
    await run_test("Multimedia", "play_file [jarvis_test_output.py]",
                   play_file("jarvis_test_output.py"),
                   expect_key=None)

    # Image generation — uses free Pollinations.ai fallback (no HF token needed)
    step("Running: generate_image [Pollinations] ...")
    print(f"  {YELLOW}  NOTE: Calls Pollinations.ai (free, no credits).{RESET}")
    await run_test("Multimedia", "generate_image [Pollinations]",
                   generate_image("a glowing blue AI robot assistant in a futuristic lab"),
                   expect_key="status",
                   expect_val="success")

    # YouTube downloader — short BBC clip (audio MP3)
    step("Running: download_youtube_media [audio] ...")
    print(f"  {YELLOW}  NOTE: Downloads a short YouTube clip as audio.{RESET}")
    await run_test("Multimedia", "download_youtube_media [audio]",
                   download_youtube_media(
                       "https://www.youtube.com/watch?v=jNQXAC9IVRw",  # Me at the zoo — 18s
                       download_type="audio",
                   ),
                   expect_key=None)


async def test_automation_tools() -> None:
    banner("AUTOMATION TOOLS")

    from services.automation.jarvis_notepad_automation import (
        create_template_code,
        write_custom_code,
    )
    from services.automation.jarvis_reminders import set_reminder, list_reminders

    # Write custom Python code to file (no GUI, auto_run=False)
    await run_test("Automation", "write_custom_code [Python]",
                   write_custom_code(
                       content='print("Hello from JARVIS test!")',
                       filename="jarvis_test_output.py",
                       auto_run=False
                   ))

    # Create template — python_hello (no run, no GUI needed)
    await run_test("Automation", "create_template_code [python_hello]",
                   create_template_code(code_type="python_hello", filename="jarvis_hello_test.py", auto_run=False))

    # Reminders (set_reminder takes time_str first, then message)
    await run_test("Automation", "set_reminder",
                   set_reminder(time_str="1 minute", message="Test reminder from JARVIS test suite"),
                   expect_key=None)

    await run_test("Automation", "list_reminders", list_reminders(), expect_key=None)

    # ── WhatsApp ── live test: send message to Matloob
    from services.automation.jarvis_whatsapp_automation import automate_whatsapp
    step("Running: whatsapp_send_message [Matloob] ...")
    print(f"  {YELLOW}  NOTE: WhatsApp Desktop will open briefly then close.{RESET}")
    await run_test(
        "Automation", "whatsapp_send_message [Matloob]",
        automate_whatsapp(
            contact_name="Matloob",
            message="JARVIS Test Suite: Automated message delivery confirmed! — Verified by JARVIS",
            close_after=True,
        ),
        expect_key=None,
    )

    # ── Keyboard / Mouse controls ──────────────────────────────────────────
    from services.automation.keyboard_mouse_ctrl import (
        type_text_tool, mouse_click_tool, move_cursor_tool,
        scroll_cursor_tool, press_key_tool, control_volume_tool,
        press_hotkey_tool, set_volume_tool, swipe_gesture_tool,
    )

    # keyboard_type_text — open Notepad, type, then close
    step("Running: keyboard_type_text [safe: Notepad] ...")
    print(f"  {YELLOW}  NOTE: Will briefly open Notepad, type, then close.{RESET}")
    _proc = subprocess.Popen(["notepad.exe"])
    await asyncio.sleep(1.5)
    await run_test("Automation", "keyboard_type_text [Notepad]",
                   type_text_tool("JARVIS test keyboard input — all systems nominal!"),
                   expect_key=None)
    subprocess.run(["taskkill", "/f", "/im", "notepad.exe"], capture_output=True, check=False)

    # press_key — press a harmless key (F15 doesn't do anything)
    await run_test("Automation", "press_key_tool [a]",
                   press_key_tool("a"), expect_key=None)

    # press_hotkey — Ctrl+Z (undo, harmless without active editor)
    await run_test("Automation", "press_hotkey_tool [Ctrl+Z]",
                   press_hotkey_tool(["ctrl", "z"]), expect_key=None)

    # volume control — mute then immediately unmute
    await run_test("Automation", "control_volume_tool [mute]",
                   control_volume_tool("mute"), expect_key=None)
    await asyncio.sleep(0.5)
    await run_test("Automation", "control_volume_tool [unmute]",
                   control_volume_tool("unmute"), expect_key=None)

    # set_volume_tool — set to 50%
    await run_test("Automation", "set_volume_tool [50%]",
                   set_volume_tool(50), expect_key=None)

    # scroll cursor — scroll down 3 clicks (very harmless)
    await run_test("Automation", "scroll_cursor_tool [down]",
                   scroll_cursor_tool("down", 3), expect_key=None)

    # move cursor — move right 50px
    await run_test("Automation", "move_cursor_tool [right 50px]",
                   move_cursor_tool("right", 50), expect_key=None)

    # mouse_click — left click at current position
    await run_test("Automation", "mouse_click_tool [left]",
                   mouse_click_tool("left"), expect_key=None)

    # swipe_gesture — swipe down (like scrolling)
    await run_test("Automation", "swipe_gesture_tool [down]",
                   swipe_gesture_tool("down"), expect_key=None)



async def test_ai_tools() -> None:
    banner("AI CORE TOOLS — SWARM MODE")

    from services.ai_core.swarm_manager import swarm_coordinator

    if not swarm_coordinator.is_active:
        log_skip("swarm_coordinator", "GOOGLE_API_KEY not set")
        results.append(TestResult("swarm_coordinator", "AI", "SKIP", 0, "No API key"))
        return

    # Decomposition test
    step("Testing task decomposition...")
    t0 = time.monotonic()
    try:
        tasks = await asyncio.wait_for(
            swarm_coordinator._decompose_task("Explain Python decorators and write an example"),
            timeout=30
        )
        ms = int((time.monotonic() - t0) * 1000)
        if tasks and len(tasks) >= 1:
            log_pass("swarm._decompose_task", f"Got {len(tasks)} sub-tasks: {[t['role'] for t in tasks]}", ms)
            results.append(TestResult("swarm._decompose_task", "AI", "PASS", ms,
                                      f"{len(tasks)} tasks decomposed"))
        else:
            log_fail("swarm._decompose_task", "Empty task list", ms)
            results.append(TestResult("swarm._decompose_task", "AI", "FAIL", ms, "", "Empty"))
    except Exception as e:  # pylint: disable=broad-exception-caught
        ms = int((time.monotonic() - t0) * 1000)
        log_fail("swarm._decompose_task", str(e), ms)
        results.append(TestResult("swarm._decompose_task", "AI", "FAIL", ms, "", str(e)))

    # Full swarm execution — wait 3s first to avoid immediate 429 after decompose
    step("Testing full swarm execution (parallel agents)...")
    await asyncio.sleep(3)  # cooldown between back-to-back Gemini calls
    t0 = time.monotonic()
    try:
        report = await asyncio.wait_for(
            swarm_coordinator.execute_swarm("What is Python's GIL and how does asyncio work with it?"),
            timeout=90
        )
        ms = int((time.monotonic() - t0) * 1000)
        if report and len(report) > 100:
            log_pass("swarm.execute_swarm [full]", report[:150], ms)
            results.append(TestResult("swarm.execute_swarm", "AI", "PASS", ms, report[:200]))
        else:
            log_fail("swarm.execute_swarm [full]", f"Short/empty response: {report[:100]}", ms)
            results.append(TestResult("swarm.execute_swarm", "AI", "FAIL", ms, "", "Short response"))
    except Exception as e:  # pylint: disable=broad-exception-caught
        ms = int((time.monotonic() - t0) * 1000)
        log_fail("swarm.execute_swarm [full]", str(e), ms)
        results.append(TestResult("swarm.execute_swarm", "AI", "FAIL", ms, "", str(e)))


# ═══════════════════════════════════════════════════════════════
#  FINAL HTML REPORT
# ═══════════════════════════════════════════════════════════════

def generate_html_report() -> str:
    total   = len(results)
    passed  = sum(1 for r in results if r.status == "PASS")
    failed  = sum(1 for r in results if r.status == "FAIL")
    skipped = sum(1 for r in results if r.status == "SKIP")
    pct     = int((passed / (passed + failed)) * 100) if (passed + failed) > 0 else 100

    colour_map = {"PASS": "#2ecc71", "FAIL": "#e74c3c", "SKIP": "#f39c12"}
    rows = ""
    for r in results:
        col  = colour_map.get(r.status, "#888")
        err  = r.error or "—"
        rows += f"""
        <tr>
          <td>{r.timestamp}</td>
          <td><b>{r.category}</b></td>
          <td>{r.tool_name}</td>
          <td style="color:{col};font-weight:bold">{r.status}</td>
          <td>{r.duration_ms} ms</td>
          <td style="font-size:12px;max-width:280px;overflow:hidden">{r.output_preview[:100] or err}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>JARVIS Live Test Report</title>
<style>
  :root {{ --bg:#0d1117; --card:#161b22; --txt:#c9d1d9; --border:#30363d;
           --green:#2ecc71; --red:#e74c3c; --yellow:#f39c12; --blue:#58a6ff; }}
  body {{ background:var(--bg); color:var(--txt); font-family:'Segoe UI',sans-serif;
          margin:0; padding:24px; }}
  h1   {{ color:var(--blue); font-size:28px; margin-bottom:4px; }}
  h2   {{ color:var(--blue); font-size:18px; margin:24px 0 8px; }}
  .subtitle {{ color:#8b949e; margin-bottom:24px; }}
  .stats   {{ display:flex; gap:16px; flex-wrap:wrap; margin-bottom:28px; }}
  .card    {{ background:var(--card); border:1px solid var(--border); border-radius:12px;
              padding:20px 28px; min-width:140px; }}
  .card .num  {{ font-size:36px; font-weight:700; }}
  .card .lbl  {{ font-size:13px; color:#8b949e; margin-top:4px; }}
  .pass {{ color:var(--green); }}
  .fail {{ color:var(--red); }}
  .skip {{ color:var(--yellow); }}
  .pct  {{ color:var(--blue); }}
  table   {{ width:100%; border-collapse:collapse; background:var(--card);
             border:1px solid var(--border); border-radius:12px; overflow:hidden; }}
  th      {{ background:#21262d; padding:10px 14px; text-align:left;
             font-size:12px; color:#8b949e; text-transform:uppercase; border-bottom:1px solid var(--border); }}
  td      {{ padding:10px 14px; border-bottom:1px solid var(--border); font-size:13px; }}
  tr:last-child td {{ border-bottom:none; }}
  tr:hover {{ background:#1c2128; }}
  .bar-outer {{ background:#21262d; border-radius:6px; height:12px; margin:12px 0; }}
  .bar-inner {{ background:var(--green); height:12px; border-radius:6px;
                width:{pct}%; transition:width 1s; }}
  footer   {{ margin-top:32px; color:#8b949e; font-size:12px; text-align:center; }}
</style>
</head>
<body>
<h1>&#129302; JARVIS Automation Test Report</h1>
<p class="subtitle">Generated: {datetime.now().strftime("%A, %B %d, %Y at %I:%M %p")} &nbsp;|&nbsp;
   Project: Personal-Assistant-main</p>

<div class="stats">
  <div class="card"><div class="num">{total}</div><div class="lbl">Total Tests</div></div>
  <div class="card"><div class="num pass">{passed}</div><div class="lbl">Passed</div></div>
  <div class="card"><div class="num fail">{failed}</div><div class="lbl">Failed</div></div>
  <div class="card"><div class="num skip">{skipped}</div><div class="lbl">Skipped</div></div>
  <div class="card"><div class="num pct">{pct}%</div><div class="lbl">Pass Rate</div></div>
</div>

<div class="bar-outer"><div class="bar-inner"></div></div>

<h2>Test Results</h2>
<table>
  <thead><tr>
    <th>Time</th><th>Category</th><th>Tool</th>
    <th>Status</th><th>Duration</th><th>Output / Error</th>
  </tr></thead>
  <tbody>{rows}</tbody>
</table>

<footer>JARVIS Live Test Suite &mdash; Verified by Matloob</footer>
</body></html>"""
    return html


# ═══════════════════════════════════════════════════════════════
#  CONSOLE SUMMARY
# ═══════════════════════════════════════════════════════════════

def print_summary() -> None:
    total   = len(results)
    passed  = sum(1 for r in results if r.status == "PASS")
    failed  = sum(1 for r in results if r.status == "FAIL")
    skipped = sum(1 for r in results if r.status == "SKIP")

    print(f"\n{BOLD}{'=' * 60}{RESET}")
    print(f"{BOLD}  JARVIS TEST SUMMARY{RESET}")
    print(f"{'=' * 60}")
    print(f"  Total  : {total}")
    print(f"  {GREEN}Passed : {passed}{RESET}")
    print(f"  {RED}Failed : {failed}{RESET}")
    print(f"  {YELLOW}Skipped: {skipped}{RESET}")
    print()

    if failed > 0:
        print(f"  {RED}FAILED TESTS:{RESET}")
        for r in results:
            if r.status == "FAIL":
                print(f"  {RED}  x {r.tool_name}{RESET}  -- {r.error[:80]}")
        print()

    pct = int((passed / (passed + failed)) * 100) if (passed + failed) > 0 else 100
    bar = "#" * (pct // 5) + "-" * (20 - pct // 5)
    colour = GREEN if pct >= 75 else (YELLOW if pct >= 50 else RED)
    print(f"  {colour}[{bar}] {pct}% pass rate{RESET}")
    print(f"{'=' * 60}\n")


# ═══════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════

async def main() -> None:
    banner("JARVIS Full Automation Test Suite — LIVE")
    print(f"  Started at : {datetime.now().strftime('%H:%M:%S')}")
    print(f"  Python     : {sys.version.split()[0]}")
    print(f"  CWD        : {os.getcwd()}")
    print()

    await test_info_tools()
    await test_system_tools()
    await test_multimedia_tools()
    await test_automation_tools()
    await test_ai_tools()

    print_summary()

    # Save HTML report
    report_path = os.path.join(os.path.dirname(__file__), "../../tmp/jarvis_test_report.html")
    report_path = os.path.abspath(report_path)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(generate_html_report())

    print(f"  {GREEN}HTML report saved:{RESET} {report_path}")

    # Open it automatically
    import webbrowser
    webbrowser.open(report_path)
    print(f"  {CYAN}Report opened in browser.{RESET}\n")


if __name__ == "__main__":
    asyncio.run(main())
