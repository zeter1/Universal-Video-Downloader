"""Настройки приложения и служебные файлы текущей сессии.

Автоматически выделено из прежнего модуля download_runtime.py без изменения тел методов.
"""

from pathlib import Path
from typing import Tuple
from datetime import datetime
import json

from src.core.constants import MAX_CONCURRENT_DL, MAX_SPLIT_HOURS


class SettingsMixin:
    def safe_int_setting(self, key: str, default: int,
                         min_value: int, max_value: int) -> int:
        raw_value = self.settings.get(key, default)
        try:
            value = int(raw_value)
        except (TypeError, ValueError) as e:
            self.record_problem(
                f"Некорректное числовое значение настройки {key}: {raw_value!r}",
                "WARNING", "load_settings",
                {"key": key, "raw_value": raw_value, "default": default},
                e
            )
            value = default
        return max(min_value, min(value, max_value))


    def make_safe_session_timestamp(self, session_ts: str) -> str:
        return session_ts.replace(':', '-').replace(' ', '_')


    def make_unique_session_file(self, directory: Path, prefix: str,
                                 session_ts: str) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        safe_ts = self.make_safe_session_timestamp(session_ts)
        candidate = directory / f"{prefix}_{safe_ts}.txt"
        counter = 2
        while candidate.exists():
            candidate = directory / f"{prefix}_{safe_ts}_{counter}.txt"
            counter += 1
        return candidate


    def ensure_download_session_files(self) -> Tuple[Path, Path]:
        if not self.current_session_start:
            self.current_session_start = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        if self.current_history_session_file is None:
            self.current_history_session_file = self.make_unique_session_file(
                self.history_dir, "downloaded", self.current_session_start
            )
            with open(self.current_history_session_file, 'w', encoding='utf-8') as f:
                f.write("Скачанные видео\n")
                f.write(f"Сессия: {self.current_session_start}\n")
                f.write("=" * 80 + "\n\n")

        if self.current_download_log_session_file is None:
            self.current_download_log_session_file = self.make_unique_session_file(
                self.downloaded_sessions_dir, "downloaded_log",
                self.current_session_start
            )
            with open(self.current_download_log_session_file, 'w', encoding='utf-8') as f:
                f.write(f"# Session: {self.current_session_start}\n")
                f.write("# Columns: canonical_url_hash\tvideo_id\tcanonical_url\toriginal_url\n")

        return self.current_history_session_file, self.current_download_log_session_file


    def load_settings(self) -> None:
        default_settings = {
            "save_path": str(Path.home() / "Downloads"),
            "quality": "1080p",
            "audio_quality": "320k",
            "concurrent_downloads": 2,
            "download_subtitles": False,
            "write_problem_logs": True,
            "proxy_enabled": False,
            "proxy_url": "",
            "merge_audio": False,
            "split_hours": MAX_SPLIT_HOURS,
            "urls": []
        }
        try:
            if self.settings_file.exists():
                with open(self.settings_file, 'r', encoding='utf-8') as f:
                    self.settings = json.load(f)
                for key, value in default_settings.items():
                    if key not in self.settings:
                        self.settings[key] = value
            else:
                self.settings = default_settings
        except Exception as e:
            self.file_logger.error(f"load_settings: {e}")
            self.settings = default_settings

        # FIX #5: корректный clamp + защита от битого JSON/ручного ввода.
        self.settings["split_hours"] = self.safe_int_setting(
            "split_hours", MAX_SPLIT_HOURS, 0, MAX_SPLIT_HOURS
        )
        self.settings["concurrent_downloads"] = self.safe_int_setting(
            "concurrent_downloads", 2, 1, MAX_CONCURRENT_DL
        )
        # Качество видео больше не выбирается пользователем: всегда авто до 1080p.
        self.settings["quality"] = "1080p"
        self.settings["proxy_enabled"] = bool(self.settings.get("proxy_enabled", False))
        self.settings["proxy_url"] = str(self.settings.get("proxy_url", "") or "").strip()
        self.settings["write_problem_logs"] = bool(
            self.settings.get("write_problem_logs", True)
        )
        self.problem_logging_enabled = self.settings["write_problem_logs"]
        # После полной загрузки настроек синхронизируем файловые обработчики логгера.
        if hasattr(self, "file_logger"):
            self.setup_logger()


    def save_settings(self) -> None:
        try:
            self.settings_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.settings_file, 'w', encoding='utf-8') as f:
                json.dump(self.settings, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.log(f"Ошибка сохранения настроек: {str(e)}", "ERROR")
