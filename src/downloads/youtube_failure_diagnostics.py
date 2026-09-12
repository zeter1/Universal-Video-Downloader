"""Pure yt-dlp failure analysis for recovery decisions and AI-friendly logs.

This module intentionally owns only text classification/extraction. It does not
run commands, mutate app state, or decide the full strategy order.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional
import re


_POSTPROCESS_MARKERS = (
    "error: postprocessing: conversion failed",
    "ffmpegpostprocessorerror: conversion failed",
    "conversion failed!",
)
_TIMESTAMP_MARKERS = (
    "timestamps are unset in a packet",
    "can't write packet with unknown timestamp",
    "cannot write packet with unknown timestamp",
)
_MUX_MARKERS = (
    "error muxing a packet",
    "error submitting a packet to the muxer",
    "av_interleaved_write_frame(): invalid argument",
)

_DIAGNOSTIC_LINE_MARKERS = (
    "[download_plan]",
    "[debug] exe versions:",
    "[debug] invoking ",
    "[info] ",
    "[merger] merging formats",
    "ffmpeg command line:",
    "timestamps are unset in a packet",
    "can't write packet with unknown timestamp",
    "cannot write packet with unknown timestamp",
    "error submitting a packet to the muxer",
    "error muxing a packet",
    "conversion failed!",
    "error: postprocessing:",
)


def is_postprocessing_conversion_failure(text: str) -> bool:
    lower = str(text or "").casefold()
    return any(marker in lower for marker in _POSTPROCESS_MARKERS)


def has_ffmpeg_timestamp_failure(text: str) -> bool:
    lower = str(text or "").casefold()
    return any(marker in lower for marker in _TIMESTAMP_MARKERS)


def _unique_tail(lines: Iterable[str], limit: int = 32) -> List[str]:
    result: List[str] = []
    seen = set()
    for raw in lines:
        line = str(raw or "").strip()
        if not line:
            continue
        # Keep individual evidence lines bounded; full process output remains in
        # the normal stdout snapshot when a detailed problem event is written.
        line = line[-700:]
        if line in seen:
            continue
        seen.add(line)
        result.append(line)
    return result[-limit:]


def diagnostic_landmarks_from_output(text: str, limit: int = 32) -> List[str]:
    """Return a compact set of lines that explain format/merge failures.

    Progress lines are deliberately excluded. This makes long fragmented HLS
    downloads diagnosable even when the bounded stdout ring buffer has already
    discarded yt-dlp's early verbose lines.
    """
    selected: List[str] = []
    for raw in str(text or "").splitlines():
        lower = raw.casefold()
        if "[info] " in lower and "downloading " not in lower and "format" not in lower:
            continue
        if any(marker in lower for marker in _DIAGNOSTIC_LINE_MARKERS):
            selected.append(raw)
    return _unique_tail(selected, limit)


def _find_first(lines: Iterable[str], marker: str) -> Optional[str]:
    marker_lower = marker.casefold()
    for line in lines:
        if marker_lower in line.casefold():
            return line
    return None


def _downloader_names(lines: Iterable[str]) -> List[str]:
    names: List[str] = []
    for line in lines:
        match = re.search(r"invoking\s+([a-z0-9_-]+)\s+downloader", line, re.IGNORECASE)
        if match:
            names.append(match.group(1).lower())
    return sorted(set(names))


def analyze_yt_dlp_failure(
    text: str,
    *,
    strategy: Optional[Dict] = None,
    returncode: Optional[int] = None,
    diagnostic_landmarks: Optional[List[str]] = None,
) -> Dict:
    """Build a bounded, structured explanation of one failed yt-dlp attempt."""
    strategy = dict(strategy or {})
    landmarks = _unique_tail(
        list(diagnostic_landmarks or []) + diagnostic_landmarks_from_output(text),
        40,
    )
    combined = "\n".join([str(text or ""), *landmarks])
    lower = combined.casefold()

    timestamp_failure = has_ffmpeg_timestamp_failure(combined)
    mux_failure = any(marker in lower for marker in _MUX_MARKERS)
    conversion_failure = is_postprocessing_conversion_failure(combined)
    fragmented_progress = bool(re.search(r"\(frag\s+\d+\s*/\s*\d+\)", combined, re.IGNORECASE))
    download_completed = "[download] 100%" in lower or "[download] 100.0%" in lower
    merger_started = "[merger] merging formats" in lower
    downloader_names = _downloader_names(landmarks)
    download_plan_lines = [line for line in landmarks if "[download_plan]" in line.casefold()]
    selected_format_line = _find_first(landmarks, "downloading 1 format") or _find_first(
        landmarks, "downloading 2 format"
    )
    ffmpeg_command_line = _find_first(landmarks, "ffmpeg command line:")

    kind = "unknown"
    stage = "yt_dlp"
    likely_root_cause = "yt-dlp завершился с ошибкой; смотрите terminal/error evidence."
    recommended_action = "Сравнить эту попытку со следующей fallback-стратегией."
    diagnostic_quality = "basic"

    if timestamp_failure:
        kind = "ffmpeg_timestamp_mux_failure"
        stage = "postprocessing_merge"
        likely_root_cause = (
            "FFmpeg не смог записать поток из-за отсутствующих/некорректных timestamps. "
            "Для YouTube это часто связано с выбранным fragmented HLS/MPEG-TS видео при merge в MKV."
        )
        recommended_action = (
            "Не повторять тот же HLS-формат бесконечно: предпочесть direct HTTP/DASH 1080p, "
            "затем перейти к progressive single-file fallback."
        )
        diagnostic_quality = "high"
    elif conversion_failure:
        kind = "yt_dlp_postprocessing_conversion_failed"
        stage = "postprocessing_merge"
        if fragmented_progress:
            likely_root_cause = (
                "Видео и аудио скачались, но FFmpeg merge завершился Conversion failed; "
                "в выводе есть fragmented download. Вероятен проблемный HLS/MPEG-TS поток "
                "или timestamp/muxing ошибка."
            )
        else:
            likely_root_cause = (
                "yt-dlp скачал медиаданные, но FFmpeg не смог выполнить merge/postprocessing."
            )
        recommended_action = (
            "Следующая попытка должна избегать HLS/MPEG-TS и выбирать direct HTTP/DASH; "
            "если merge снова падает — перейти к progressive single-file."
        )
        diagnostic_quality = "medium" if (download_plan_lines or selected_format_line) else "basic"
    elif "requested format is not available" in lower or "format is not available" in lower:
        kind = "yt_dlp_format_unavailable"
        stage = "format_selection"
        likely_root_cause = "Текущий client/format profile не предоставляет запрошенный формат."
        recommended_action = "Пробовать следующий YouTube client/format profile без снижения лимита 1080p."
    elif any(marker in lower for marker in ("timed out", "connection reset", "http error 403", "forbidden")):
        kind = "network_or_access_failure"
        stage = "download_or_access"
        likely_root_cause = "Сбой сетевого доступа/CDN или блокировка текущего клиента."
        recommended_action = "Использовать существующую сетевую/client fallback-логику."

    return {
        "kind": kind,
        "stage": stage,
        "returncode": returncode,
        "diagnostic_quality": diagnostic_quality,
        "likely_root_cause": likely_root_cause,
        "recommended_action": recommended_action,
        "format_profile": strategy.get("format_profile") or (
            "progressive" if strategy.get("progressive_format")
            else "any" if strategy.get("fallback_format")
            else "direct_http"
        ),
        "progressive_format": bool(strategy.get("progressive_format")),
        "fallback_any_format": bool(strategy.get("fallback_format")),
        "download_completed_marker": download_completed,
        "fragmented_progress_seen": fragmented_progress,
        "merger_started_marker": merger_started,
        "ffmpeg_timestamp_failure": timestamp_failure,
        "ffmpeg_mux_failure": mux_failure,
        "downloader_names": downloader_names,
        "download_plan_lines": download_plan_lines[-6:],
        "selected_format_line": selected_format_line,
        "ffmpeg_command_line": ffmpeg_command_line,
        "diagnostic_landmarks": landmarks[-24:],
    }
