"""Включение/выключение problem logs и настройка Python logging.

Автоматически выделено из прежнего модуля problem_diagnostics.py без изменения тел методов.
"""

from pathlib import Path
from logging.handlers import RotatingFileHandler
import json
import logging

from src.core.constants import DEBUG_LOG_BACKUP_COUNT, DEBUG_LOG_MAX_BYTES


class DiagnosticsLoggerMixin:
    def read_problem_logging_setting_default(self, default: bool = True) -> bool:
        """Раннее чтение флага логов проблем до полной загрузки настроек и UI."""
        try:
            if self.settings_file.exists():
                with open(self.settings_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                return bool(data.get("write_problem_logs", default))
        except Exception:
            return default
        return default


    def should_write_problem_logs(self) -> bool:
        """Единая проверка: можно ли сейчас писать в папку «Логи проблем»."""
        try:
            var = getattr(self, "write_problem_logs", None)
            if var is not None and hasattr(var, "get"):
                return bool(var.get())
        except Exception:
            pass
        try:
            settings = getattr(self, "settings", None)
            if isinstance(settings, dict) and "write_problem_logs" in settings:
                return bool(settings.get("write_problem_logs", True))
        except Exception:
            pass
        return bool(getattr(self, "problem_logging_enabled", True))


    def ensure_problem_log_dirs(self) -> None:
        """Создаёт папки логов проблем только когда они реально включены."""
        try:
            self.problem_log_dir.mkdir(parents=True, exist_ok=True)
            self.problem_sessions_dir.mkdir(parents=True, exist_ok=True)
            self.problem_session_dir.mkdir(parents=True, exist_ok=True)
            self.problem_attachments_dir.mkdir(parents=True, exist_ok=True)
            for jsonl_path in (
                self.problem_events_file,
                self.problem_log_file,
                self.problem_incidents_file,
            ):
                with open(jsonl_path, "a", encoding="utf-8"):
                    pass
        except Exception:
            pass


    def _is_problem_log_handler(self, handler: logging.Handler) -> bool:
        try:
            filename = Path(getattr(handler, "baseFilename", "")).resolve()
            problem_root = self.problem_log_dir.resolve()
            return str(filename).lower().startswith(str(problem_root).lower())
        except Exception:
            return False


    def _remove_problem_log_handlers(self) -> None:
        """Убирает файловые обработчики, которые пишут внутрь «Логи проблем»."""
        logger = getattr(self, "file_logger", logging.getLogger('VideoDownloader'))
        for handler in list(logger.handlers):
            if self._is_problem_log_handler(handler):
                logger.removeHandler(handler)
                try:
                    handler.close()
                except Exception:
                    pass


    def _add_file_logger_handler(self, log_path: Path, formatter: logging.Formatter) -> None:
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            target = str(log_path.resolve())
            for handler in self.file_logger.handlers:
                try:
                    if str(Path(getattr(handler, "baseFilename", "")).resolve()) == target:
                        return
                except Exception:
                    continue
            fh = RotatingFileHandler(
                log_path,
                maxBytes=DEBUG_LOG_MAX_BYTES,
                backupCount=DEBUG_LOG_BACKUP_COUNT,
                encoding="utf-8",
            )
            fh.setFormatter(formatter)
            self.file_logger.addHandler(fh)
        except Exception:
            pass


    def setup_logger(self) -> None:
        self.file_logger = logging.getLogger('VideoDownloader')
        self.file_logger.setLevel(logging.DEBUG)
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

        # Обычный debug-лог программы всегда остаётся в «Логи скачивания».
        for log_path in (
            self.log_dir / "app_debug.log",
            self.debug_session_log_file,
        ):
            self._add_file_logger_handler(log_path, formatter)

        # Логи в папку «Логи проблем» добавляются только при включённой галочке.
        if self.should_write_problem_logs():
            self.ensure_problem_log_dirs()
            for log_path in (self.problem_debug_session_log_file,):
                self._add_file_logger_handler(log_path, formatter)
        else:
            self._remove_problem_log_handlers()
