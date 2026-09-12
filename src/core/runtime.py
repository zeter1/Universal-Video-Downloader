"""Compatibility facade for the pre-refactor shared runtime namespace.

New code must import from the owner module directly. This file intentionally
contains no implementation so Codex never needs it for normal feature work.
"""

from src.core import constants as _constants
from src.core.concurrency import SafeCounter, ThreadSafeList
from src.core.errors import CommandCancelledError, ProblematicDownloadSkipped
from src.core.session_settings import session_setting
from src.ui.helpers import (
    find_clickable_urls,
    is_copy_shortcut,
    is_paste_shortcut,
    log_tabs_for_entry,
)

_CONSTANT_NAMES = tuple(name for name in dir(_constants) if name.isupper())
globals().update({name: getattr(_constants, name) for name in _CONSTANT_NAMES})

__all__ = [
    *_CONSTANT_NAMES,
    "SafeCounter",
    "ThreadSafeList",
    "CommandCancelledError",
    "ProblematicDownloadSkipped",
    "session_setting",
    "find_clickable_urls",
    "is_copy_shortcut",
    "is_paste_shortcut",
    "log_tabs_for_entry",
]
