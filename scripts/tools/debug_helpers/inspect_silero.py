from livekit.plugins import silero

print(f"Silero Attributes: {dir(silero)}")
if hasattr(silero, 'VAD'):
    print(f"VAD Attributes: {dir(silero.VAD)}")
