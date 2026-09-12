"""Запись событий/инцидентов и обновление unresolved/resolved state.

Автоматически выделено из прежнего модуля problem_diagnostics.py без изменения тел методов.
"""

from collections import Counter
from typing import Dict
from typing import List
from typing import Optional
from pathlib import Path
from datetime import datetime
import hashlib
import io
import json
import threading

from src.core.constants import PROBLEM_LOG_FORMAT_VERSION, PROBLEM_LOG_MAX_DETAILS_PER_SIGNATURE, PROBLEM_LOG_SCHEMA_VERSION


class DiagnosticsEventsMixin:
    def _append_jsonl_file(self, path: Path, data: Dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = getattr(self, "problem_jsonl_write_lock", None)
        if lock is None:
            lock = threading.RLock()
            self.problem_jsonl_write_lock = lock
        with lock:
            with open(path, "a", encoding="utf-8", newline="\n") as f:
                f.write(
                    json.dumps(
                        data, ensure_ascii=False, separators=(",", ":")
                    ) + "\n"
                )
                f.flush()


    def _validate_problem_event_shape(self, entry: Dict) -> List[str]:
        required = (
            "schema_version", "event_id", "timestamp", "level",
            "operation", "message", "error_signature",
            "problem_log_session_id",
        )
        errors = [
            f"missing:{key}" for key in required
            if entry.get(key) is None
        ]
        if entry.get("schema_version") != PROBLEM_LOG_SCHEMA_VERSION:
            errors.append("invalid:schema_version")
        if str(entry.get("level") or "").upper() not in {
            "INFO", "WARNING", "ERROR", "CRITICAL"
        }:
            errors.append("invalid:level")
        return errors


    def _collect_problem_metrics_locked(self, entry: Dict) -> None:
        level = str(entry.get("level") or "UNKNOWN").upper()
        operation = str(entry.get("operation") or "unknown")
        signature = str(entry.get("error_signature") or "unknown")
        self.problem_level_counts[level] += 1
        self.problem_operation_counts[operation] += 1
        self.problem_signature_counts[signature] += 1
        if level in {"WARNING", "ERROR", "CRITICAL"}:
            self.problem_error_signature_counts[signature] += 1
        self.problem_last_event_id = entry.get("event_id")
        task_id = entry.get("task_id")
        if task_id:
            if not entry.get("parent_event_id"):
                entry["parent_event_id"] = self.problem_last_event_by_task.get(
                    str(task_id)
                )
            if entry.get("event_id"):
                self.problem_last_event_by_task[str(task_id)] = str(
                    entry["event_id"]
                )
        if (entry.get("schema_validation") or {}).get("status") == "failed":
            self.problem_schema_validation_failures += 1

        context = entry.get("context") if isinstance(entry.get("context"), dict) else {}
        strategy = (
            context.get("strategy")
            or context.get("strategy_name")
            or context.get("successful_strategy")
        )
        if strategy:
            metrics = self.problem_strategy_metrics.setdefault(
                str(strategy), Counter()
            )
            metrics["events"] += 1
            metrics[level.casefold()] += 1
            if operation == "yt_dlp_attempt_started":
                metrics["attempts_started"] += 1
            elif operation == "yt_dlp_attempt_succeeded":
                metrics["attempts_succeeded"] += 1
            elif operation == "yt_dlp_download_attempt" and level in {
                "WARNING", "ERROR", "CRITICAL"
            }:
                metrics["attempts_failed"] += 1
            elif operation == "yt_dlp_recovered_after_fallback":
                metrics["fallback_recoveries"] += 1
            if entry.get("resolved"):
                metrics["recovered"] += 1

        for key in ("elapsed_sec", "attempt_elapsed_sec", "duration_sec"):
            raw = context.get(key)
            if isinstance(raw, (int, float)) and 0 <= float(raw) <= 7 * 24 * 3600:
                self.problem_duration_samples.append(float(raw))
                break


    def _write_problem_attachment_locked(self, entry: Dict) -> Optional[Dict]:
        signature = str(entry.get("error_signature") or "unknown")
        occurrence = int(self.problem_signature_counts.get(signature, 0))
        if occurrence > PROBLEM_LOG_MAX_DETAILS_PER_SIGNATURE:
            self.problem_suppressed_detail_count += 1
            return {
                "status": "suppressed_repetition",
                "reason": (
                    f"Для сигнатуры сохранено максимум "
                    f"{PROBLEM_LOG_MAX_DETAILS_PER_SIGNATURE} полных событий"
                ),
                "occurrence": occurrence,
            }

        filename = f"event_{int(entry.get('sequence') or 0):06d}.json"
        event_pointer_path = self.problem_attachments_dir / filename
        safe_entry = self._json_safe_value(entry)
        event_metadata_keys = {
            "schema_version", "sequence", "event_id", "parent_event_id",
            "task_id", "attempt_id", "command_id", "incident_id",
            "incident_key", "problem_log_session_id", "timestamp",
            "timestamp_unix", "problem_fingerprint", "resolved",
            "attachment",
        }
        diagnostic_payload = {
            "schema_version": PROBLEM_LOG_SCHEMA_VERSION,
            "content_type": "problem_event_diagnostic",
            "diagnostic": {
                key: value for key, value in safe_entry.items()
                if key not in event_metadata_keys
            },
        }
        canonical = json.dumps(
            diagnostic_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        content_sha256 = hashlib.sha256(canonical).hexdigest()
        blob_dir = self.problem_attachments_dir / "blobs"
        blob_path = blob_dir / f"{content_sha256}.json"
        reused_blob = False
        if blob_path.exists():
            try:
                existing = json.loads(blob_path.read_text(encoding="utf-8"))
                existing_canonical = json.dumps(
                    existing,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                if hashlib.sha256(existing_canonical).hexdigest() != content_sha256:
                    raise OSError(
                        "content-addressed attachment hash mismatch"
                    )
                reused_blob = True
            except (OSError, UnicodeError, json.JSONDecodeError) as error:
                raise OSError(
                    f"attachment blob verification failed: {error}"
                ) from error
        else:
            self._atomic_write_json_file(blob_path, diagnostic_payload)
            self.problem_unique_attachment_blob_count = (
                getattr(self, "problem_unique_attachment_blob_count", 0) + 1
            )
        if reused_blob:
            self.problem_reused_attachment_blob_count = (
                getattr(self, "problem_reused_attachment_blob_count", 0) + 1
            )

        pointer = {
            "schema_version": PROBLEM_LOG_SCHEMA_VERSION,
            "content_type": "problem_event_pointer",
            "event_id": entry.get("event_id"),
            "sequence": entry.get("sequence"),
            "timestamp": entry.get("timestamp"),
            "problem_log_session_id": entry.get("problem_log_session_id"),
            "error_signature": entry.get("error_signature"),
            "incident_id": entry.get("incident_id"),
            "content_sha256": content_sha256,
            "content_path": str(blob_path),
            "relative_content_path": str(
                Path("attachments") / "blobs" / blob_path.name
            ),
            "reused_existing_blob": reused_blob,
            "hint_for_codex": (
                "Метаданные события находятся в events.jsonl, "
                "полная диагностика — в content_path."
            ),
        }
        self._atomic_write_json_file(event_pointer_path, pointer)
        self.problem_full_detail_count += 1
        return {
            "status": "saved",
            "path": str(event_pointer_path),
            "relative_path": str(Path("attachments") / filename),
            "content_path": str(blob_path),
            "relative_content_path": pointer["relative_content_path"],
            "content_sha256": content_sha256,
            "reused_existing_blob": reused_blob,
            "pointer_size_bytes": (
                event_pointer_path.stat().st_size
                if event_pointer_path.exists() else None
            ),
            "content_size_bytes": (
                blob_path.stat().st_size if blob_path.exists() else None
            ),
        }


    def _append_incident_transition_locked(self, entry: Dict,
                                           resolved: bool,
                                           forced_status: Optional[str] = None) -> None:
        level = str(entry.get("level") or "").upper()
        if (
            not resolved
            and not forced_status
            and level not in {"WARNING", "ERROR", "CRITICAL"}
        ):
            return
        incident_key = str(entry.get("incident_key") or "unknown")
        incident_id = self._incident_id(incident_key)
        now = str(entry.get("timestamp") or datetime.now().isoformat(timespec="seconds"))
        existing = self.problem_incident_records.get(incident_key)
        if existing is None:
            existing = {
                "incident_id": incident_id,
                "incident_key": incident_key,
                "first_seen_at": now,
                "occurrence_count": 0,
                "signatures": Counter(),
            }
            self.problem_incident_records[incident_key] = existing
        if not forced_status:
            existing["occurrence_count"] += 1
        existing["last_seen_at"] = now
        existing["last_event_id"] = entry.get("event_id")
        existing["last_level"] = level
        existing["last_operation"] = entry.get("operation")
        existing["signatures"][str(entry.get("error_signature") or "unknown")] += 1
        if forced_status:
            status = forced_status
            existing["closed_at"] = now
        elif resolved:
            status = "recovered"
            existing["resolved_at"] = now
        elif existing["occurrence_count"] > 1:
            status = "retrying"
        else:
            status = "detected"
        existing["status"] = status

        transition = {
            "schema_version": PROBLEM_LOG_SCHEMA_VERSION,
            "timestamp": now,
            "problem_log_session_id": self.problem_log_session_id,
            "incident_id": incident_id,
            "incident_key": incident_key,
            "event_id": entry.get("event_id"),
            "parent_event_id": entry.get("parent_event_id"),
            "status": status,
            "level": level,
            "operation": entry.get("operation"),
            "message": entry.get("message"),
            "error_signature": entry.get("error_signature"),
            "occurrence_count": existing["occurrence_count"],
            "first_seen_at": existing["first_seen_at"],
            "attachment": entry.get("attachment"),
        }
        self._append_jsonl_file(self.problem_incidents_file, transition)


    def _problem_entry_priority(self, entry: Dict) -> int:
        """Приоритет для latest_problem_snapshot.json.

        Нужен, чтобы реальная ошибка yt-dlp не затиралась последующим
        app_log "Остановка..." или штатной отменой при закрытии окна.
        """
        level = str(entry.get("level", "")).upper()
        operation = str(entry.get("operation", ""))
        message = str(entry.get("message", "")).lower()
        category = str(entry.get("ai_debug_hint", {}).get("category", "")).lower()

        score = {"CRITICAL": 100, "ERROR": 80, "WARNING": 50, "INFO": 10}.get(level, 20)
        if operation.startswith("yt_dlp") or "yt-dlp" in message:
            score += 25
        if category in {"network_or_tls", "youtube_forbidden_or_client_blocked", "auth_or_cookies"}:
            score += 20
        if operation in {"app_log", "terminate_active_processes_snapshot"}:
            score -= 20
        if any(word in message for word in ("останов", "отмен", "cancel", "closing")):
            score -= 25
        return score


    def _problem_incident_key(self, entry: Dict) -> str:
        context = entry.get("context") if isinstance(entry.get("context"), dict) else {}
        video_id = (
            context.get("expected_video_id")
            or context.get("video_id")
            or (entry.get("url_diagnostics") or {}).get("video_id")
        )
        if video_id:
            return f"video:{video_id}"

        media_path = (
            context.get("video_file")
            or context.get("original_video_path")
            or context.get("audio_file")
        )
        if media_path:
            digest = hashlib.sha256(
                str(media_path).casefold().encode("utf-8", errors="replace")
            ).hexdigest()[:16]
            return f"media:{digest}"

        return f"signature:{entry.get('error_signature') or entry.get('problem_fingerprint', 'unknown')}"


    def _is_unresolved_problem_entry(self, entry: Dict) -> bool:
        level = str(entry.get("level", "")).upper()
        operation = str(entry.get("operation", ""))
        category = str((entry.get("ai_debug_hint") or {}).get("category", "")).lower()
        if category == "user_cancelled":
            return False
        if operation in {
            "app_log",
            "terminate_active_processes_snapshot",
            "yt_dlp_cancel_before_terminate",
        }:
            return False
        if operation == "yt_dlp_download_attempt" and level == "WARNING":
            return True
        return level in {"ERROR", "CRITICAL"}


    def _problem_entry_index_summary(self, entry: Optional[Dict]) -> Optional[Dict]:
        if not entry:
            return None
        context = entry.get("context") if isinstance(entry.get("context"), dict) else {}
        return {
            "timestamp": entry.get("timestamp"),
            "sequence": entry.get("sequence"),
            "level": entry.get("level"),
            "operation": entry.get("operation"),
            "message": entry.get("message"),
            "category": (entry.get("ai_debug_hint") or {}).get("category"),
            "problem_fingerprint": entry.get("problem_fingerprint"),
            "error_signature": entry.get("error_signature"),
            "event_id": entry.get("event_id"),
            "incident_id": entry.get("incident_id"),
            "incident_key": entry.get("incident_key"),
            "attachment": entry.get("attachment"),
            "video_id": (
                context.get("expected_video_id")
                or (entry.get("url_diagnostics") or {}).get("video_id")
            ),
        }


    def _refresh_problem_status_files_locked(self) -> None:
        """Обновляет снимок нерешённой проблемы и маленький корневой индекс."""
        unresolved = sorted(
            self.problem_unresolved_entries.values(),
            key=lambda item: (
                int(item.get("sequence") or 0),
                str(item.get("timestamp") or ""),
            ),
        )
        latest = unresolved[-1] if unresolved else None
        unresolved_summaries = [
            self._problem_entry_index_summary(item) for item in unresolved[-20:]
        ]
        updated_at = datetime.now().isoformat(timespec="seconds")

        if latest:
            snapshot = dict(latest)
            snapshot["status"] = "unresolved"
            snapshot["unresolved_count"] = len(unresolved)
            snapshot["unresolved_problems"] = unresolved_summaries
            report_buffer = io.StringIO()
            self._write_problem_report_block(report_buffer, latest)
            report_buffer.write(
                f"\nНерешённых проблем в этой сессии: {len(unresolved)}\n"
            )
            report_text = report_buffer.getvalue()
            status = "unresolved"
        else:
            snapshot = {
                "schema_version": PROBLEM_LOG_SCHEMA_VERSION,
                "format_version": PROBLEM_LOG_FORMAT_VERSION,
                "status": "no_unresolved_problems",
                "problem_log_session_id": self.problem_log_session_id,
                "updated_at": updated_at,
                "unresolved_count": 0,
                "last_resolution": self.problem_last_resolution,
                "hint_for_ai": (
                    "В текущей сессии нет нерешённых проблем. "
                    "Итог работы смотрите в session_summary.json."
                ),
            }
            report_text = (
                "Нерешённых проблем в текущей сессии нет.\n"
                f"Обновлено: {updated_at}\n"
                f"Разрешено проблем: {self.problem_resolution_count}\n"
            )
            status = "no_unresolved_problems"

        self._atomic_write_json_file(
            self.problem_latest_json_file, self._json_safe_value(snapshot)
        )
        self._atomic_write_text_file(self.problem_report_file, report_text)
        self._atomic_write_json_file(
            self.problem_latest_unresolved_index_file,
            {
                "schema_version": PROBLEM_LOG_SCHEMA_VERSION,
                "format_version": PROBLEM_LOG_FORMAT_VERSION,
                "status": status,
                "updated_at": updated_at,
                "problem_log_session_id": self.problem_log_session_id,
                "session_dir": str(self.problem_session_dir),
                "session_snapshot": str(self.problem_latest_json_file),
                "session_summary": str(self.problem_session_summary_file),
                "incidents_file": str(self.problem_incidents_file),
                "event_log": str(self.problem_events_file),
                "legacy_event_log": str(self.problem_log_file),
                "unresolved_count": len(unresolved),
                "latest_unresolved": self._problem_entry_index_summary(latest),
            },
        )


    def _update_problem_resolution_state_locked(self, entry: Dict,
                                                resolved: bool = False) -> None:
        incident_key = self._problem_incident_key(entry)
        entry["incident_key"] = incident_key
        entry["incident_id"] = self._incident_id(incident_key)
        entry["resolved"] = bool(resolved)
        if resolved:
            previous = self.problem_unresolved_entries.pop(incident_key, None)
            if previous is not None:
                self.problem_resolution_count += 1
            self.problem_last_resolution = {
                "timestamp": entry.get("timestamp"),
                "operation": entry.get("operation"),
                "message": entry.get("message"),
                "incident_key": incident_key,
                "resolved_problem_fingerprint": (
                    previous.get("problem_fingerprint") if previous else None
                ),
                "resolution_context": self._compact_problem_context(
                    entry.get("context", {})
                ),
            }
        elif self._is_unresolved_problem_entry(entry):
            previous = self.problem_unresolved_entries.get(incident_key)
            if (
                previous is None
                or self._problem_entry_priority(entry)
                >= self._problem_entry_priority(previous)
            ):
                self.problem_unresolved_entries[incident_key] = entry

        self._append_incident_transition_locked(entry, resolved)
        if resolved or self._is_unresolved_problem_entry(entry):
            self._refresh_problem_status_files_locked()
