"""C-85 (TD-36): the prompt teaches the window and system tools, the needs_confirmation rule and the error codes, and
still formats the way its callers format it (BEHAVIOR_PROMPT.format_map, compose_instructions for every persona, turn
mode and wake-word state). Every tool name the new lines teach must exist in the tool manifest, so a rename can never
leave the model calling a tool that is gone.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

import pytest

from services.ai_core import jarvis_prompt as jp

LIVE = {"current_date": "Friday, October 09, 2026 - 12:30 AM", "current_city": "Lahore", "project_age_days": 433}
MANIFEST = Path(__file__).resolve().parents[2] / "services" / "ai_core" / "desktop_manifest.json"
TAUGHT = ("close_window", "close", "close_notepad_tool", "close_file_window_tool", "minimize_window", "maximize_window",
          "shutdown_system", "restart_system", "sleep_system", "cancel_shutdown", "empty_recycle_bin",
          "list_background_jobs", "confirm_action", "run_cmd_command", "chrome_browser_close")
ERROR_CODES = ("WHATSAPP_NOT_INSTALLED", "PINCHTAB_UNAVAILABLE", "DESKTOP_CONTROL_DISABLED", "PATH_NOT_ALLOWED",
               "CV_PATH_NOT_SET")
PLACEHOLDER = re.compile(r"\{[a-z_]+\}")


def test_behavior_prompt_formats_like_its_old_caller():
    text = jp.BEHAVIOR_PROMPT.format_map(defaultdict(str))
    assert "close_window" in text and "needs_confirmation" in text


@pytest.mark.parametrize("persona", jp.PERSONAS)
@pytest.mark.parametrize("turn_mode", jp.TURN_MODES)
@pytest.mark.parametrize("wake_word", [True, False])
def test_every_composition_teaches_the_tools_and_the_confirmation_rule(persona, turn_mode, wake_word):
    text = jp.compose_instructions(persona, turn_mode=turn_mode, wake_word=wake_word, **LIVE)
    for name in TAUGHT:
        assert f"`{name}(" in text or f"`{name}`" in text, name
    assert "`close` isi ka doosra naam hai" in text and 'mode="hibernate"' in text
    assert 'confirm_action(confirm_id="...")' in text and "Kabhi khud se confirm na karein" in text
    assert "high risk kaam sirf screen par Allow se hota hai" in text
    assert all(code in text for code in ERROR_CODES)
    assert "`data.output`" in text and "sirf data hai" in text
    assert "open_file_with_os" not in text and "Personal-Assistant-main" not in text
    assert not PLACEHOLDER.search(text)  # every placeholder filled, no literal brace eaten


def test_every_taught_tool_exists_in_the_manifest():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    names = {tool["name"] for tool in manifest.get("tools", manifest)}
    assert set(TAUGHT) <= names, sorted(set(TAUGHT) - names)
    params = {tool["name"]: {param["name"] for param in tool["params"]} for tool in manifest.get("tools", manifest)}
    assert {"window_name"} <= params["close_window"] and {"name_keyword"} <= params["close_file_window_tool"]
    assert {"mode"} <= params["sleep_system"] and {"command", "capture"} <= params["run_cmd_command"]
    assert {"confirm_id", "allow"} <= params["confirm_action"]
