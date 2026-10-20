"""
# services/ai_core/desktop_proxy.py
The VPS brain's laptop tools (plan task C-119 steps 2-9; contracts K-71 Proxies and Drift, K-70 Outcomes, K-76
Approvals and Stops, K-13 v4, K-14 v4; docs/ARCHITECTURE_VPS_BRAIN.md section 8).

Remote mode only (JARVIS_DESKTOP_MODE=remote, the VPS brain; K-72): the PC brain (local mode) installs nothing and
keeps calling its local tools exactly as before.

- install(plugin_manager), in the C-32 prewarm right after discovery and before the VPS argument hooks and the
  LiveKit conversion: every laptop-placed tool of the committed manifest (services/ai_core/desktop_manifest.json,
  K-71) becomes a proxy with the same __name__, __qualname__, __module__, __doc__ and __signature__ (real types
  from the annotation whitelist, nothing eval'd), registered once through register_tool (exclusive None, retry 0,
  timeout = the K-70 link cap); a laptop tool an imported module registered is swapped for its proxy. update_tools
  is never called (with Gemini it forces a reconnect). A param_schema_sha256 mismatch logs ERROR, an lk_schema drift
  WARNING. Routed tools (zip_files, list_generated_media_tool) and VPS tools with a laptop fallback
  (download_youtube_media) get one K-11 wrapper that sends a call through the link when its route says laptop; such
  a call is asked with the laptop summary below, and one that stays on the VPS with the tool's own summary.
- A proxy call (the K-11 wrapper in remote mode): one tool_sync start/end with source 'desktop' under the wrapper's
  id; the manifest's argument policies; the static risk floor (the class floor: code_exec high, messaging,
  availability and exposure medium; the manifest's fixed risk; K-45 through the wrapper) goes to the ConfirmFlow
  broker before anything is sent (create_pending desktop=True), whose summary shows the normalised arguments
  (control, bidi and zero-width characters escaped, whitespace collapsed, metacharacters marked): the path first,
  then every argument but an empty default (flags such as auto_run included), a long value as its head and tail
  around '…(+N)…', all within the 300-character K-14 summary. A cut never hides a metacharacter mark or an escape
  silently: such a value shows every one of them with the text around it ('echo AAA…(+558)…AAA ⟦&⟧ del /s /q ...'),
  or, when they are too many for that, its marker names every kind it hides, those that chain or redirect a command
  and line breaks first ('…(+N: ⟦&⟧×12 \\n ⟦%⟧×3)…'; with the counts while they take at most half the value, and a
  long list takes room from the head and the tail). Only a list too long for the whole value fails closed: it says
  how many kinds it leaves out ('+4 aur qism'). The dashboard Allow is the only gate for code_exec, and its approval
  covers the whole arguments. The approved run sends the exec with the agent-signed approval reference {confirm_id,
  channel, risk, args_sha256} (jarvis_confirm.current_approval) only when that approval is this tool's, for these
  arguments and still unused: another action's approval (a task an approved run started inherits its context) is no
  approval, so the node asks for one. APPROVAL_REQUIRED from the
  node (dynamic risk: an ack refusal, or the 'not_started' result of its pre-run foreground re-check) becomes a broker
  item (desktop=True, high: the dashboard Allow covers any effective risk) whose approval re-sends the exec under the
  same call_id (the node judges a never-started call_id again). There is no laptop dialog (user decision D3).
- Outcomes come from services/ai_core/desktop_link.py: DESKTOP_OFFLINE, CLOCK_SKEW and STOPPED_BY_USER at once,
  DESKTOP_NOT_STARTED, DESKTOP_RESULT_UNKNOWN, DESKTOP_TIMEOUT, the node's codes as they are, and the node's K-12
  result (one that may have run in part, such as the node's own TIMEOUT or a tool that raised mid-send, arrives as
  DESKTOP_RESULT_UNKNOWN, never as a failure: desktop_link.partial_k12; a late one is announced the same way).
  Every one is a K-12 dict, never an exception, so no link problem counts as a circuit-breaker failure: the
  link gets what is left of the K-11 timeout (budget_s) and ends the call, cancel and grace included, before it.
  A code from the laptop is used only in the K-12 form (desktop_link.clean_code): the DESKTOP_CALL audit line and a
  delivery failure's chat line carry nothing else, and that failure is spoken as a fixed line (K-18 v17).
- Repeat guard: a messaging, code_exec, availability, exposure, type_text_tool or type_code_animation_tool call
  with the same args_sha256 within 120 s of one that was sent asks first through ConfirmFlow ('dobara karun?'). A
  send is recorded right before it goes out, so a parallel call or a retry after a cancelled wait sees it; only an
  outcome that proves the call never ran takes it back.
- Ordering (K-71, the node's C-120 SeqGate): a call the model made is submitted to the link synchronously in the
  proxy's first step, before its first await, naming its LiveKit speech (CallRequest.speech_id). The link sends calls
  in the order they were submitted and stamps seq {speech_id, n} right before it builds the exec (n counts the execs
  of that speech the link sent), so the node keeps the reply's order. Only an exec that is sent takes a number: a call
  answered here (a fast refusal, the repeat guard's question, APPROVAL_INVALID, BAD_ARGS) never reaches the link, and
  one the link answers without a send gives its number back, so the SeqGate never waits for a number no exec
  carries. Approved re-sends, the synchronous fallback and a VPS job's laptop re-run carry no seq.
- Lost outputs: a completed call whose output did not reach function_tools_executed (2 s after it ended, or once its
  speech was interrupted or done) is announced; so is a late outcome of an unknown, timed-out or stopped call, whose
  tool_sync end is also corrected under the same id. A stopped call whose late result shows it ended on the laptop
  before the stop is announced as done there only when its K-12 result says it was ('completed' is any tool that
  returned, an error included); else its own result is announced. A call the laptop's WAL reports ended without its
  result (the WAL's 'completed' is any end, failed and cancelled ones included) is announced as ended with its result
  unknown, never as done, and its row ends error with DESKTOP_RESULT_UNKNOWN.
- Stops: 'ruko' (a final voice transcript or typed text) and the dashboard's Stop end every laptop call that may
  still run (also one answered DESKTOP_RESULT_UNKNOWN after a link loss: desktop_link sends stop_all for it) and
  refuse new ones for 3 s with STOPPED_BY_USER; 'desktop band karo', 'desktop control band karo' or 'stop desktop'
  as the whole utterance (fillers such as 'Jarvis' or 'please' aside, like 'ruko'; Latin, Urdu or Devanagari script;
  voice or typed, matched before the model, and typed text never reaches the model; a sentence or question that
  only contains one is Sir's message for the model) sends the agent-signed desktop_stop, and Jarvis says the laptop
  is read-only until its tray switches back to Full. The link keeps the stop until the laptop reports read-only,
  also across an offline spell and, kept on disk, past the end of this job process; when it cannot be kept on disk
  Jarvis says it holds only for this session and names the dashboard's Stop desktop. Typed, Jarvis's answer comes
  after Sir's own message in the chat. A stop_all hint from the bridge (after a UI desktop_stop) ends in-flight
  calls the same way (desktop_link).
- End of a job: end_job(job_id) retires the process's link once no job of it uses the link (agent_runner calls it as
  a remote-mode job ends), so a draining job process never reconnects as the room's peer.
- Fallback: a VPS result with a code in the tool's fallback_on (YT_BOT_CHECK, MAPS_BLOCKED) and no side effect gets
  one re-run on the laptop; VPS runs of that tool then back off for 6 h (conversations/desktop_backoff.json). A VPS
  run that started a K-20 job (status 'started' with its job_id, as download_youtube_media does) gets the same check
  on the job's final result (jarvis_jobs.on_job_end), and that re-run's outcome is announced as what the laptop did:
  ran there, waits for an Allow, outcome not known yet, or could not run there either. No LLM turn waits for that
  re-run, so its exec has origin 'job' (K-70, K-76; D21's presence gate applies to it); the re-run inside the LLM
  call (a synchronous VPS result) and every other call the model made have origin 'llm', and a K-57 probe or canary
  room makes any of them 'canary'.
- Node window_context events go to assistant.vision_handler.on_window_context({title, ts}) (C-112).
Nothing here logs an argument or a result: call ids, tools, codes and timings only.
"""
# pylint: disable=too-many-lines  # one place for the proxies, their outcomes, approvals, stops and lost outputs

from __future__ import annotations

import asyncio
import functools
import inspect
import json
import math
import os
import re
import time
import unicodedata
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from services.ai_core import desktop_link, jarvis_confirm, jarvis_guard, tool_placement
from services.ai_core import jarvis_plugin_manager as plugin_module
from services.ai_core.desktop_link import (
    APPROVAL_REQUIRED,
    BAD_ARGS,
    DESKTOP_OFFLINE,
    NOT_STARTED,
    REFUSED,
    RESULT,
    SPEECH_ID_RE,
    CallOutcome,
    CallRequest,
    DesktopLink,
    LateOutcome,
    k12_error,
)
from services.utils import desktop_protocol as dp
from services.utils.jarvis_config import DESKTOP_MODE_REMOTE, desktop_mode
from services.utils.jarvis_logger import setup_logger

logger = setup_logger("DESKTOP-PROXY")

RISK_LEVELS = ("low", "medium", "high")
REPEAT_GUARD_S = 120.0  # K-71: the same side-effect call within 120 s asks first
REPEAT_TYPING_TOOLS = frozenset({"type_text_tool", "type_code_animation_tool"})
REPEAT_CLASSES = frozenset({tool_placement.MESSAGING, tool_placement.CODE_EXEC, tool_placement.AVAILABILITY,
                            tool_placement.EXPOSURE})
LOST_OUTPUT_S = 2.0  # a completed call whose output did not reach function_tools_executed by then
LOST_OUTPUT_RECHECK_S = 1.0
LOST_OUTPUT_MAX_S = 120.0  # a speech that never ends still gets its output announced
FALLBACK_BACKOFF_S = 6 * 3600.0  # K-71: VPS runs of a tool back off this long after a shared-IP block
BACKOFF_FILE = "desktop_backoff.json"
INPUT_LOCK_WAIT_S = 30.0  # K-11: how long an input call waits for the node's desktop_input lock
DEFAULT_TIMEOUT_S = 15.0
ENDED_JOBS_MAX = 64  # ended job ids remembered (end_job), so a late session start of one builds no link
SUMMARY_VALUE_MAX = 120  # normalise_value's default cut
SUMMARY_MAX = plugin_module.CONFIRM_SUMMARY_MAX  # K-14: a confirm_request summary is at most 300 characters
SUMMARY_VALUE_MIN = 24  # a long value is never cut shorter than this (its head and tail stay readable)
WINDOW_CONTEXT_MIN = 8  # a mark a cut would hide is shown with at least this much text on each side, else named
NOTE_MAX = 40  # a cut's list of the mark kinds it hides takes this much at first; a longer list takes more room
PROXY_FLAG = "__jarvis_desktop_proxy__"
DISPATCH_FLAG = "__jarvis_desktop_dispatch__"
UI_STOP_FLAG = "__jarvis_desktop_ui_stop__"
_Claim = tuple[tuple[str, str], float]  # one send in the repeat guard: ((tool, args_sha256), when it went out)
# The K-16 desktop_input tools: the node takes its input lock for each of them (src/core/desktop/executor.py).
K16_DESKTOP_INPUT = frozenset({
    "open_app", "save_notepad", "create_template_code", "write_custom_code", "edit_notepad_file_tool",
    "append_notepad_file_tool", "move_cursor_tool", "mouse_click_tool", "scroll_cursor_tool", "press_key_tool",
    "swipe_gesture_tool", "press_hotkey_tool", "type_text_tool", "type_code_animation_tool", "automate_whatsapp"})
# Shell and CMD metacharacters, marked in a confirmation summary (K-76).
_META_CHARS = frozenset("&|;<>^%!`$()\"'")
# What a cut's list names first (C-119 review): the marks that chain or redirect a command and the line breaks, then
# the escapes of the other invisible characters (controls, bidi, zero-width), then the other marks and \t.
_CHAIN_KINDS = frozenset([*(f"⟦{char}⟧" for char in "&|;<>`"), "\\n", "\\r"])
# Fail closed: a list that cannot name every hidden kind even with no head or tail left says how many it leaves out.
_UNNAMED_KINDS = "+{count} aur qism"
_ESCAPED_CATEGORIES = frozenset({"Cc", "Cf", "Co", "Cs", "Cn", "Zl", "Zp"})  # controls, bidi/zero-width, unassigned
_PATH_HOOKS = frozenset({"open_roots", "outputs_basename"})

# What Jarvis says (K-12/K-18: short Roman Urdu).
REPEAT_PREFIX = "Yeh laptop kaam {age} s pehle bhi bheja tha, dobara karun? "
APPROVAL_PREFIX = "Laptop ne is kaam ke liye ijazat maangi. "
STOP_DESKTOP_DONE = ("Sir, laptop ab read-only hai aur us ke chalte kaam rok diye gaye; wapas Full sirf laptop ke tray "
                     "se hoga.")
STOP_DESKTOP_QUEUED = ("Sir, laptop abhi online nahi; judte hi woh read-only ho jayega, aur wapas Full sirf laptop ke "
                       "tray se hoga.")
# The stop could not be kept on disk (desktop_link: desktop_stop_persisted False): it holds only while this job runs.
STOP_DESKTOP_QUEUED_SESSION = ("Sir, laptop abhi online nahi; yeh hukm sirf tab lagega jab laptop is session ke "
                               "dauran jude. Pakki rok ke liye dashboard par Stop desktop dabayein; wapas Full sirf "
                               "laptop ke tray se hoga.")
STOP_DESKTOP_NO_LINK = "Sir, laptop ka link is waqt chal nahi raha, is liye desktop band ka hukm bheja nahi ja saka."
_STOPPED_DONE = "Sir, rokne se pehle laptop par {tool} ho chuka tha."
# K-13 statuses (jarvis_plugin_manager.result_status) of a result that did what it was asked; only such a late result
# of a stopped call is announced as done (C-119 review), any other one with its own message
_DONE_STATUSES = frozenset({"success", "started"})
_LATE_RESULT = "Laptop par {tool} ka nateeja ab aaya: {message}"
_LATE_SPOKEN = "Sir, laptop par {tool} ka nateeja ab aaya hai, tafseel chat mein hai."
_LATE_NOT_STARTED = "Sir, laptop par {tool} shuru nahi hua tha."
_LATE_INTERRUPTED = "Sir, laptop par {tool} beech mein ruk gaya; ho sakta hai us ka kuch hissa ho chuka ho."
# The WAL's 'completed' without its result: it ended (done, failed, cancelled or timed out), the outcome is not known.
_LATE_COMPLETED = ("Sir, laptop par {tool} ab nahi chal raha, magar us ka nateeja maloom nahi ho saka; main ise "
                   "dobara nahi kar raha.")
_LOST_RESULT = "Laptop par {tool}: {message}"
_LOST_SPOKEN = "Sir, laptop par {tool} ka nateeja aa gaya hai, tafseel chat mein hai."
_DELIVERY_FAILED = "Sir, laptop par file ya link nahi khul saka ({code})."
_DELIVERY_FAILED_SPOKEN = "Sir, laptop par file ya link nahi khul saka, wajah chat mein hai."  # K-18 v17: fixed
_DELIVERY_FAILED_CODE = "DELIVERY_FAILED"  # shown when the laptop's code is not of the K-12 form
_DELIVERY_UNKNOWN = "Sir, laptop par file ya link khula ya nahi, yeh maloom nahi ho saka."
# Late states whose outcome is not known: it may have run, so never announced as done or as a failure.
_UNKNOWN_LATE_STATES = frozenset({"completed", "interrupted"})
# A VPS job's laptop re-run (step 9): what is said follows what the laptop did with it.
_FALLBACK_RESULT = "VPS par {tool} ruk gaya tha, laptop par dobara chalaya: {message}"
_FALLBACK_SPOKEN = "Sir, {tool} VPS par nahi chal saka, laptop par chalaya; nateeja chat mein hai."
_FALLBACK_NOT_RUN = "VPS par {tool} ruk gaya tha, aur laptop par bhi nahi chal saka: {message}"
_FALLBACK_NOT_RUN_SPOKEN = "Sir, {tool} na VPS par chal saka na laptop par; wajah chat mein hai."
_FALLBACK_ASKED = "VPS par {tool} ruk gaya tha; laptop par chalane ke liye ijazat chahiye: {message}"
_FALLBACK_ASKED_SPOKEN = "Sir, {tool} VPS par nahi chal saka; laptop par chalane ke liye dashboard par Allow chahiye."
_FALLBACK_UNKNOWN = "VPS par {tool} ruk gaya tha; laptop par bheja, magar nateeja abhi maloom nahi: {message}"
_FALLBACK_UNKNOWN_SPOKEN = ("Sir, {tool} VPS par nahi chal saka; laptop par bheja magar nateeja abhi maloom nahi, "
                            "tafseel chat mein hai.")
_SUMMARY_CUT_NOTE = " …(baqi arguments yahan nahi dikh sake)"

# 'desktop band karo' and friends (K-76: matched in the agent before the model, like 'ruko': the whole utterance,
# fillers aside); only ever tightens.
STOP_DESKTOP_PHRASES = frozenset({"desktop band karo", "desktop band kar do", "desktop band kardo",
                                  "desktop control band karo", "desktop control band kar do",
                                  "desktop control band kardo", "stop desktop"})
# An explicit stop: the whole utterance (fillers aside) is one of these.
EXPLICIT_STOP_PHRASES = frozenset({"ruko", "ruk jao", "ruko ruko", "ruk", "stop", "stop stop", "bas karo"})
_FILLERS = frozenset({"jarvis", "sir", "please", "plz", "bhai", "yaar", "ok", "okay", "acha", "achha", "ji"})
# The same phrases in Urdu and Devanagari script (C-119 review): the realtime model pins no transcription language
# (a final transcript may come as 'ڈیسک ٹاپ بند کرو' or 'रुको') and typed text goes to the model as it is. Each word
# of these scripts maps to its Latin form above, compared after the same folding (_fold), so the phrase sets and the
# whole-utterance rule stay the ones of the Latin phrases.
_SCRIPT_WORDS_RAW = {
    # Urdu (Arabic script)
    "ڈیسکٹاپ": "desktop", "کنٹرول": "control", "بند": "band", "کرو": "karo", "کر": "kar", "دو": "do",
    "کردو": "kardo", "اسٹاپ": "stop", "سٹاپ": "stop", "رکو": "ruko", "رک": "ruk", "جاؤ": "jao", "جاو": "jao",
    "بس": "bas", "جاروس": "jarvis", "جارویس": "jarvis", "سر": "sir", "پلیز": "please", "بھائی": "bhai",
    "یار": "yaar", "اوکے": "ok", "اچھا": "acha", "جی": "ji",
    # Devanagari
    "डेस्कटॉप": "desktop", "डेस्कटोप": "desktop", "कंट्रोल": "control", "कन्ट्रोल": "control", "बंद": "band",
    "बन्द": "band", "करो": "karo", "कर": "kar", "दो": "do", "करदो": "kardo", "स्टॉप": "stop", "स्टोप": "stop",
    "रुको": "ruko", "रुक": "ruk", "जाओ": "jao", "बस": "bas", "जार्विस": "jarvis", "जारविस": "jarvis", "सर": "sir",
    "प्लीज़": "please", "प्लीज": "please", "भाई": "bhai", "यार": "yaar", "ओके": "ok", "अच्छा": "acha", "जी": "ji",
}
# 'desktop' written as two words ('ڈیسک ٹاپ', 'डेस्क टॉप').
_SCRIPT_PAIRS_RAW = {("ڈیسک", "ٹاپ"): "desktop", ("डेस्क", "टॉप"): "desktop", ("डेस्क", "टोप"): "desktop"}
# Folding: marks that never change an Urdu word (harakat, superscript alef, Quranic signs; the hamza a decomposed
# 'ؤ' or 'ئ' leaves) and the Devanagari nukta are dropped, and so are the zero-width (non-)joiner and the tatweel,
# which only shape a word; Arabic yeh, alef maksura and kaf become the Urdu letters.
_DROPPED_MARKS = re.compile("[ؐ-ًؚ-ٰٟۖ-ۜ۟-۪ۨ-़ۭ"
                            "‌‍ـ]")
_LETTER_FORMS = str.maketrans({"ي": "ی", "ى": "ی", "ك": "ک"})


def _fold(text: str) -> str:
    """text as the phrase matcher compares it: NFKD, the _DROPPED_MARKS left out, every character that is not a
    letter, mark, digit or '_' a space (punctuation of any script: '!', '،', '-'), back to NFKC, lower case, Urdu
    letter forms, single spaces. Unlike r'\\w', marks are word characters: a Devanagari vowel sign such as the 'ु' of
    'रुको' never splits its word."""
    pieces = []
    for char in _DROPPED_MARKS.sub("", unicodedata.normalize("NFKD", text)):
        pieces.append(char if char == "_" or unicodedata.category(char)[0] in "LMN" else " ")
    return " ".join(unicodedata.normalize("NFKC", "".join(pieces)).lower().translate(_LETTER_FORMS).split())


_SCRIPT_WORDS = {_fold(word): latin for word, latin in _SCRIPT_WORDS_RAW.items()}
_SCRIPT_PAIRS = {(_fold(first), _fold(second)): latin for (first, second), latin in _SCRIPT_PAIRS_RAW.items()}


def _phrase_text(text: Any) -> str:
    return _fold(text) if isinstance(text, str) else ""


def _command(text: Any) -> str:
    """The utterance as a spoken command: lower case, punctuation gone, Urdu and Devanagari words in their Latin form,
    fillers such as 'Jarvis' or 'please' left out ('Jarvis, desktop band karo please!' and 'جاروس، ڈیسک ٹاپ بند کرو'
    are both 'desktop band karo')."""
    words, latin, index = _phrase_text(text).split(), [], 0
    while index < len(words):
        pair = _SCRIPT_PAIRS.get((words[index], words[index + 1])) if index + 1 < len(words) else None
        if pair is not None:
            latin.append(pair)
            index += 2
            continue
        latin.append(_SCRIPT_WORDS.get(words[index], words[index]))
        index += 1
    return " ".join(word for word in latin if word not in _FILLERS)


def is_stop_desktop(text: Any) -> bool:
    """True when the whole utterance (fillers aside) asks to stop desktop control ('desktop band karo', 'desktop
    control band karo', 'stop desktop'; in Latin, Urdu or Devanagari script), typed or spoken alike. A sentence that
    only contains one, such as a question ('how do I stop desktop sharing?'), a negation ('desktop band karo mat,
    ...') or 'stop desktop notifications', is Sir's message for the model, never a stop."""
    return _command(text) in STOP_DESKTOP_PHRASES


def is_explicit_stop(text: Any) -> bool:
    """True for an explicit stop such as 'ruko', 'رکو', 'रुको' or 'stop' (the whole utterance, fillers like 'Jarvis'
    aside)."""
    return _command(text) in EXPLICIT_STOP_PHRASES


def _max_risk(*levels: str) -> str:
    return max(levels, key=lambda level: RISK_LEVELS.index(level) if level in RISK_LEVELS else len(RISK_LEVELS))


_Spans = tuple[tuple[int, int], ...]  # (start, end) of each mark and escape in a normalised value


def _marker(hidden: int, note: str = "") -> str:
    """What stands for a hidden stretch of a value: '…(+N)…', or '…(+N: ⟦&⟧×2 \\n)…' naming the marks and escapes
    in it (K-76: a metacharacter is never hidden silently)."""
    return f"…(+{hidden}: {note})…" if note else f"…(+{hidden})…"


def _cut_sizes(length: int, width: int, reserve: int = 0) -> tuple[int, int]:
    """(head, tail): how much of a value's start and end fits within width around one marker, reserve characters
    kept for the marker's note."""
    keep = max(0, width - reserve - len(_marker(10)))
    for _ in range(4):  # the marker's own length depends on N's digits: settles within a few rounds
        fitted = max(0, width - reserve - len(_marker(length - keep)))
        if fitted == keep:
            break
        keep = fitted
    return (keep + 1) // 2, keep // 2


def _shorten(text: str, width: int) -> str:
    """text within width characters: its head and its tail around '…(+N)…', N the characters left out, so the start
    and the end of a long value stay visible (K-76)."""
    if len(text) <= width:
        return text
    head, tail = _cut_sizes(len(text), width)
    return text[:head] + _marker(len(text) - head - tail) + (text[len(text) - tail:] if tail else "")


def _windows(text: str, spans: _Spans, context: int) -> str:
    """text with every span (a mark or an escape) shown with up to context characters on each side, and its first
    and last context characters; each stretch between them becomes '…(+N)…' (a stretch no longer than that stays)."""
    length = len(text)
    wanted = sorted([(0, min(context, length)), (max(0, length - context), length)]
                    + [(max(0, start - context), min(length, end + context)) for start, end in spans])
    shown: list[list[int]] = []
    for start, end in wanted:
        if shown and start <= shown[-1][1]:
            shown[-1][1] = max(shown[-1][1], end)
        else:
            shown.append([start, end])
    parts: list[str] = []
    at = 0
    for start, end in shown:
        gap = start - at
        if gap > 0:
            parts.append(text[at:start] if gap <= len(_marker(gap)) else _marker(gap))
        parts.append(text[start:end])
        at = end
    return "".join(parts)


def _kind_rank(token: str) -> int:
    """Where a hidden kind goes in a cut's list: 0 for a mark that chains or redirects a command and a line break, 1
    for the escape of any other invisible character, 2 for the other marks and \\t."""
    if token in _CHAIN_KINDS:
        return 0
    return 1 if token.startswith("\\") and token != "\\t" else 2


def _hidden_kinds(text: str, spans: _Spans, head: int, tail_from: int) -> list[tuple[str, int]]:
    """Each kind of mark or escape between head and tail_from with its count, ranked (_kind_rank) and, within a rank,
    in the order they first appear."""
    counts: dict[str, int] = {}
    for start, end in spans:
        if end > head and start < tail_from:
            counts[text[start:end]] = counts.get(text[start:end], 0) + 1
    return sorted(counts.items(), key=lambda item: _kind_rank(item[0]))  # stable: first appearance within a rank


def _partial_note(kinds: list[tuple[str, int]], room: int) -> str:
    """Fail closed, for a list too long for the whole value: the first kinds in rank order that fit, then how many
    more it leaves out ('⟦&⟧ \\n +4 aur qism'), so the dialog says plainly that it does not name them all."""
    tokens = [token for token, _ in kinds]
    kept, used = 0, 0
    for token in tokens:
        if used + len(token) + 1 + len(_UNNAMED_KINDS.format(count=len(tokens) - kept - 1)) > room:
            break
        used += len(token) + 1
        kept += 1
    return " ".join([*tokens[:kept], _UNNAMED_KINDS.format(count=len(tokens) - kept)])


def _noted_cut(text: str, spans: _Spans, width: int) -> str:
    """text's head and tail around one marker that names every kind of mark and escape it hides ('…(+N: ⟦&⟧×12
    \\n)…'), for a value with too many of them to show each with its context (C-119 review: a kind is never left out
    of the list). The list starts with the kinds that chain or redirect a command and gives each kind's count (a kind
    seen once alone) while that takes at most half the value; past that it names each kind without a count. A list
    longer than NOTE_MAX takes the room it needs from the head and the tail, and only one too long for the whole
    value fails closed (_partial_note). The cut never splits a mark or an escape."""
    length = len(text)
    most = max(0, width - len(_marker(length)) - 2)  # the note's room with no head and no tail left
    room = min(NOTE_MAX, width // 3, most)
    counts_room = min(max(room, width // 2), most)  # the counts may take up to half the value
    while True:
        head, tail = _cut_sizes(length, width, reserve=room + 2)  # ': ' and the note
        tail_from = length - tail
        for start, end in spans:  # a mark the cut would split is hidden whole (the marker only grows by what it hides)
            if start < head < end:
                head = start
            if start < tail_from < end:
                tail_from = end
        kinds = _hidden_kinds(text, spans, head, tail_from)
        note = " ".join(token if count == 1 else f"{token}×{count}" for token, count in kinds)
        if len(note) > counts_room:
            note = " ".join(token for token, _ in kinds)
        if len(note) <= room:
            return text[:head] + _marker(tail_from - head, note) + text[tail_from:]
        if room >= most:
            return text[:head] + _marker(tail_from - head, _partial_note(kinds, room)) + text[tail_from:]
        room = min(most, len(note))  # more room for the list: a shorter head and tail (which only hide more)


def _cut(text: str, spans: _Spans, width: int) -> str:
    """A normalised value within width characters (K-76). A long one keeps its head and tail around '…(+N)…'; when
    that cut would hide a metacharacter mark or an escape, every one of them is shown with as much text around it as
    fits instead, and when they are too many for that, the marker names every kind it hides (_noted_cut): a dialog
    never hides one silently."""
    length = len(text)
    if length <= width:
        return text
    head, tail = _cut_sizes(length, width)
    if not any(end > head and start < length - tail for start, end in spans):
        return _shorten(text, width)
    if len(_windows(text, spans, WINDOW_CONTEXT_MIN)) > width:
        return _noted_cut(text, spans, width)
    low, high = WINDOW_CONTEXT_MIN, length
    while low < high:  # the widest context that still fits (the shown length only grows with the context)
        middle = (low + high + 1) // 2
        if len(_windows(text, spans, middle)) <= width:
            low = middle
        else:
            high = middle - 1
    return _windows(text, spans, low)


def _render(value: Any) -> tuple[str, _Spans]:
    """An argument as a confirmation summary shows it (K-76), and where its marks and escapes are: control, bidi,
    zero-width and other invisible characters escaped (\\n, \\x07, \\u202e), whitespace collapsed, shell
    metacharacters marked (⟦&⟧)."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    pieces: list[str] = []
    spans: list[tuple[int, int]] = []
    size, space = 0, False
    for char in text:
        if char in "\n\r\t":
            piece = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}[char]
        elif unicodedata.category(char) in _ESCAPED_CATEGORIES:
            code = ord(char)
            piece = f"\\x{code:02x}" if code < 0x100 else f"\\u{code:04x}" if code < 0x10000 else f"\\U{code:08x}"
        elif char.isspace():
            space = bool(pieces)  # a run of whitespace is one space between two shown pieces, none at either end
            continue
        elif char in _META_CHARS:
            piece = f"⟦{char}⟧"
        else:
            piece = char
        if space:
            pieces.append(" ")
            size, space = size + 1, False
        if len(piece) > 1:  # an escape or a mark
            spans.append((size, size + len(piece)))
        pieces.append(piece)
        size += len(piece)
    return "".join(pieces), tuple(spans)


def normalise_value(value: Any, limit: int | None = SUMMARY_VALUE_MAX) -> str:
    """An argument as a confirmation summary shows it (K-76): control, bidi, zero-width and other invisible
    characters escaped (\\n, \\x07, \\u202e), whitespace collapsed, shell metacharacters marked; longer than limit,
    it is cut (_cut: its head and tail around '…(+N)…', never hiding a mark or an escape silently; limit None: never
    cut)."""
    text, spans = _render(value)
    return text if limit is None else _cut(text, spans, max(limit, SUMMARY_VALUE_MIN))


def _fit_widths(lengths: list[int], room: int, floor: int) -> list[int]:
    """Widths for values of these lengths within room characters: the short ones stay whole, the long ones share
    what is left equally, never below floor."""
    widths = list(lengths)
    order = sorted(range(len(lengths)), key=lambda index: lengths[index])
    left = room
    for position, index in enumerate(order):
        share = left // (len(order) - position)
        if lengths[index] <= share:
            left -= lengths[index]
            continue
        for rest in order[position:]:
            widths[rest] = max(floor, min(lengths[rest], share))
        break
    return widths


def _empty(value: Any) -> bool:
    return value is None or (isinstance(value, (str, list, tuple, dict)) and not value)


# ---------------------------------------------------------------- the manifest's laptop tools


@dataclass(frozen=True)
class LaptopTool:
    """One laptop-capable tool of the committed manifest (K-71): what its proxy rebuilds and sends."""

    name: str
    module: str
    doc: str | None
    params: tuple[Mapping[str, Any], ...]
    meta: Mapping[str, Any]
    cls: str
    param_schema_sha256: str
    lk_schema_sha256: str | None
    timeout_s: float
    lock_wait_s: float
    path_args: tuple[str, ...]

    @property
    def cap_s(self) -> float:
        """The K-70 proxy cap: the proxy's K-11 timeout."""
        return desktop_link.link_cap(self.timeout_s, self.lock_wait_s)

    @property
    def defaults(self) -> dict[str, Any]:
        return {param["name"]: param.get("default") for param in self.params if not param.get("required")}

    def signature(self) -> inspect.Signature:
        """The tool's signature with real type objects (tool_placement.annotation_from_str; nothing is eval'd)."""
        return inspect.Signature([inspect.Parameter(
            param["name"], getattr(inspect.Parameter, param["kind"]),
            default=inspect.Parameter.empty if param["required"] else param["default"],
            annotation=tool_placement.annotation_from_str(param["annotation"])) for param in self.params])

    @property
    def repeat_guarded(self) -> bool:
        return self.cls in REPEAT_CLASSES or self.name in REPEAT_TYPING_TOOLS

    @property
    def static_risk(self) -> str:
        """The risk the agent asks at before sending (K-76): the class floor and the manifest's fixed risk; a
        'dynamic' risk is decided on the node (APPROVAL_REQUIRED). The K-11 wrapper adds K-45 on top."""
        fixed = self.meta.get("risk")
        base = fixed if fixed in RISK_LEVELS else "low"
        floor = ("high" if self.cls == tool_placement.CODE_EXEC else
                 "medium" if self.cls in tool_placement.CONFIRM_CLASSES else "low")
        return _max_risk(base, floor)


def laptop_tool(tool: Mapping[str, Any], entry: tool_placement.ToolPlacement | None) -> LaptopTool:
    """The LaptopTool of one manifest entry and its placement (timeout_override, class, path hooks)."""
    meta = dict(tool.get("meta") or {})
    timeout = (entry.timeout_override if entry is not None and entry.timeout_override else None) or meta.get("timeout")
    if not (isinstance(timeout, (int, float)) and not isinstance(timeout, bool) and 0 < timeout <= 3600):
        timeout = DEFAULT_TIMEOUT_S
    name = str(tool["name"])
    exclusive = meta.get("exclusive") == "desktop_input" or name in K16_DESKTOP_INPUT
    hooks = entry.arg_hooks if entry is not None else ()
    paths = [arg for hook, _, arg in (item.partition(":") for item in hooks) if hook in _PATH_HOOKS]
    paths += [arg for arg, policy in (meta.get("arg_policies") or {}).items() if policy == "path"]
    cls = (entry.cls if entry is not None and entry.cls else None) or str(tool.get("cls") or tool_placement.INPUT)
    return LaptopTool(name=name, module=str(tool.get("module") or __name__), doc=tool.get("doc"),
                      params=tuple(dict(param) for param in tool.get("params") or ()), meta=meta, cls=cls,
                      param_schema_sha256=str(tool.get("param_schema_sha256") or ""),
                      lk_schema_sha256=tool.get("lk_schema_sha256"), timeout_s=float(timeout),
                      lock_wait_s=INPUT_LOCK_WAIT_S if exclusive else 0.0, path_args=tuple(dict.fromkeys(paths)))


def laptop_summary(tool: LaptopTool, arguments: Mapping[str, Any], limit: int = SUMMARY_MAX) -> str:
    """The ConfirmFlow summary of a laptop action (K-76), at most limit characters: the tool, any path first, then
    every other argument, each normalised. Only an empty default is left out, so a flag at its default (auto_run,
    force, close_after) is shown too. When the values do not all fit, the longest ones are cut (_cut: head and tail
    around '…(+N)…', every hidden metacharacter mark or escape shown with its context, or its kind named in the
    marker); arguments that still do not fit end the summary with a note saying so."""
    defaults = tool.defaults
    items: list[tuple[str, str, _Spans, str]] = []  # (name, the full normalised value, its marks, tag)
    for name in tool.path_args:
        if not _empty(arguments.get(name)):
            items.append((name, *_render(arguments[name]), " [path]"))
    for name, value in arguments.items():
        if name in tool.path_args or (name in defaults and _empty(value) and value == defaults[name]):
            continue
        items.append((name, *_render(value), ""))
    head = f"Laptop: {tool.name}"

    def render(values: list[str]) -> str:
        if not items:
            return head
        return head + " - " + "; ".join(f"{name}={value}{tag}" for (name, _, _, tag), value in zip(items, values))

    text = render([full for _, full, _, _ in items])
    if len(text) <= limit:
        return text
    room = limit - len(render([""] * len(items)))
    widths = _fit_widths([len(full) for _, full, _, _ in items], room, SUMMARY_VALUE_MIN)
    text = render([_cut(full, spans, width) for (_, full, spans, _), width in zip(items, widths)])
    if len(text) > limit:  # too many arguments even at their shortest: say so instead of hiding it
        text = text[:max(0, limit - len(_SUMMARY_CUT_NOTE))] + _SUMMARY_CUT_NOTE
    return text


@dataclass(frozen=True)
class DesktopToolInfo:
    """What the K-11 wrapper reads from a function's __jarvis_desktop__ (jarvis_plugin_manager, C-119): whether one
    call goes to the laptop (tool_sync source 'desktop', create_pending desktop=True) and its K-76 class."""

    name: str
    cls: str | None
    route: Callable[[Mapping[str, Any]], bool]

    def goes_to_laptop(self, arguments: Mapping[str, Any]) -> bool:
        return bool(self.route(arguments))


def livekit_call_context() -> tuple[Any, str | None]:
    """The SpeechHandle and the LiveKit function call id of the tool call running in this task (LiveKit 1.4
    internals, read defensively): (None, None) outside one, or when a LiveKit release moved them."""
    speech: Any = None
    call_id: Any = None
    try:
        from livekit.agents.voice.agent_activity import (  # pylint: disable=import-outside-toplevel
            _SpeechHandleContextVar,
        )
        speech = _SpeechHandleContextVar.get(None)
    except Exception:  # pylint: disable=broad-exception-caught
        speech = None
    try:
        from livekit.agents.voice.agent import _get_activity_task_info  # pylint: disable=import-outside-toplevel
        task = asyncio.current_task()
        info = _get_activity_task_info(task) if task is not None else None
        call_id = getattr(getattr(info, "function_call", None), "call_id", None)
    except Exception:  # pylint: disable=broad-exception-caught
        call_id = None
    return speech, call_id if isinstance(call_id, str) else None


def _speech_id(speech: Any) -> str | None:
    """The id of the LiveKit speech a laptop call belongs to, when it has the K-70 seq speech_id form, else None (the
    call then carries no seq)."""
    speech_id = getattr(speech, "id", None)
    return speech_id if isinstance(speech_id, str) and SPEECH_ID_RE.fullmatch(speech_id) else None


def _bind(signature: inspect.Signature, args: tuple[Any, ...], kwargs: Mapping[str, Any]) -> dict[str, Any]:
    """A call's arguments by name with omitted ones at their defaults (the exec sends them all)."""
    try:
        bound = signature.bind_partial(*args, **kwargs)
    except TypeError:
        names = list(signature.parameters)
        return {**dict(zip(names, args)), **dict(kwargs)}
    bound.apply_defaults()
    return dict(bound.arguments)


def _preview(arguments: Mapping[str, Any]) -> str:
    """args_preview of a ConfirmFlow item this module creates: argument names only, never values."""
    return ", ".join(sorted(arguments))


def _summary(prefix: str, tool: LaptopTool, arguments: Mapping[str, Any]) -> str:
    """prefix plus the laptop summary, together within the K-14 summary limit."""
    return prefix + laptop_summary(tool, arguments, SUMMARY_MAX - len(prefix))


def _approval_for(tool: LaptopTool) -> jarvis_confirm.Approval | None:
    """The approval of the approved run in progress when it is this tool's and unused (K-76). Another action's
    approval, or a used one, is no approval for this call: a task an approved run started inherits the run's
    context, and nothing it does is approved by it."""
    approval = jarvis_confirm.current_approval()
    if approval is None or approval.tool != tool.name or approval.spent:
        return None
    return approval


def _never_ran(outcome: CallOutcome) -> bool:
    """Nothing reached the node, or the outcome proves the call never ran (its send leaves the repeat guard)."""
    return not outcome.sent or outcome.never_ran


def _done_result(result: Mapping[str, Any] | None) -> bool:
    """A late K-12 result that did what it was asked (K-13 success or started, as on_result counts 'ho gaya'); a
    completed call without one counts as success, as CallOutcome.k12 has it."""
    return not result or plugin_module.result_status(dict(result)) in _DONE_STATUSES


def _needs_approval(outcome: CallOutcome) -> bool:
    """The node asks for an approval (K-76): an ack refusal, or the 'not_started' result of its pre-run foreground
    re-check (effect none, WAL never_started), after which it judges the same call_id again."""
    if outcome.code != APPROVAL_REQUIRED:
        return False
    return outcome.kind == REFUSED or (outcome.kind == RESULT and outcome.status == "not_started"
                                       and outcome.effect == "none")


@dataclass
class _Watch:
    """A completed LLM laptop call until its output reaches function_tools_executed (or is announced as lost)."""

    tool: str
    result: Mapping[str, Any]
    fn_call_id: str | None
    speech: Any
    done_at: float
    delivered: bool = False


# ---------------------------------------------------------------- the controller


class DesktopProxies:  # pylint: disable=too-many-instance-attributes,too-many-public-methods  # one per process
    """The process's laptop proxies and their state (see the module docstring)."""

    def __init__(self, *, link_getter: Callable[[], DesktopLink | None] = desktop_link.current,
                 wall: Callable[[], float] = time.time, mono: Callable[[], float] = time.monotonic) -> None:
        self.link_getter = link_getter
        self.call_context: Callable[[], tuple[Any, str | None]] = livekit_call_context
        self._wall, self._mono = wall, mono
        self.tools: dict[str, LaptopTool] = {}
        self._recent: dict[tuple[str, str], list[float]] = {}  # (tool, args_sha256) -> its send times (mono)
        self._watches: list[_Watch] = []
        self._late_unsubscribe: Callable[[], None] | None = None
        self._event_unsubscribe: Callable[[], None] | None = None
        self._late_link: DesktopLink | None = None
        self._jobs: set[str] = set()  # the jobs of this process whose sessions use the link (attach_session)
        self._ended_jobs: OrderedDict[str, None] = OrderedDict()  # end_job: their late session starts get no link
        self._backoff: dict[str, float] | None = None
        self.backoff_path: Path | None = None
        self._tasks: set[asyncio.Task[Any]] = set()

    # --- helpers ----------------------------------------------------------------------------------------------

    def link(self) -> DesktopLink | None:
        try:
            return self.link_getter()
        except Exception:  # pylint: disable=broad-exception-caught  # no link is an honest DESKTOP_OFFLINE
            return None

    @staticmethod
    def broker() -> Any:
        """The ConfirmFlow broker, looked up at call time (tests replace the module's one)."""
        return jarvis_confirm.confirmation_broker

    def _spawn(self, coro: Any) -> asyncio.Task[Any] | None:
        """coro as a task on the running loop, kept referenced until it ends; without a loop it is closed (None)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            coro.close()
            return None
        task = loop.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    @staticmethod
    async def say(text: str, *, spoken: str | None = None, priority: str = "normal", kind: str = "desktop") -> None:
        """Shown in the chat and spoken (K-18 announcer); spoken replaces what is said (a result may hold untrusted
        text, which must never reach the model as instructions)."""
        from services.utils import jarvis_announcer  # pylint: disable=import-outside-toplevel

        try:
            await jarvis_announcer.announce(text, priority=priority, kind=kind, spoken_text=spoken)
        except Exception as exc:  # pylint: disable=broad-exception-caught  # an announcement never fails a call
            logger.warning("Desktop announcement not delivered (%s)", type(exc).__name__)

    @staticmethod
    def _audit(tool: str, call_id: str, kind: str, code: str | None) -> None:
        """DESKTOP_CALL line in logs/security.log: the call, never its arguments (design section 8). The code may come
        from the laptop: only its K-12 form is written (desktop_link.clean_code), so no line can be forged."""
        try:
            from services.utils import jarvis_audit  # pylint: disable=import-outside-toplevel
            jarvis_audit.JarvisAudit.log_event("DESKTOP_CALL", f"TOOL={tool} | CALL={call_id} | OUTCOME={kind} | "
                                                               f"CODE={desktop_link.clean_code(code) or '-'}")
        except Exception:  # pylint: disable=broad-exception-caught
            logger.debug("DESKTOP_CALL audit not written", exc_info=True)

    # --- install (C-32 prewarm) -------------------------------------------------------------------------------

    def install(self, manager: Any, *, manifest: Mapping[str, Any] | None = None,
                places: Mapping[str, Any] | None = None, remote: bool | None = None) -> dict[str, Any]:
        """Remote mode: the laptop proxies, the routed and fallback dispatchers (see the module docstring),
        registered once; a second call changes nothing. Local mode: nothing. Returns what it did."""
        if not (desktop_mode() == DESKTOP_MODE_REMOTE if remote is None else remote):
            return {"active": False}
        report: dict[str, Any] = {"active": True, "proxies": [], "swapped": [], "dispatchers": [], "missing": [],
                                  "schema_mismatch": [], "lk_drift": []}
        try:
            manifest = manifest if manifest is not None else tool_placement.load_manifest()
        except tool_placement.ManifestError as exc:
            logger.error("DESKTOP_PROXY not installed: the desktop manifest is unusable (%s)", exc)
            report["error"] = "manifest"
            return report
        tools = tool_placement.manifest_tools(manifest)
        if places is None:  # the K-71 table after the validated overrides (discovery logged the refused ones)
            overrides = tool_placement.parse_overrides(os.environ.get(tool_placement.OVERRIDES_ENV), tools)[0]
            places = tool_placement.effective_placements(overrides)
        lk_drift = tool_placement.lk_schema_drift(manifest)
        lock = getattr(manager, "_tools_lock", None) or _NullLock()
        with lock:
            registered = {getattr(tool, "__name__", ""): tool for tool in list(getattr(manager, "tools", []) or [])}
        for name in sorted(places):
            kind = tool_placement.placement_kind(places[name])
            entry = tool_placement.PLACEMENTS.get(name)
            tool = tools.get(name)
            existing = registered.get(name)
            if kind == tool_placement.LAPTOP:
                if tool is None:
                    report["missing"].append(name)
                    continue
                if existing is not None and getattr(existing, PROXY_FLAG, False):
                    continue  # registered once (a second prewarm in this process)
                if existing is not None:
                    if _live_schema(existing) != tool.get("param_schema_sha256"):  # the deployed code drifted
                        report["schema_mismatch"].append(name)
                        logger.error("DESKTOP_PROXY %s: the registered tool's parameters differ from the committed "
                                     "manifest (param_schema_sha256); the proxy follows the manifest", name)
                    _unregister(manager, name)
                    report["swapped"].append(name)
                self._register_proxy(manager, laptop_tool(tool, entry), report, check_lk=not lk_drift)
            elif _laptop_capable_vps(kind, entry) and tool is not None and existing is not None:
                if getattr(existing, DISPATCH_FLAG, False):
                    continue
                self._register_dispatcher(manager, existing, laptop_tool(tool, entry), entry, places[name])
                report["dispatchers"].append(name)
        if lk_drift:
            logger.warning("DESKTOP_PROXY lk_schema not compared, other library versions: %s", ", ".join(lk_drift))
        if report["lk_drift"]:
            logger.warning("DESKTOP_PROXY lk_schema drift (the LLM declaration differs from the manifest): %s",
                           ", ".join(report["lk_drift"]))
        if report["missing"]:
            logger.error("DESKTOP_PROXY laptop tools missing from the manifest: %s", ", ".join(report["missing"]))
        logger.info("DESKTOP_PROXY installed: %d laptop proxies (%d swapped), %d routed or fallback tools",
                    len(report["proxies"]), len(report["swapped"]), len(report["dispatchers"]))
        return report

    def build_proxy(self, tool: LaptopTool) -> Callable[..., Any]:
        """A proxy with the tool's own name, qualname, module, doc and signature (K-71)."""
        signature = tool.signature()
        controller = self

        async def proxy(*args: Any, **kwargs: Any) -> Any:
            return await controller.call_laptop(tool, signature, args, kwargs)

        proxy.__name__ = proxy.__qualname__ = tool.name
        proxy.__module__ = tool.module
        proxy.__doc__ = tool.doc
        proxy.__signature__ = signature  # type: ignore[attr-defined]
        proxy.__annotations__ = {param.name: param.annotation for param in signature.parameters.values()}
        setattr(proxy, "__jarvis_desktop__", DesktopToolInfo(tool.name, tool.cls, lambda _arguments: True))
        setattr(proxy, PROXY_FLAG, True)
        return proxy

    def _register_proxy(self, manager: Any, tool: LaptopTool, report: dict[str, Any], *, check_lk: bool) -> None:
        proxy = self.build_proxy(tool)
        meta = tool.meta
        manager.register_tool(
            proxy, retry_attempts=0, timeout=tool.cap_s, exclusive=None,
            arg_policies=dict(meta.get("arg_policies") or {}), risk=tool.static_risk,
            confirm_summary=functools.partial(self.confirm_summary, tool),
            max_output_chars=int(meta.get("max_output_chars") or plugin_module.DEFAULT_MAX_OUTPUT_CHARS),
            long_running=bool(meta.get("long_running")))
        self.tools[tool.name] = tool
        report["proxies"].append(tool.name)
        if _live_schema(proxy) != tool.param_schema_sha256:
            report["schema_mismatch"].append(tool.name)
            logger.error("DESKTOP_PROXY %s: its parameter schema differs from the manifest's param_schema_sha256",
                         tool.name)
        if check_lk and tool.lk_schema_sha256:
            try:
                drift = tool_placement.lk_schema_sha256(proxy) != tool.lk_schema_sha256
            except Exception:  # pylint: disable=broad-exception-caught
                drift = True
            if drift:
                report["lk_drift"].append(tool.name)

    def _register_dispatcher(self, manager: Any, existing: Any, tool: LaptopTool,
                             entry: tool_placement.ToolPlacement, place: Any) -> None:
        """A routed tool, or a VPS tool with a laptop fallback: one K-11 wrapper around a dispatcher that runs the
        original function on the VPS or sends the call to the laptop. A fallback tool's K-11 timeout is its VPS
        timeout plus the laptop cap, so its one re-run on the laptop still gets the whole cap."""
        original = getattr(existing, "__wrapped__", None)
        meta = dict(getattr(existing, "__jarvis_meta__", None) or {})
        if not callable(original):
            logger.error("DESKTOP_PROXY %s: no original function to route; left as it is", tool.name)
            return
        signature = inspect.signature(original)
        vps_timeout = meta.get("timeout") if isinstance(meta.get("timeout"), (int, float)) else DEFAULT_TIMEOUT_S
        controller = self
        routed = isinstance(place, tool_placement.Route)

        def goes_to_laptop(arguments: Mapping[str, Any]) -> bool:
            if routed:
                link = controller.link()
                return place(arguments, bool(link is not None and link.online())) == tool_placement.LAPTOP
            return controller.backoff_active(tool.name)

        @functools.wraps(original)
        async def dispatcher(*args: Any, **kwargs: Any) -> Any:
            arguments = _bind(signature, args, kwargs)
            if goes_to_laptop(arguments):
                return await controller.call_laptop(tool, signature, args, kwargs)
            result = await asyncio.wait_for(_run_original(original, args, kwargs), timeout=vps_timeout)
            if not routed:
                if _fallback_due(result, entry.fallback_on):
                    logger.warning("DESKTOP_FALLBACK %s: %s on the VPS; one re-run on the laptop, VPS runs back off "
                                   "%.0f h", tool.name, result.get("error_code"), FALLBACK_BACKOFF_S / 3600)
                    controller.set_backoff(tool.name)
                    return await controller.call_laptop(tool, signature, args, kwargs, stamp_seq=False)
                controller.watch_job_fallback(tool, arguments, result, entry.fallback_on)
            return result

        own_summary = meta.get("confirm_summary")

        def confirm_summary(arguments: Mapping[str, Any]) -> Any:
            """K-14 v4 / K-76: a call that goes to the laptop is asked with the laptop summary (the normalised
            arguments, bidi and zero-width characters escaped, the path named); one that stays on the VPS keeps the
            tool's own summary: its template, its callable, or (None) the wrapper's default."""
            if goes_to_laptop(arguments):
                return controller.confirm_summary(tool, arguments)
            if isinstance(own_summary, str):  # filled the way the wrapper fills a template (an unknown field stays)
                template_args = plugin_module._TemplateArgs  # pylint: disable=protected-access
                return own_summary.format_map(template_args(arguments))
            return own_summary(arguments) if callable(own_summary) else None

        dispatcher.__signature__ = signature  # type: ignore[attr-defined]
        setattr(dispatcher, "__jarvis_desktop__", DesktopToolInfo(tool.name, tool.cls, goes_to_laptop))
        setattr(dispatcher, DISPATCH_FLAG, True)
        _unregister(manager, tool.name)
        vps_s = float(vps_timeout or DEFAULT_TIMEOUT_S)
        timeout = max(vps_s, tool.cap_s) if routed else vps_s + tool.cap_s
        manager.register_tool(
            dispatcher, retry_attempts=0, timeout=timeout, arg_policies=dict(meta.get("arg_policies") or {}),
            risk=meta.get("risk"), confirm_summary=confirm_summary, voice_confirm=meta.get("voice_confirm"),
            exclusive=meta.get("exclusive"),
            max_output_chars=int(meta.get("max_output_chars") or plugin_module.DEFAULT_MAX_OUTPUT_CHARS),
            long_running=bool(meta.get("long_running")))
        self.tools[tool.name] = tool

    # --- one laptop call --------------------------------------------------------------------------------------

    def confirm_summary(self, tool: LaptopTool, arguments: Mapping[str, Any]) -> str:
        """The K-11 confirm_summary of a proxy (the static-risk ConfirmFlow): the normalised arguments, with
        'dobara karun?' when the same call was sent within the repeat window."""
        try:
            age = self._recent_age(tool.name, dp.args_sha256(dict(arguments)))
        except (dp.CanonicalJsonError, TypeError, ValueError):
            age = None
        if age is not None and tool.repeat_guarded:
            return _summary(REPEAT_PREFIX.format(age=int(age)), tool, arguments)
        return laptop_summary(tool, arguments)

    async def call_laptop(self, tool: LaptopTool, signature: inspect.Signature, args: tuple[Any, ...],
                          kwargs: Mapping[str, Any], *, stamp_seq: bool = True) -> Any:
        """A proxy's body: the call goes to the laptop through the link (see the module docstring)."""
        approval = _approval_for(tool)
        speech, fn_call_id = (None, None) if approval is not None else self.call_context()
        arguments = _bind(signature, args, kwargs)
        current = plugin_module.current_tool_call.get() or {}
        k13 = current.get("id") if current.get("tool") == tool.name else None
        return await self._call(tool, arguments, k13=k13, approval=approval, speech=speech, fn_call_id=fn_call_id,
                                budget_s=plugin_module.tool_time_left(tool.name), stamp_seq=stamp_seq)

    async def _call(self, tool: LaptopTool, arguments: dict[str, Any], *, k13: str | None,
                    approval: jarvis_confirm.Approval | None, speech: Any, fn_call_id: str | None,
                    budget_s: float | None = None, stamp_seq: bool = False, origin: str = "llm",
                    outcomes: list[CallOutcome] | None = None) -> Any:
        """One laptop call: the checks that answer at once, then the send and its outcome (handed to outcomes too,
        when given). Everything before the send runs synchronously, in the proxy's first step. origin is the K-70
        origin of the exec: 'llm' for a call the model made (also the synchronous fallback inside that call), 'job'
        for a VPS job's laptop re-run (_rerun_on_laptop); a K-57 probe or canary room makes it 'canary'."""
        # pylint: disable=too-many-arguments,too-many-return-statements
        name = tool.name
        try:
            digest = dp.args_sha256(arguments)
        except (dp.CanonicalJsonError, TypeError, ValueError):
            return k12_error(name, BAD_ARGS)
        link = self.link()
        code = DESKTOP_OFFLINE if link is None else link.fast_refusal()
        if code is not None or link is None:
            code = code or DESKTOP_OFFLINE
            self._audit(name, "-", "fast_refusal", code)
            return k12_error(name, code, status="cancelled" if code == desktop_link.STOPPED_BY_USER else "error")
        if approval is None and tool.repeat_guarded:
            age = self._recent_age(name, digest)
            if age is not None:
                logger.info("DESKTOP_REPEAT %s: the same call was sent %.0f s ago; asking first", name, age)
                request = self._request(tool, arguments, link, k13=k13, origin=origin)
                return await self._ask(tool, arguments, digest, request,
                                       summary=_summary(REPEAT_PREFIX.format(age=int(age)), tool, arguments),
                                       risk=_max_risk("medium", tool.static_risk))
        reference = None
        if approval is not None:
            if not approval.covers(name, digest):
                logger.warning("DESKTOP_APPROVAL %s: the approval is not for these arguments; not sent", name)
                return k12_error(name, "APPROVAL_INVALID")
            approval.spend()  # one exec per approval: nothing else in this run, or started by it, can use it again
            reference = approval.reference(digest)
        # ordering (K-71): the call names its speech, and the link numbers it (seq n) right before it builds the exec,
        # so only an exec that is sent takes a number; this send is submitted synchronously, so in call order
        speech_id = _speech_id(speech) if stamp_seq else None
        request = self._request(tool, arguments, link, k13=k13, approval=reference, budget_s=budget_s,
                                origin=origin, speech_id=speech_id)
        claim = self._claim(name, digest)  # before the send: a parallel call or a later retry sees it at once
        outcome = await self._await(link, request, tool, claim)
        if outcomes is not None:
            outcomes.append(outcome)
        return await self._after(tool, arguments, digest, request, outcome, claim=claim, approval=approval,
                                 speech=speech, fn_call_id=fn_call_id)

    def _request(self, tool: LaptopTool, arguments: Mapping[str, Any], link: DesktopLink, *, k13: str | None,
                 approval: Mapping[str, Any] | None = None, budget_s: float | None = None, origin: str = "llm",
                 speech_id: str | None = None) -> CallRequest:
        # pylint: disable=too-many-arguments
        risk = jarvis_guard.effective_risk(tool.name, tool.static_risk, arguments)  # as the wrapper's tool_sync shows
        return CallRequest(tool=tool.name, args=dict(arguments), param_schema_sha256=tool.param_schema_sha256,
                           timeout_s=tool.timeout_s, lock_wait_s=tool.lock_wait_s,
                           origin=desktop_link.origin_for(link.room, origin), approval=approval,
                           label={"k13": k13, "tool": tool.name, "risk": risk}, budget_s=budget_s,
                           speech_id=speech_id)

    async def _await(self, link: DesktopLink, request: CallRequest, tool: LaptopTool, claim: _Claim) -> CallOutcome:
        """The call's outcome from the link loop. The laptop call is never cancelled with the caller: a caller that
        goes away (a cancelled tool task) leaves an output that is announced instead (lost output), and its send
        stays in the repeat guard unless that outcome proves it never ran."""
        future = link.submit_call(request)
        try:
            return await asyncio.shield(asyncio.wrap_future(future))
        except asyncio.CancelledError:
            loop = asyncio.get_running_loop()

            def lost(done: Any) -> None:
                try:
                    loop.call_soon_threadsafe(self._lost_after_cancel, tool.name, done, claim)
                except RuntimeError:  # the job loop closed meanwhile
                    pass

            future.add_done_callback(lost)
            raise

    async def _after(self, tool: LaptopTool, arguments: dict[str, Any], digest: str, request: CallRequest,
                     outcome: CallOutcome, *, claim: _Claim, approval: jarvis_confirm.Approval | None, speech: Any,
                     fn_call_id: str | None) -> Any:
        # pylint: disable=too-many-arguments
        name = tool.name
        if _never_ran(outcome):
            self._unclaim(claim)
        self._audit(name, request.call_id, outcome.kind, outcome.code)
        if _needs_approval(outcome) and (approval is None or approval.risk != "high"):
            # dynamic risk on the node (K-76): ask through ConfirmFlow, then re-send the same call_id with it
            logger.info("DESKTOP_APPROVAL %s: the laptop asks for an approval (dynamic risk)", name)
            resend = replace(request, seq=None, speech_id=None, approval=None, budget_s=None)  # after Sir's Allow
            return await self._ask(tool, arguments, digest, resend,
                                   summary=_summary(APPROVAL_PREFIX, tool, arguments), risk="high")
        result = outcome.k12(name)
        if outcome.kind == RESULT and fn_call_id is not None:
            self._watch(name, result, fn_call_id, speech)
        return result

    async def _ask(self, tool: LaptopTool, arguments: dict[str, Any], digest: str, request: CallRequest, *,
                   summary: str, risk: str) -> Any:
        """A ConfirmFlow item this module creates (the repeat guard, APPROVAL_REQUIRED): its approval re-sends the
        call with the agent-signed approval reference (_approved_resend)."""
        run = functools.partial(self._approved_resend, tool, arguments, digest, request)
        try:
            return await self.broker().create_pending(tool.name, summary, risk, arguments=arguments,
                                                      args_preview=_preview(arguments), run=run, desktop=True,
                                                      desktop_class=tool.cls)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("DESKTOP_APPROVAL %s: confirmation not started (%s)", tool.name, type(exc).__name__)
            return k12_error(tool.name, "CONFIRM_UI_UNREACHABLE",
                             "Sir, confirmation shuru nahi ho saki, is liye yeh kaam nahi kiya.")

    async def _approved_resend(self, tool: LaptopTool, arguments: dict[str, Any], digest: str,
                               request: CallRequest) -> Any:
        """The approved run of an item from _ask (in the broker's task): the exec goes out with the approval
        reference; the result reaches Sir through the broker's on_result, and the call's own tool_sync row (its
        K-13 id) gets the real end."""
        name = tool.name
        approval = jarvis_confirm.current_approval()
        link = self.link()
        code = DESKTOP_OFFLINE if link is None else link.fast_refusal()
        if approval is None:
            result = k12_error(name, APPROVAL_REQUIRED)
        elif not approval.covers(name, digest):
            result = k12_error(name, "APPROVAL_INVALID")
        elif link is None or code is not None:
            code = code or DESKTOP_OFFLINE
            result = k12_error(name, code, status="cancelled" if code == desktop_link.STOPPED_BY_USER else "error")
        else:
            approval.spend()
            approved = replace(request, approval=approval.reference(digest), seq=None, speech_id=None, budget_s=None)
            claim = self._claim(name, digest)
            outcome = await self._await(link, approved, tool, claim)
            if _never_ran(outcome):
                self._unclaim(claim)
            self._audit(name, approved.call_id, outcome.kind, outcome.code)
            if _needs_approval(outcome) and approval.risk != "high":
                # K-76: the node raised the call above the approval's risk: asked again at high, then re-sent
                return await self._ask(tool, arguments, digest, request,
                                       summary=_summary(APPROVAL_PREFIX, tool, arguments), risk="high")
            result = outcome.k12(name)
        k13 = request.label.get("k13")
        if isinstance(k13, str) and k13:
            plugin_module.post_tool_sync(k13, name, phase="end", status="auto", result=result,
                                         risk=str(request.label.get("risk") or "low"), source="desktop")
        return result

    # --- repeat guard and ordering ----------------------------------------------------------------------------

    def _claim(self, name: str, digest: str) -> _Claim:
        """Records a send of (name, digest) right before it goes out (K-71 repeat guard), so a parallel call and a
        retry after a cancelled wait both see it; _unclaim takes it back when the outcome proves it never ran."""
        now = self._mono()
        self._recent = {key: kept for key, times in self._recent.items()
                        if (kept := [at for at in times if now - at <= REPEAT_GUARD_S])}
        self._recent.setdefault((name, digest), []).append(now)
        return (name, digest), now

    def _unclaim(self, claim: _Claim) -> None:
        key, at = claim
        times = self._recent.get(key)
        if times and at in times:
            times.remove(at)
            if not times:
                del self._recent[key]

    def _recent_age(self, name: str, digest: str) -> float | None:
        """Seconds since the newest send of the same call within the repeat window, else None."""
        times = self._recent.get((name, digest))
        if not times:
            return None
        age = self._mono() - max(times)
        return age if age <= REPEAT_GUARD_S else None

    # --- lost outputs and late outcomes ----------------------------------------------------------------------

    def _watch(self, tool: str, result: Mapping[str, Any], fn_call_id: str, speech: Any) -> None:
        watch = _Watch(tool, dict(result), fn_call_id, speech, self._mono())
        self._watches.append(watch)
        try:
            asyncio.get_running_loop().call_later(LOST_OUTPUT_S, self._check_watch, watch)
        except RuntimeError:
            self._watches.remove(watch)

    def _check_watch(self, watch: _Watch) -> None:
        if watch.delivered:
            return
        speech = watch.speech
        waiting = speech is not None and not bool(getattr(speech, "interrupted", False)) and not _done(speech)
        if waiting and self._mono() - watch.done_at < LOST_OUTPUT_MAX_S:
            asyncio.get_running_loop().call_later(LOST_OUTPUT_RECHECK_S, self._check_watch, watch)
            return
        if watch in self._watches:
            self._watches.remove(watch)
        logger.info("DESKTOP_LOST_OUTPUT %s: its output did not reach the model; announced", watch.tool)
        self._spawn(self._announce_result(watch.tool, watch.result, _LOST_RESULT, _LOST_SPOKEN))

    def on_function_tools_executed(self, event: Any) -> None:
        """LiveKit's function_tools_executed: these outputs reached the model."""
        calls = list(getattr(event, "function_calls", None) or ())
        ids = {getattr(call, "call_id", None) for call in calls}
        for watch in list(self._watches):
            if watch.fn_call_id in ids:
                watch.delivered = True
                self._watches.remove(watch)

    def _lost_after_cancel(self, tool: str, future: Any, claim: _Claim) -> None:
        try:
            outcome = future.result()
        except BaseException:  # pylint: disable=broad-exception-caught
            return
        if _never_ran(outcome):
            self._unclaim(claim)  # it provably never ran: a repeat needs no question
        if outcome.kind == RESULT:
            logger.info("DESKTOP_LOST_OUTPUT %s: the caller went away; announced", tool)
            self._spawn(self._announce_result(tool, outcome.k12(tool), _LOST_RESULT, _LOST_SPOKEN))

    async def _announce_result(self, tool: str, result: Mapping[str, Any], shown: str, spoken: str) -> None:
        message = result.get("message") if isinstance(result, Mapping) else None
        text = plugin_module.one_line(str(message or plugin_module.result_status(result)), 400)
        await self.say(shown.format(tool=tool, message=text), spoken=spoken.format(tool=tool), kind="desktop_result")

    def on_late(self, late: LateOutcome) -> None:
        """A late outcome from the link (on the job loop): announced, and the call's tool_sync end corrected. A call
        whose outcome is still not known (the WAL's 'completed' without its result, which is any end: done, failed,
        cancelled or timed out; or 'interrupted'; or a result of a call that started and ended early, which may have
        run in part) is never announced as done or as a failure: Jarvis says its result is not known, and its row ends
        error with DESKTOP_RESULT_UNKNOWN. A stopped call that had ended on the laptop before the stop reached it is
        announced as done only when its own K-12 result says so (the node reports 'completed' for any tool that
        returned, an error included); else its own result is announced as it is. A delivery failure names only a K-12
        code and is spoken as a fixed line; a delivery that may have opened (also one the link answered at once as
        unknown) is never called a failure."""
        partial = late.state == RESULT and desktop_link.may_have_run(late.status, late.effect)
        if late.reason == "delivery" or late.label.get("delivery"):  # a jarvis_delivery open: only a failure
            result = late.result or {}
            if late.state == RESULT and late.status == "completed" and result.get("status") in ("success", "ok"):
                return
            if late.state in _UNKNOWN_LATE_STATES or partial or late.effect == "possible":  # it may have opened
                self._spawn(self.say(_DELIVERY_UNKNOWN, kind="desktop_delivery"))
                return
            code = desktop_link.clean_code(result.get("error_code")) or _DELIVERY_FAILED_CODE
            self._spawn(self.say(_DELIVERY_FAILED.format(code=code), spoken=_DELIVERY_FAILED_SPOKEN,
                                 kind="desktop_delivery"))
            return
        stopped = late.reason == "stopped"
        if stopped and not (late.state == RESULT and late.status == "completed"):
            return  # stopped as Sir asked: nothing more to say
        result: Mapping[str, Any]
        if stopped and _done_result(late.result):  # done on the laptop before the stop reached it
            text = _STOPPED_DONE.format(tool=late.tool)
            result = late.result or {"status": "success", "tool": late.tool, "message": text}
            self._spawn(self.say(text, kind="desktop_result"))
        elif late.state == RESULT:  # (a stopped call's own result too: it ended before the stop, but not as done)
            result = (desktop_link.partial_k12(late.tool, late.result, late.status) if partial else
                      late.result or {"status": "success", "tool": late.tool, "message": ""})
            self._spawn(self._announce_result(late.tool, result, _LATE_RESULT, _LATE_SPOKEN))
        elif late.state == NOT_STARTED:
            text = _LATE_NOT_STARTED.format(tool=late.tool)
            result = {"status": "cancelled", "tool": late.tool, "message": text}
            self._spawn(self.say(text, kind="desktop_result"))
        else:  # interrupted, or completed without its result: it no longer runs, and its outcome is not known
            text = (_LATE_INTERRUPTED if late.state == "interrupted" else _LATE_COMPLETED).format(tool=late.tool)
            result = k12_error(late.tool, desktop_link.DESKTOP_RESULT_UNKNOWN, text)
            self._spawn(self.say(text, kind="desktop_result"))
        k13 = late.label.get("k13")
        if isinstance(k13, str) and k13:
            plugin_module.post_tool_sync(k13, late.tool, phase="end", status="auto", result=result,
                                         risk=str(late.label.get("risk") or "low"), source="desktop")

    # --- stops ------------------------------------------------------------------------------------------------

    def explicit_stop(self, source: str) -> bool:
        """'ruko' or the dashboard's Stop: every in-flight laptop call ends with STOPPED_BY_USER and new ones are
        refused for 3 s. False without a link."""
        link = self.link()
        if link is None:
            return False
        link.stop_all(source)
        return True

    def _stop_desktop_now(self, source: str) -> str:
        """'desktop band karo', at once: the agent-signed control desktop_stop (the laptop goes read-only and cancels
        every call; only its tray loosens it) and in-flight calls end with STOPPED_BY_USER. Returns what Jarvis says:
        done, or (laptop offline) when it applies: at the laptop's next connect when the link kept the stop on disk,
        else only within this session, with the dashboard's Stop desktop (kept by the bridge) as the lasting way."""
        link = self.link()
        if link is None:
            return STOP_DESKTOP_NO_LINK
        online = link.online()
        link.stop_all(source, desktop_stop=True)
        if online:
            return STOP_DESKTOP_DONE
        return STOP_DESKTOP_QUEUED if getattr(link, "desktop_stop_persisted", False) is True else \
            STOP_DESKTOP_QUEUED_SESSION

    async def stop_desktop(self, source: str) -> None:
        """'desktop band karo' (a final voice transcript): the stop at once (_stop_desktop_now), then Jarvis says so."""
        await self.say(self._stop_desktop_now(source), priority="high", kind="desktop_stop")

    async def typed_hook(self, text: str) -> Any:
        """ConversationRouter hook (typed text, before the model). A stop-desktop phrase never reaches the model: the
        stop takes effect at once and the hook returns Jarvis's answer as a follow-up, which the router runs once
        Sir's own message is shown, so the chat never shows the answer above it. An explicit stop also stops laptop
        calls and still goes on to the model (False)."""
        if is_stop_desktop(text):
            answer = self._stop_desktop_now("chat")
            return functools.partial(self.say, answer, priority="high", kind="desktop_stop")
        if is_explicit_stop(text):
            self.explicit_stop("chat")
        return False

    def on_user_input_transcribed(self, event: Any, assistant: Any = None) -> None:
        """A final voice transcript (LiveKit user_input_transcribed): the stop-desktop phrases and 'ruko'."""
        text = getattr(event, "transcript", None)
        if not getattr(event, "is_final", False) or not isinstance(text, str) or not text.strip():
            return
        if getattr(assistant, "muted", False) is True:
            return
        if is_stop_desktop(text):
            self._spawn(self.stop_desktop("voice"))
        elif is_explicit_stop(text):
            self.explicit_stop("voice")

    def install_ui_stop(self) -> None:
        """The dashboard's Stop (ui_command 'stop', K-03) also stops laptop calls: its registered handler is
        chained once (the registry is the extension point, K-60), so whatever 'stop' does today (C-51: the waiting
        announcements and the typed rescue dropped, the speech and the server's reply cut, notify STOP, the audit
        line) still runs, exactly once per Stop, also when the laptop stop fails."""
        from services.utils import ui_command_registry as registry  # pylint: disable=import-outside-toplevel

        previous = registry.UI_COMMANDS.get("stop")
        if getattr(previous, UI_STOP_FLAG, False):
            return
        controller = self

        async def stop_with_laptop(payload: Any, ctx: Any) -> None:
            try:
                controller.explicit_stop("ui_stop")
            except Exception as exc:  # pylint: disable=broad-exception-caught  # the speech stop still runs
                logger.error("DESKTOP_STOP the dashboard Stop did not reach the laptop calls (%s)", type(exc).__name__)
            if previous is not None:
                result = previous(payload, ctx)
                if inspect.isawaitable(result):
                    await result

        setattr(stop_with_laptop, UI_STOP_FLAG, True)
        registry.register_ui_command("stop")(stop_with_laptop)

    # --- fallback back-off ------------------------------------------------------------------------------------

    def _backoff_file(self) -> Path:
        if self.backoff_path is not None:
            return self.backoff_path
        from services.utils.jarvis_config import config  # pylint: disable=import-outside-toplevel

        return Path(config.project_root) / "conversations" / BACKOFF_FILE

    def _read_backoff(self) -> dict[str, float]:
        try:
            data = json.loads(self._backoff_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {str(name): float(until) for name, until in data.items()
                if isinstance(until, (int, float)) and math.isfinite(until)} if isinstance(data, dict) else {}

    def backoff_active(self, name: str) -> bool:
        """True while VPS runs of name back off (6 h after a shared-IP block); the file is shared by the processes."""
        if self._backoff is None:
            self._backoff = self._read_backoff()
        until = self._backoff.get(name, 0.0)
        if until > self._wall():
            return True
        fresh = self._read_backoff()  # another process may have set it meanwhile
        self._backoff.update(fresh)
        return fresh.get(name, 0.0) > self._wall()

    def set_backoff(self, name: str) -> None:
        until = self._wall() + FALLBACK_BACKOFF_S
        self._backoff = {**(self._backoff or {}), name: until}
        path = self._backoff_file()
        try:
            from services.utils.jarvis_filelock import locked  # pylint: disable=import-outside-toplevel

            path.parent.mkdir(parents=True, exist_ok=True)
            with locked(path):
                data = {**self._read_backoff(), name: until}
                tmp = path.with_name(path.name + ".tmp")
                tmp.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
                os.replace(tmp, path)
        except (OSError, TimeoutError) as exc:
            logger.warning("DESKTOP_FALLBACK back-off kept in memory only (%s)", type(exc).__name__)

    def watch_job_fallback(self, tool: LaptopTool, arguments: Mapping[str, Any], result: Any,
                           codes: tuple[str, ...]) -> bool:
        """A VPS run that started a K-20 job (status 'started' with its job_id, as download_youtube_media does): the
        job's final result gets the same fallback check (jarvis_jobs.on_job_end). True while it is watched."""
        job_id = result.get("job_id") if isinstance(result, Mapping) and result.get("status") == "started" else None
        if not codes or not isinstance(job_id, str) or not job_id:
            return False
        from services.utils import jarvis_jobs  # pylint: disable=import-outside-toplevel

        return jarvis_jobs.on_job_end(job_id, functools.partial(self._job_ended, tool, dict(arguments), codes))

    def _job_ended(self, tool: LaptopTool, arguments: dict[str, Any], codes: tuple[str, ...], final: Any) -> None:
        if not _fallback_due(final, codes):
            return
        logger.warning("DESKTOP_FALLBACK %s: %s in its VPS job; one re-run on the laptop, VPS runs back off %.0f h",
                       tool.name, final.get("error_code"), FALLBACK_BACKOFF_S / 3600)
        self.set_backoff(tool.name)
        self._spawn(self._rerun_on_laptop(tool, arguments))

    async def _rerun_on_laptop(self, tool: LaptopTool, arguments: dict[str, Any]) -> None:
        """The one laptop run after a VPS job's shared-IP block: no LLM call waits for it any more, so its outcome
        is announced (a late one too, through on_late). It carries no approval, K-13 id or K-11 budget: the job's
        context, which this task inherits, holds only the old tool call's. No LLM turn is in progress, so its exec
        has origin 'job' (K-70, K-76): with JARVIS_ORIGIN_PRESENCE_GATE=1 (D21) its actuation needs presence."""
        outcomes: list[CallOutcome] = []
        result = await self._call(tool, arguments, k13=None, approval=None, speech=None, fn_call_id=None,
                                  origin="job", outcomes=outcomes)
        shown, spoken = _fallback_lines(result, outcomes[-1] if outcomes else None)
        await self._announce_result(tool.name, result, shown, spoken)

    # --- the job's session (agent_runner) ---------------------------------------------------------------------

    def attach_session(self, session: Any, assistant: Any, *, room: str, job_id: str) -> DesktopLink | None:
        """After every session start of a remote-mode job: the process's link (started on the first call), the late
        outcomes and node events on this job's loop, the stop phrases on final transcripts and typed text, the
        lost-output watch and the dashboard's Stop. A session start of a job that already ended (an in-place rebuild
        racing the job's end) changes nothing: it would build a new link for a job process that drains."""
        job_id = str(job_id or "")
        if job_id and job_id in self._ended_jobs:
            logger.info("DESKTOP_PROXY job %s already ended; its late session start gets no link", job_id)
            return None
        self._jobs.add(job_id)
        link = self.link() or desktop_link.ensure_link(room=room, job_id=job_id)
        loop = asyncio.get_running_loop()
        if link is not None and self._late_link is not link:
            if self._late_unsubscribe is not None:
                self._late_unsubscribe()
            self._late_unsubscribe, self._late_link = link.subscribe("late", self.on_late, loop), link
        if link is not None:
            if self._event_unsubscribe is not None:
                self._event_unsubscribe()
            self._event_unsubscribe = link.subscribe("event", functools.partial(_on_node_event, assistant), loop)
        on = getattr(session, "on", None)
        if callable(on):
            on("user_input_transcribed", functools.partial(_transcribed, self, assistant))
            on("function_tools_executed", self.on_function_tools_executed)
        router = getattr(assistant, "conversation_router", None)
        add_hook = getattr(router, "add_typed_hook", None)
        if callable(add_hook):
            add_hook(self.typed_hook)
        self.install_ui_stop()
        return link

    def end_job(self, job_id: str) -> bool:
        """agent_runner, as a remote-mode job ends (before its process drains; C-119 review): once no other job of
        this process uses the link (a thread executor runs several), this controller's listeners on it go and the
        link is retired (desktop_link.retire: stopped for good). Every job of a room is the agent peer job-<room>, and
        the bridge keeps one socket per peer, so a draining process whose link reconnected would push the next job's
        link off the bridge and could acknowledge that job's results. True when it retired the link; idempotent."""
        job_id = str(job_id or "")
        self._jobs.discard(job_id)
        if job_id:
            self._ended_jobs[job_id] = None
            while len(self._ended_jobs) > ENDED_JOBS_MAX:
                self._ended_jobs.popitem(last=False)
        if self._jobs:
            logger.info("DESKTOP_PROXY job %s ended; the link stays for %d other job(s) of this process",
                        job_id or "-", len(self._jobs))
            return False
        link = self._late_link or self.link()
        for unsubscribe in (self._late_unsubscribe, self._event_unsubscribe):
            if unsubscribe is not None:
                try:
                    unsubscribe()
                except Exception:  # pylint: disable=broad-exception-caught  # the link is retired all the same
                    logger.debug("DESKTOP_PROXY listener already gone", exc_info=True)
        self._late_unsubscribe = self._event_unsubscribe = None
        self._late_link = None
        return desktop_link.retire(link) if link is not None else False


class _NullLock:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *_exc: object) -> None:
        return None


def _transcribed(controller: DesktopProxies, assistant: Any, event: Any) -> None:
    """The session's user_input_transcribed for the stop phrases. Never raises: livekit's EventEmitter passes a
    TypeError on to the code that emitted, and the session's own transcript handler registered after this one (C-75's
    spoken confirmations) must still get every transcript."""
    try:
        controller.on_user_input_transcribed(event, assistant)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error("DESKTOP_STOP a spoken stop phrase was not handled (%s)", type(exc).__name__)


def _on_node_event(assistant: Any, body: Any) -> None:
    """A node event for this job: window_context goes to the vision handler (C-112) as {title, ts}."""
    if not isinstance(body, Mapping) or body.get("kind") != "window_context":
        return
    handler = getattr(getattr(assistant, "vision_handler", None), "on_window_context", None)
    if callable(handler):
        handler({"title": body.get("title"), "ts": body.get("ts")})


def _done(speech: Any) -> bool:
    done = getattr(speech, "done", None)
    try:
        return bool(done()) if callable(done) else bool(done)
    except Exception:  # pylint: disable=broad-exception-caught
        return True


def _live_schema(func: Any) -> str:
    try:
        return tool_placement.live_param_schema_sha256(func)
    except Exception:  # pylint: disable=broad-exception-caught  # an unreadable signature never matches
        return ""


def _laptop_capable_vps(kind: str | None, entry: tool_placement.ToolPlacement | None) -> bool:
    """A tool the VPS runs that may also go to the laptop: routed, or placed on the VPS with a laptop fallback."""
    return entry is not None and (kind == tool_placement.ROUTED or (
        kind == tool_placement.VPS and entry.fallback == tool_placement.LAPTOP))


def _unregister(manager: Any, name: str) -> None:
    lock = getattr(manager, "_tools_lock", None) or _NullLock()
    with lock:
        manager.tools = [tool for tool in list(getattr(manager, "tools", []) or [])
                         if getattr(tool, "__name__", "") != name]


async def _run_original(func: Callable[..., Any], args: tuple[Any, ...], kwargs: Mapping[str, Any]) -> Any:
    """A routed or fallback tool's own function on the VPS (a sync one in a worker thread, as the wrapper runs it)."""
    if inspect.iscoroutinefunction(func):
        return await func(*args, **kwargs)
    result = await plugin_module._run_sync_tool(func, args, dict(kwargs))  # pylint: disable=protected-access
    return await result if inspect.isawaitable(result) else result


def _fallback_due(result: Any, codes: tuple[str, ...]) -> bool:
    """K-71: a VPS result with a code in fallback_on and no side effect (effect none, or not reported)."""
    if not isinstance(result, Mapping) or result.get("error_code") not in codes:
        return False
    data = result.get("data") if isinstance(result.get("data"), Mapping) else {}
    return result.get("effect", "none") == "none" and data.get("effect", "none") == "none"


def _fallback_lines(result: Any, outcome: CallOutcome | None) -> tuple[str, str]:
    """(shown, spoken) for a VPS job's laptop re-run, from what the laptop did: 'ran on the laptop' only for a result
    the laptop returned for a run; an approval it waits for; an outcome that may have run (unknown, timed out, or
    stopped while it ran: its late outcome is announced later); else it never ran there (offline, refused, stopped
    before the send, not started)."""
    if isinstance(result, Mapping) and result.get("status") == "needs_confirmation":
        return _FALLBACK_ASKED, _FALLBACK_ASKED_SPOKEN
    if outcome is None or outcome.never_ran:
        return _FALLBACK_NOT_RUN, _FALLBACK_NOT_RUN_SPOKEN
    if outcome.kind == RESULT:
        return _FALLBACK_RESULT, _FALLBACK_SPOKEN
    if outcome.sent and outcome.effect != "none":
        return _FALLBACK_UNKNOWN, _FALLBACK_UNKNOWN_SPOKEN
    return _FALLBACK_NOT_RUN, _FALLBACK_NOT_RUN_SPOKEN


# ---------------------------------------------------------------- the process's controller

proxies = DesktopProxies()


def install(manager: Any, **kwargs: Any) -> dict[str, Any]:
    """The C-32 prewarm hook (remote mode; a no-op in local mode). Never raises: a failure is logged and the laptop
    tools are then missing, which remote discovery reports as degraded (K-71)."""
    try:
        return proxies.install(manager, **kwargs)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("DESKTOP_PROXY install failed")
        return {"active": True, "error": "install"}


def attach_session(session: Any, assistant: Any, *, room: str, job_id: str) -> DesktopLink | None:
    """agent_runner's per-session hook (remote mode). Never raises: a session starts without it, and laptop calls
    then answer DESKTOP_OFFLINE."""
    try:
        return proxies.attach_session(session, assistant, room=room, job_id=job_id)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("DESKTOP_PROXY session hooks not attached")
        return None


def end_job(job_id: str) -> bool:
    """agent_runner's job-end hook (remote mode): the process's link is retired once no job of it uses the link
    (DesktopProxies.end_job). Never raises: a job always ends."""
    try:
        return proxies.end_job(job_id)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("DESKTOP_PROXY link not retired at the job's end")
        return False
