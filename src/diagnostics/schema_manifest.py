"""Схема problem logs, README и manifest исходного bundle.

Автоматически выделено из прежнего модуля problem_diagnostics.py без изменения тел методов.
"""

from typing import Dict
from typing import Optional
from pathlib import Path
from datetime import datetime
import hashlib
import json
import sys

from src.core.constants import APP_VERSION, PROBLEM_LOG_COMPRESSION_AFTER_DAYS, PROBLEM_LOG_FORMAT_VERSION, PROBLEM_LOG_MAX_DETAILS_PER_SIGNATURE, PROBLEM_LOG_MAX_TOTAL_BYTES, PROBLEM_LOG_RETENTION_DAYS, PROBLEM_LOG_SCHEMA_VERSION, PROBLEM_LOG_SESSION_ARCHIVE_AFTER_DAYS, SOURCE_CODE_FILES


class DiagnosticsSchemaMixin:
    def _problem_readme_text(self) -> str:
        return f"""# Логи проблем — порядок чтения для Codex

Формат диагностики: {PROBLEM_LOG_FORMAT_VERSION}. Кодировка всех текстовых файлов: UTF-8.

## Быстрый порядок чтения

1. Откройте `latest_run.json`.
2. Перейдите по `session_summary` и прочитайте `session_summary.json`.
3. Проверьте `emergency.count`; при значении больше нуля прочитайте
   `emergency_problem_log.jsonl` — это сбои самой диагностической подсистемы.
4. Если `unresolved_count` больше нуля, откройте `latest_unresolved_problem.json`.
5. Для хронологии читайте `events.jsonl`.
6. Для жизненного цикла ошибок читайте `incidents.jsonl`.
7. `ai_problem_log.jsonl` — совместимое зеркало `events.jsonl` для старых инструментов.
8. Полный снимок выбранного WARNING/ERROR/CRITICAL находится по
   `attachment.content_path`; `attachment.path` содержит небольшой указатель события.
9. `validation_report.json` проверяет JSONL, связи событий и доступные вложения.
10. `manifest.json` содержит версии программы, Python, yt-dlp, FFmpeg и параметры среды.
11. `health_history.jsonl` хранит по одной итоговой записи на запуск.

## Важные поля

- `event_id`, `parent_event_id`, `task_id`, `attempt_id`, `command_id` связывают действия.
- `error_signature` — стабильная сигнатура без времени, PID, путей и прогресса.
- `problem_fingerprint` — точный отпечаток конкретного события для совместимости.
- `incident_id` и `status` показывают переходы `detected -> retrying -> recovered`.
- `strategy_metrics`, `strategy_findings` и `recommended_next_checks` показывают
  неэффективные стратегии и подтверждённо успешные fallback даже при итоговом успехе.
- В `yt_dlp_download_attempt` сетевые счётчики уже включают текущую попытку.
- Для ошибок скачивания сначала смотрите `context.failure_analysis`: там stage, kind,
  likely_root_cause, format_profile и рекомендуемый следующий шаг.
- `context.yt_dlp_diagnostic_landmarks` сохраняет важные строки отдельно от progress:
  `DOWNLOAD_PLAN`, выбранные format_id/protocol, downloader, FFmpeg merge и mux/timestamp ошибки.
- `stream_capture_mode=yt_dlp_stderr_merged_into_stdout` означает, что пустой `stderr` штатный;
  подробности yt-dlp/FFmpeg нужно искать в stdout и diagnostic landmarks.
- `target_files_after_attempt` показывает, какие video/audio/.part части остались после сбоя.
- `attachment.status=suppressed_repetition` означает, что полный повтор не записан из-за лимита.
- Одинаковые диагностические блоки хранятся один раз по SHA-256.

## Ограничения хранения

- Полные детали: максимум {PROBLEM_LOG_MAX_DETAILS_PER_SIGNATURE} событий одной сигнатуры.
- Полные снимки старше {PROBLEM_LOG_COMPRESSION_AFTER_DAYS} дней сжимаются в `.json.gz`;
  исходный `.json` остаётся маленьким указателем на архив.
- Сессии старше {PROBLEM_LOG_SESSION_ARCHIVE_AFTER_DAYS} дней собираются в проверенный
  `session_archive.zip`; сводка и `archive_manifest.json` остаются рядом.
- История проблем: {PROBLEM_LOG_RETENTION_DAYS} дней.
- Общий предел распознанных сессий: {PROBLEM_LOG_MAX_TOTAL_BYTES // (1024 * 1024)} МБ.
- Очистка не затрагивает посторонние файлы и каталоги пользователя.

Секреты, cookies, пароли, токены и данные авторизации перед записью маскируются.

Отдельная проверка: `python problem_log_validator.py`.
Постоянные сценарии схем 3/4: `python problem_log_validator.py --examples`.
"""


    def _problem_log_schema(self) -> Dict:
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "skachat_video_online.problem_event.schema.v4",
            "title": "Skachat_video_online Codex problem event",
            "x-supported-source-schema-versions": [3, 4],
            "x-legacy-normalizer": "problem_log_validator.normalize_event_record",
            "type": "object",
            "required": [
                "schema_version", "event_id", "timestamp", "level",
                "operation", "message", "error_signature",
                "problem_log_session_id",
            ],
            "properties": {
                "schema_version": {"const": PROBLEM_LOG_SCHEMA_VERSION},
                "event_id": {"type": "string"},
                "parent_event_id": {"type": ["string", "null"]},
                "task_id": {"type": ["string", "null"]},
                "attempt_id": {"type": ["string", "null"]},
                "command_id": {"type": ["string", "null"]},
                "incident_id": {"type": ["string", "null"]},
                "timestamp": {"type": "string"},
                "level": {
                    "enum": ["INFO", "WARNING", "ERROR", "CRITICAL"]
                },
                "operation": {"type": "string"},
                "message": {"type": "string"},
                "error_signature": {"type": "string"},
                "error_code": {"type": "string"},
                "problem_fingerprint": {"type": "string"},
                "resolved": {"type": "boolean"},
                "attachment": {"type": ["object", "null"]},
            },
            "additionalProperties": True,
        }


    def _write_problem_format_files(self) -> None:
        self._atomic_write_text_file(
            self.problem_readme_file, self._problem_readme_text()
        )
        self._atomic_write_json_file(
            self.problem_schema_file, self._problem_log_schema()
        )


    def _file_sha256(self, path: Path) -> Optional[str]:
        try:
            digest = hashlib.sha256()
            with open(path, "rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            return digest.hexdigest()
        except OSError:
            return None


    def _source_sha256(self) -> Optional[str]:
        """Хэширует весь модульный source bundle, а не только один mixin-файл."""
        try:
            base_dir = Path(getattr(self, "app_dir", Path(__file__).resolve().parent))
            digest = hashlib.sha256()
            found = False
            for filename in SOURCE_CODE_FILES:
                path = base_dir / filename
                if not path.is_file():
                    continue
                found = True
                digest.update(filename.encode("utf-8"))
                digest.update(b"\0")
                with open(path, "rb") as source:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        digest.update(chunk)
                digest.update(b"\0")
            return digest.hexdigest() if found else None
        except OSError:
            return None


    def _entry_source_file(self) -> str:
        base_dir = Path(getattr(self, "app_dir", Path(__file__).resolve().parent))
        return str((base_dir / "video_downloader.py").resolve())


    def _settings_hash_for_manifest(self) -> Optional[str]:
        try:
            safe_settings = self._json_safe_value(getattr(self, "settings", {}))
            payload = json.dumps(
                safe_settings, ensure_ascii=False, sort_keys=True, default=str
            )
            return hashlib.sha256(payload.encode("utf-8")).hexdigest()
        except Exception:
            return None


    def _write_problem_manifest(self) -> None:
        if not self.should_write_problem_logs():
            return
        manifest = {
            "schema_version": PROBLEM_LOG_SCHEMA_VERSION,
            "format_version": PROBLEM_LOG_FORMAT_VERSION,
            "supported_source_schema_versions": [3, 4],
            "app_name": "Skachat_video_online",
            "app_version": APP_VERSION,
            "problem_log_session_id": self.problem_log_session_id,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "source_sha256": self._source_sha256(),
            "source_bundle_files": list(SOURCE_CODE_FILES),
            "settings_sha256": self._settings_hash_for_manifest(),
            "run_mode": "frozen_exe" if getattr(sys, "frozen", False) else "python_source",
            "environment": self._environment_snapshot_for_problem_log(),
            "diagnostic_files": {
                "event_log": str(self.problem_events_file),
                "legacy_event_log": str(self.problem_log_file),
                "incidents": str(self.problem_incidents_file),
                "session_summary": str(self.problem_session_summary_file),
                "session_summary_markdown": str(self.problem_session_summary_md_file),
                "latest_problem_snapshot": str(self.problem_latest_json_file),
                "active_run_state": str(self.problem_session_active_state_file),
                "attachments_dir": str(self.problem_attachments_dir),
                "validation_report": str(self.problem_validation_report_file),
                "schema": str(self.problem_schema_file),
                "readme": str(self.problem_readme_file),
            },
            "retention": {
                "days": PROBLEM_LOG_RETENTION_DAYS,
                "attachment_compression_after_days": (
                    PROBLEM_LOG_COMPRESSION_AFTER_DAYS
                ),
                "session_archive_after_days": (
                    PROBLEM_LOG_SESSION_ARCHIVE_AFTER_DAYS
                ),
                "max_total_bytes": PROBLEM_LOG_MAX_TOTAL_BYTES,
                "max_details_per_signature": PROBLEM_LOG_MAX_DETAILS_PER_SIGNATURE,
            },
        }
        self._atomic_write_json_file(
            self.problem_manifest_file, self._json_safe_value(manifest)
        )
