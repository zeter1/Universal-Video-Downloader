"""Конвертация скачанных видео в аудио.

Автоматически выделено из прежнего модуля audio_processing.py без изменения тел методов.
"""

from typing import Dict
from typing import List
from pathlib import Path
from typing import Set
import subprocess

from src.core.constants import AUDIO_DURATION_TOLERANCE_SEC, CONVERT_TIMEOUT_SEC, LOG_CATEGORY_DOWNLOAD_FAILED
from src.core.errors import CommandCancelledError
from src.core.session_settings import session_setting


class AudioConvertMixin:
    def convert_videos_to_audio(self, video_paths: List[str], audio_dir: Path) -> List[str]:
        video_files: List[Path] = []
        seen: Set[Path] = set()
        for video_path in video_paths:
            path = Path(video_path)
            try:
                resolved = path.resolve()
            except OSError:
                resolved = path
            if resolved in seen:
                continue
            if path.exists() and self.is_supported_video_file(path):
                video_files.append(path)
                seen.add(resolved)

        if not video_files:
            self.log("⚠️ Нет видео текущей сессии для конвертации", "WARNING")
            return []

        self.log(f"🎵 КОНВЕРТАЦИЯ {len(video_files)} ВИДЕО ТЕКУЩЕЙ СЕССИИ", "INFO")
        converted: List[str] = []
        failed_conversions: List[Dict] = []
        audio_quality_setting = session_setting(self, "audio_quality")

        for idx, video_file in enumerate(video_files, 1):
            if self.cancel_flag.is_set():
                self.log("🛑 Конвертация отменена", "WARNING")
                break

            audio_dir.mkdir(parents=True, exist_ok=True)
            # Equal names/durations do not establish that an MP3 belongs to this video.
            audio_file = self.make_unique_media_path(audio_dir / f"{video_file.stem}.mp3")

            source_duration = self.get_media_duration(str(video_file))
            if not self.has_audio_stream(str(video_file)):
                reason = "в исходном видео нет аудиодорожки"
                self.log(f"[{idx}/{len(video_files)}] ❌ {reason}: {video_file.name}", "ERROR")
                failed_conversions.append({
                    "video_file": str(video_file),
                    "audio_file": str(audio_file),
                    "reason": reason,
                    "source_duration": source_duration,
                })
                self.record_problem(
                    "Конвертация невозможна: в видео нет аудиодорожки",
                    "ERROR", "convert_video_to_audio_no_stream",
                    failed_conversions[-1]
                )
                continue

            self.log(
                f"[{idx}/{len(video_files)}] 🔄 {video_file.name} "
                f"(длительность видео: {self.format_duration(source_duration)})",
                "INFO"
            )

            temp_audio_file = self._conversion_temp_path(audio_file)
            temp_audio_file = self.make_unique_media_path(temp_audio_file)

            audio_filter = "aresample=async=1:first_pts=0"
            cmd = [
                "ffmpeg", "-nostdin", "-y", "-i", str(video_file),
                "-map", "0:a:0", "-vn",
            ]
            if source_duration > 0:
                audio_filter += ",apad"
                cmd.extend(["-af", audio_filter, "-t", f"{source_duration:.3f}"])
            else:
                cmd.extend(["-af", audio_filter])
            cmd.extend(["-acodec", "libmp3lame"])
            if audio_quality_setting == "VBR-0 (лучшее)":
                cmd.extend(["-q:a", "0"])
            else:
                cmd.extend(["-b:a", audio_quality_setting])
            cmd.extend(["-ar", "48000", "-ac", "2", str(temp_audio_file)])

            try:
                r = self.run_command(
                    cmd, CONVERT_TIMEOUT_SEC, "convert_video_to_audio",
                    {
                        "video_file": str(video_file),
                        "audio_file": str(audio_file),
                        "temp_audio_file": str(temp_audio_file),
                        "audio_quality": audio_quality_setting,
                        "source_duration": source_duration,
                        "duration_tolerance_sec": AUDIO_DURATION_TOLERANCE_SEC,
                    }
                )
                ok, reason, src_dur, out_dur = self.validate_converted_audio(
                    video_file, temp_audio_file
                )
                if r.returncode == 0 and ok:
                    with self.file_lock:
                        audio_file = self.make_unique_media_path(audio_file)
                        temp_audio_file.rename(audio_file)
                    sz  = audio_file.stat().st_size / (1024 * 1024)
                    self.log(
                        f"[{idx}/{len(video_files)}] ✅ {audio_file.name} "
                        f"({sz:.1f} MB) | видео {self.format_duration(src_dur)}, "
                        f"mp3 {self.format_duration(out_dur)} | {reason}",
                        "SUCCESS"
                    )
                    converted.append(str(audio_file))
                else:
                    err = self._diagnostic_stderr_excerpt(r.stderr) or reason
                    partial_audio = self.move_audio_to_manual_processing(
                        temp_audio_file, "ffmpeg_failed_or_audio_validation_failed"
                    )
                    normalized_returncode = self._normalized_process_returncode(
                        r.returncode
                    )
                    self.log(
                        f"[{idx}/{len(video_files)}] ❌ Ошибка/потеря при конвертации: "
                        f"{video_file.name} | {reason} | {err[-500:]}",
                        "ERROR"
                    )
                    failed_conversions.append({
                        "video_file": str(video_file),
                        "audio_file": str(audio_file),
                        "partial_audio_file": str(partial_audio) if partial_audio else None,
                        "reason": reason,
                        "source_duration": src_dur,
                        "audio_duration": out_dur,
                        "ffmpeg_returncode": normalized_returncode,
                        "ffmpeg_returncode_raw": r.returncode,
                        "ffmpeg_stderr_excerpt": err,
                    })
                    self.record_problem(
                        "Конвертация завершилась ошибкой или mp3 короче исходника",
                        "ERROR", "convert_video_to_audio_failed_validation",
                        failed_conversions[-1],
                        command=cmd, stdout=r.stdout, stderr=r.stderr
                    )
            except subprocess.TimeoutExpired:
                reason = f"таймаут >{CONVERT_TIMEOUT_SEC // 60} мин"
                partial_audio = self.move_audio_to_manual_processing(
                    temp_audio_file, "ffmpeg_conversion_timeout"
                )
                self.log(
                    f"[{idx}/{len(video_files)}] ⏰ Таймаут конвертации: {video_file.name}",
                    "ERROR"
                )
                failed_conversions.append({
                    "video_file": str(video_file),
                    "audio_file": str(audio_file),
                    "partial_audio_file": str(partial_audio) if partial_audio else None,
                    "reason": reason,
                    "source_duration": source_duration,
                })
                self.record_problem(
                    "Таймаут конвертации видео в аудио",
                    "ERROR", "convert_video_to_audio_timeout",
                    failed_conversions[-1]
                )
            except CommandCancelledError:
                self.move_audio_to_manual_processing(
                    temp_audio_file, "conversion_cancelled"
                )
                self.log("🛑 Конвертация остановлена пользователем", "WARNING")
                break
            except KeyboardInterrupt:
                self.move_audio_to_manual_processing(
                    temp_audio_file, "conversion_keyboard_interrupt"
                )
                break
            except Exception as e:
                partial_audio = self.move_audio_to_manual_processing(
                    temp_audio_file, "conversion_exception"
                )
                self.log(f"[{idx}/{len(video_files)}] ❌ {str(e)}", "ERROR")
                failed_conversions.append({
                    "video_file": str(video_file),
                    "audio_file": str(audio_file),
                    "partial_audio_file": str(partial_audio) if partial_audio else None,
                    "reason": str(e),
                    "source_duration": source_duration,
                })
                self.record_problem(
                    "Непредвиденная ошибка при конвертации видео в аудио",
                    "ERROR", "convert_video_to_audio_exception",
                    failed_conversions[-1], e
                )

        if self.cancel_flag.is_set():
            self.log(
                f"🛑 Конвертация прервана: готово {len(converted)}/{len(video_files)}",
                "WARNING"
            )
        elif failed_conversions:
            self.log(
                f"❌ Конвертация завершена с проблемами: "
                f"готово {len(converted)}/{len(video_files)}, ошибок {len(failed_conversions)}",
                "ERROR"
            )
            for item in failed_conversions:
                original_video = str(item.get("video_file", "") or "")
                if not original_video:
                    continue
                manual_video = self.move_video_to_manual_processing(
                    original_video, str(item.get("reason", "conversion_failed"))
                )
                item["original_video_file"] = original_video
                item["video_file"] = manual_video
                if manual_video != original_video:
                    item["manual_processing_video"] = manual_video

            failed_conversion_file = self.save_failed_conversions_session(failed_conversions)
            failed_location = (failed_conversion_file.name if failed_conversion_file
                               else "Обработать вручную")
            self.log(
                f"⚠️ Некоторые видео не удалось конвертировать в аудио. "
                f"Исходные видео и файл сессии помещены в «Обработать вручную»: "
                f"{failed_location}",
                "WARNING"
            )
            self.log(
                f"📁 Открыть папку Обработать вручную: {self.manual_processing_dir}",
                "MANUAL_FOLDER_LINK",
                category=LOG_CATEGORY_DOWNLOAD_FAILED,
            )
            self.record_problem(
                "Итог конвертации: часть видео не сконвертировалась или потеряла длительность",
                "ERROR", "convert_videos_to_audio_summary",
                {
                    "converted_count": len(converted),
                    "total_video_count": len(video_files),
                    "failed_conversions": failed_conversions,
                }
            )
        else:
            self.log(
                f"🎵 Конвертация завершена и проверена: {len(converted)}/{len(video_files)}",
                "SUCCESS"
            )
        return converted
