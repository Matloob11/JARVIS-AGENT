import asyncio
from livekit import rtc
print("RTC OK")

def test_import(mod_name):
    print(f"Importing livekit.agents.{mod_name}...")
    try:
        if mod_name == "cli": from livekit.agents import cli
        elif mod_name == "inference": from livekit.agents import inference
        elif mod_name == "ipc": from livekit.agents import ipc
        elif mod_name == "llm": from livekit.agents import llm
        elif mod_name == "metrics": from livekit.agents import metrics
        elif mod_name == "stt": from livekit.agents import stt
        elif mod_name == "tokenize": from livekit.agents import tokenize
        elif mod_name == "tts": from livekit.agents import tts
        elif mod_name == "utils": from livekit.agents import utils
        elif mod_name == "vad": from livekit.agents import vad
        elif mod_name == "voice": from livekit.agents import voice
        print(f"SUCCESS: {mod_name}")
    except Exception as e:
        print(f"FAILED: {mod_name} - {e}")

mods = ["cli", "inference", "ipc", "llm", "metrics", "stt", "tokenize", "tts", "utils", "vad", "voice"]
for m in mods:
    test_import(m)
