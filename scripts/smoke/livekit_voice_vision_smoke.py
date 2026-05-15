"""Credential-aware smoke checks for LiveKit, voice, and vision plumbing.

Default mode is offline and safe for CI: it verifies imports, local audio helper
contracts, and vision frame decoding without opening devices or calling APIs.
Use --require-secrets when you want the script to fail if live credentials are
not configured, and --include-voice-id when you explicitly want the heavy
SpeechBrain voice fingerprint model import path included.
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
from io import BytesIO
from pathlib import Path
from typing import Callable

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
    from livekit.plugins import google  # noqa: F401

    if require_secrets:
        _require_env(["LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "GOOGLE_API_KEY"])


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Run LiveKit/voice/vision smoke checks.")
    parser.add_argument("--require-secrets", action="store_true", help="Fail if LiveKit/Gemini env vars are missing.")
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
    ]
    if args.include_voice_id:
        checks.append(("voice id import", check_voice_id_import))

    for label, check in checks:
        check()
        print(f"PASS {label}")

    print("Smoke checks completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
