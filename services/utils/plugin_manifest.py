"""
# plugin_manifest.py
Defines the permission system for JARVIS plugins.
"""

from enum import Enum


class Permission(Enum):
    FILESYSTEM_READ = "filesystem:read"
    FILESYSTEM_WRITE = "filesystem:write"
    WEB_SEARCH = "web:search"
    WEB_REQUEST = "web:request"
    SYSTEM_CONTROL = "system:control"
    MULTIMEDIA_GEN = "multimedia:generate"
    VISION_ACCESS = "vision:access"
    REASONING_BYPASS = "reasoning:bypass"  # High privilege

# Default permissions for unregistered/external plugins
DEFAULT_PERMISSIONS: set[Permission] = {
    Permission.VISION_ACCESS,  # Most tools can see what's happening
}

# Explicit manifest for critical services
PLUGIN_SECURITY_MANIFEST: dict[str, set[Permission]] = {
    "jarvis_window_ctrl": {Permission.SYSTEM_CONTROL, Permission.FILESYSTEM_READ},
    "jarvis_image_gen": {Permission.MULTIMEDIA_GEN, Permission.FILESYSTEM_WRITE},
    "jarvis_advanced_tools": {Permission.WEB_SEARCH, Permission.WEB_REQUEST, Permission.FILESYSTEM_READ},
    "jarvis_reminders": {Permission.FILESYSTEM_READ, Permission.FILESYSTEM_WRITE},
}

def get_permissions_for_module(module_name: str) -> set[Permission]:
    """Retrieves allowed permissions for a given module."""
    # Strip package prefix if present
    base_name = module_name.rsplit('.', maxsplit=1)[-1]
    return PLUGIN_SECURITY_MANIFEST.get(base_name, DEFAULT_PERMISSIONS)
