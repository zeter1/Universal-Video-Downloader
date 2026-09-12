"""Build yt-dlp attempt context and first-attempt user messages.

DEPENDS ON app methods: log(), youtube_ejs_status_text(), proxy_status_text(),
_snapshot_download_target_files(). No retry or file-finalization decisions live here.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List
import shutil

from src.core.constants import (
    PROBLEM_LOG_INFO_FILE_SNAPSHOT_LIMIT, VIDEO_TARGET_HEIGHT, YT_DLP_HTTP_CHUNK_SIZE,
)
from src.core.session_settings import session_setting


@dataclass(frozen=True)
class DownloadSetup:
    video_dir: Path
    quality: str
    expected_video_id: str
    download_temp_dir: Path
    video_files_before: object
    quality_candidate_dir: Path
    proxy_args: List[str]
    proxy_enabled: bool
    proxy_url_masked: str


def prepare_download_setup(app, url: str) -> DownloadSetup:
    video_dir = Path(session_setting(app, "save_path")) / "Видео"
    video_dir.mkdir(parents=True, exist_ok=True)
    quality = f"{VIDEO_TARGET_HEIGHT}p"
    expected_video_id = app.extract_video_id(url)
    download_temp_dir = app.make_video_download_temp_dir(video_dir, url, expected_video_id)
    download_temp_dir.mkdir(parents=True, exist_ok=True)
    video_files_before = app.snapshot_video_files(download_temp_dir)
    quality_candidate_dir = video_dir / "_quality_candidates" / download_temp_dir.name
    try:
        shutil.rmtree(quality_candidate_dir, ignore_errors=True)
    except Exception:
        pass
    proxy_url = app.get_proxy_url()
    proxy_args = app.build_proxy_args()
    return DownloadSetup(
        video_dir=video_dir,
        quality=quality,
        expected_video_id=expected_video_id,
        download_temp_dir=download_temp_dir,
        video_files_before=video_files_before,
        quality_candidate_dir=quality_candidate_dir,
        proxy_args=proxy_args,
        proxy_enabled=bool(proxy_args),
        proxy_url_masked=app.mask_proxy_url(proxy_url),
    )


def log_first_attempt(
    app,
    quality: str,
    fast_fragments: str,
    strategy: Dict,
    strategy_retries: int,
    strategy_fragment_retries: int,
    strategy_socket_timeout: int,
    proxy_enabled: bool,
    proxy_url_masked: str,
) -> None:
    app.log(f"⬇️ Загрузка видео ({quality})...", "INFO")
    if quality != "Лучшее":
        app.log(
            f"🎯 Авто-качество: до {quality}. Если такого качества нет, "
            "скачаю ближайшее доступное ниже.",
            "INFO",
        )
        if quality == "360p":
            app.log(
                "⚡ 360p быстрый режим: сначала пробую цельный MP4-поток со звуком, "
                "а раздельные DASH-потоки оставляю как запасные стратегии.",
                "INFO",
            )
    app.log(
        "⚡ Устойчивый режим YouTube + EJS fallback: "
        f"потоков={getattr(app, 'max_concurrent', '?')}, "
        f"fragments={fast_fragments}, "
        f"retries={strategy_retries}, fragment-retries={strategy_fragment_retries}, "
        f"socket-timeout={strategy_socket_timeout} сек, chunk={YT_DLP_HTTP_CHUNK_SIZE}, "
        f"{app.youtube_ejs_status_text(strategy.get('ejs_mode', 'github'))}, "
        f"{app.proxy_status_text()}",
        "INFO",
    )
    if proxy_enabled:
        app.log(
            f"🌐 Proxy для yt-dlp включён: {proxy_url_masked}. "
            "Сам видеопоток googlevideo.com пойдёт через proxy.",
            "SUCCESS",
        )
    else:
        app.log(
            "🌐 Proxy для yt-dlp выключен. Если googlevideo.com постоянно даёт Read timed out, "
            "включите proxy/VPN и укажите proxy в настройках программы.",
            "INFO",
        )


def build_attempt_context(
    app,
    *,
    url: str,
    attempt: int,
    strategy: Dict,
    quality: str,
    expected_video_id: str,
    download_temp_dir: Path,
    video_dir: Path,
    strategy_count: int,
    default_network_args: List[str],
    strategy_socket_timeout: int,
    strategy_retries: int,
    strategy_fragment_retries: int,
    proxy_enabled: bool,
    proxy_url_masked: str,
    ejs_args: List[str],
) -> Dict:
    return {
        "url": url,
        "attempt": attempt,
        "strategy": strategy["name"],
        "quality": quality,
        "expected_video_id": expected_video_id,
        "target_dir": str(download_temp_dir),
        "final_video_dir": str(video_dir),
        "strategy_count": strategy_count,
        "network_args": strategy.get("network_args", default_network_args),
        "socket_timeout": strategy_socket_timeout,
        "retries": strategy_retries,
        "fragment_retries": strategy_fragment_retries,
        "proxy_enabled": proxy_enabled,
        "proxy_url_masked": proxy_url_masked,
        "ejs_mode": strategy.get("ejs_mode", "github"),
        "diagnostic_verbose": bool(strategy.get("diagnostic_verbose")),
        "format_profile": (
            str(strategy.get("format_profile"))
            if strategy.get("format_profile")
            else "progressive" if strategy.get("progressive_format")
            else "any" if strategy.get("fallback_format")
            else "direct_http"
        ),
        "ejs_args": ejs_args,
        "extra_args": strategy.get("args", []),
        "format_mode": (
            "progressive" if strategy.get("progressive_format")
            else "fallback_any_format" if strategy.get("fallback_format")
            else "normal"
        ),
        "target_files_before_attempt": app._snapshot_download_target_files(
            str(download_temp_dir),
            expected_video_id,
            limit=PROBLEM_LOG_INFO_FILE_SNAPSHOT_LIMIT,
        ),
    }
