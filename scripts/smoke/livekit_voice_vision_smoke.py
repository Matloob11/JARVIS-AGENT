"""Credential-aware smoke checks for LiveKit, voice, and vision plumbing.

Default mode is offline and safe for CI: it verifies imports, local audio helper
contracts, and vision frame decoding without opening devices or calling APIs.
Use --require-secrets when you want the script to fail if live credentials are
not configured. Use --include-google-plugin or --include-voice-id when you
explicitly want heavy provider/model import paths included.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import os
import sys
from io import BytesIO
from pathlib import Path
from typing import Callable
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


Check = Callable[[], None]


def _require_env(names: list[str]) -> None:
    missing = [name for name in names if not os.getenv(name)]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")


def check_livekit_imports(require_secrets: bool) -> None:
    from livekit import rtc  # noqa: F401
    from livekit.agents import AgentSession, llm  # noqa: F401

    if require_secrets:
        _require_env(["LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "GOOGLE_API_KEY"])


def check_google_plugin_import() -> None:
    from livekit.plugins import google  # noqa: F401


def check_audio_helpers() -> None:
    from services.multimedia.jarvis_audio import generate_speech, process_audio_input

    payload = process_audio_input(b"\0" * 320)
    if payload.get("bytes") != 320:
        raise RuntimeError(f"Unexpected audio processing payload: {payload}")

    speech = generate_speech("smoke")
    if not isinstance(speech, bytes) or not speech:
        raise RuntimeError("generate_speech did not return non-empty bytes.")


def check_vision_frame_decode(require_secrets: bool) -> None:
    from services.ai_core.jarvis_vision import ScreenPerceiver, get_google_client

    image = Image.new("RGB", (2, 2), color=(0, 0, 0))
    buffer = BytesIO()
    image.save(buffer, format="JPEG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")

    perceiver = ScreenPerceiver()
    perceiver.update_webcam_frame(f"data:image/jpeg;base64,{encoded}")
    if not perceiver.latest_frame:
        raise RuntimeError("Vision frame did not decode into latest_frame.")

    if require_secrets:
        _require_env(["GOOGLE_API_KEY"])
        if get_google_client() is None:
            raise RuntimeError("Google GenAI client was not created.")


def check_voice_id_import() -> None:
    from services.ai_core.voice_fingerprint import VoiceFingerprintEngine

    master_voice_path = PROJECT_ROOT / "data" / "identity" / "master_voice.wav"
    VoiceFingerprintEngine(str(master_voice_path))


def check_core_assistant_turn_flow() -> None:
    async def _run() -> None:
        os.environ["JARVIS_TEST_MODE"] = "true"

        from livekit.agents import Agent, llm
        from src.core.agent_core import BrainAssistant

        with patch("livekit.agents.Agent.__init__", return_value=None), \
                patch("livekit.agents.Agent.on_user_turn_completed", new_callable=AsyncMock, return_value="ok"), \
                patch("src.core.agent_core.JarvisPluginManager") as mock_plugins, \
                patch("src.core.agent_core.MemoryExtractor") as mock_memory_cls, \
                patch("src.core.agent_core.process_with_advanced_reasoning", new_callable=AsyncMock) as mock_reasoning:
            mock_plugins.return_value.discover_plugins.return_value = None
            memory = mock_memory_cls.return_value.memory
            memory.get_semantic_context = AsyncMock(
                return_value=[{"document": "User prefers concise answers.", "score": 0.95}],
            )
            mock_reasoning.return_value = {"is_agentic": True, "plan": "Confirm context then answer."}

            assistant = BrainAssistant(chat_ctx=MagicMock())
            assistant._run_back = lambda coro: coro.close() if hasattr(coro, "close") else None
            assistant.persona_manager.inject_emotional_context = AsyncMock(return_value=None)
            assistant.vision_handler.last_vision_frame = "data:image/jpeg;base64,ZmFrZV9qcGVn"

            turn_ctx = SimpleNamespace(chat_ctx=SimpleNamespace(messages=[]))
            message = llm.ChatMessage(role="user", content=["Jarvis, what do you see on camera?"])

            result = await assistant.on_user_turn_completed(turn_ctx, message)

            if result != "ok":
                raise RuntimeError(f"Unexpected assistant turn result: {result!r}")
            if not any(isinstance(part, llm.ImageContent) for part in message.content):
                raise RuntimeError("Vision frame was not injected into the user message.")

            context = "\n".join(str(item.content[0]) for item in turn_ctx.chat_ctx.messages)
            expected = ["[VISION SYSTEM ACTIVE]", "[LONG-TERM MEMORY CONTEXT]", "[EXECUTION PLAN]"]
            missing = [marker for marker in expected if marker not in context]
            if missing:
                raise RuntimeError(f"Core turn flow missed context markers: {', '.join(missing)}")

            if not Agent.on_user_turn_completed.await_count:
                raise RuntimeError("LiveKit Agent turn handoff was not called.")

    asyncio.run(_run())


def main() -> int:
    parser = argparse.ArgumentParser(description="Run LiveKit/voice/vision smoke checks.")
    parser.add_argument("--require-secrets", action="store_true", help="Fail if LiveKit/Gemini env vars are missing.")
    parser.add_argument(
        "--include-google-plugin",
        action="store_true",
        help="Import the heavier LiveKit Google plugin path.",
    )
    parser.add_argument(
        "--include-voice-id",
        action="store_true",
        help="Import the heavy SpeechBrain voice fingerprint path.",
    )
    args = parser.parse_args()

    checks: list[tuple[str, Check]] = [
        ("livekit imports", lambda: check_livekit_imports(args.require_secrets)),
        ("audio helpers", check_audio_helpers),
        ("vision frame decode", lambda: check_vision_frame_decode(args.require_secrets)),
        ("core assistant turn flow", check_core_assistant_turn_flow),
    ]
    if args.include_google_plugin or args.require_secrets:
        checks.append(("google plugin import", check_google_plugin_import))
    if args.include_voice_id:
        checks.append(("voice id import", check_voice_id_import))

    for label, check in checks:
        check()
        print(f"PASS {label}", flush=True)

    print("Smoke checks completed.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
