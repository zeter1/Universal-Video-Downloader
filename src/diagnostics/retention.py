"""Фасад retention: координирует узкие retention mixins.

Большие детали вынесены по ответственности для экономии контекста Codex.
"""

from src.core.constants import DOWNLOAD_HISTORY_RETENTION_DAYS, PROBLEM_LOG_COMPRESSION_AFTER_DAYS, PROBLEM_LOG_RETENTION_DAYS, PROBLEM_LOG_SESSION_ARCHIVE_AFTER_DAYS
from src.diagnostics.retention_base import RetentionBaseMixin
from src.diagnostics.retention_archive import RetentionArchiveMixin
from src.diagnostics.retention_storage import RetentionStorageMixin


class DiagnosticsRetentionMixin(
    RetentionBaseMixin,
    RetentionArchiveMixin,
    RetentionStorageMixin,
):
    def cleanup_old_logs(self) -> int:
        """Чистит только распознанные файлы приложения с отдельными сроками хранения."""
        history_cutoff = self.retention_cutoff(DOWNLOAD_HISTORY_RETENTION_DAYS)
        problem_cutoff = self.retention_cutoff(PROBLEM_LOG_RETENTION_DAYS)
        cleaned = 0

        # Старые общие файлы не содержат даты на каждую запись, поэтому их нельзя
        # честно отфильтровать по сроку хранения. Новые записи хранятся в
        # сессионных файлах с датой, а эти файлы удаляются как устаревший формат.
        cleaned += self._delete_legacy_flat_logs([
            self.history_file,
            self.log_file,
            self.video_ids_file,
            self.manual_processing_dir / "failed_all.txt",
        ])

        cleaned += self._delete_old_files(
            self.history_dir, ["downloaded_*.txt", "downloaded_history.txt"], history_cutoff
        )
        cleaned += self._delete_old_files(
            self.downloaded_sessions_dir, ["downloaded_log_*.txt"], history_cutoff
        )
        cleaned += self._delete_old_files(
            self.manual_processing_dir,
            ["failed_*.txt", "failed_all.txt", "not_converted_*.txt", "problematic_*.txt"],
            history_cutoff
        )

        # Удаляем старые плоские файлы из корня «Логи проблем» от прошлых версий.
        # Новая диагностика хранится только в «Логи проблем/Сессии/<запуск>».
        cleaned += self._delete_legacy_flat_logs([
            self.problem_log_dir / "ai_problem_log.jsonl",
            self.problem_log_dir / "latest_problem_snapshot.json",
            self.problem_log_dir / "latest_problem_report.txt",
            self.problem_log_dir / "problem_report.txt",
            self.problem_log_dir / "app_debug.log",
        ])
        cleaned += self._trim_jsonl_log_by_timestamp(self.problem_log_file, problem_cutoff)
        cleaned += self._trim_jsonl_log_by_timestamp(
            self.problem_events_file, problem_cutoff
        )
        cleaned += self._trim_report_log_by_timestamp(self.problem_report_file, problem_cutoff)
        cleaned += self._trim_jsonl_log_by_timestamp(
            self.problem_emergency_log_file, problem_cutoff
        )
        cleaned += self._trim_debug_log_by_timestamp(
            self.log_dir / "app_debug.log", history_cutoff
        )
        if hasattr(self, "problem_sessions_dir"):
            cleaned += self._delete_old_dirs(self.problem_sessions_dir, problem_cutoff)
            cleaned += self._compress_old_problem_attachments(
                self.retention_cutoff(PROBLEM_LOG_COMPRESSION_AFTER_DAYS)
            )
            cleaned += self._archive_old_problem_sessions(
                self.retention_cutoff(PROBLEM_LOG_SESSION_ARCHIVE_AFTER_DAYS)
            )
            cleaned += self._enforce_problem_log_size_limit()
        cleaned += self._trim_health_history()
        if hasattr(self, "debug_sessions_dir"):
            cleaned += self._delete_old_files(
                self.debug_sessions_dir, ["app_debug_*.log*"], history_cutoff
            )

        return cleaned
