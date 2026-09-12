"""Guarantee a final MP4 container without needlessly re-encoding video.

The normal yt-dlp path already asks FFmpeg to merge into MP4.  This module is
an additional publication guard for progressive/fallback formats that arrive
as WebM/MKV.  It first performs a lossless stream-copy remux; only if the
container/codecs cannot be copied to MP4 does it fall back to H.264/AAC
re-encoding.

DEPENDS ON app methods: run_command(), record_problem(), log(),
validate_downloaded_video().
"""

from pathlib import Path
from typing import Optional
import os

from src.core.constants import DOWNLOAD_TIMEOUT_SEC


def _conversion_target(source: Path) -> Path:
    return source.with_name(f".{source.stem}.{os.getpid()}.mp4-normalizing.mp4")


def ensure_mp4_video(app, source_path: str, *, expected_video_id: Optional[str] = None) -> Optional[str]:
    """Return an MP4 path for ``source_path`` or ``None`` when conversion fails.

    Existing MP4 files are returned unchanged.  Non-MP4 files are first
    remuxed with ``-c copy`` (fast/no quality loss).  Re-encoding is a rare
    fallback for codec/container combinations that MP4 cannot stream-copy.
    """
    source = Path(source_path)
    if not source.exists() or not source.is_file():
        return None
    if source.suffix.casefold() == ".mp4":
        return str(source)

    temp_target = _conversion_target(source)
    final_target = source.with_suffix(".mp4")
    try:
        temp_target.unlink(missing_ok=True)
    except OSError:
        pass
    if final_target.exists() and final_target != source:
        try:
            final_target.unlink()
        except OSError as error:
            app.record_problem(
                "Не удалось освободить имя для итогового MP4",
                "ERROR", "mp4_output_target_locked",
                {
                    "source_path": str(source),
                    "target_path": str(final_target),
                    "expected_video_id": expected_video_id,
                },
                error,
            )
            return None

    common_context = {
        "source_path": str(source),
        "target_path": str(final_target),
        "source_extension": source.suffix.lower(),
        "expected_video_id": expected_video_id,
    }

    remux_cmd = [
        "ffmpeg", "-y", "-nostdin", "-v", "error",
        "-i", str(source),
        "-map", "0:v:0", "-map", "0:a?",
        "-c", "copy", "-movflags", "+faststart",
        str(temp_target),
    ]
    try:
        remux = app.run_command(
            remux_cmd, DOWNLOAD_TIMEOUT_SEC, "mp4_output_remux", common_context
        )
    except Exception as error:
        remux = None
        app.record_problem(
            "Ошибка запуска FFmpeg при remux в MP4",
            "WARNING", "mp4_output_remux_exception",
            common_context, error, command=remux_cmd,
        )

    if remux is not None and remux.returncode == 0 and temp_target.exists():
        try:
            source.unlink()
            temp_target.replace(final_target)
            app.record_problem(
                "Видео без потери качества remux-ировано в MP4",
                "INFO", "mp4_output_remux_succeeded",
                {**common_context, "mode": "stream_copy"},
                resolved=True,
            )
            return str(final_target)
        except OSError as error:
            app.record_problem(
                "FFmpeg создал MP4, но не удалось опубликовать файл",
                "ERROR", "mp4_output_publish_failed",
                common_context, error,
            )
            return None

    remux_stderr = getattr(remux, "stderr", "") if remux is not None else ""
    app.record_problem(
        "Потоковый remux в MP4 не удался; используется совместимое H.264/AAC перекодирование",
        "WARNING", "mp4_output_remux_failed_fallback_transcode",
        {
            **common_context,
            "returncode": getattr(remux, "returncode", None),
        },
        command=remux_cmd,
        stdout=getattr(remux, "stdout", "") if remux is not None else "",
        stderr=remux_stderr,
    )
    try:
        temp_target.unlink(missing_ok=True)
    except OSError:
        pass

    transcode_cmd = [
        "ffmpeg", "-y", "-nostdin", "-v", "error",
        "-i", str(source),
        "-map", "0:v:0", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(temp_target),
    ]
    try:
        transcode = app.run_command(
            transcode_cmd, DOWNLOAD_TIMEOUT_SEC, "mp4_output_transcode", common_context
        )
    except Exception as error:
        app.record_problem(
            "Не удалось перекодировать видео в MP4",
            "ERROR", "mp4_output_transcode_exception",
            common_context, error, command=transcode_cmd,
        )
        return None

    if transcode.returncode != 0 or not temp_target.exists():
        app.record_problem(
            "FFmpeg не смог создать итоговый MP4 даже через H.264/AAC",
            "ERROR", "mp4_output_transcode_failed",
            {**common_context, "returncode": transcode.returncode},
            command=transcode_cmd, stdout=transcode.stdout, stderr=transcode.stderr,
        )
        return None

    try:
        source.unlink()
        temp_target.replace(final_target)
    except OSError as error:
        app.record_problem(
            "Перекодированный MP4 создан, но не удалось опубликовать файл",
            "ERROR", "mp4_output_transcode_publish_failed",
            common_context, error,
        )
        return None

    app.record_problem(
        "Видео перекодировано в совместимый MP4 (H.264/AAC)",
        "INFO", "mp4_output_transcode_succeeded",
        {**common_context, "mode": "h264_aac"},
        resolved=True,
    )
    return str(final_target)
