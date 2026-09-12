"""Pure UI helpers with no application-state dependencies."""

import re
from typing import List, Optional, Tuple

from src.core.constants import (
    LOG_CATEGORY_DOWNLOAD_FAILED, LOG_TAB_ALL, LOG_TAB_DOWNLOAD_FAILED, LOG_TAB_ERRORS,
)

HTTP_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def find_clickable_urls(text: str) -> List[Tuple[int, int, str]]:
    result: List[Tuple[int, int, str]] = []
    for match in HTTP_URL_RE.finditer(text):
        url = match.group(0).rstrip(".,;:!?…»”")
        for opening, closing in (("(", ")"), ("[", "]"), ("{", "}")):
            while url.endswith(closing) and url.count(closing) > url.count(opening):
                url = url[:-1]
        if url:
            result.append((match.start(), match.start() + len(url), url))
    return result


def log_tabs_for_entry(level: str, category: Optional[str]) -> Tuple[str, ...]:
    tabs = [LOG_TAB_ALL]
    if category == LOG_CATEGORY_DOWNLOAD_FAILED:
        tabs.append(LOG_TAB_DOWNLOAD_FAILED)
    if level in ("ERROR", "CRITICAL"):
        tabs.append(LOG_TAB_ERRORS)
    return tuple(tabs)


def is_copy_shortcut(keysym: str, keycode: Optional[int]) -> bool:
    normalized = (keysym or "").casefold()
    return keycode == ord("C") or normalized in {"c", "с", "cyrillic_es"}


def is_paste_shortcut(keysym: str, keycode: Optional[int]) -> bool:
    normalized = (keysym or "").casefold()
    return keycode == ord("V") or normalized in {"v", "м", "cyrillic_em"}
