"""C-86 (TI-26): the prompt asks for plan progress on 3+ step tasks, teaches swarm and the long 'started' jobs, and
its self-healing rule is report-only on Sir's word: never write_custom_code or Notepad for project source files.
Every composition still formats (no literal brace in the new text, which goes through format_map), every taught tool
and argument exists in the manifest, and the prompt grows by at most 1.5 KB.
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
PLACEHOLDER = re.compile(r"\{[a-z_]+\}")
# The composed sizes (UTF-8 bytes) at 8883313, before C-86, with LIVE and the wake word on in fast mode.
BEFORE_C86 = {"jarvis": 19186, "anna": 17741}
GROWTH_LIMIT = 1536

RULES = {
    "progress": ('`tool_report_plan_progress(step_index=N, total_steps=T, action="...")`', "(N 1 se shuru)",
                 '`status="COMPLETED"`', '`status="FAILED"`', "Steps ke beech Sir ke jawab ka intezar na karein",
                 # the same rule again in the late TOOL RULES; live (14 Oct) with it, a clearly stated 3-step task
                 # reported 1/3, 2/3, 3/3 and COMPLETED in one run
                 "3 ya zyada steps ka kaam: har step se pehle `tool_report_plan_progress`"),
    "swarm": ('`start_swarm_mode(task="...")`', 'jab Sir "swarm" kahein'),
    "long_jobs": ("`download_youtube_media`, `autonomous_research_and_email` aur `start_swarm_mode` foran `started`",
                  "wahi call dobara na karein"),
    "self_healing": ('`autonomous_self_repair(error_details="...", file_path="...")` sirf jab Sir kahein',
                     "Default sirf report hai", "`apply=true` sirf Sir ke wazeh kehne par", "screen par Allow chahiye",
                     "agent restart ke baad", "`write_custom_code` ya Notepad se kabhi na badlein"),
}
TAUGHT_PARAMS = {
    "tool_report_plan_progress": {"step_index", "total_steps", "action", "status"},
    "start_swarm_mode": {"task"},
    "download_youtube_media": set(),
    "autonomous_research_and_email": set(),
    "autonomous_self_repair": {"error_details", "file_path", "apply"},
    "list_background_jobs": set(),
}


def _compositions():
    for persona in jp.PERSONAS:
        for turn_mode in jp.TURN_MODES:
            for wake_word in (True, False):
                yield persona, turn_mode, wake_word


@pytest.mark.parametrize("persona,turn_mode,wake_word", list(_compositions()))
def test_every_composition_carries_the_four_rules(persona, turn_mode, wake_word):
    text = jp.compose_instructions(persona, turn_mode=turn_mode, wake_word=wake_word, **LIVE)
    for rule, phrases in RULES.items():
        for phrase in phrases:
            assert phrase in text, (rule, phrase)
    assert not PLACEHOLDER.search(text)  # every placeholder filled, no literal brace eaten


@pytest.mark.parametrize("persona", jp.PERSONAS)
def test_the_old_notepad_self_repair_is_gone(persona):
    text = jp.compose_instructions(persona, **LIVE)
    assert "IMMORTAL MODE" not in text and "main ise khud repair kar raha hun" not in text
    assert "se edit karein aur verify karein ke Pylint score" not in text
    # write_custom_code stays taught for Notepad code; in the self-healing section it is only forbidden
    section = text.split("SELF-HEALING (SIRF SIR KE KEHNE PAR)", 1)[1].split("🌟", 1)[0]
    assert section.count("write_custom_code") == 1 and "kabhi na badlein" in section


def test_the_new_text_has_no_literal_brace_and_the_old_caller_still_formats():
    text = jp.BEHAVIOR_PROMPT.format_map(defaultdict(str))
    for phrases in RULES.values():
        for phrase in phrases:
            assert "{" not in phrase and "}" not in phrase
            assert phrase in text, phrase


@pytest.mark.parametrize("persona", jp.PERSONAS)
def test_the_prompt_grows_by_at_most_one_and_a_half_kb(persona):
    size = len(jp.compose_instructions(persona, turn_mode="fast", wake_word=True, **LIVE).encode("utf-8"))
    assert size - BEFORE_C86[persona] <= GROWTH_LIMIT, size - BEFORE_C86[persona]


def test_every_taught_tool_and_argument_exists_in_the_manifest():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    tools = manifest.get("tools", manifest)
    params = {tool["name"]: {param["name"] for param in tool["params"]} for tool in tools}
    missing = sorted(set(TAUGHT_PARAMS) - set(params))
    assert not missing, missing
    for name, wanted in TAUGHT_PARAMS.items():
        assert wanted <= params[name], (name, sorted(wanted - params[name]))
