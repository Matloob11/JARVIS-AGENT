import agent_runner
import jarvis_instrumentation
import os
import sys
import time

print("1. Patching environment...")
os.environ["GOOGLE_API_CORE_SUPPRESS_VERSION_CHECK"] = "1"

print("2. Importing agent_runner (step-by-step)...")
# We don't import yet, we will check what's inside

print("3. Calling setup_instrumentation() manually to see if it hangs...")
start = time.time()
jarvis_instrumentation.setup_instrumentation()
print(f"setup_instrumentation finished in {time.time() - start:.2f}s")

print("4. Importing agent_runner now...")
start = time.time()
print(f"agent_runner imported in {time.time() - start:.2f}s")
