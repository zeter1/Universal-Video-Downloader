"""Метрики, health history, regression snapshot и валидация сессии.

Автоматически выделено из прежнего модуля problem_diagnostics.py без изменения тел методов.
"""

from typing import Dict
from typing import List
from typing import Optional
from pathlib import Path
from typing import Set
from datetime import datetime
import json

from src.core.constants import PROBLEM_HEALTH_HISTORY_MAX_LINES, PROBLEM_LOG_SCHEMA_VERSION
from src.diagnostics.validator import validate_session_logs


class DiagnosticsHealthMixin:
    def _problem_percentile(self, values: List[float], percentile: float) -> Optional[float]:
        if not values:
            return None
        ordered = sorted(float(value) for value in values)
        if len(ordered) == 1:
            return round(ordered[0], 3)
        index = (len(ordered) - 1) * percentile
        lower = int(index)
        upper = min(lower + 1, len(ordered) - 1)
        fraction = index - lower
        result = ordered[lower] + (ordered[upper] - ordered[lower]) * fraction
        return round(result, 3)


    def _problem_strategy_summary(self) -> Dict[str, Dict]:
        result: Dict[str, Dict] = {}
        for strategy, metrics in sorted(self.problem_strategy_metrics.items()):
            values = dict(metrics)
            failed = int(metrics.get("attempts_failed", 0))
            succeeded = int(metrics.get("attempts_succeeded", 0))
            started = int(metrics.get("attempts_started", 0))
            attempts = started or (failed + succeeded)
            values["attempts"] = attempts
            values["failure_rate"] = round(failed / max(1, attempts), 4)
            values["success_rate"] = round(succeeded / max(1, attempts), 4)
            result[strategy] = values
        return result


    def _problem_strategy_findings(self,
                                   strategy_metrics: Dict[str, Dict]) -> List[Dict]:
        """Выделяет устойчиво плохие стратегии и подтверждённые fallback."""
        findings: List[Dict] = []
        for strategy, metrics in strategy_metrics.items():
            attempts = int(metrics.get("attempts") or 0)
            failed = int(metrics.get("attempts_failed") or 0)
            succeeded = int(metrics.get("attempts_succeeded") or 0)
            failure_rate = float(metrics.get("failure_rate") or 0.0)
            success_rate = float(metrics.get("success_rate") or 0.0)
            fallback_recoveries = int(metrics.get("fallback_recoveries") or 0)
            if attempts >= 3 and failure_rate >= 0.8:
                findings.append({
                    "kind": "ineffective_strategy",
                    "strategy": strategy,
                    "attempts": attempts,
                    "failed": failed,
                    "succeeded": succeeded,
                    "failure_rate": failure_rate,
                    "message": (
                        f"Стратегия «{strategy}» неуспешна в {failed} из "
                        f"{attempts} попыток ({failure_rate:.0%}); проверить её "
                        "параметры или понизить приоритет."
                    ),
                })
            if (
                attempts >= 3
                and fallback_recoveries >= 3
                and success_rate >= 0.8
            ):
                findings.append({
                    "kind": "effective_fallback",
                    "strategy": strategy,
                    "attempts": attempts,
                    "failed": failed,
                    "succeeded": succeeded,
                    "success_rate": success_rate,
                    "fallback_recoveries": fallback_recoveries,
                    "message": (
                        f"Fallback «{strategy}» восстановил "
                        f"{fallback_recoveries} загрузок и успешен в {succeeded} "
                        f"из {attempts} попыток ({success_rate:.0%}); рассмотреть "
                        "его более ранний запуск."
                    ),
                })
        findings.sort(key=lambda item: (
            0 if item.get("kind") == "ineffective_strategy" else 1,
            -int(item.get("attempts") or 0),
            str(item.get("strategy") or ""),
        ))
        return findings


    def _deduplicate_problem_health_records(
        self, records: List[Dict]
    ) -> List[Dict]:
        """Оставляет один итог запуска, не теряя счётчики из completed."""
        order: List[str] = []
        merged_by_session: Dict[str, Dict] = {}
        for index, item in enumerate(records):
            if not isinstance(item, dict):
                continue
            session_id = str(
                item.get("problem_log_session_id") or f"unknown:{index}"
            )
            if session_id not in merged_by_session:
                order.append(session_id)
                merged_by_session[session_id] = dict(item)
                continue
            merged = merged_by_session[session_id]
            for key, value in item.items():
                if value is not None:
                    merged[key] = value
            merged_by_session[session_id] = merged
        return [merged_by_session[session_id] for session_id in order]


    def _read_problem_health_history(
        self,
        limit: int = 20,
        exclude_session_id: Optional[str] = None,
    ) -> List[Dict]:
        path = self.problem_health_history_file
        if not path.exists():
            return []
        try:
            lines = path.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()
        except OSError:
            return []
        result = []
        for line in lines:
            try:
                item = json.loads(line)
                if isinstance(item, dict):
                    result.append(item)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        result = self._deduplicate_problem_health_records(result)
        if exclude_session_id:
            result = [
                item for item in result
                if str(item.get("problem_log_session_id") or "")
                != str(exclude_session_id)
            ]
        return result[-limit:]


    def _problem_regression_snapshot(self, current_error_count: int,
                                     current_signatures: List[str]) -> Dict:
        history = self._read_problem_health_history(
            20, exclude_session_id=self.problem_log_session_id
        )
        previous_signatures: Set[str] = set()
        previous_error_counts: List[int] = []
        previous_elapsed: List[float] = []
        for item in history:
            previous_signatures.update(item.get("top_error_signatures") or [])
            error_count = item.get("error_count")
            if isinstance(error_count, int):
                previous_error_counts.append(error_count)
            elapsed = item.get("elapsed_sec")
            if isinstance(elapsed, (int, float)):
                previous_elapsed.append(float(elapsed))
        average_errors = (
            round(sum(previous_error_counts) / len(previous_error_counts), 2)
            if previous_error_counts else None
        )
        return {
            "compared_sessions": len(history),
            "new_error_signatures": [
                signature for signature in current_signatures
                if signature not in previous_signatures
            ],
            "previous_average_error_count": average_errors,
            "error_count_change_from_average": (
                round(current_error_count - average_errors, 2)
                if average_errors is not None else None
            ),
            "previous_elapsed_p95_sec": self._problem_percentile(
                previous_elapsed, 0.95
            ),
            "regression_suspected": bool(
                average_errors is not None
                and current_error_count > max(average_errors * 1.5, average_errors + 2)
            ),
        }


    def _problem_summary_markdown(self, summary: Dict) -> str:
        counts = summary.get("event_counts") or {}
        emergency = summary.get("emergency") or {}
        top_signatures = summary.get("top_error_signatures") or []
        unresolved = summary.get("unresolved_problems") or []
        regression = summary.get("regression") or {}
        strategy_findings = summary.get("strategy_findings") or []
        recommended_checks = summary.get("recommended_next_checks") or []
        lines = [
            "# Итог диагностической сессии",
            "",
            f"- Статус: `{summary.get('status')}`",
            f"- Сессия: `{summary.get('problem_log_session_id')}`",
            f"- Обновлено: `{summary.get('updated_at')}`",
            f"- Событий: {summary.get('event_count', 0)}",
            f"- WARNING: {counts.get('WARNING', 0)}",
            f"- ERROR: {counts.get('ERROR', 0)}",
            f"- CRITICAL: {counts.get('CRITICAL', 0) + int(emergency.get('critical_count') or 0)} "
            f"(структурный журнал: {counts.get('CRITICAL', 0)}, "
            f"аварийный журнал: {emergency.get('critical_count', 0)})",
            f"- Целостность диагностики: `{summary.get('diagnostic_integrity_status')}`",
            f"- Восстановлено проблем: {summary.get('resolved_count', 0)}",
            f"- Осталось нерешённых: {summary.get('unresolved_count', 0)}",
            f"- Полных вложений: {summary.get('full_detail_count', 0)}",
            f"- Уникальных диагностических блоков: {summary.get('unique_attachment_blob_count', 0)}",
            f"- Повторно использованных блоков: {summary.get('reused_attachment_blob_count', 0)}",
            f"- Повторов без полного вложения: {summary.get('suppressed_detail_count', 0)}",
            f"- Ошибок проверки схемы: {summary.get('schema_validation_failures', 0)}",
            f"- Проверка JSONL с диска: `{summary.get('disk_validation_status')}`",
            "",
            "## Что читать Codex",
            "",
            f"1. `{Path(summary.get('event_log', '')).name}` — хронология.",
            f"2. `{Path(summary.get('incidents_file', '')).name}` — жизненный цикл проблем.",
            "3. Поле `attachment.content_path` — полный снимок; `attachment.path` — указатель.",
            f"4. `{Path(summary.get('emergency_log', '')).name}` — сбои записи самой диагностики.",
            f"5. `{Path(summary.get('manifest_file', '')).name}` — версии и окружение.",
        ]
        if top_signatures:
            lines.extend(["", "## Основные сигнатуры", ""])
            for item in top_signatures[:10]:
                lines.append(
                    f"- `{item.get('error_signature')}` — {item.get('count')} событий"
                )
        if unresolved:
            lines.extend(["", "## Нерешённые проблемы", ""])
            for item in unresolved[:10]:
                lines.append(
                    f"- `{item.get('error_signature')}`: {item.get('message')}"
                )
        if strategy_findings:
            lines.extend(["", "## Выводы по стратегиям", ""])
            for item in strategy_findings[:10]:
                lines.append(f"- {item.get('message')}")
        if recommended_checks:
            lines.extend(["", "## Рекомендуемые проверки", ""])
            for item in recommended_checks[:20]:
                lines.append(f"- {item}")
        if regression.get("compared_sessions"):
            lines.extend([
                "",
                "## Сравнение с предыдущими запусками",
                "",
                f"- Сравнено сессий: {regression.get('compared_sessions')}",
                f"- Подозрение на регрессию: {regression.get('regression_suspected')}",
                f"- Новые сигнатуры: {len(regression.get('new_error_signatures') or [])}",
            ])
        lines.append("")
        return "\n".join(lines)


    def _append_problem_health_history_locked(self, summary: Dict) -> None:
        if summary.get("status") not in {
            "completed", "cancelled", "failed", "crashed", "finished"
        }:
            return
        context = summary.get("context") if isinstance(summary.get("context"), dict) else {}
        emergency = summary.get("emergency") if isinstance(summary.get("emergency"), dict) else {}
        record = {
            "schema_version": PROBLEM_LOG_SCHEMA_VERSION,
            "timestamp": summary.get("updated_at"),
            "problem_log_session_id": summary.get("problem_log_session_id"),
            "status": summary.get("status"),
            "event_count": summary.get("event_count"),
            "warning_count": (summary.get("event_counts") or {}).get("WARNING", 0),
            "error_count": (
                (summary.get("event_counts") or {}).get("ERROR", 0)
                + (summary.get("event_counts") or {}).get("CRITICAL", 0)
                + int(emergency.get("critical_count") or 0)
            ),
            "structured_error_count": (
                (summary.get("event_counts") or {}).get("ERROR", 0)
                + (summary.get("event_counts") or {}).get("CRITICAL", 0)
            ),
            "emergency_critical_count": int(
                emergency.get("critical_count") or 0
            ),
            "resolved_count": summary.get("resolved_count"),
            "unresolved_count": summary.get("unresolved_count"),
            "elapsed_sec": context.get("elapsed_sec"),
            "queued_url_count": context.get("queued_url_count"),
            "downloaded_video_count": context.get("downloaded_video_count"),
            "failed_download_count": context.get("failed_download_count"),
            "problematic_download_count": context.get(
                "problematic_download_count"
            ),
            "converted_audio_count": context.get("converted_audio_count"),
            "top_error_signatures": [
                item.get("error_signature")
                for item in (summary.get("top_error_signatures") or [])[:10]
            ],
            "regression_suspected": (
                summary.get("regression") or {}
            ).get("regression_suspected"),
            "summary_file": str(self.problem_session_summary_file),
        }
        existing = self._read_problem_health_history(
            PROBLEM_HEALTH_HISTORY_MAX_LINES
        )
        session_id = str(record.get("problem_log_session_id") or "")
        existing = [
            item for item in existing
            if str(item.get("problem_log_session_id") or "") != session_id
        ]
        existing.append(self._json_safe_value(record))
        existing = self._deduplicate_problem_health_records(existing)
        existing = existing[-PROBLEM_HEALTH_HISTORY_MAX_LINES:]
        text = "".join(
            json.dumps(
                item, ensure_ascii=False, separators=(",", ":")
            ) + "\n"
            for item in existing
        )
        self._atomic_write_text_file(self.problem_health_history_file, text)
        self.problem_health_written_keys.add(session_id)
        self._trim_health_history()


    def _validate_problem_session_files_locked(
        self,
        expected_summary: Optional[Dict] = None,
    ) -> Dict:
        """Перечитывает JSONL с диска и сохраняет машинный отчёт проверки."""
        try:
            report = validate_session_logs(
                self.problem_events_file,
                self.problem_incidents_file,
                self.problem_schema_file,
                legacy_events_file=self.problem_log_file,
                emergency_file=self.problem_emergency_log_file,
                problem_log_session_id=self.problem_log_session_id,
                expected_summary=expected_summary,
            )
        except Exception as error:
            report = {
                "validator_version": "1.1",
                "status": "failed",
                "validated_at": datetime.now().isoformat(timespec="seconds"),
                "error_type": type(error).__name__,
                "error": self._sanitize_text_for_log(str(error)),
            }
        self.problem_last_disk_validation_status = report.get("status")
        self._atomic_write_json_file(
            self.problem_validation_report_file,
            self._json_safe_value(report),
        )
        return report
