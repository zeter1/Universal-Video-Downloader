"""UI-обработчики настроек, путей и retention.

Автоматически выделено из прежнего модуля ui_components.py без изменения тел методов.
"""

from tkinter import filedialog
from tkinter import messagebox
import tkinter as tk

from src.core.constants import DOWNLOAD_HISTORY_RETENTION_DAYS, MAX_SPLIT_HOURS, PROBLEM_LOG_RETENTION_DAYS


class SettingsControlsMixin:
    def on_problem_logging_change(self) -> None:
        enabled = bool(self.write_problem_logs.get())
        self.problem_logging_enabled = enabled
        if hasattr(self, "settings"):
            self.settings["write_problem_logs"] = enabled

        if enabled:
            self.problem_runtime_terminal = False
            self.ensure_problem_log_dirs()
            self.setup_logger()
            self.initialize_problem_diagnostics()
            self.log(
                "✅ Логи проблем включены. При ошибках будет создана подробная диагностика для ИИ.",
                "SUCCESS"
            )
            self.record_problem(
                "Пользователь включил запись логов проблем",
                "INFO", "problem_logging_toggle", {"enabled": True}
            )
        else:
            self._write_active_run_state(
                "disabled",
                "problem_logging_disabled",
                {"disabled_by_user": True},
                terminal=True,
                force=True,
            )
            self._remove_problem_log_handlers()
            self.log(
                "ℹ️ Логи проблем отключены. Окно программы продолжит показывать ход загрузки, но папка «Логи проблем» пополняться не будет.",
                "INFO"
            )

        self.save_current_settings()


    def on_merge_change(self) -> None:
        if self.merge_audio.get():
            self.split_frame.grid()
        else:
            self.split_frame.grid_remove()


    def check_split_warning(self) -> None:
        # FIX #17: при нечисловом вводе сбрасываем в MAX вместо молчаливого ignore
        try:
            val = self.split_hours.get()
            if val > MAX_SPLIT_HOURS:
                self.split_hours.set(MAX_SPLIT_HOURS)
                messagebox.showwarning(
                    "Ограничение", f"Максимум {MAX_SPLIT_HOURS} часов на часть!"
                )
        except tk.TclError:
            self.split_hours.set(MAX_SPLIT_HOURS)


    def browse_path(self) -> None:
        path = filedialog.askdirectory()
        if path:
            self.save_path.set(path)


    def log_retention_status(self) -> None:
        self.log(
            f"🧹 История скачиваний хранится {DOWNLOAD_HISTORY_RETENTION_DAYS} дней, "
            f"логи проблем — {PROBLEM_LOG_RETENTION_DAYS} дней",
            "INFO"
        )
        if self.should_write_problem_logs():
            self.log(
                "📉 Логи проблем включены: компактная история, итог сессии "
                "и индекс последней нерешённой проблемы",
                "INFO"
            )
            self.log(
                f"📄 Логи этого запуска: Логи проблем/Сессии/{self.app_session_timestamp}",
                "PROBLEM_FOLDER_LINK"
            )
        else:
            self.log(
                "📄 Логи проблем отключены галочкой «Писать логи проблем». Папка «Логи проблем» пополняться не будет.",
                "INFO"
            )
        self.log(
            f"📄 Debug этого запуска: Логи скачивания/Сессии запусков/{self.debug_session_log_file.name}",
            "INFO"
        )
        if getattr(self, "retention_cleanup_count", 0):
            self.log(
                f"🧹 Старые логи удалены или сжаты: {self.retention_cleanup_count}",
                "INFO"
            )
        errors = getattr(self, "retention_cleanup_errors", [])
        if errors:
            self.log(
                f"⚠️ Не удалось очистить часть старых логов: {len(errors)}. "
                "Подробности записаны в app_debug.log",
                "WARNING"
            )
            for error in errors[:10]:
                self.file_logger.warning(f"log retention cleanup: {error}")
