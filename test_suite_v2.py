# ruff: noqa: T201, PLC0415

import asyncio
import os
import sys

# Define project root
project_root = r"d:\Personal-Assistant-main"
sys.path.append(project_root)
os.environ["PYTHONPATH"] = project_root

async def run_visual_verification():
    print("🚀 [STARTING v2 VERIFICATION]")

    from services.automation.jarvis_notepad_automation import write_custom_code

    # Unified HTML/CSS/JS code string
    code = """<!DOCTYPE html>
<html lang='en'>
<head>
    <meta charset='UTF-8'>
    <style>
        body { background: #0f172a; color: #38bdf8; display: flex; align-items: center; justify-content: center; height: 100vh; font-family: sans-serif; }
        .box { border: 2px solid #38bdf8; padding: 20px; border-radius: 15px; box-shadow: 0 0 20px #38bdf8; }
    </style>
</head>
<body>
    <div class='box'>
        <h1>JARVIS 3x SPEED OK</h1>
        <p id='timer'>Closing in 30s...</p>
    </div>
    <script>
        let s = 30;
        setInterval(() => { if(s > 0) s--; document.getElementById('timer').innerText = `Closing in ${s}s...`; }, 1000);
    </script>
</body>
</html>"""

    print("\n--- Testing 3x Typing Speed & Browser Auto-Close ---")
    # This should:
    # 1. Type in Notepad (Fast)
    # 2. Save and Close Notepad
    # 3. Open Browser
    # 4. START 30s timer
    result = await write_custom_code(code, "unified_test_v2.html", auto_run=True)
    print(f"Agent Result: {result}")

    print("\n[VITAL]: Browser is open. Starting 35s verification wait for auto-close...")
    await asyncio.sleep(35)
    print("Verification wait finished. Check if browser is closed.")

if __name__ == "__main__":
    asyncio.run(run_visual_verification())
