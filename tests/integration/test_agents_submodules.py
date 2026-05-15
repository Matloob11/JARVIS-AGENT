import pytest
from livekit import rtc

MODULES = ["cli", "inference", "ipc", "llm", "metrics", "stt", "tokenize", "tts", "utils", "vad", "voice"]


@pytest.mark.parametrize("mod_name", MODULES)
def test_import(mod_name):
    print(f"Importing livekit.agents.{mod_name}...")
    try:
        __import__(f"livekit.agents.{mod_name}", fromlist=[mod_name])
        print(f"SUCCESS: {mod_name}")
    except Exception as e:
        pytest.fail(f"FAILED: {mod_name} - {e}")
