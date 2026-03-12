"""
Dependency Repair Module
Handles automated fixing of missing or conflicting dependencies.
"""
import subprocess
import sys
try:
    import pkg_resources
except ImportError:
    pkg_resources = None

import os
from services.utils.jarvis_logger import jarvis_log as log


def check_and_fix_dependencies():
    """
    Automated repair script for dependency drift.
    Verifies installed packages against requirements.
    """
    requirements_file = "requirements.txt"
    
    if not os.path.exists(requirements_file):
        log.warning("⚠️ No requirements.txt file found for repair.")
        return

    if not pkg_resources:
        log.error(
            "❌ pkg_resources (setuptools) is missing. Cannot check dependencies.")
        return

    log.info("🔍 Checking for dependency drift...")
    try:
        with open(requirements_file, "r", encoding="utf-8") as f:
            requirements = pkg_resources.parse_requirements(f.read())

        missing = []
        for req in requirements:
            try:
                pkg_resources.require(str(req))
            except (pkg_resources.DistributionNotFound, pkg_resources.VersionConflict):
                missing.append(str(req))

        if missing:
            log.warning(
                "🩹 Detected drift in: %s. Repairing...", ", ".join(missing))
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install"] + missing)
            log.info("✅ Dependency repair successful.")
        else:
            log.info("✅ All dependencies are up to date.")

    except Exception: # pylint: disable=broad-except
        log.exception("❌ Dependency repair failed")


if __name__ == "__main__":
    check_and_fix_dependencies()
