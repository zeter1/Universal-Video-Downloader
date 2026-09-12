"""Finalize a lower-quality fallback and report total yt-dlp failure.

DEPENDS ON app methods: finalize_downloaded_video_file(), _log_video_info(),
record_problem(). It does not choose strategies or execute subprocesses.
"""

from pathlib import Path
from typing import Dict, List, Optional

from src.core.constants import (
    DOWNLOAD_TIMEOUT_SEC,
    VIDEO_MAX_HEIGHT,
    VIDEO_TARGET_HEIGHT,
    YT_DLP_EXTRACTOR_RETRIES,
    YT_DLP_FAST_FAIL_ENABLED,
    YT_DLP_FRAGMENT_RETRIES,
    YT_DLP_HTTP_CHUNK_SIZE,
    YT_DLP_NETWORK_RETRY_PAUSE_SEC,
    YT_DLP_RETRIES,
    YT_DLP_SAFE_CONCURRENT_FRAGMENTS,
    YT_DLP_SAFE_HTTP_CHUNK_SIZE,
    YT_DLP_SLOW_FRAGMENT_RETRIES,
    YT_DLP_SLOW_RETRIES,
    YT_DLP_SLOW_SOCKET_TIMEOUT,
    YT_DLP_SOCKET_TIMEOUT,
    YT_DLP_TINY_HTTP_CHUNK_SIZE,
)


def finalize_lower_quality_candidate(
    app,
    *,
    url: str,
    candidate_path: str,
    candidate_width: int,
    candidate_height: int,
    video_dir: Path,
    expected_video_id: str,
) -> Optional[str]:
    path = Path(candidate_path)
    if not path.exists():
        return None
    try:
        result = app.finalize_downloaded_video_file(str(path), video_dir, expected_video_id)
        if not result:
            return None
        app.log(
            f"✅ 1080p получить не удалось; использую лучший реально скачанный вариант "
            f"{candidate_width}x{candidate_height} (лимит программы {VIDEO_MAX_HEIGHT}p).",
            "WARNING",
        )
        app._log_video_info(result)
        app.record_problem(
            "1080p не получено; выбран лучший сохранённый fallback ниже лимита",
            "INFO",
            "yt_dlp_lower_quality_fallback_selected",
            {
                "url": url,
                "result_path": result,
                "width": candidate_width,
                "height": candidate_height,
                "target_height": VIDEO_TARGET_HEIGHT,
                "max_height": VIDEO_MAX_HEIGHT,
            },
            resolved=True,
        )
        return result
    except Exception as error:
        app.record_problem(
            "Не удалось использовать сохранённый fallback качества",
            "ERROR",
            "yt_dlp_lower_quality_fallback_finalize_failed",
            {
                "url": url,
                "candidate_path": candidate_path,
                "candidate_height": candidate_height,
            },
            error,
        )
        return None


def record_all_strategies_failed(
    app,
    *,
    url: str,
    quality: str,
    expected_video_id: str,
    last_error: Optional[str],
    strategy_count: int,
    proxy_enabled: bool,
    proxy_url_masked: str,
    attempt_summaries: List[Dict],
    fast_fragments: str,
) -> None:
    app.record_problem(
        "Не удалось скачать видео после перебора стратегий yt-dlp",
        "ERROR",
        "yt_dlp_download_failed_all_strategies",
        {
            "url": url,
            "quality": quality,
            "expected_video_id": expected_video_id,
            "last_error": last_error,
            "strategy_count": strategy_count,
            "timeout_sec": DOWNLOAD_TIMEOUT_SEC,
            "proxy_enabled": proxy_enabled,
            "proxy_url_masked": proxy_url_masked,
            "attempt_summaries": attempt_summaries[-12:],
            "network_diagnosis": (
                "Если во всех попытках повторяется Read timed out/SSL/10054 именно от googlevideo.com, "
                "а youtube.com метаданные получает, проблема почти наверняка в сетевом пути до CDN. "
                "Включите VPN/proxy и проверьте, что в command_details.args появился --proxy."
            ),
            "network_tuning": {
                "yt_dlp_retries": YT_DLP_RETRIES,
                "fragment_retries": YT_DLP_FRAGMENT_RETRIES,
                "extractor_retries": YT_DLP_EXTRACTOR_RETRIES,
                "socket_timeout_sec": YT_DLP_SOCKET_TIMEOUT,
                "slow_socket_timeout_sec": YT_DLP_SLOW_SOCKET_TIMEOUT,
                "slow_retries": YT_DLP_SLOW_RETRIES,
                "slow_fragment_retries": YT_DLP_SLOW_FRAGMENT_RETRIES,
                "retry_pause_sec": YT_DLP_NETWORK_RETRY_PAUSE_SEC,
                "fast_fail_enabled": YT_DLP_FAST_FAIL_ENABLED,
                "http_chunk_size": YT_DLP_HTTP_CHUNK_SIZE,
                "safe_http_chunk_size": YT_DLP_SAFE_HTTP_CHUNK_SIZE,
                "tiny_http_chunk_size": YT_DLP_TINY_HTTP_CHUNK_SIZE,
                "fast_concurrent_fragments": fast_fragments,
                "safe_concurrent_fragments": YT_DLP_SAFE_CONCURRENT_FRAGMENTS,
            },
        },
    )
