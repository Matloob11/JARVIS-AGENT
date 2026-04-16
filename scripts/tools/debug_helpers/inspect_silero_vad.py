from livekit.plugins import silero

print(f"silero.vad Type: {type(silero.vad)}")
print(f"silero.vad Attributes: {dir(silero.vad)}")
try:
    print(f"silero.VAD.load() Identity: {silero.VAD.load}")
except Exception:  # pylint: disable=broad-exception-caught
    pass
