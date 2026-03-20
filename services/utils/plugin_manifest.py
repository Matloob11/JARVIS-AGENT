"""
# plugin_manifest.py
Defines the permission system for JARVIS plugins.
"""

from enum import Enum
from typing import Set, Dict

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
DEFAULT_PERMISSIONS: Set[Permission] = {
    Permission.VISION_ACCESS  # Most tools can see what's happening
}

# Explicit manifest for critical services
PLUGIN_SECURITY_MANIFEST: Dict[str, Set[Permission]] = {
    "jarvis_window_ctrl": {Permission.SYSTEM_CONTROL, Permission.FILESYSTEM_READ},
    "jarvis_image_gen": {Permission.MULTIMEDIA_GEN, Permission.FILESYSTEM_WRITE},
    "jarvis_advanced_tools": {Permission.WEB_SEARCH, Permission.WEB_REQUEST, Permission.FILESYSTEM_READ},
    "jarvis_reminders": {Permission.FILESYSTEM_READ, Permission.FILESYSTEM_WRITE}
}

def get_permissions_for_module(module_name: str) -> Set[Permission]:
    """Retrieves allowed permissions for a given module."""
    # Strip package prefix if present
    base_name = module_name.split('.')[-1]
    return PLUGIN_SECURITY_MANIFEST.get(base_name, DEFAULT_PERMISSIONS)
