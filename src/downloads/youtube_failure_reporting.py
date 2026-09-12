"""Record one failed yt-dlp attempt and return data needed by retry orchestration."""

from dataclasses import dataclass
from typing import Dict, List

from src.downloads.youtube_failure_diagnostics import analyze_yt_dlp_failure
from src.downloads.youtube_retry import build_attempt_summary


@dataclass(frozen=True)
class FailedAttemptReport:
    last_error: str
    error_lower: str
    network_failure_count: int
    slow_network_failure_count: int
    is_network_error: bool
    failure_analysis: Dict


def report_failed_attempt(
    app,
    *,
    url: str,
    attempt: int,
    strategy: Dict,
    command: List[str],
    result,
    elapsed_sec: float,
    socket_timeout: int,
    proxy_enabled: bool,
    proxy_url_masked: str,
    network_failure_count: int,
    slow_network_failure_count: int,
    attempt_summaries: List[Dict],
    strategy_count: int,
    download_temp_dir,
    expected_video_id: str,
) -> FailedAttemptReport:
    combined_error = "\n".join(
        part for part in (result.stderr, result.stdout) if part
    )
    landmarks = list(getattr(result, "diagnostic_landmarks", []) or [])
    analysis = analyze_yt_dlp_failure(
        combined_error,
        strategy=strategy,
        returncode=result.returncode,
        diagnostic_landmarks=landmarks,
    )
    last_error = combined_error[-3000:] if combined_error else "Неизвестная ошибка"

    if strategy["name"] == "EJS GitHub stable auto quality":
        app._note_youtube_primary_strategy_result(False, last_error)

    network_failure_count, slow_network_failure_count, is_network_error = (
        app._updated_youtube_network_failure_counts(
            last_error,
            elapsed_sec,
            network_failure_count,
            slow_network_failure_count,
        )
    )
    attempt_summaries.append(build_attempt_summary(
        attempt=attempt,
        strategy_name=strategy["name"],
        returncode=result.returncode,
        elapsed_sec=elapsed_sec,
        socket_timeout=socket_timeout,
        proxy_enabled=proxy_enabled,
        proxy_url_masked=proxy_url_masked,
        error_text=last_error,
        failure_analysis=analysis,
    ))
    will_retry = attempt < strategy_count
    app.record_yt_dlp_attempt(
        url,
        attempt,
        strategy["name"],
        command,
        result,
        "yt-dlp вернул ненулевой код",
        "WARNING" if will_retry else "ERROR",
        {
            "attempt_elapsed_sec": round(elapsed_sec, 2),
            "network_failure_count": network_failure_count,
            "slow_network_failure_count": slow_network_failure_count,
            "is_network_error": is_network_error,
            "will_retry": will_retry,
            "remaining_strategy_count": max(0, strategy_count - attempt),
            "failure_analysis": analysis,
            "yt_dlp_diagnostic_landmarks": landmarks[-40:],
            "target_files_after_attempt": app._snapshot_download_target_files(
                str(download_temp_dir), expected_video_id, limit=12
            ),
        },
    )
    return FailedAttemptReport(
        last_error=last_error,
        error_lower=last_error.lower(),
        network_failure_count=network_failure_count,
        slow_network_failure_count=slow_network_failure_count,
        is_network_error=is_network_error,
        failure_analysis=analysis,
    )
