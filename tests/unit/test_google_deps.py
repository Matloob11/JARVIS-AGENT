import sys
import time
def test_import(module_name):
    print(f"Importing {module_name}...")
    start = time.time()
    try:
        __import__(module_name)
        print(f"SUCCESS: {module_name} in {time.time()-start:.2f}s")
    except Exception as e:
        print(f"FAILED: {module_name}: {e}")

test_import("google.auth")
test_import("google.cloud.speech")
test_import("google.cloud.texttospeech")
test_import("google.genai")
