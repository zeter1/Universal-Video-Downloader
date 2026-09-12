"""Classify yt-dlp failures without owning the retry loop.

Pure helpers keep long error-marker lists out of youtube_downloader.py so Codex
can reason about retry classification independently from download orchestration.
"""

import re
from typing import Dict, List, Optional, Tuple

CRITICAL_MARKERS = (
    "this video is unavailable", "video unavailable", "has been removed",
    "copyright", "this live event will begin",
)
AUTH_MARKERS = (
    "private video", "members-only", "sign in to confirm", "age-restricted",
    "confirm your age", "inappropriate", "login required", "cookies",
    "cookie database", "could not copy chrome cookie", "not a bot",
)
COOKIE_READ_FAILURE_MARKERS = (
    "cookie database", "could not copy chrome cookie", "failed to decrypt",
    "permission denied", "database is locked",
)
YOUTUBE_RETRY_MARKERS = (
    "no longer supported", "http error 401", "http error 403", "forbidden",
    "unable to extract", "failed to extract", "this video is not available",
    "premieres in", "nsig extraction failed", "unable to download api page",
    "requested format is not available", "format is not available",
    "only images are available", "n challenge solving failed",
    "remote components challenge solver", "gvs po token", "po token",
)


def has_auth_error(error_lower: str) -> bool:
    return any(marker in error_lower for marker in AUTH_MARKERS)


def has_critical_error(error_lower: str) -> bool:
    return any(marker in error_lower for marker in CRITICAL_MARKERS)


def has_youtube_retry_error(error_lower: str) -> bool:
    return any(marker in error_lower for marker in YOUTUBE_RETRY_MARKERS)


def cookie_read_failed(error_lower: str) -> bool:
    return any(marker in error_lower for marker in COOKIE_READ_FAILURE_MARKERS)


def extract_googlevideo_hosts(error_text: str) -> List[str]:
    pairs = re.findall(
        r"host='([^']*googlevideo\.com)'|https?://([^/\s]*googlevideo\.com)",
        error_text,
    )
    return sorted({host for pair in pairs for host in pair if host})


def build_attempt_summary(
    *,
    attempt: int,
    strategy_name: str,
    returncode: int,
    elapsed_sec: float,
    socket_timeout: int,
    proxy_enabled: bool,
    proxy_url_masked: str,
    error_text: str,
    failure_analysis: Optional[Dict] = None,
) -> Dict:
    summary = {
        "attempt": attempt,
        "strategy": strategy_name,
        "returncode": returncode,
        "elapsed_sec": round(elapsed_sec, 2),
        "socket_timeout": socket_timeout,
        "proxy_enabled": proxy_enabled,
        "proxy_url_masked": proxy_url_masked,
        "googlevideo_hosts": extract_googlevideo_hosts(error_text),
        "error_tail": error_text[-700:],
    }
    if failure_analysis:
        summary["failure_kind"] = failure_analysis.get("kind")
        summary["failure_stage"] = failure_analysis.get("stage")
        summary["format_profile"] = failure_analysis.get("format_profile")
        summary["diagnostic_quality"] = failure_analysis.get("diagnostic_quality")
    return summary
