"""Главная запись problem events и компактные текстовые отчёты для AI.

Автоматически выделено из прежнего модуля problem_diagnostics.py без изменения тел методов.
"""

from typing import Dict
from typing import List
from typing import Optional
from pathlib import Path
from datetime import datetime
import json
import logging
import time
import traceback

from src.core.constants import PROBLEM_LOG_COMPACT_INFO, PROBLEM_LOG_INFO_CONTEXT_TEXT_CHARS, PROBLEM_LOG_INFO_FILE_SNAPSHOT_LIMIT, PROBLEM_LOG_INFO_LIST_LIMIT, PROBLEM_LOG_INFO_STDOUT_TAIL_CHARS, PROBLEM_LOG_SCHEMA_VERSION


class DiagnosticsReportingMixin:
    def _write_problem_report_block(self, f, entry: Dict) -> None:
        def dump_section(title: str, value) -> None:
            f.write(f"{title}:\n")
            f.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")

        f.write("\n" + "=" * 100 + "\n")
        f.write(
            f"#{entry['sequence']} {entry['timestamp']} [{entry['level']}] "
            f"{entry['operation']}\n"
        )
        f.write(f"Сообщение: {entry['message']}\n")
        f.write(f"Фингерпринт: {entry['problem_fingerprint']}\n")
        f.write(f"Стабильная сигнатура: {entry.get('error_signature')}\n")
        f.write(f"ID события: {entry.get('event_id')}\n")
        f.write(f"ID инцидента: {entry.get('incident_id')}\n")
        if entry.get("attachment"):
            dump_section("Полный файл события", entry.get("attachment"))
        f.write(f"Диагностическая сессия: {entry['problem_log_session_id']}\n")
        f.write(f"Последний полный JSON: {entry['paths']['problem_latest_json_file']['path']}\n")
        dump_section("Где вызван record_problem", entry["source_location"])
        dump_section("Подсказка для Codex", entry["ai_debug_hint"])
        dump_section("Контекст операции", entry["context"])
        if entry.get("url_diagnostics"):
            dump_section("Диагностика URL", entry["url_diagnostics"])
        if entry.get("command_details"):
            dump_section("Команда", entry["command_details"])
        if entry.get("stderr", {}).get("tail"):
            f.write("stderr tail:\n")
            f.write(entry["stderr"]["tail"] + "\n")
        if entry.get("stdout", {}).get("tail"):
            f.write("stdout tail:\n")
            f.write(entry["stdout"]["tail"] + "\n")
        if entry.get("exception"):
            dump_section("Исключение", entry["exception"])
            f.write("traceback:\n")
            f.write(entry.get("traceback", "") + "\n")
        dump_section("Состояние приложения", entry["app_state"])
        dump_section("Настройки и целевые папки", entry["settings"])
        dump_section("Состояние сессии", entry["session_state"])
        dump_section("Файлы и папки", entry["paths"])
        dump_section("Окружение и зависимости", entry["environment"])


    def _compact_problem_context(self, value, depth: int = 0):
        """Уменьшает INFO-диагностику, чтобы логи были полезными, но не гигантскими."""
        if depth > 4:
            return str(value)[:500]
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            value = self._sanitize_text_for_log(value)
            if len(value) <= PROBLEM_LOG_INFO_CONTEXT_TEXT_CHARS:
                return value
            return {
                "truncated": True,
                "char_count": len(value),
                "tail": value[-PROBLEM_LOG_INFO_CONTEXT_TEXT_CHARS:],
            }
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, datetime):
            return value.isoformat(timespec="seconds")
        if isinstance(value, dict):
            compact = {}
            for key, item in value.items():
                key_s = str(key)
                item = self._redact_for_log(item, key_s)
                if key_s in ("captured_output_tail", "stdout_tail", "stderr_tail"):
                    text = str(item or "")
                    lines = text.splitlines()
                    tail_lines = lines[-25:]
                    tail_text = "\n".join(tail_lines)
                    compact[key_s] = {
                        "line_count": len(lines),
                        "tail_line_count": len(tail_lines),
                        "tail": tail_text[-PROBLEM_LOG_INFO_CONTEXT_TEXT_CHARS:],
                    }
                elif key_s in ("target_files_snapshot", "target_files_before_attempt"):
                    items = item if isinstance(item, list) else []
                    compact[key_s] = {
                        "total_items": len(items),
                        "items": [self._compact_problem_context(x, depth + 1)
                                  for x in items[:PROBLEM_LOG_INFO_FILE_SNAPSHOT_LIMIT]],
                        "omitted_items": max(0, len(items) - PROBLEM_LOG_INFO_FILE_SNAPSHOT_LIMIT),
                    }
                elif key_s == "urls" and isinstance(item, list):
                    compact[key_s] = {
                        "total_items": len(item),
                        "items": [self._compact_problem_context(x, depth + 1)
                                  for x in item[:PROBLEM_LOG_INFO_LIST_LIMIT]],
                        "omitted_items": max(0, len(item) - PROBLEM_LOG_INFO_LIST_LIMIT),
                    }
                else:
                    compact[key_s] = self._compact_problem_context(item, depth + 1)
            return compact
        if isinstance(value, (list, tuple, set)):
            items = list(value)
            result = [self._compact_problem_context(x, depth + 1)
                      for x in items[:PROBLEM_LOG_INFO_LIST_LIMIT]]
            if len(items) > PROBLEM_LOG_INFO_LIST_LIMIT:
                result.append({"omitted_items": len(items) - PROBLEM_LOG_INFO_LIST_LIMIT})
            return result
        if isinstance(value, BaseException):
            return {
                "type": type(value).__name__,
                "message": self._sanitize_text_for_log(str(value)),
            }
        return self._sanitize_text_for_log(repr(value))[:1000]


    def _compact_app_state_for_problem_log(self) -> Dict:
        return {
            "is_downloading": getattr(self, "is_downloading", None),
            "cancel_requested": (
                self.cancel_flag.is_set() if hasattr(self, "cancel_flag") else None
            ),
            "active_process_count": len(getattr(self, "active_processes", {})),
            "total_files": self._safe_counter_value("total_files"),
            "completed_files": self._safe_counter_value("completed_files"),
            "max_concurrent": getattr(self, "max_concurrent", None),
        }


    def record_problem(self, message: str, level: str = "ERROR",
                       operation: str = "unknown",
                       context: Optional[Dict] = None,
                       exception: Optional[BaseException] = None,
                       command: Optional[List[str]] = None,
                       stdout: Optional[str] = None,
                       stderr: Optional[str] = None,
                       resolved: bool = False) -> None:
        """Пишет структурированный лог, который удобно отдавать нейросети для диагностики."""
        if not self.should_write_problem_logs():
            return
        self.ensure_problem_log_dirs()
        try:
            # INFO-события нужны для хронологии, но не должны каждый раз
            # дублировать окружение, PATH, список файлов и полный stdout.
            # Полные диагностические снимки остаются для WARNING/ERROR/CRITICAL.
            if PROBLEM_LOG_COMPACT_INFO and str(level).upper() == "INFO":
                context_safe = self._compact_problem_context(context or {})
                stdout_tail = self._tail_text(
                    self._sanitize_text_for_log(stdout or ""),
                    PROBLEM_LOG_INFO_STDOUT_TAIL_CHARS,
                )
                stderr_tail = self._tail_text(
                    self._sanitize_text_for_log(stderr or ""),
                    PROBLEM_LOG_INFO_STDOUT_TAIL_CHARS,
                )
                with self.problem_log_lock:
                    self.problem_log_sequence += 1
                    sequence = self.problem_log_sequence
                safe_message = self._sanitize_text_for_log(message)
                event_id = self._event_id(sequence)
                correlation = self._correlation_fields(context_safe, command)
                fingerprint = self._problem_fingerprint(
                    operation, safe_message, context_safe, None,
                    stderr_tail or stdout_tail
                )
                error_code = self._canonical_problem_error_code(
                    stderr_tail or stdout_tail
                )
                error_signature = self._stable_error_signature(
                    operation, safe_message, context_safe, None,
                    stderr_tail or stdout_tail,
                    error_code=error_code,
                )

                entry = {
                    "schema_version": PROBLEM_LOG_SCHEMA_VERSION,
                    "compact": True,
                    "sequence": sequence,
                    "event_id": event_id,
                    "problem_log_session_id": getattr(self, "problem_log_session_id", None),
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    "timestamp_unix": time.time(),
                    "level": level,
                    "operation": operation,
                    "message": safe_message,
                    "problem_fingerprint": fingerprint,
                    "error_signature": error_signature,
                    "error_code": error_code,
                    "context": context_safe,
                    "url_diagnostics": self._url_diagnostics_for_problem_log(context_safe),
                    "command": self._command_to_string(command),
                    "command_details": self._command_snapshot(command),
                    "stdout_tail": stdout_tail,
                    "stderr_tail": stderr_tail,
                    "app_state_compact": self._compact_app_state_for_problem_log(),
                    "hint_for_ai": (
                        "Компактная INFO-запись. Полные снимки с environment/paths/settings "
                        "пишутся только для WARNING/ERROR/CRITICAL или latest_problem_snapshot.json."
                    ),
                }
                entry.update(correlation)
                entry["incident_key"] = self._problem_incident_key(entry)
                entry["incident_id"] = self._incident_id(entry["incident_key"])
                entry["resolved"] = bool(resolved)
                validation_errors = self._validate_problem_event_shape(entry)
                entry["schema_validation"] = {
                    "status": "failed" if validation_errors else "passed",
                    "errors": validation_errors,
                }
                with self.problem_log_lock:
                    self._collect_problem_metrics_locked(entry)
                    for log_path in dict.fromkeys([
                        self.problem_events_file,
                        self.problem_log_file,
                        self.problem_session_log_file,
                    ]):
                        self._append_jsonl_file(log_path, entry)
                    if resolved:
                        self._update_problem_resolution_state_locked(
                            entry, resolved=True
                        )
                self._write_active_run_state(
                    "running", operation, {"event_id": event_id}
                )
                if resolved:
                    self.write_problem_session_summary(
                        "running",
                        {
                            "live_refresh_reason": "resolved_event",
                            "last_event_id": event_id,
                            "last_operation": operation,
                            "last_level": str(level).upper(),
                        },
                        live_refresh=True,
                    )
                return

            context_safe = self._json_safe_value(context or {})
            stdout_snapshot = self._stream_snapshot(stdout)
            stderr_snapshot = self._stream_snapshot(stderr)
            traceback_text = (
                self._sanitize_text_for_log(''.join(traceback.format_exception(
                    type(exception), exception, exception.__traceback__
                )))
                if exception else ""
            )
            exception_snapshot = (
                {
                    "type": type(exception).__name__,
                    "message": self._sanitize_text_for_log(str(exception)),
                    "repr": self._sanitize_text_for_log(repr(exception)),
                }
                if exception else None
            )
            with self.problem_log_lock:
                self.problem_log_sequence += 1
                sequence = self.problem_log_sequence

            exception_type = type(exception).__name__ if exception else None
            safe_message = self._sanitize_text_for_log(message)
            ai_debug_hint = self._ai_debug_hint_for_problem_log(
                operation, safe_message, stderr_snapshot["tail"], exception,
                stdout_snapshot["tail"], context_safe
            )
            event_id = self._event_id(sequence)
            correlation = self._correlation_fields(context_safe, command)
            fingerprint = self._problem_fingerprint(
                operation, safe_message, context_safe, exception_type,
                stderr_snapshot["tail"] or stdout_snapshot["tail"]
            )
            error_signature = self._stable_error_signature(
                operation, safe_message, context_safe, exception_type,
                ai_debug_hint.get("terminal_error")
                or stderr_snapshot["tail"] or stdout_snapshot["tail"],
                ai_debug_hint.get("category"),
                ai_debug_hint.get("error_code"),
            )
            entry = {
                "schema_version": PROBLEM_LOG_SCHEMA_VERSION,
                "sequence": sequence,
                "event_id": event_id,
                "problem_log_session_id": getattr(self, "problem_log_session_id", None),
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "timestamp_unix": time.time(),
                "level": level,
                "operation": operation,
                "message": safe_message,
                "problem_fingerprint": fingerprint,
                "error_signature": error_signature,
                "error_code": ai_debug_hint.get("error_code"),
                "context": context_safe,
                "url_diagnostics": self._url_diagnostics_for_problem_log(context_safe),
                "exception_type": exception_type,
                "exception": exception_snapshot,
                "traceback": traceback_text,
                "command": self._command_to_string(command),
                "command_details": self._command_snapshot(command),
                "stdout_tail": stdout_snapshot["tail"],
                "stderr_tail": stderr_snapshot["tail"],
                "stdout": stdout_snapshot,
                "stderr": stderr_snapshot,
                "app_state": self._app_state_for_problem_log(),
                "settings": self._settings_snapshot_for_problem_log(),
                "session_state": self._session_state_for_problem_log(),
                "paths": self._paths_snapshot_for_problem_log(),
                "environment": self._environment_snapshot_for_problem_log(),
                "source_location": self._source_location_for_problem_log(),
                "source_file": self._entry_source_file(),
                "ai_debug_hint": ai_debug_hint,
                "hint_for_ai": (
                    "Это подробная диагностическая запись приложения для Codex. "
                    "Начните с error_signature, operation, ai_debug_hint.category, source_location.caller, "
                    "context.failure_analysis, context.yt_dlp_diagnostic_landmarks, command_details.args, "
                    "stdout.tail/stderr.tail, app_state, settings и paths. "
                    "Полный снимок этого события хранится в attachment.content_path; "
                    "attachment.path содержит только указатель."
                ),
            }
            entry.update(correlation)
            entry["incident_key"] = self._problem_incident_key(entry)
            entry["incident_id"] = self._incident_id(entry["incident_key"])
            entry["resolved"] = bool(resolved)
            validation_errors = self._validate_problem_event_shape(entry)
            entry["schema_validation"] = {
                "status": "failed" if validation_errors else "passed",
                "errors": validation_errors,
            }
            with self.problem_log_lock:
                self._collect_problem_metrics_locked(entry)
                entry["attachment"] = self._write_problem_attachment_locked(entry)
                event_entry = {
                    "schema_version": entry["schema_version"],
                    "compact": True,
                    "sequence": entry["sequence"],
                    "event_id": entry["event_id"],
                    "parent_event_id": entry.get("parent_event_id"),
                    "task_id": entry.get("task_id"),
                    "attempt_id": entry.get("attempt_id"),
                    "command_id": entry.get("command_id"),
                    "problem_log_session_id": entry["problem_log_session_id"],
                    "timestamp": entry["timestamp"],
                    "timestamp_unix": entry["timestamp_unix"],
                    "level": entry["level"],
                    "operation": entry["operation"],
                    "message": entry["message"],
                    "problem_fingerprint": entry["problem_fingerprint"],
                    "error_signature": entry["error_signature"],
                    "error_code": entry.get("error_code"),
                    "category": entry.get("ai_debug_hint", {}).get("category"),
                    "incident_id": entry["incident_id"],
                    "incident_key": entry["incident_key"],
                    "resolved": entry["resolved"],
                    "context": self._compact_problem_context(entry.get("context", {})),
                    "url_diagnostics": entry.get("url_diagnostics"),
                    "command": entry.get("command"),
                    "stdout_tail": self._tail_text(
                        entry.get("stdout_tail"), PROBLEM_LOG_INFO_STDOUT_TAIL_CHARS
                    ),
                    "stderr_tail": self._tail_text(
                        entry.get("stderr_tail"), PROBLEM_LOG_INFO_STDOUT_TAIL_CHARS
                    ),
                    "attachment": entry.get("attachment"),
                    "latest_unresolved_snapshot": str(self.problem_latest_json_file),
                    "hint_for_ai": (
                        "Полный снимок этого события находится в attachment.content_path; "
                        "attachment.path содержит только указатель. "
                        "Повторы сверх лимита остаются в JSONL со счётчиком."
                    ),
                }
                event_entry["schema_validation"] = entry["schema_validation"]
                for log_path in dict.fromkeys([
                    self.problem_events_file,
                    self.problem_log_file,
                    self.problem_session_log_file,
                ]):
                    self._append_jsonl_file(log_path, event_entry)
                self._update_problem_resolution_state_locked(entry, resolved=resolved)
            self._write_active_run_state(
                "running", operation, {"event_id": event_id}
            )
            if str(level).upper() in {"WARNING", "ERROR", "CRITICAL"} or resolved:
                self.write_problem_session_summary(
                    "running",
                    {
                        "live_refresh_reason": "problem_event",
                        "last_event_id": event_id,
                        "last_operation": operation,
                        "last_level": str(level).upper(),
                    },
                    live_refresh=True,
                )
        except Exception as e:
            try:
                logger = getattr(
                    self, "file_logger", logging.getLogger("VideoDownloader")
                )
                logger.error(
                    "Не удалось записать структурированный лог проблемы: %s: %s",
                    type(e).__name__,
                    e,
                )
            except Exception:
                pass
            self._write_emergency_problem_log(
                "structured_problem_log_write_failed",
                e,
                {"operation": operation, "level": level},
            )
