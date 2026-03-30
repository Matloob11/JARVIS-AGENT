# ruff: noqa: T201, PTH118, PTH110, ASYNC240, PLC0415

import asyncio
import os
import sys

# Define project root
project_root = r"d:\Personal-Assistant-main"
sys.path.append(project_root)

# Correct paths via environment
os.environ["PYTHONPATH"] = project_root

async def test_tools():
    print("--- Starting Agent Tool Verification ---")

    # Imports
    from services.automation.jarvis_notepad_automation import write_custom_code

    # 1. Test Notepad Code Animation and Workspace
    print("\n[TEST 1] Notepad Animation & Workspace")
    html_code = """<!DOCTYPE html>
<html>
<head><title>Test</title></head>
<body style='background: black; color: white;'>
    <h1>JARVIS OK!</h1>
</body>
</html>"""

    # Use write_custom_code which uses workspace and visual typing
    result = await write_custom_code(html_code, "test_verification.html", auto_run=False)
    print(f"Notepad Result: {result}")

    # Verify file in workspace
    workspace_file = os.path.join(project_root, "Jarvis_Outputs", "test_verification.html")
    if os.path.exists(workspace_file):
        print(f"✅ Workspace verification OK! File found: {workspace_file}")
    else:
        print("❌ Workspace verification FAILED! File not found in Jarvis_Outputs.")

    # 2. Test WhatsApp Delay (Wait for manual confirmation but trigger the flow)
    print("\n[TEST 2] WhatsApp Automation (Matloob)")
    # We will try to send a test message. If WhatsApp isn't open, it will try to open and focus.
    # Note: If it fails to select Matloob, it's fine for verification as we are checking the flow.
    from services.automation.jarvis_whatsapp_automation import automate_whatsapp
    wa_result = await automate_whatsapp("Matloob", "Assalam o alaikum Matloob Bhai, Main JARVIS hoon. Tool verification mukammal ho rahi hai. Ye message 5s delay check ke liye hai.", close_after=True)
    print(f"WhatsApp Result: {wa_result}")

if __name__ == "__main__":
    asyncio.run(test_tools())
