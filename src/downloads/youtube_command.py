"""Сборка одной команды yt-dlp.

Owner-файл для CLI-аргументов yt-dlp, format selector, subtitles и output template.
Не определяет порядок стратегий и не интерпретирует результат процесса.
"""

from typing import Dict
from typing import List
from pathlib import Path

from src.core.constants import VIDEO_MAX_HEIGHT, YT_DLP_EXTRACTOR_RETRIES, YT_DLP_FRAGMENT_RETRIES, YT_DLP_NETWORK_RETRY_PAUSE_SEC, YT_DLP_RETRIES, YT_DLP_SOCKET_TIMEOUT
from src.core.session_settings import session_setting


YOUTUBE_PREFERRED_AUDIO_LANGUAGE = "ru"


def _with_youtube_language_preference(args: List[str], language: str = YOUTUBE_PREFERRED_AUDIO_LANGUAGE) -> List[str]:
    """Merge a YouTube language preference into strategy extractor args.

    YouTube may expose multiple audio tracks (original + auto-dubs).  Current
    yt-dlp versions can otherwise receive/select a translated track depending
    on the player client.  Keep every strategy-specific extractor option, but
    add one explicit language preference instead of replacing the client.
    """
    result = list(args)
    for index, value in enumerate(result[:-1]):
        if value != "--extractor-args":
            continue
        extractor_args = str(result[index + 1])
        if not extractor_args.startswith("youtube:"):
            continue
        payload = extractor_args.split(":", 1)[1]
        parts = [part for part in payload.split(";") if part]
        parts = [part for part in parts if not part.startswith("lang=")]
        parts.append(f"lang={language}")
        result[index + 1] = "youtube:" + ";".join(parts)
        return result
    result.extend(["--extractor-args", f"youtube:lang={language}"])
    return result


def video_format_args(
    progressive: bool = False,
    fallback_any: bool = False,
    format_profile: str = "",
    preferred_audio_language: str = "",
) -> List[str]:
    """Build a 1080p-capped selector with a merge-safe direct HTTP default.

    Recent YouTube HLS formats can download successfully and then fail in FFmpeg
    while merging into MKV because packet timestamps are missing. The normal
    high-quality profile therefore prefers direct HTTP/DASH formats. Riskier
    any-protocol formats remain available only in explicit fallback strategies.
    """
    profile = str(format_profile or "").strip().lower()
    language = str(preferred_audio_language or "").strip()
    language_filter = f"[language^={language}]" if language else ""
    if progressive:
        # For YouTube prefer a ready single-file HTTP(S) stream carrying the
        # requested language. Other sites keep the historical selector.
        preferred_http = (
            f"best[height<={VIDEO_MAX_HEIGHT}]{language_filter}"
            f"[protocol^=http][protocol!*=dash]/"
            if language_filter else ""
        )
        preferred_any = (
            f"best[height<={VIDEO_MAX_HEIGHT}]{language_filter}/"
            if language_filter else ""
        )
        selector = (
            preferred_http
            + f"best[height<={VIDEO_MAX_HEIGHT}][protocol^=http][protocol!*=dash]/"
            + preferred_any
            + f"best[height<={VIDEO_MAX_HEIGHT}]"
        )
    elif fallback_any or profile == "any":
        preferred_separate = (
            f"bestvideo*[height<={VIDEO_MAX_HEIGHT}]+bestaudio{language_filter}/"
            if language_filter else ""
        )
        preferred_combined = (
            f"best[height<={VIDEO_MAX_HEIGHT}]{language_filter}/"
            if language_filter else ""
        )
        selector = (
            preferred_separate
            + f"bestvideo*[height<={VIDEO_MAX_HEIGHT}]+bestaudio/"
            + preferred_combined
            + f"best[height<={VIDEO_MAX_HEIGHT}]"
        )
    else:
        # Preserve the existing 1080p-ready-stream optimization while adding
        # a YouTube audio-language preference.  Without a language preference,
        # behavior stays byte-for-byte equivalent to the current main policy:
        # exact 1080p combined -> split direct streams -> lower combined.
        video = f"bestvideo[height<={VIDEO_MAX_HEIGHT}][protocol^=http][protocol!*=dash]"
        audio_any = "bestaudio[protocol^=http][protocol!*=dash]"
        ready_target_any = (
            f"best[height={VIDEO_MAX_HEIGHT}][protocol^=http][protocol!*=dash]"
        )
        combined_any = f"best[height<={VIDEO_MAX_HEIGHT}][protocol^=http][protocol!*=dash]"
        if language_filter:
            audio_preferred = f"bestaudio{language_filter}[protocol^=http][protocol!*=dash]"
            ready_target_preferred = (
                f"best[height={VIDEO_MAX_HEIGHT}]{language_filter}"
                f"[protocol^=http][protocol!*=dash]"
            )
            combined_preferred = (
                f"best[height<={VIDEO_MAX_HEIGHT}]{language_filter}"
                f"[protocol^=http][protocol!*=dash]"
            )
            selector = (
                f"{ready_target_preferred}/"
                f"{video}+{audio_preferred}/"
                f"{ready_target_any}/"
                f"{video}+{audio_any}/"
                f"{combined_preferred}/"
                f"{combined_any}"
            )
        else:
            selector = (
                f"{ready_target_any}/"
                f"{video}+{audio_any}/"
                f"{combined_any}"
            )
    # With YouTube language preference, restore extractor `lang` before the
    # container/bitrate tie-breakers. Other sites retain the historical sort.
    sort_order = (
        "height,lang,vext,aext,vbr,abr,res,fps"
        if language_filter
        else "height,vext,aext,vbr,abr,res,fps"
    )
    return ["-f", selector, "--format-sort-force", "-S", sort_order]

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
        "--print", "before_dl:[DOWNLOAD_PLAN] id=%(id)s format_id=%(format_id)s protocol=%(protocol)s ext=%(ext)s height=%(height)s width=%(width)s vcodec=%(vcodec)s acodec=%(acodec)s language=%(language)s format_note=%(format_note)s",
        "--print", "after_move:filepath",
    ]
    if proxy_args:
        cmd.extend(proxy_args)
    cmd.extend(strategy.get('network_args', default_network_args))

    is_youtube = app.is_youtube_url(url)
    ejs_mode = strategy.get('ejs_mode', 'github')
    ejs_args = app.build_youtube_ejs_args(url, ejs_mode) if is_youtube else []
    cmd.extend(ejs_args)
    strategy_args = list(strategy['args'])
    if is_youtube:
        strategy_args = _with_youtube_language_preference(strategy_args)
    cmd.extend(strategy_args)
    if strategy.get("diagnostic_verbose"):
        cmd.append("--verbose")
    cmd.extend(video_format_args(
        bool(strategy.get('progressive_format')),
        bool(strategy.get('fallback_format')),
        str(strategy.get('format_profile') or ""),
        YOUTUBE_PREFERRED_AUDIO_LANGUAGE if is_youtube else "",
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
