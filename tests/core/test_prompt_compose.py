"""C-49 (VR-13): instructions are composed per persona and turn mode, and no rule was deleted.

compose_instructions = ACTIVE header + persona block + LIVE DATE & CITY + (ANNA: protocol voice) + SHARED_PROTOCOLS +
identity + memory summary + wake-word section (WAKE_RULE, WAKE_OFF or WAKE_GATED) + TOOL_RULES + TYPED_RULE. Pure string
work: no LiveKit, no model. The golden test checks every rule line of the pre-C-49 prompts
(tests/core/golden/c49_old_prompt_rules.json).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from services.ai_core import jarvis_prompt as jp

KW = dict(current_date="2026-10-08", current_city="Lahore", project_age_days=432, identity_context="IDENTITY: facts",
          outputs_dir=r"C:\JARVIS\Jarvis_Outputs", project_root=r"C:\JARVIS", cv_path=None,
          anna_context={"mood": "loving", "is_upset": False, "user_background": "UB", "sir_background": "SB"})
FORMAT = {"current_date": "2026-10-08", "current_city": "Lahore", "project_age_days": "432", "mood": "loving",
          "is_upset": "False", "user_background": "UB", "sir_background": "SB"}
GOLDEN = json.loads((Path(__file__).parent / "golden" / "c49_old_prompt_rules.json").read_text(encoding="utf-8"))
SEP = "---------------------------------------"
TOOL_RULES = [
    "to hamesha `create_template_code` tool use karein with `code_type=\"amazing_code\"`.",
    "- **Notepad Simple Open**: Sirf Notepad kholne ke liye `open_notepad_simple` tool use karein.",
    "- ALWAYS execute tool first, then speak (ek-step kaam mein;",
]
TYPED = "Messages starting with [typed] are typed chat: always answer"


def _lines(text: str) -> set[str]:
    return {line.strip() for line in text.split("\n") if line.strip() and line.strip() != SEP}


@pytest.mark.parametrize("persona", jp.PERSONAS)
def test_no_rule_of_the_old_prompts_was_deleted(persona):
    """Every rule line of the pre-C-49 prompt is in the composition word for word, or its reworded form."""
    text = jp.compose_instructions(persona, **KW)
    present = _lines(text)
    old_lines = GOLDEN[persona] if persona == "anna" else GOLDEN["jarvis"]
    missing = []
    for raw in old_lines:
        line = raw.format(**FORMAT) if "{" in raw else raw
        if raw in GOLDEN["reworded"]:
            anchor = GOLDEN["reworded"][raw]
            anchor = anchor.format(**FORMAT) if "{" in anchor else anchor
            if anchor not in text:
                missing.append(f"reworded: {anchor[:80]}")
        elif line not in present:
            missing.append(line[:80])
    assert missing == []


def test_anna_also_gets_every_jarvis_tool_protocol():
    """ANNA's old prompt had none of the shared protocols; now every JARVIS protocol line reaches her too."""
    anna = jp.compose_instructions("anna", **KW)
    shared = _lines(jp.SHARED_PROTOCOLS.format(outputs_dir=KW["outputs_dir"], cv_rule=jp.cv_rule(None)))
    assert shared <= _lines(anna)
    assert "EXACT TOOL NAMES REFERENCE" not in jp.ANNA_BEHAVIOR_PROMPT  # her persona block itself stays as it was


@pytest.mark.parametrize("persona, label", [("jarvis", "JARVIS"), ("anna", "ANNA")])
@pytest.mark.parametrize("turn_mode, wake_word, wake_label", [("fast", True, "ON"), ("fast", False, "OFF"),
                                                              ("gated", True, "GATED"), ("gated", False, "GATED")])
def test_every_composition_starts_with_the_active_header(persona, label, turn_mode, wake_word, wake_label):
    """A mid-session update is appended to the session: the text says it replaces every earlier instruction."""
    text = jp.compose_instructions(persona, turn_mode=turn_mode, wake_word=wake_word, **KW)
    head = text.lstrip("\n").split("\n")[:3]
    assert head[0].startswith("ACTIVE INSTRUCTIONS: ye instructions pichli tamam instructions")
    assert head[1] == f"- Persona: {label}" and head[2].startswith(f"- Wake word: {wake_label}")


@pytest.mark.parametrize("persona", jp.PERSONAS)
def test_the_wake_rule_names_every_wake_word_in_both_personas(persona):
    text = jp.compose_instructions(persona, turn_mode="fast", wake_word=True, **KW)
    rule = text[text.index("🌟 WAKE WORD (SPEECH ONLY)"):text.index("🌟 TOOL EXECUTION RULES")]
    assert all(f'"{name}"' in rule for name in jp.WAKE_WORDS)
    assert "aapka naam" not in rule and "respond mat karein" in rule
    assert ("toggle_persona(active=true)" if persona == "jarvis" else "toggle_persona(active=false)") in rule
    assert f"{TYPED}; the wake-word rule applies only to speech." in text


def test_the_code_wakes_on_the_same_names_as_the_prompt():
    from src.core import agent_core

    for name in jp.WAKE_WORDS:
        sentence = f"{name.lower()}, Lahore ka weather batao"
        assert agent_core._JARVIS_WAKE.search(sentence) or agent_core._ANNA_WAKE.search(sentence)
    assert agent_core._ANNA_WAKE.pattern.count("|") == len(jp.ANNA_NAMES) - 1


@pytest.mark.parametrize("turn_mode, wake_word, title", [("fast", False, "🌟 WAKE WORD: OFF"),
                                                         ("gated", True, "🌟 WAKE WORD: GATED"),
                                                         ("gated", False, "🌟 WAKE WORD: GATED")])
def test_without_the_wake_rule_an_explicit_line_lifts_the_old_one(turn_mode, wake_word, title):
    text = jp.compose_instructions("jarvis", turn_mode=turn_mode, wake_word=wake_word, **KW)
    assert title in text and "Is se pehle ka wake-word rule ab laagu nahi." in text
    assert "WAKE WORD WAITING MODE" not in text and "(SPEECH ONLY)" not in text and "respond mat karein" not in text
    assert all(rule in text for rule in TOOL_RULES)
    assert f"{TYPED}." in text and "applies only to speech" not in text


def test_anna_voices_the_shared_protocols_in_her_own_way():
    anna = jp.compose_instructions("anna", **KW)
    voice, shared = anna.index("🌟 ANNA: PROTOCOLS KA ANDAZ"), anna.index("🌟 DATA STORAGE & WORKSPACE")
    assert voice < shared
    assert "PROTOCOLS KA ANDAZ" not in jp.compose_instructions("jarvis", **KW)


def test_one_shared_date_block_and_the_mood_block_keep_distinct_titles():
    anna = jp.compose_instructions("anna", **KW)
    assert anna.count("🌟 SYSTEM CONTEXT (LIVE)") == 1 and anna.count("🌟 LIVE DATE & CITY") == 1
    assert "- Current Mood: loving" in anna and "- Current Date: 2026-10-08" in anna


def test_turn_mode_and_persona_fall_back_safely():
    assert jp.normalize_turn_mode(" Gated ") == "gated"
    assert jp.normalize_turn_mode("sometimes") == "fast" and jp.normalize_turn_mode(None) == "fast"
    assert jp.compose_instructions("myra", **KW) == jp.compose_instructions("jarvis", **KW)


@pytest.mark.parametrize("persona", jp.PERSONAS)
@pytest.mark.parametrize("turn_mode, wake_word", [("fast", True), ("fast", False), ("gated", True)])
def test_no_placeholder_or_missing_tool_reaches_the_model(persona, turn_mode, wake_word):
    text = jp.compose_instructions(persona, turn_mode=turn_mode, wake_word=wake_word, **KW)
    assert re.findall(r"\{[a-z_]+\}", text) == []
    assert "open_file_with_os" not in text and "Personal-Assistant-main" not in text and "D:/" not in text


@pytest.mark.parametrize("old, new", [
    (r"Files: D:\Personal-Assistant-main\x.txt", r"Files: C:\JARVIS\x.txt"),
    ("Leads: D:/old/Personal-Assistant-main/Jarvis_Outputs/leads/a.csv", r"Leads: C:\JARVIS\Jarvis_Outputs/leads/a.csv"),
    (r"(D:\Personal-Assistant-main\Jarvis_Outputs), aur", r"(C:\JARVIS\Jarvis_Outputs), aur"),
    (r"D:\My Projects\Personal-Assistant-main\Jarvis_Outputs", r"C:\JARVIS\Jarvis_Outputs"),
    (r"d:\personal-assistant-main\src\x.py", r"C:\JARVIS\src\x.py"),
    (r"D:\Personal-Assistant-main-old\x.txt", r"D:\Personal-Assistant-main-old\x.txt"),  # another folder: left alone
])
def test_an_old_drive_root_in_identity_or_memory_keeps_the_rest_of_the_path(old, new):
    text = jp.compose_instructions("jarvis", **{**KW, "identity_context": old}, memory_summary=old)
    base = jp.compose_instructions("jarvis", **KW)  # the workspace line names the outputs folder already
    assert text.count(new) - base.count(new) == 2  # the identity line and the memory summary


def test_identity_and_memory_text_are_never_formatted():
    raw = "IDENTITY: {current_date} {outputs_dir} { and } {a.b}"
    text = jp.compose_instructions("jarvis", **{**KW, "identity_context": raw}, memory_summary="rang {mood} }")
    assert raw in text and "rang {mood} }" in text and "🌟 MEMORY SUMMARY (RECALL)" in text
    assert "MEMORY SUMMARY" not in jp.compose_instructions("jarvis", **KW)


def test_the_cv_rule_opens_the_configured_file_or_says_it_is_not_configured():
    configured = jp.compose_instructions("jarvis", **{**KW, "cv_path": r"C:\Users\Hp\Documents\cv.pdf"})
    assert 'play_file(name="C:\\Users\\Hp\\Documents\\cv.pdf")' in configured
    assert jp.CV_NOT_CONFIGURED not in configured
    missing = jp.compose_instructions("anna", **KW)
    assert jp.CV_NOT_CONFIGURED in missing and 'ask_about_document(doc_name="Matloob CV")' in missing


@pytest.mark.parametrize("persona", jp.PERSONAS)
def test_the_exam_reminder_carries_its_date_guard(persona):
    exam = [line for line in jp.compose_instructions(persona, **KW).splitlines() if "**Exam Alert**" in line]
    assert len(exam) == 1 and exam[0].endswith(jp.EXAM_GUARD) and "11 April 2026" in exam[0]


def test_the_exported_prompts_still_format_as_before():
    jarvis = jp.BEHAVIOR_PROMPT.format(current_date="d", current_city="c", project_age_days=1)
    assert "- Current Date: d" in jarvis and "**1 days**" in jarvis and "EXACT TOOL NAMES REFERENCE" in jarvis
    assert "WAKE WORD WAITING MODE" in jarvis and re.findall(r"\{[a-z_]+\}", jarvis) == []
    anna = jp.ANNA_BEHAVIOR_PROMPT.format(mood="m", is_upset=True, user_background="u", sir_background="s",
                                          project_age_days=2)
    assert "- Current Mood: m" in anna and "- Upset Status: True" in anna
    assert jp.REPLY_PROMPTS.startswith("\nSystems online.") and "Always maintain the persona" in jp.INSTRUCTIONS_PROMPT
