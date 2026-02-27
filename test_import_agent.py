import os
import sys
import time

print("Starting import test...")
start_time = time.time()
try:
    import agent
    print(f"Import successful in {time.time() - start_time:.2f}s")
    print(
        f"Env var: {os.environ.get('GOOGLE_API_CORE_SUPPRESS_VERSION_CHECK')}")
except Exception as e:
    print(f"Import failed: {e}")
