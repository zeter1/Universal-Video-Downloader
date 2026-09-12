"""Очередь, ThreadPoolExecutor и запуск пользовательской сессии скачивания.

Автоматически выделено из прежнего модуля download_workflow.py без изменения тел методов.
"""

from typing import List
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed
from datetime import datetime
from tkinter import messagebox
import threading
import time
import tkinter as tk

from src.core.concurrency import ThreadSafeList
from src.core.constants import DEFAULT_PROXY_EXAMPLE, LOG_CATEGORY_DOWNLOAD_FAILED, MAX_CONCURRENT_DL, MAX_URLS_PER_SESSION
from src.core.errors import CommandCancelledError
from src.core.session_settings import session_setting


class DownloadManagerMixin:
    def download_manager(self, urls: List[str]) -> None:
        self.download_outcome = "running"
        try:
            self._run_download_session(urls)
        except CommandCancelledError:
            self.download_outcome = "cancelled"
            self.write_problem_session_summary("cancelled", {"queued_url_count": len(urls)})
        except Exception as error:
            self.download_outcome = "failed"
            self.record_problem(
                "Сессия скачивания прервана ошибкой", "ERROR", "download_manager_failed",
                {"queued_url_count": len(urls)}, error,
            )
            self.log(f"❌ Ошибка сессии скачивания: {error}", "ERROR")
            self.write_problem_session_summary("failed", {"queued_url_count": len(urls)})
        finally:
            self.finish_download()

    def _run_download_session(self, urls: List[str]) -> None:
        # FIX #14: ThreadSafeList вместо plain list — устраняет гонку без list_lock
        downloaded_videos: ThreadSafeList = ThreadSafeList()
        failed_urls: ThreadSafeList = ThreadSafeList()
        problematic_downloads: ThreadSafeList = ThreadSafeList()
        converted: List[str] = []

        save_dir  = Path(session_setting(self, "save_path"))
        audio_dir = save_dir / "Аудио"
        audio_dir.mkdir(parents=True, exist_ok=True)
        video_dir = save_dir / "Видео"
        video_dir.mkdir(parents=True, exist_ok=True)

        stale_count = self.cleanup_stale_download_temp_files(video_dir)
        stale_temp_dirs = self.cleanup_stale_download_temp_dirs(video_dir)
        if stale_count:
            self.log(f"🧹 Перед новой сессией очищены старые временные хвосты yt-dlp: {stale_count}", "INFO")
        if stale_temp_dirs:
            self.log(f"🧹 Перед новой сессией очищены старые временные папки yt-dlp: {stale_temp_dirs}", "INFO")

        self.log(f"📺 СКАЧИВАНИЕ {len(urls)} ВИДЕО", "INFO")
        self.log(
            f"🚀 Параллельные загрузки: {self.max_concurrent}. "
            "YouTube больше не блокируется одним общим слотом.",
            "INFO"
        )
        self.download_threads.clear()

        with ThreadPoolExecutor(max_workers=self.max_concurrent) as executor:
            futures = {}
            for i, url in enumerate(urls):
                if self.cancel_flag.is_set():
                    self.log("🛑 Загрузка отменена пользователем", "WARNING")
                    break
                if self.is_url_downloaded(url):
                    self.log(f"⚠️ Пропущено (уже скачано ранее): {url}", "WARNING")
                    self.update_progress()
                    continue
                future = executor.submit(
                    self.download_single_video_task, url, i,
                    downloaded_videos, failed_urls, problematic_downloads
                )
                futures[future] = url

            for future in as_completed(futures):
                try:
                    future.result()
                except CommandCancelledError:
                    self.cancel_flag.set()
                except Exception as error:
                    failed_url = futures[future]
                    if failed_url not in failed_urls.copy():
                        failed_urls.append(failed_url)
                    self.record_problem(
                        "Необработанная ошибка задачи скачивания", "ERROR",
                        "download_worker_failed", {"url": failed_url}, error,
                    )
                if self.cancel_flag.is_set():
                    for f in futures:
                        f.cancel()
                    break

        if not self.cancel_flag.is_set():
            current_downloaded = downloaded_videos.copy()
            converted = self.convert_videos_to_audio(current_downloaded, audio_dir)

            if self.cancel_flag.is_set():
                self.log("🛑 Объединение аудио не запускается: операция отменена", "WARNING")
            elif session_setting(self, "merge_audio"):
                # Важное поведение: объединяем ВСЕ успешно созданные MP3,
                # даже если часть видео не скачалась или часть роликов не
                # сконвертировалась. Раньше программа пропускала merge при
                # неполной конвертации, из-за чего пользователь оставался без
                # общего аудио даже при 70+ успешных файлах.
                if converted:
                    if len(converted) < len(current_downloaded):
                        self.log(
                            "⚠️ Объединяю только успешно сконвертированные аудио: "
                            f"{len(converted)}/{len(current_downloaded)}. "
                            "Итоговый файл будет неполным относительно всей очереди, "
                            "но все успешные MP3 будут включены.",
                            "WARNING"
                        )
                        self.record_problem(
                            "Объединение аудио запущено по успешным MP3, несмотря на неполную конвертацию",
                            "WARNING", "merge_audio_partial_success",
                            {
                                "downloaded_video_count": len(current_downloaded),
                                "converted_audio_count": len(converted),
                                "missing_audio_count": max(0, len(current_downloaded) - len(converted)),
                                "converted_audio_files": converted,
                            }
                        )
                    else:
                        self.log(
                            f"🔗 ОБЪЕДИНЕНИЕ АУДИО: {len(converted)} успешных MP3",
                            "INFO"
                        )

                    self.merge_audio_files(
                        converted, str(save_dir),
                        self.sanitize_filename(save_dir.name)
                    )
                else:
                    self.log(
                        "⚠️ Объединение аудио не запускается: нет успешно сконвертированных MP3",
                        "WARNING"
                    )
                    self.record_problem(
                        "Объединение аудио не запущено: список успешных MP3 пуст",
                        "WARNING", "merge_audio_no_converted_files",
                        {
                            "downloaded_video_count": len(current_downloaded),
                            "converted_audio_count": 0,
                        }
                    )

        current_problematic = problematic_downloads.copy()
        if current_problematic:
            problematic_file = self.save_problematic_downloads_session(current_problematic)
            problematic_location = (
                problematic_file.name if problematic_file else "Обработать вручную"
            )
            self.log(
                f"⚠️ Пропущено {len(current_problematic)} проблемно скачиваемых видео. "
                f"Ссылки сохранены для ручной обработки: {problematic_location}",
                "WARNING",
                category=LOG_CATEGORY_DOWNLOAD_FAILED,
            )
            self.log(
                f"📁 Открыть папку Обработать вручную: {self.manual_processing_dir}",
                "MANUAL_FOLDER_LINK",
                category=LOG_CATEGORY_DOWNLOAD_FAILED,
            )

        current_failed = failed_urls.copy()

        if current_failed:
            failed_file = self.save_failed_downloads_session(current_failed)
            failed_location = failed_file.name if failed_file else "Обработать вручную"
            self.log(
                f"⚠️ Не удалось скачать {len(current_failed)} видео. "
                f"Ссылки сохранены для ручной обработки: {failed_location}",
                "WARNING",
                category=LOG_CATEGORY_DOWNLOAD_FAILED,
            )
            self.log(
                f"📁 Открыть папку Обработать вручную: {self.manual_processing_dir}",
                "MANUAL_FOLDER_LINK"
            )

        self.download_outcome = (
            "cancelled" if self.cancel_flag.is_set() else
            "failed" if current_failed or current_problematic or len(converted) < len(downloaded_videos)
            else "completed"
        )
        self.write_problem_session_summary(
            self.download_outcome,
            {
                "queued_url_count": len(urls),
                "downloaded_video_count": len(downloaded_videos),
                "failed_download_count": len(current_failed),
                "problematic_download_count": len(current_problematic),
                "converted_audio_count": len(converted),
                "cancel_requested": self.cancel_flag.is_set(),
                "elapsed_sec": round(
                    time.time() - self.download_start_time, 2
                ) if self.download_start_time else 0,
            },
        )


    def start_download(self) -> None:
        if self.is_downloading:
            return
        if self.missing_deps:
            messagebox.showerror(
                "Ошибка",
                "Невозможно начать загрузку!\n\n"
                f"Не установлены: {', '.join(self.missing_deps)}"
            )
            return

        self.save_current_settings()

        if hasattr(self, "proxy_enabled") and session_setting(self, "proxy_enabled") and not self.get_proxy_url():
            messagebox.showwarning(
                "Прокси указан неправильно",
                "Галочка «Прокси yt-dlp» включена, но адрес proxy пустой или некорректный.\n\n"
                f"Пример: {DEFAULT_PROXY_EXAMPLE}\n"
                "Можно либо исправить адрес, либо временно выключить галочку."
            )
            return

        raw = self.url_text.get("1.0", tk.END).strip().split("\n")
        all_urls = [u.strip() for u in raw if u.strip()]

        valid_urls: List[str] = []
        invalid_urls: List[str] = []
        for url in all_urls:
            if self.validate_url(url):
                valid_urls.append(url)
            else:
                if url:
                    invalid_urls.append(url)

        # Дедупликация URL в текстовом поле: preserve order + одинаковый YouTube video_id.
        valid_urls, duplicate_urls = self.deduplicate_urls_by_identity(valid_urls)
        if duplicate_urls:
            self.log(f"ℹ️ Убрано {len(duplicate_urls)} дублей внутри очереди", "INFO")

        if invalid_urls:
            self.log(f"⚠️ Пропущено {len(invalid_urls)} невалидных URL", "WARNING")

        already_done = [u for u in valid_urls if self.is_url_downloaded(u)]
        urls = [u for u in valid_urls if not self.is_url_downloaded(u)]

        if already_done:
            self.log(f"⏭️ Пропущено {len(already_done)} уже скачанных видео", "WARNING")

        if len(urls) > MAX_URLS_PER_SESSION:
            messagebox.showwarning(
                "Слишком много URL",
                f"Максимум {MAX_URLS_PER_SESSION} видео за раз.\n"
                f"Вы добавили {len(urls)}.\nРазделите на несколько очередей."
            )
            return

        if not urls:
            messagebox.showwarning(
                "Предупреждение",
                "Нет новых видео для скачивания.\n\nВсе URL уже скачаны или список пуст."
            )
            return

        if not self.check_internet_connection():
            messagebox.showerror("Ошибка", "Нет подключения к интернету!")
            return

        session_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.current_session_start = session_time
        self.current_history_session_file = None
        self.current_download_log_session_file = None
        self.current_session_download_count = 0

        self.is_downloading = True
        self.cancel_flag.clear()
        try:
            self.max_concurrent = max(1, min(self.concurrent_var.get(), MAX_CONCURRENT_DL))
        except tk.TclError:
            self.max_concurrent = 2

        # VPN/TUN-режим: если пользователь выбрал 2 потока, не понижаем YouTube до 1.
        # При включённом системном VPN два параллельных ролика обычно качаются стабильно,
        # а ограничение до 1 потока сильно замедляло большие очереди.
        if any(self.is_youtube_url(u) for u in urls):
            self.log(
                f"🌐 VPN/TUN-режим: YouTube будет качаться в {self.max_concurrent} поток(а/ов). "
                "Если VPN выключен и снова пойдут Read timed out, поставьте 1 поток.",
                "INFO"
            )

        self.total_files.reset(len(urls))
        self.completed_files.reset(0)
        self.download_start_time = time.time()
        with self.youtube_strategy_state_lock:
            self.youtube_primary_auth_failure_count = 0
            self.youtube_primary_success_count = 0
            self.youtube_adaptive_strategy_logged = False

        self.download_btn.config(state=tk.DISABLED)
        self.cancel_btn.config(state=tk.NORMAL)
        self.main_progress_frame.grid()
        self.main_progress['value'] = 0
        self.main_progress['maximum'] = len(urls)
        self.update_main_progress(0)

        self.log(f"🚀 Начало загрузки {len(urls)} видео", "INFO")
        self.record_problem(
            "Старт сессии скачивания: полный снимок очереди и настроек",
            "INFO", "download_session_started",
            {
                "url_count": len(urls),
                "urls": urls[:MAX_URLS_PER_SESSION],
                "quality": "1080p",
                "audio_quality": session_setting(self, "audio_quality"),
                "max_concurrent": self.max_concurrent,
                "merge_audio": session_setting(self, "merge_audio"),
                "download_subtitles": session_setting(self, "download_subtitles"),
                "proxy_enabled": bool(self.get_proxy_url()),
                "proxy_url_masked": self.mask_proxy_url(self.get_proxy_url()),
                "save_path": session_setting(self, "save_path"),
            }
        )
        self.write_problem_session_summary(
            "running",
            {
                "queued_url_count": len(urls),
                "quality": "1080p",
                "max_concurrent": self.max_concurrent,
                "merge_audio": session_setting(self, "merge_audio"),
                "proxy_enabled": bool(self.get_proxy_url()),
            },
        )

        # Snapshot must not assume that every persisted setting has a Tk control.
        # Proxy settings can intentionally exist only in self.settings; the old
        # getattr(...).get() code raised AttributeError before yt-dlp could start.
        try:
            self.download_settings_snapshot = {
                name: session_setting(self, name)
                for name in (
                    "save_path", "audio_quality", "merge_audio", "download_subtitles",
                    "split_hours", "proxy_enabled", "proxy_url",
                )
            }
            thread = threading.Thread(
                target=self.download_manager, args=(urls,), daemon=True
            )
            thread.start()
        except Exception as error:
            # Do not leave the UI stuck in "downloading" state if session
            # preparation or worker startup fails before download_manager runs.
            self.download_outcome = "failed"
            self.record_problem(
                "Не удалось запустить поток сессии скачивания",
                "ERROR",
                "download_thread_start_failed",
                {"queued_url_count": len(urls)},
                error,
            )
            self.log(f"❌ Не удалось запустить скачивание: {error}", "ERROR")
            self.write_problem_session_summary(
                "failed", {"queued_url_count": len(urls), "worker_started": False}
            )
            self._finish_download_ui()
