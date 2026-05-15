import time

import pytest


MODULES = [
    "google.auth",
    "google.cloud.speech",
    "google.cloud.texttospeech",
    "google.genai",
]


@pytest.mark.parametrize("module_name", MODULES)
def test_import(module_name):
    print(f"Importing {module_name}...")
    start = time.time()
    try:
        __import__(module_name)
        print(f"SUCCESS: {module_name} in {time.time()-start:.2f}s")
    except Exception as e:
        pytest.fail(f"FAILED: {module_name}: {e}")
