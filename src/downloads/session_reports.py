"""Отчёты по проблемным загрузкам, ошибкам и конвертациям.

Автоматически выделено из прежнего модуля download_workflow.py без изменения тел методов.
"""

from typing import Dict
from typing import List
from typing import Optional
from pathlib import Path
from datetime import datetime
import json


class DownloadSessionReportsMixin:
    def save_problematic_downloads_session(self, problematic_items: List[Dict]) -> Optional[Path]:
        """Сохраняет ссылки на видео, которые качались слишком долго/рывками.

        Это отдельный список, не смешиваем его с «Неудачными загрузками»: такие
        ролики часто скачиваются нормально позже после смены VPN-сервера.
        """
        if not problematic_items:
            return None
        try:
            self.manual_processing_dir.mkdir(parents=True, exist_ok=True)
            session_ts = (self.current_session_start
                          or datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
            problematic_file = self.make_unique_session_file(
                self.manual_processing_dir, "problematic", session_ts
            )

            with open(problematic_file, 'w', encoding='utf-8') as f:
                f.write("Обработать вручную — слишком медленно скачивалось\n")
                f.write(f"Сессия: {session_ts}\n")
                f.write(f"Количество: {len(problematic_items)}\n")
                f.write("Причина: видео качалось слишком медленно через текущий VPN/CDN YouTube.\n")
                f.write("Рекомендация: сменить VPN-сервер и повторно вставить эти ссылки в программу.\n")
                f.write("=" * 80 + "\n\n")

                for idx, item in enumerate(problematic_items, 1):
                    url = item.get("url") or item.get("context", {}).get("url") or "-"
                    reason = item.get("reason", "слишком медленное скачивание")
                    context = item.get("context", {}) or {}
                    diag = context.get("slow_download_diagnostic", {}) or {}
                    f.write(f"{idx}. URL: {url}\n")
                    f.write(f"   Причина: {reason}\n")
                    if item.get("index_in_session"):
                        f.write(
                            f"   Номер в очереди: {item.get('index_in_session')}/"
                            f"{item.get('total_in_session', '-')}\n"
                        )
                    if context.get("strategy"):
                        f.write(f"   Стратегия yt-dlp: {context.get('strategy')}\n")
                    if diag:
                        f.write(f"   Прошло минут: {diag.get('elapsed_min', '-')}\n")
                        f.write(f"   Прогресс: {diag.get('last_percent', '-')}%\n")
                        f.write(
                            f"   Фрагмент: {diag.get('last_fragment', '-')}"
                            f"/{diag.get('last_fragment_total', '-')}\n"
                        )
                        f.write(f"   Скорость: {diag.get('last_speed_text', '-')}\n")
                        f.write(f"   Последняя строка: {diag.get('last_progress_line', '-')}\n")
                    f.write("   JSON для ИИ: ")
                    f.write(json.dumps(item, ensure_ascii=False, default=str))
                    f.write("\n\n")

            self.log(
                f"💾 Ссылки на медленные загрузки сохранены для ручной обработки: {problematic_file.name}",
                "INFO"
            )
            return problematic_file
        except Exception as e:
            self.log(f"Ошибка сохранения проблемно скачиваемых видео: {str(e)}", "ERROR")
            self.record_problem(
                "Не удалось сохранить файл проблемно скачиваемых видео текущей сессии",
                "ERROR", "save_problematic_downloads_session",
                {"problematic_items": problematic_items}, e
            )
            return None


    def save_failed_downloads_session(self, failed_urls: List[str]) -> Optional[Path]:
        if not failed_urls:
            return None
        try:
            self.manual_processing_dir.mkdir(parents=True, exist_ok=True)
            session_ts = (self.current_session_start
                          or datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
            safe_ts = session_ts.replace(':', '-').replace(' ', '_')
            failed_file = self.manual_processing_dir / f"failed_{safe_ts}.txt"

            counter = 2
            while failed_file.exists():
                failed_file = self.manual_processing_dir / f"failed_{safe_ts}_{counter}.txt"
                counter += 1

            with open(failed_file, 'w', encoding='utf-8') as f:
                f.write("Обработать вручную — видео не скачалось\n")
                f.write(f"Сессия: {session_ts}\n")
                f.write(f"Количество: {len(failed_urls)}\n")
                f.write("=" * 80 + "\n\n")
                for idx, url in enumerate(failed_urls, 1):
                    f.write(f"{idx}. {url}\n")

            self.log(f"💾 Неудачные загрузки сохранены для ручной обработки: {failed_file.name}", "INFO")
            return failed_file
        except Exception as e:
            self.log(f"Ошибка сохранения неудачных загрузок: {str(e)}", "ERROR")
            self.record_problem(
                "Не удалось сохранить файл неудачных загрузок текущей сессии",
                "ERROR", "save_failed_downloads_session",
                {"failed_urls": failed_urls}, e
            )
            return None


    def save_failed_conversions_session(self, failed_conversions: List[Dict]) -> Optional[Path]:
        if not failed_conversions:
            return None
        try:
            self.manual_processing_dir.mkdir(parents=True, exist_ok=True)
            session_ts = (self.current_session_start
                          or datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
            failed_file = self.make_unique_session_file(
                self.manual_processing_dir, "not_converted", session_ts
            )

            def duration_text(value) -> Optional[str]:
                if value is None:
                    return None
                if isinstance(value, (int, float)):
                    return f"{self.format_duration(float(value))} ({float(value):.3f} сек)"
                return str(value)

            with open(failed_file, 'w', encoding='utf-8') as f:
                f.write("Обработать вручную — не конвертировалось в аудио\n")
                f.write(f"Сессия: {session_ts}\n")
                f.write(f"Количество проблемных видео: {len(failed_conversions)}\n")
                f.write("Важно: остальные скачанные видео программа продолжила конвертировать.\n")
                f.write("=" * 80 + "\n\n")

                for idx, item in enumerate(failed_conversions, 1):
                    f.write(f"{idx}. Видео: {item.get('video_file', '-')}\n")
                    if item.get("original_video_file") != item.get("video_file"):
                        f.write(f"   Исходное расположение: {item.get('original_video_file', '-')}\n")
                    f.write(f"   MP3: {item.get('audio_file', '-')}\n")
                    if item.get("partial_audio_file"):
                        f.write(
                            f"   Сохранён неполный MP3: "
                            f"{item.get('partial_audio_file')}\n"
                        )
                    f.write(f"   Причина: {item.get('reason', '-')}\n")

                    source_duration = duration_text(item.get("source_duration"))
                    if source_duration:
                        f.write(f"   Длительность видео: {source_duration}\n")

                    audio_duration = duration_text(item.get("audio_duration"))
                    if audio_duration:
                        f.write(f"   Длительность mp3: {audio_duration}\n")

                    if "ffmpeg_returncode" in item:
                        f.write(f"   Код FFmpeg: {item.get('ffmpeg_returncode')}\n")

                    if item.get("ffmpeg_stderr_excerpt"):
                        f.write(f"   FFmpeg stderr: {item.get('ffmpeg_stderr_excerpt')}\n")

                    f.write("   JSON для ИИ: ")
                    f.write(json.dumps(item, ensure_ascii=False, default=str))
                    f.write("\n\n")

            self.log(
                f"💾 Проблемы конвертации сохранены в отдельный файл: {failed_file.name}",
                "INFO"
            )
            return failed_file
        except Exception as e:
            self.log(f"Ошибка сохранения лога не сконвертированных видео: {str(e)}", "ERROR")
            self.record_problem(
                "Не удалось сохранить файл проблемных конвертаций текущей сессии",
                "ERROR", "save_failed_conversions_session",
                {"failed_conversions": failed_conversions}, e
            )
            return None
