"""Прогресс, отмена, завершение и сохранение настроек перед запуском.

Автоматически выделено из прежнего модуля download_workflow.py без изменения тел методов.
"""

import socket
import time
import tkinter as tk

from src.core.constants import MAX_CONCURRENT_DL, MAX_SPLIT_HOURS


class DownloadProgressMixin:
    def save_current_settings(self) -> None:
        try:
            concurrent_downloads = self.concurrent_var.get()
        except tk.TclError:
            concurrent_downloads = self.settings.get("concurrent_downloads", 2)
        try:
            split_hours = self.split_hours.get()
        except tk.TclError:
            split_hours = self.settings.get("split_hours", MAX_SPLIT_HOURS)
        try:
            concurrent_downloads = int(concurrent_downloads)
        except (TypeError, ValueError):
            concurrent_downloads = 2
        try:
            split_hours = int(split_hours)
        except (TypeError, ValueError):
            split_hours = MAX_SPLIT_HOURS

        self.settings.update({
            "save_path": self.save_path.get(),
            "quality": "1080p",
            "audio_quality": self.audio_quality.get(),
            "concurrent_downloads": max(1, min(int(concurrent_downloads), MAX_CONCURRENT_DL)),
            "download_subtitles": self.download_subtitles.get(),
            "write_problem_logs": (
                self.write_problem_logs.get()
                if hasattr(self, "write_problem_logs") else self.problem_logging_enabled
            ),
            "proxy_enabled": (
                bool(self.proxy_enabled.get()) if hasattr(self, "proxy_enabled") else self.settings.get("proxy_enabled", False)
            ),
            "proxy_url": (
                str(self.proxy_url.get()).strip() if hasattr(self, "proxy_url") else self.settings.get("proxy_url", "")
            ),
            "merge_audio": self.merge_audio.get(),
            "split_hours": max(0, min(int(split_hours), MAX_SPLIT_HOURS)),
            "urls": []
        })
        self.save_settings()


    def check_internet_connection(self) -> bool:
        # Если пользователь включил proxy для yt-dlp, прямой TCP-тест до YouTube может
        # быть нерелевантен: сам yt-dlp будет ходить через proxy. Поэтому не блокируем
        # старт загрузки, а оставляем проверку реальной доступности на yt-dlp.
        if self.get_proxy_url():
            self.log(
                f"🌐 Прямую проверку YouTube пропускаю: yt-dlp будет использовать proxy {self.proxy_status_text()}",
                "INFO"
            )
            return True

        targets = [("www.youtube.com", 443), ("8.8.8.8", 53), ("1.1.1.1", 53)]
        last_error = None
        for host, port in targets:
            try:
                with socket.create_connection((host, port), timeout=5):
                    return True
            except OSError as e:
                last_error = e
        self.record_problem(
            "Проверка интернета не смогла подключиться ни к одному тестовому адресу",
            "WARNING", "check_internet_connection",
            {"targets": targets}, last_error
        )
        return False


    def update_main_progress(self, current: int) -> None:
        def _update():
            total = self.total_files.value()
            if total > 0:
                self.main_progress['value'] = current
                p = int((current / total) * 100)
                self.progress_percent.config(text=f"{p}%")
        self.root.after(0, _update)


    def update_progress(self) -> None:
        current = self.completed_files.increment(1)
        self.update_main_progress(current)


    def cancel_download(self) -> None:
        self.cancel_flag.set()
        stopped = self.terminate_active_processes()
        if stopped:
            self.log(f"🛑 Остановлено активных процессов: {stopped}", "WARNING")
        self.log("🛑 Отмена загрузки...", "WARNING")


    def finish_download(self) -> None:
        self.root.after(0, self._finish_download_ui)


    def _finish_download_ui(self) -> None:
        self.is_downloading = False
        self.download_btn.config(state=tk.NORMAL)
        self.cancel_btn.config(state=tk.DISABLED)
        try:
            self.main_progress_frame.grid_remove()
        except Exception:
            pass
        elapsed = (time.time() - self.download_start_time
                   if self.download_start_time else 0)
        outcome = getattr(self, "download_outcome", "failed")
        message, level = {
            "completed": ("✅ Завершено", "SUCCESS"),
            "cancelled": ("🛑 Загрузка отменена", "WARNING"),
            "failed": ("❌ Завершено с ошибками; подробности в логе", "ERROR"),
        }.get(outcome, ("⚠️ Сессия завершена без подтверждённого результата", "WARNING"))
        self.log(f"{message}. Время: {self.format_duration(elapsed)}", level)
