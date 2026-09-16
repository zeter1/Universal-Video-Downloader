"""Сборка одной команды yt-dlp.

Owner-файл для CLI-аргументов yt-dlp, format selector, subtitles и output template.
Не определяет порядок стратегий и не интерпретирует результат процесса.
"""

from typing import Dict
from typing import List
from pathlib import Path

from src.core.constants import VIDEO_MAX_HEIGHT, YT_DLP_EXTRACTOR_RETRIES, YT_DLP_FRAGMENT_RETRIES, YT_DLP_NETWORK_RETRY_PAUSE_SEC, YT_DLP_RETRIES, YT_DLP_SOCKET_TIMEOUT
from src.core.session_settings import session_setting


def video_format_args(
    progressive: bool = False,
    fallback_any: bool = False,
    format_profile: str = "",
) -> List[str]:
    """Build a 1080p-capped selector with a merge-safe direct HTTP default.

    Recent YouTube HLS formats can download successfully and then fail in FFmpeg
    while merging into MKV because packet timestamps are missing. The normal
    high-quality profile therefore prefers direct HTTP/DASH formats. A ready
    single-file stream at the target height is preferred before split streams;
    lower progressive formats must not displace a higher-resolution split pair.
    Riskier any-protocol formats remain available only in explicit fallbacks.
    """
    profile = str(format_profile or "").strip().lower()
    if progressive:
        # Prefer a ready single-file HTTP(S) stream. The second branch keeps a
        # last-resort progressive option for clients that expose only HLS.
        selector = (
            f"best[height<={VIDEO_MAX_HEIGHT}][protocol^=http][protocol!*=dash]/"
            f"best[height<={VIDEO_MAX_HEIGHT}]"
        )
    elif fallback_any or profile == "any":
        selector = (
            f"bestvideo*[height<={VIDEO_MAX_HEIGHT}]+bestaudio/"
            f"best[height<={VIDEO_MAX_HEIGHT}]"
        )
    else:
        # Prefer a ready single-file stream when it already reaches the target
        # height. Otherwise keep 1080p-first behavior with separate direct
        # video+audio, instead of letting (for example) a combined 360p stream
        # displace an available 1080p pair.
        selector = (
            f"best[height={VIDEO_MAX_HEIGHT}][protocol^=http][protocol!*=dash]/"
            f"bestvideo[height<={VIDEO_MAX_HEIGHT}][protocol^=http][protocol!*=dash]+"
            f"bestaudio[protocol^=http][protocol!*=dash]/"
            f"best[height<={VIDEO_MAX_HEIGHT}][protocol^=http][protocol!*=dash]"
        )
    # Keep resolution as the first priority, but prefer MP4/M4A when yt-dlp has
    # several formats at the same height. This preserves 1080p-first behavior
    # while making the common successful path land directly in MP4.
    return [
        "-f", selector, "--format-sort-force",
        "-S", "height,vext,aext,vbr,abr,res,fps",
    ]


def build_ytdlp_download_command(
    app,
    url: str,
    strategy: Dict,
    default_network_args: List[str],
    download_temp_dir: Path,
    proxy_args: List[str],
):
    """Вернуть command + вычисленные retry/EJS параметры одной попытки."""
    strategy_retries = int(strategy.get('retries', YT_DLP_RETRIES))
    strategy_fragment_retries = int(
        strategy.get('fragment_retries', YT_DLP_FRAGMENT_RETRIES)
    )
    strategy_socket_timeout = int(
        strategy.get('socket_timeout', YT_DLP_SOCKET_TIMEOUT)
    )
    cmd = [
        "yt-dlp",
        "--user-agent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "--no-playlist",
        "--encoding", "utf-8",
        "--merge-output-format", "mp4",
        "--no-hls-use-mpegts",
        "--retries", str(strategy_retries),
        "--fragment-retries", str(strategy_fragment_retries),
        "--extractor-retries", str(YT_DLP_EXTRACTOR_RETRIES),
        "--retry-sleep", str(YT_DLP_NETWORK_RETRY_PAUSE_SEC),
        "--socket-timeout", str(strategy_socket_timeout),
        "--file-access-retries", "3",
        "--progress",
        "--newline",
        "--throttled-rate", "100K",
        "--continue",
        "--no-mtime",
        "--print", "before_dl:[DOWNLOAD_PLAN] id=%(id)s format_id=%(format_id)s protocol=%(protocol)s ext=%(ext)s height=%(height)s width=%(width)s vcodec=%(vcodec)s acodec=%(acodec)s",
        "--print", "after_move:filepath",
    ]
    if proxy_args:
        cmd.extend(proxy_args)
    cmd.extend(strategy.get('network_args', default_network_args))

    ejs_mode = strategy.get('ejs_mode', 'github')
    ejs_args = app.build_youtube_ejs_args(url, ejs_mode) if app.is_youtube_url(url) else []
    cmd.extend(ejs_args)
    cmd.extend(strategy['args'])
    if strategy.get("diagnostic_verbose"):
        cmd.append("--verbose")
    cmd.extend(video_format_args(
        bool(strategy.get('progressive_format')),
        bool(strategy.get('fallback_format')),
        str(strategy.get('format_profile') or ""),
    ))

    if session_setting(app, "download_subtitles"):
        cmd.extend(["--write-sub", "--write-auto-sub", "--sub-lang", "ru,en"])

    output_template = download_temp_dir / "%(title)s.%(ext)s"
    cmd.extend([
        "--windows-filenames", "--trim-filenames", "240",
        "-o", str(output_template), url,
    ])
    return (
        cmd,
        ejs_args,
        strategy_retries,
        strategy_fragment_retries,
        strategy_socket_timeout,
    )
