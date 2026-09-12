"""Recovery side-effects for yt-dlp merge failures and unexpected exceptions."""

from typing import Dict, List, Tuple


def handle_postprocessing_failure(
    app,
    *,
    url: str,
    attempt: int,
    strategy: Dict,
    command: List[str],
    result,
    failure_analysis: Dict,
    attempt_summaries: List[Dict],
    download_temp_dir,
    expected_video_id: str,
    failure_count: int,
    skip_risky_any_formats: bool,
    force_progressive_fallback: bool,
) -> Tuple[int, bool, bool]:
    failure_count += 1
    format_profile = str(failure_analysis.get("format_profile") or "")
    if format_profile == "any":
        skip_risky_any_formats = True
    if failure_count >= 2 and format_profile != "any":
        force_progressive_fallback = True

    if skip_risky_any_formats:
        transition = (
            "⚠️ FFmpeg не смог объединить any-protocol поток. Пропускаю остальные "
            "рискованные HLS-варианты и продолжаю direct HTTP/DASH 1080p; "
            "затем будет single-file fallback."
        )
    elif force_progressive_fallback:
        transition = (
            "⚠️ FFmpeg повторно не смог объединить direct 1080p. "
            "Перехожу к цельному progressive video+audio без merge."
        )
    else:
        transition = (
            "⚠️ FFmpeg не смог объединить скачанные video+audio. "
            "Пробую другую direct HTTP/DASH 1080p стратегию."
        )
    app.log(transition, "WARNING")
    app.record_problem(
        "Автовосстановление после ошибки FFmpeg postprocessing/merge",
        "WARNING", "yt_dlp_postprocess_recovery",
        {
            "url": url,
            "attempt": attempt,
            "strategy": strategy["name"],
            "postprocessing_failure_count": failure_count,
            "skip_risky_any_formats": skip_risky_any_formats,
            "force_progressive_fallback": force_progressive_fallback,
            "failure_analysis": failure_analysis,
            "attempt_summaries": attempt_summaries[-4:],
            "target_files_after_attempt": app._snapshot_download_target_files(
                str(download_temp_dir), expected_video_id, limit=12
            ),
        },
        command=command, stdout=result.stdout, stderr=result.stderr,
    )
    return failure_count, skip_risky_any_formats, force_progressive_fallback


def record_orchestration_exception(
    app,
    *,
    error: BaseException,
    url: str,
    attempt: int,
    strategy: Dict,
    quality: str,
    expected_video_id: str,
    download_temp_dir,
    command: List[str],
    postprocessing_failure_count: int,
    force_progressive_fallback: bool,
    skip_risky_any_formats: bool,
) -> None:
    app.record_problem(
        "Неожиданное исключение внутри orchestration yt-dlp",
        "ERROR", "yt_dlp_orchestration_exception",
        {
            "url": url,
            "attempt": attempt,
            "strategy": strategy.get("name"),
            "quality": quality,
            "expected_video_id": expected_video_id,
            "target_dir": str(download_temp_dir),
            "postprocessing_failure_count": postprocessing_failure_count,
            "force_progressive_fallback": force_progressive_fallback,
            "skip_risky_any_formats": skip_risky_any_formats,
            "target_files_snapshot": app._snapshot_download_target_files(
                str(download_temp_dir), expected_video_id, limit=20
            ),
        },
        exception=error,
        command=command,
    )
