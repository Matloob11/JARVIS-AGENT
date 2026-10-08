"""
# voices.py
Gemini Live voices for the realtime session (plan task C-50).

GEMINI_VOICES holds the 30 voice names livekit-plugins-google 1.4.6 accepts (api_proto.Voice; the pinned copy below
is used where the plugin cannot load, and a test keeps the two equal). OPENAI_ALIAS maps the OpenAI-style names that
older prompts, settings and the dashboard still use to their nearest Gemini voice. resolve_voice(name) gives the
canonical Gemini name (any case, aliases included) or None; persona_voice(persona) the voice of a persona from
JARVIS_VOICE_JARVIS / JARVIS_VOICE_ANNA (K-69).
"""
from __future__ import annotations

import os
import typing
from typing import Any

_PINNED_VOICES = (
    "Achernar", "Achird", "Algenib", "Algieba", "Alnilam", "Aoede", "Autonoe", "Callirrhoe", "Charon", "Despina",
    "Enceladus", "Erinome", "Fenrir", "Gacrux", "Iapetus", "Kore", "Laomedeia", "Leda", "Orus", "Pulcherrima", "Puck",
    "Rasalgethi", "Sadachbia", "Sadaltager", "Schedar", "Sulafat", "Umbriel", "Vindemiatrix", "Zephyr",
    "Zubenelgenubi",
)
try:
    from livekit.plugins.google.realtime import api_proto as _api_proto

    GEMINI_VOICES: tuple[str, ...] = tuple(typing.get_args(_api_proto.Voice)) or _PINNED_VOICES
except Exception:  # pylint: disable=broad-exception-caught  # a host without the plugin
    GEMINI_VOICES = _PINNED_VOICES

OPENAI_ALIAS = {
    "alloy": "Zephyr", "echo": "Charon", "shimmer": "Aoede", "ash": "Orus", "ballad": "Algieba", "coral": "Leda",
    "sage": "Sadaltager", "verse": "Puck",
}
PERSONA_VOICE_ENV = {"jarvis": ("JARVIS_VOICE_JARVIS", "Charon"), "anna": ("JARVIS_VOICE_ANNA", "Aoede")}
_BY_LOWER = {voice.lower(): voice for voice in GEMINI_VOICES}


def resolve_voice(name: Any) -> str | None:
    """The canonical Gemini voice for name (case-insensitive; an OpenAI-style alias maps to its voice), else None."""
    if not isinstance(name, str):
        return None
    key = name.strip().lower()
    return _BY_LOWER.get(key) or OPENAI_ALIAS.get(key)


def persona_voice(persona: str) -> str:
    """The persona's voice: JARVIS_VOICE_JARVIS (default Charon) or JARVIS_VOICE_ANNA (default Aoede); a name that is
    no voice falls back to the default."""
    env, default = PERSONA_VOICE_ENV.get(persona, PERSONA_VOICE_ENV["jarvis"])
    return resolve_voice(os.getenv(env)) or default


def voices_help() -> str:
    """The valid names, for an answer to an unknown voice and the change_voice tool description."""
    aliases = ", ".join(f"{alias}={voice}" for alias, voice in OPENAI_ALIAS.items())
    return f"Valid voices: {', '.join(GEMINI_VOICES)} (aliases: {aliases})"
