#!/usr/bin/env python3
"""Print the smallest useful file scope for a Codex task.

Usage:
    python scripts/codex_scope.py "скачивание не стартует"
    python scripts/codex_scope.py "1080p fallback"
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Route:
    name: str
    keywords: tuple[str, ...]
    primary: tuple[str, ...]
    related: tuple[str, ...] = ()
    tests: tuple[str, ...] = ()
    avoid: tuple[str, ...] = ()


ROUTES = (
    Route(
        "download-start",
        ("не старт", "не начина", "download start", "кнопк", "очеред", "executor", "proxy_enabled"),
        ("src/downloads/manager.py", "src/downloads/task.py"),
        ("src/downloads/progress.py", "src/core/session_settings.py"),
        ("tests/test_download_manager_failures.py",),
        ("src/diagnostics/", "src/audio/"),
    ),
    Route(
        "youtube-quality",
        ("1080", "качество", "quality", "resolution", "720", "format selector"),
        ("src/downloads/youtube_downloader.py", "src/downloads/youtube_command.py"),
        ("src/downloads/youtube_finalize.py", "src/downloads/media_probe.py"),
        ("tests/test_download_quality_audio.py", "tests/test_youtube_helpers.py"),
        ("src/diagnostics/", "src/ui/"),
    ),
    Route(
        "mp4-output",
        ("mp4", "mkv", "webm", "контейнер", "remux", "итоговый формат"),
        ("src/downloads/mp4_output.py", "src/downloads/files.py"),
        ("src/downloads/youtube_command.py", "src/downloads/media_probe.py"),
        ("tests/test_download_results.py", "tests/test_youtube_helpers.py"),
        ("src/diagnostics/", "src/ui/"),
    ),
    Route(
        "youtube-postprocess",
        ("conversion failed", "postprocessing", "ffmpeg", "mux", "timestamp", "timestamps", "merge failed"),
        ("src/downloads/youtube_failure_diagnostics.py", "src/downloads/youtube_recovery.py"),
        ("src/downloads/youtube_failure_reporting.py", "src/downloads/youtube_command.py", "src/core/subprocess_runner.py"),
        ("tests/test_youtube_helpers.py", "tests/test_problem_diagnostics.py"),
        ("src/audio/", "src/ui/"),
    ),
    Route(
        "youtube-retry",
        ("yt-dlp", "youtube", "fallback", "retry", "googlevideo", "timeout", "403", "cookies", "po token"),
        ("src/downloads/youtube_downloader.py", "src/downloads/youtube_retry.py"),
        ("src/downloads/youtube_strategy.py", "src/downloads/youtube_strategy_catalog.py", "src/downloads/youtube_attempt.py"),
        ("tests/test_problem_diagnostics.py", "tests/test_youtube_helpers.py"),
        ("src/audio/", "src/ui/"),
    ),
    Route(
        "ytdlp-command",
        ("аргумент", "command", "cli", "format", "chunk", "client", "ejs"),
        ("src/downloads/youtube_command.py", "src/downloads/youtube_strategy_catalog.py"),
        ("src/downloads/youtube_access.py", "src/core/constants.py"),
        ("tests/test_download_quality_audio.py",),
        ("src/diagnostics/",),
    ),
    Route(
        "subprocess",
        ("subprocess", "процесс", "завис", "stall", "heartbeat", "cancel", "отмен", "timeout"),
        ("src/core/subprocess_runner.py", "src/core/ytdlp_runtime.py"),
        ("src/core/process_control.py",),
        ("tests/test_download_manager_failures.py", "tests/test_audio_pipeline_failures.py"),
        ("src/ui/",),
    ),
    Route(
        "audio",
        ("mp3", "audio", "аудио", "склей", "разби", "конвер"),
        ("src/audio/convert.py", "src/audio/merge.py", "src/audio/split.py"),
        ("src/downloads/media_validation.py", "src/downloads/media_probe.py"),
        ("tests/test_audio_pipeline_failures.py", "tests/test_download_quality_audio.py"),
        ("src/diagnostics/",),
    ),
    Route(
        "ui",
        ("ui", "tkinter", "кнопк", "поле", "окно", "layout", "интерфейс", "clipboard", "встав"),
        ("src/ui/layout.py",),
        ("src/ui/clipboard.py", "src/ui/settings_controls.py", "src/ui/video_list.py"),
        ("tests/test_log_ui_helpers.py",),
        ("src/diagnostics/",),
    ),
    Route(
        "diagnostics",
        ("лог проблем", "diagnostic", "incident", "validator", "schema", "retention", "архив лог"),
        ("src/diagnostics/reporting.py", "src/diagnostics/events.py"),
        ("src/diagnostics/validator.py", "src/diagnostics/retention.py", "src/diagnostics/snapshots.py"),
        ("tests/test_problem_diagnostics.py", "tests/test_problem_log_validator.py"),
        ("src/audio/",),
    ),
    Route(
        "settings-paths",
        ("настрой", "settings", "путь", "папк", "path", "save_path"),
        ("src/core/settings.py", "src/app/paths.py"),
        ("src/ui/settings_controls.py", "src/core/session_settings.py"),
        ("tests/test_download_manager_failures.py",),
        ("src/diagnostics/",),
    ),
)


def _score(route: Route, task: str) -> int:
    return sum(2 if " " in keyword else 1 for keyword in route.keywords if keyword in task)


def _existing(paths: Iterable[str]) -> list[str]:
    return [path for path in paths if (ROOT / path.rstrip("/")).exists()]


def main(argv: list[str]) -> int:
    task = " ".join(argv[1:]).strip().casefold()
    if not task:
        print('Usage: python scripts/codex_scope.py "описание задачи"')
        return 2

    ranked = sorted(((route, _score(route, task)) for route in ROUTES), key=lambda item: item[1], reverse=True)
    best, score = ranked[0]
    if score == 0:
        print("ROUTE: unknown")
        print("PRIMARY")
        print("  docs/CODE_MAP.md")
        print("NEXT")
        print('  rg -n "<текст ошибки|имя метода>" src tests')
        return 0

    print(f"ROUTE: {best.name} (score={score})")
    for title, paths in (
        ("PRIMARY", best.primary),
        ("RELATED", best.related),
        ("TESTS", best.tests),
        ("DO NOT READ INITIALLY", best.avoid),
    ):
        values = _existing(paths)
        if values:
            print(title)
            for path in values:
                print(f"  {path}")
    if len(ranked) > 1 and ranked[1][1] > 0:
        print(f"SECONDARY ROUTE: {ranked[1][0].name} (score={ranked[1][1]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
