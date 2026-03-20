import sys
import time
print("Importing google plugin...")
start = time.time()
try:
    from livekit.plugins import google
    print(f"Import success in {time.time()-start:.2f}s")
except Exception as e:
    print(f"FAILED: {e}")
