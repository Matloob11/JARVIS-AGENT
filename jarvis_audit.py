"""
# jarvis_audit.py
JARVIS Real-Time Ultra-Advanced Audit System

Monitors file changes and automatically runs:
1. Pylint (Code Quality)
2. Mypy (Type Safety)
3. Pytest (Runtime Correctness)
4. Tracemalloc (Memory Leak Detection)
"""

import os
import subprocess
import time
import sys
import logging
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("JARVIS-AUDIT")

PROJECT_PATH = os.getcwd()


class AuditHandler(FileSystemEventHandler):
    """Handles file system events and triggers audits."""

    def __init__(self):
        self.last_run = 0
        self.cooldown = 2  # Cooldown in seconds to prevent double triggers

    def run_audit(self):
        """Executes the full audit suite."""
        current_time = time.time()
        if current_time - self.last_run < self.cooldown:
            return

        self.last_run = current_time

        py_files = [f for f in os.listdir(PROJECT_PATH) if f.endswith(
            '.py') and f != 'jarvis_audit.py']
        if not py_files:
            logger.warning("No Python files found for auditing.")
            return

        print("-" * 60)
        print(">>> JARVIS ULTRA-STRICT AUDIT INITIATED")
        print("-" * 60)

        # 1. Pylint Ultra Strict
        print("\n[1] Step 1: Pylint Analysis...")
        pylint_cmd = ["pylint"] + py_files + [
            "--rcfile=.pylintrc",
            "--disable=I",  # Disable informational messages to focus on score
            "--max-line-length=120",
            "--reports=n",
            "--score=y",
            "--output-format=colorized"
        ]
        subprocess.run(pylint_cmd, shell=True if os.name ==
                       'nt' else False)  # nosec B602

        # 2. Mypy Type Checks
        print("\n[2] Step 2: Mypy Type Integrity Check...")
        mypy_cmd = ["mypy"] + py_files + ["--ignore-missing-imports",
                                          "--follow-imports=silent", "--show-error-codes", "--pretty"]
        subprocess.run(mypy_cmd, shell=True if os.name ==
                       'nt' else False)  # nosec B602

        # 3. Pytest Runtime Checks
        print("\n[3] Step 3: Pytest Runtime Verification...")
        pytest_cmd = ["pytest", "--maxfail=3",
                      "--disable-warnings", "--tb=short"]
        subprocess.run(pytest_cmd, shell=True if os.name ==
                       'nt' else False)  # nosec B602

        # 4. Tracemalloc for memory leaks
        print("\n[4] Step 4: Memory Leak Detection (Core Module)...")
        # Profile agent.py as it's the main entry point
        if "agent.py" in py_files:
            subprocess.run([sys.executable, "-X", "dev", "-m", "tracemalloc",
                           "-c", "import agent"], shell=True if os.name == 'nt' else False)  # nosec B602

        print("-" * 60)
        print(">>> AUDIT COMPLETE. STANDING BY...")
        print("-" * 60)

    def on_modified(self, event):
        if not event.is_directory and event.src_path.endswith(".py"):
            filename = os.path.basename(event.src_path)
            if filename == "jarvis_audit.py":
                return
            logger.info("File change detected: %s", filename)
            self.run_audit()


if __name__ == "__main__":
    event_handler = AuditHandler()
    observer = Observer()
    observer.schedule(event_handler, PROJECT_PATH, recursive=False)
    observer.start()

    print("\n" + "+" + "="*58 + "+")
    print("| LIVE: Real-Time Ultra-Strict JARVIS Audit started.       |")
    print("| Watching for changes in .py files...                     |")
    print("| Press Ctrl+C to stop.                                    |")
    print("+" + "="*58 + "+\n")

    # Run initial audit
    event_handler.run_audit()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Stopping auditor...")
        observer.stop()
    observer.join()
