"""Формирование итогового summary problem-log сессии.

Автоматически выделено из прежнего модуля problem_diagnostics.py без изменения тел методов.
"""

from collections import Counter
from typing import Dict
from typing import List
from typing import Optional
from datetime import datetime

from src.core.constants import PROBLEM_LOG_FORMAT_VERSION, PROBLEM_LOG_SCHEMA_VERSION


class DiagnosticsSummaryMixin:
    def write_problem_session_summary(self, status: str,
                                      context: Optional[Dict] = None,
                                      *, live_refresh: bool = False) -> None:
        """Пишет summary сессии.

        ``live_refresh`` используется после WARNING/ERROR/CRITICAL во время
        активной загрузки. Он обновляет счётчики и latest-индексы сразу после
        записи события в JSONL, но не запускает тяжёлую полную валидацию
        файлов на каждом сбое. Полная валидация выполняется итоговыми summary.
        """
        if not self.should_write_problem_logs():
            return
        self.ensure_problem_log_dirs()
        incoming_context = dict(context or {})
        if status in {"completed", "cancelled"}:
            self.problem_last_completion_context = dict(incoming_context)
        elif status == "finished" and self.problem_last_completion_context:
            incoming_context = {
                **self.problem_last_completion_context,
                **incoming_context,
            }
        try:
            with self.problem_log_lock:
                updated_at = datetime.now().isoformat(timespec="seconds")
                unresolved = sorted(
                    self.problem_unresolved_entries.values(),
                    key=lambda item: int(item.get("sequence") or 0),
                )
                terminal_incident_status = None
                if status == "cancelled":
                    terminal_incident_status = "cancelled"
                elif status in {"completed", "failed", "crashed", "finished"}:
                    terminal_incident_status = "failed"
                if terminal_incident_status:
                    for unresolved_entry in unresolved:
                        incident_key = str(
                            unresolved_entry.get("incident_key") or ""
                        )
                        incident_record = self.problem_incident_records.get(
                            incident_key, {}
                        )
                        if incident_record.get("status") != terminal_incident_status:
                            self._append_incident_transition_locked(
                                unresolved_entry,
                                resolved=False,
                                forced_status=terminal_incident_status,
                            )
                event_counts = dict(self.problem_level_counts)
                structured_error_count = (
                    int(event_counts.get("ERROR", 0))
                    + int(event_counts.get("CRITICAL", 0))
                )
                emergency = self._problem_emergency_snapshot()
                error_count = (
                    structured_error_count
                    + int(emergency.get("critical_count") or 0)
                )
                top_signatures = [
                    {
                        "error_signature": signature,
                        "count": count,
                    }
                    for signature, count in self.problem_error_signature_counts.most_common(20)
                ]
                regression = self._problem_regression_snapshot(
                    error_count,
                    [item["error_signature"] for item in top_signatures],
                )
                strategy_metrics = self._problem_strategy_summary()
                strategy_findings = self._problem_strategy_findings(
                    strategy_metrics
                )
                recommended_next_checks: List[str] = []
                for unresolved_entry in reversed(unresolved):
                    for check in (
                        (unresolved_entry.get("ai_debug_hint") or {}).get(
                            "next_checks"
                        ) or []
                    ):
                        if check not in recommended_next_checks:
                            recommended_next_checks.append(check)
                        if len(recommended_next_checks) >= 20:
                            break
                    if len(recommended_next_checks) >= 20:
                        break
                for finding in strategy_findings:
                    check = str(finding.get("message") or "").strip()
                    if check and check not in recommended_next_checks:
                        recommended_next_checks.append(check)
                    if len(recommended_next_checks) >= 20:
                        break
                incident_status_counts = Counter(
                    str(item.get("status") or "unknown")
                    for item in self.problem_incident_records.values()
                )
                context_safe = self._json_safe_value(incoming_context)
                summary = {
                    "schema_version": PROBLEM_LOG_SCHEMA_VERSION,
                    "format_version": PROBLEM_LOG_FORMAT_VERSION,
                    "status": status,
                    "updated_at": updated_at,
                    "problem_log_session_id": self.problem_log_session_id,
                    "session_started_at": self.current_session_start or self.problem_runtime_started_at,
                    "session_dir": str(self.problem_session_dir),
                    "event_log": str(self.problem_events_file),
                    "legacy_event_log": str(self.problem_log_file),
                    "emergency_log": str(self.problem_emergency_log_file),
                    "incidents_file": str(self.problem_incidents_file),
                    "manifest_file": str(self.problem_manifest_file),
                    "active_run_state_file": str(self.problem_session_active_state_file),
                    "attachments_dir": str(self.problem_attachments_dir),
                    "validation_report": str(
                        self.problem_validation_report_file
                    ),
                    "summary_markdown": str(self.problem_session_summary_md_file),
                    "latest_problem_snapshot": str(self.problem_latest_json_file),
                    "event_count": sum(event_counts.values()),
                    "event_counts": event_counts,
                    "structured_error_count": structured_error_count,
                    "error_count": error_count,
                    "emergency": emergency,
                    "diagnostic_integrity_status": (
                        "degraded" if emergency.get("count") else "clean"
                    ),
                    "operation_counts": dict(
                        self.problem_operation_counts.most_common(30)
                    ),
                    "unresolved_count": len(unresolved),
                    "resolved_count": self.problem_resolution_count,
                    "recovery_rate": round(
                        self.problem_resolution_count
                        / max(
                            1,
                            self.problem_resolution_count + len(unresolved),
                        ),
                        4,
                    ),
                    "full_detail_count": self.problem_full_detail_count,
                    "suppressed_detail_count": self.problem_suppressed_detail_count,
                    "unique_attachment_blob_count": (
                        self.problem_unique_attachment_blob_count
                    ),
                    "reused_attachment_blob_count": (
                        self.problem_reused_attachment_blob_count
                    ),
                    "schema_validation_failures": self.problem_schema_validation_failures,
                    "disk_validation_report": str(
                        self.problem_validation_report_file
                    ),
                    "incident_count": len(self.problem_incident_records),
                    "incident_status_counts": dict(incident_status_counts),
                    "unresolved_problems": [
                        self._problem_entry_index_summary(item)
                        for item in unresolved[-20:]
                    ],
                    "latest_unresolved": self._problem_entry_index_summary(
                        unresolved[-1] if unresolved else None
                    ),
                    "top_error_signatures": top_signatures,
                    "strategy_metrics": strategy_metrics,
                    "strategy_findings": strategy_findings,
                    "recommended_next_checks": recommended_next_checks,
                    "timings": {
                        "sample_count": len(self.problem_duration_samples),
                        "total_recorded_sec": round(
                            sum(self.problem_duration_samples), 3
                        ),
                        "p50_sec": self._problem_percentile(
                            self.problem_duration_samples, 0.50
                        ),
                        "p95_sec": self._problem_percentile(
                            self.problem_duration_samples, 0.95
                        ),
                        "max_sec": (
                            round(max(self.problem_duration_samples), 3)
                            if self.problem_duration_samples else None
                        ),
                    },
                    "regression": regression,
                    "context": context_safe,
                    "codex_read_order": [
                        str(self.problem_session_summary_file),
                        str(self.problem_latest_json_file),
                        str(self.problem_emergency_log_file),
                        str(self.problem_incidents_file),
                        str(self.problem_events_file),
                        str(self.problem_manifest_file),
                    ],
                }
                if emergency.get("count"):
                    recommended_next_checks.insert(
                        0,
                        "Проверить emergency_problem_log.jsonl: диагностика записывалась с ошибками.",
                    )
                if live_refresh:
                    disk_validation = {
                        "status": "deferred_live",
                        "reason": (
                            "Полная проверка JSONL отложена до итогового summary; "
                            "live-summary построен из счётчиков после flush события."
                        ),
                        "event_count_at_refresh": summary["event_count"],
                        "last_event_id": getattr(self, "problem_last_event_id", None),
                    }
                else:
                    disk_validation = self._validate_problem_session_files_locked(
                        expected_summary=summary
                    )
                summary["live_refresh"] = bool(live_refresh)
                summary["event_counts_source"] = (
                    "in_memory_after_jsonl_flush" if live_refresh else "validated_session_state"
                )
                summary["last_event_id"] = getattr(
                    self, "problem_last_event_id", None
                )
                summary["disk_validation_status"] = disk_validation.get("status")
                summary["disk_validation"] = disk_validation
                self._atomic_write_json_file(self.problem_session_summary_file, summary)
                self._atomic_write_text_file(
                    self.problem_session_summary_md_file,
                    self._problem_summary_markdown(summary),
                )
                latest_index = {
                    "schema_version": PROBLEM_LOG_SCHEMA_VERSION,
                    "format_version": PROBLEM_LOG_FORMAT_VERSION,
                    "status": status,
                    "updated_at": updated_at,
                    "problem_log_session_id": self.problem_log_session_id,
                    "session_dir": str(self.problem_session_dir),
                    "session_summary": str(self.problem_session_summary_file),
                    "session_summary_markdown": str(
                        self.problem_session_summary_md_file
                    ),
                    "manifest_file": str(self.problem_manifest_file),
                    "event_log": str(self.problem_events_file),
                    "legacy_event_log": str(self.problem_log_file),
                    "incidents_file": str(self.problem_incidents_file),
                    "emergency_log": str(self.problem_emergency_log_file),
                    "unresolved_count": len(unresolved),
                    "resolved_count": self.problem_resolution_count,
                    "structured_error_count": structured_error_count,
                    "error_count": error_count,
                    "emergency_count": emergency.get("count", 0),
                    "emergency_critical_count": emergency.get("critical_count", 0),
                    "diagnostic_integrity_status": summary.get(
                        "diagnostic_integrity_status"
                    ),
                    "regression_suspected": regression.get("regression_suspected"),
                    "disk_validation_status": disk_validation.get("status"),
                    "validation_report": str(
                        self.problem_validation_report_file
                    ),
                    "codex_read_order": summary["codex_read_order"],
                }
                self._atomic_write_json_file(
                    self.problem_latest_session_index_file, latest_index
                )
                self._atomic_write_json_file(
                    self.problem_latest_run_index_file, latest_index
                )
                self._append_problem_health_history_locked(summary)
                self._refresh_problem_status_files_locked()
            if not live_refresh:
                self._write_active_run_state(
                    "running",
                    f"session_summary_{status}",
                    {
                        "summary_status": status,
                        "unresolved_count": summary["unresolved_count"],
                        "event_count": summary["event_count"],
                    },
                )
        except Exception as e:
            try:
                self.file_logger.error(
                    f"write_problem_session_summary: {type(e).__name__}: {e}"
                )
            except Exception:
                pass
            self._write_emergency_problem_log(
                "write_problem_session_summary_failed",
                e,
                {"status": status},
            )
