"""
# services/ai_core/announcer.py
Re-export of the AnnouncerAPI (contract K-18) for the tools-info import path. The implementation lives in
services/utils/jarvis_announcer.py (plan task C-55).
"""
from services.utils.jarvis_announcer import (  # noqa: F401
    Announcer,
    announce,
    announcer,
    attach,
    detach,
    set_assistant,
    set_session,
)

__all__ = ["Announcer", "announce", "announcer", "attach", "detach", "set_assistant", "set_session"]
