"""Handle a successful yt-dlp process result (returncode == 0).

This owner contains file validation, 1080p/candidate decisions and successful
finalization. The orchestration loop only applies the returned decision.

DEPENDS ON app methods: parse_yt_dlp_output(), validate_downloaded_video(),
move_video_to_manual_processing(), get_video_dimensions(), record_problem(),
cleanup_download_temp_files(), finalize_downloaded_video_file(), _log_video_info().
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional
import shutil

from src.core.constants import SUPPORTED_VIDEO_EXTENSIONS, VIDEO_MAX_HEIGHT, VIDEO_TARGET_HEIGHT


@dataclass(frozen=True)
class SuccessDecision:
    action: str  # retry | success | fatal
    video_path: Optional[str] = None
    last_error: Optional[str] = None
    candidate_path: Optional[str] = None
    candidate_height: int = -1
    candidate_width: int = -1


def handle_successful_attempt(
    app,
    *,
    result,
    url: str,
    attempt: int,
    strategy: Dict,
    command: List[str],
    download_temp_dir: Path,
    video_dir: Path,
    quality_candidate_dir: Path,
    video_files_before,
    expected_video_id: str,
    best_candidate_path: Optional[str],
    best_candidate_height: int,
    best_candidate_width: int,
    attempt_summaries: List[Dict],
) -> SuccessDecision:
    temp_video_path = app.parse_yt_dlp_output(
        result.stdout, url, download_temp_dir, video_files_before, expected_video_id
    )
    if not temp_video_path:
        app.record_problem(
            "yt-dlp завершился успешно, но программа не нашла скачанный видеофайл",
            "ERROR",
            "yt_dlp_success_without_file",
            {
                "url": url,
                "attempt": attempt,
                "strategy": strategy["name"],
                "target_dir": str(download_temp_dir),
                "final_video_dir": str(video_dir),
                "expected_video_id": expected_video_id,
                "files_before_count": len(video_files_before),
                "supported_extensions": SUPPORTED_VIDEO_EXTENSIONS,
            },
            command=command,
            stdout=result.stdout,
            stderr=result.stderr,
        )
        return SuccessDecision("retry")

    valid, validation_reason = app.validate_downloaded_video(temp_video_path, url)
    if not valid:
        app.move_video_to_manual_processing(temp_video_path, validation_reason)
        return SuccessDecision("retry", last_error=validation_reason)

    width, height = app.get_video_dimensions(temp_video_path)
    dimension_source = "ffprobe"
    if width <= 0 or height <= 0:
        plan_dimensions = app.parse_download_plan_dimensions(result.stdout)
        if plan_dimensions:
            width, height = plan_dimensions
            dimension_source = "yt_dlp_download_plan"
            app.record_problem(
                "ffprobe не вернул разрешение; использованы проверенные размеры из DOWNLOAD_PLAN yt-dlp",
                "INFO", "yt_dlp_resolution_fallback_to_download_plan",
                {
                    "url": url,
                    "attempt": attempt,
                    "strategy": strategy["name"],
                    "video_path": temp_video_path,
                    "width": width,
                    "height": height,
                },
                resolved=True,
            )

    resolution_text = (
        f"{width}x{height} ({height}p)" if width > 0 and height > 0
        else "не удалось определить"
    )

    if height > VIDEO_MAX_HEIGHT:
        app.log(
            f"❌ Получен файл выше допустимого лимита: {resolution_text}. "
            f"Максимум программы — {VIDEO_MAX_HEIGHT}p; пробую другую стратегию.",
            "ERROR",
        )
        app.record_problem(
            "yt-dlp вернул видео выше жёсткого лимита качества",
            "ERROR",
            "yt_dlp_resolution_above_limit",
            {
                "url": url,
                "attempt": attempt,
                "strategy": strategy["name"],
                "video_path": temp_video_path,
                "width": width,
                "height": height,
                "dimension_source": dimension_source,
                "max_height": VIDEO_MAX_HEIGHT,
            },
            command=command,
            stdout=result.stdout,
            stderr=result.stderr,
        )
        try:
            Path(temp_video_path).unlink(missing_ok=True)
        except Exception:
            pass
        return SuccessDecision("retry")

    if height <= 0:
        # The selector itself excludes unknown heights and caps selected formats
        # to VIDEO_MAX_HEIGHT.  Do not re-download the same valid file dozens of
        # times just because one ffprobe metadata query failed.
        app.record_problem(
            "Разрешение готового видео не определено, но yt-dlp selector гарантирует лимит качества",
            "WARNING", "yt_dlp_resolution_unknown_accepted",
            {
                "url": url,
                "attempt": attempt,
                "strategy": strategy["name"],
                "video_path": temp_video_path,
                "max_height": VIDEO_MAX_HEIGHT,
            },
            command=command, stdout=result.stdout, stderr=result.stderr,
            resolved=True,
        )
        # Unknown height must not be treated as a low-quality candidate either.
        height = VIDEO_TARGET_HEIGHT

    reached_target = height >= VIDEO_TARGET_HEIGHT
    is_progressive = bool(strategy.get("progressive_format"))
    if not reached_target and not is_progressive:
        candidate_score = height if height > 0 else 0
        keep_candidate = best_candidate_path is None or candidate_score > best_candidate_height
        if keep_candidate:
            quality_candidate_dir.mkdir(parents=True, exist_ok=True)
            if best_candidate_path:
                try:
                    Path(best_candidate_path).unlink(missing_ok=True)
                except Exception:
                    pass
            source_candidate = Path(temp_video_path)
            candidate_target = quality_candidate_dir / source_candidate.name
            if candidate_target.exists():
                candidate_target.unlink(missing_ok=True)
            with app.file_lock:
                shutil.move(str(source_candidate), str(candidate_target))
            best_candidate_path = str(candidate_target)
            best_candidate_height = candidate_score
            best_candidate_width = width if width > 0 else 0
            app.log(
                f"↗️ Получено {resolution_text}, но цель — {VIDEO_TARGET_HEIGHT}p. "
                "Сохраняю как резерв и продолжаю искать 1080p.",
                "WARNING",
            )
        else:
            try:
                Path(temp_video_path).unlink(missing_ok=True)
            except Exception:
                pass
        app.cleanup_download_temp_files(
            download_temp_dir,
            expected_video_id=expected_video_id,
            final_video_path=None,
            reason="quality_search_continue",
        )
        return SuccessDecision(
            "retry",
            candidate_path=best_candidate_path,
            candidate_height=best_candidate_height,
            candidate_width=best_candidate_width,
        )

    video_path = app.finalize_downloaded_video_file(temp_video_path, video_dir, expected_video_id)
    if not video_path:
        return SuccessDecision("fatal")

    app._log_video_info(video_path)
    if strategy["name"] == "EJS GitHub stable auto quality":
        app._note_youtube_primary_strategy_result(True)
    app.cleanup_download_temp_files(
        download_temp_dir,
        expected_video_id=expected_video_id,
        final_video_path=None,
        reason="download_success_temp_dir",
    )
    try:
        shutil.rmtree(download_temp_dir, ignore_errors=True)
        shutil.rmtree(quality_candidate_dir, ignore_errors=True)
    except Exception:
        pass
    if attempt > 1:
        app.log(f"✅ Успешно через: {strategy['name']}", "SUCCESS")
        app.record_problem(
            "Видео успешно скачано резервной стратегией",
            "INFO",
            "yt_dlp_recovered_after_fallback",
            {
                "url": url,
                "expected_video_id": expected_video_id,
                "successful_attempt": attempt,
                "successful_strategy": strategy["name"],
                "failed_attempt_count": len(attempt_summaries),
                "attempt_summaries": attempt_summaries[-8:],
                "result_path": video_path,
                "width": width,
                "height": height,
            },
            resolved=True,
        )
    return SuccessDecision("success", video_path=video_path)
