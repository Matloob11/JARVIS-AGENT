import asyncio
from typing import Any

import requests

from services.ai_core.jarvis_plugin_manager import jarvis_tool
from services.utils.jarvis_logger import setup_logger

# Setup Logger
logger = setup_logger("JARVIS-CHROME-BROWSER")

# Config
# Note: PinchTab server hamesha Chrome engine use karta hai Sir Matloob ke liye.
PINCHTAB_BASE_URL = "http://localhost:9867"

class PinchTabSession:
    """Manages the current persistent Chrome browser session state."""
    instance_id: str | None = None
    tab_id: str | None = None
    mode: str | None = None

_session = PinchTabSession()

async def _api_call(method: str, path: str, payload: dict | None = None) -> dict[str, Any]:
    """Helper for Chrome/PinchTab HTTP communication."""
    url = f"{PINCHTAB_BASE_URL}{path}"

    def _do_request():
        try:
            if method == "POST":
                resp = requests.post(url, json=payload, timeout=20)
            else:
                resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"PinchTab/Chrome API Error at {path}: {e}")
            return {"error": str(e)}

    return await asyncio.to_thread(_do_request)

@jarvis_tool
async def automate_chrome_browser(url: str, mode: str = "headed") -> str:
    """
    Sir, main real Chrome browser ki madad se koi bhi website open kar sakta hoon.
    Voice command par main background (headless) ya desktop (headed) mode use karun ga.
    
    Arguments:
    - url: Website ka address (e.g. 'https://google.com')
    - mode: 'headed' (desktop par nazar ayega) ya 'headless' (background mein). Default is 'headed'.
    """
    clean_mode = mode.lower().strip()
    if clean_mode not in ["headless", "headed"]:
        clean_mode = "headed"

    # Step 1: Manage Instance
    if not _session.instance_id or _session.mode != clean_mode:
        # Check profiles/start instance
        prof_data = await _api_call("POST", "/profiles", {"name": "jarvis_default"})
        if "error" in prof_data:
            return "Sir, Chrome browser service (PinchTab) active nahi hai. Please ensure 'pinchtab.exe' is running on port 9867."

        prof_id = prof_data.get("id")
        # Ensure we request a clean Chrome instance
        inst_data = await _api_call("POST", "/instances/start", {"profileId": prof_id, "mode": clean_mode, "browser": "chrome"})
        if "error" in inst_data:
            return f"Sir, Chrome instance start karne mein masla aya: {inst_data['error']}"

        _session.instance_id = inst_data.get("id")
        _session.mode = clean_mode

    # Step 2: Open Tab
    nav_data = await _api_call("POST", f"/instances/{_session.instance_id}/tabs/open", {"url": url})
    if "error" in nav_data:
        return f"Sir, navigation failed: {nav_data['error']}"

    _session.tab_id = nav_data.get("tabId")
    return f"Sir, main ne Chrome mein '{url}' open kar di hai. Main ab is page ko read bhi kar sakta hoon aur click bhi."

@jarvis_tool
async def chrome_browser_read_page() -> str:
    """Read the visible text content of the currently open Chrome tab."""
    if not _session.tab_id:
        return "Sir, pehle 'automate_chrome_browser' use karke website open karein."

    data = await _api_call("GET", f"/tabs/{_session.tab_id}/snapshot?filter=interactive")
    if "error" in data:
        return f"Sir, page content read karne mein error aya: {data['error']}"

    text = data.get("text", "No content found")
    title = data.get("title", "Untitled Page")
    return f"Sir, Page Title: {title}\nSummary ye hai:\n\n{text[:1500]}..."

@jarvis_tool
async def chrome_browser_perform_action(action_type: str, element_ref: str, text_input: str | None = None) -> str:
    """
    Interact with Chrome page elements (click, type, press).
    - action_type: 'click', 'fill', or 'press'
    - element_ref: Element ID jo snapshot mein mili thi (e.g., 'e12')
    - text_input: Input ki surat mein text (e.g. search query)
    """
    if not _session.tab_id:
        return "Sir, browser session active nahi hai."

    payload = {"kind": action_type.lower(), "ref": element_ref}
    if text_input: payload["text"] = text_input

    res = await _api_call("POST", f"/tabs/{_session.tab_id}/action", payload)
    if "error" in res:
        return f"Sir, Chrome action failed: {res['error']}"

    return f"Sir, main ne '{action_type}' action successfully perform kar diya hai."

@jarvis_tool
async def chrome_browser_close() -> str:
    """Close the current Chrome browser instance and clean up."""
    if not _session.instance_id:
        return "Sir, koi active browser session nahi mil rahi."

    await _api_call("POST", f"/instances/{_session.instance_id}/stop")
    _session.instance_id = None
    _session.tab_id = None
    _session.mode = None
    return "Sir, Chrome browser close kar diya gaya hai."

