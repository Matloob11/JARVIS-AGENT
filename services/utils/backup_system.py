"""
# services/utils/backup_system.py
Automated backup system for JARVIS-AGENT.
Archives critical data like identity, outputs, and configs.
"""

import datetime
import os
import shutil

from services.utils.jarvis_logger import jarvis_log as log


class JarvisBackup:
    """
    Automated backup system for JARVIS-AGENT.
    Archives critical data like identity, outputs, and configs.
    """
    def __init__(self, source_dirs=None, backup_root="backups"):
        """Initializes the backup system with source and destination."""
        self.source_dirs = source_dirs or [
            "Jarvis_Outputs",
            "conversations/identity.json",
            "services/utils/jarvis_config.py",
        ]
        self.backup_root = backup_root
        os.makedirs(self.backup_root, exist_ok=True)

    def perform_backup(self) -> str:
        """Executes a full system backup."""
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_folder = os.path.join(self.backup_root, f"backup_{timestamp}")

        log.info("[BACKUP] Starting backup to %s...", backup_folder)

        try:
            os.makedirs(backup_folder, exist_ok=True)
            for item in self.source_dirs:
                if os.path.exists(item):
                    dest = os.path.join(backup_folder, os.path.basename(item))
                    if os.path.isdir(item):
                        shutil.copytree(item, dest)
                    else:
                        shutil.copy2(item, dest)
                else:
                    log.warning("[WARNING] Backup source not found: %s", item)

            # Create a zip for easy transfer
            shutil.make_archive(backup_folder, 'zip', backup_folder)
            shutil.rmtree(backup_folder)  # Keep only the zip

            log.info("[SUCCESS] Backup completed: %s.zip", backup_folder)
            return f"{backup_folder}.zip"
        except (OSError, shutil.Error) as e:
            log.error("[ERROR] Backup failed: %s", e)
            return None

    def rotate_backups(self, keep=7):
        """Keep only the latest 'keep' archives."""
        all_backups = []
        for f in os.listdir(self.backup_root):
            if f.endswith(".zip"):
                path = os.path.join(self.backup_root, f)
                all_backups.append((path, os.path.getmtime(path)))

        # Sort by mtime descending
        all_backups.sort(key=lambda x: x[1], reverse=True)

        if len(all_backups) > keep:
            for path, _ in all_backups[keep:]:
                log.info("[CLEANUP] Rotating old backup: %s", path)
                os.remove(path)


if __name__ == "__main__":
    backup_system = JarvisBackup()
    backup_system.perform_backup()
    backup_system.rotate_backups()
