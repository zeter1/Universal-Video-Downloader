"""Жизненный цикл диагностики, crash hooks и active-run state.

Автоматически выделено из прежнего модуля problem_diagnostics.py без изменения тел методов.
"""

from collections import Counter
from typing import Dict
from typing import List
from typing import Optional
from pathlib import Path
from datetime import datetime
import json
import os
import sys
import threading
import time

from src.core.constants import PROBLEM_LOG_FORMAT_VERSION, PROBLEM_LOG_SCHEMA_VERSION


class DiagnosticsLifecycleMixin:
    def _read_json_object(self, path: Path) -> Optional[Dict]:
        try:
            if not path.exists():
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, ValueError, json.JSONDecodeError):
            return None


    def _is_pid_running(self, pid) -> bool:
        try:
            pid_value = int(pid)
            if pid_value <= 0:
                return False
            os.kill(pid_value, 0)
            return True
        except (OSError, TypeError, ValueError):
            return False


    def _write_active_run_state(self, status: str, stage: str,
                                context: Optional[Dict] = None,
                                terminal: bool = False,
                                force: bool = False) -> None:
        if not force and not self.should_write_problem_logs():
            return
        if self.problem_runtime_terminal and not terminal:
            return
        try:
            updated_at = datetime.now().isoformat(timespec="seconds")
            payload = {
                "schema_version": PROBLEM_LOG_SCHEMA_VERSION,
                "format_version": PROBLEM_LOG_FORMAT_VERSION,
                "status": status,
                "stage": stage,
                "problem_log_session_id": self.problem_log_session_id,
                "pid": os.getpid(),
                "started_at": self.problem_runtime_started_at,
                "updated_at": updated_at,
                "heartbeat_at": None if terminal else updated_at,
                "finished_at": updated_at if terminal else None,
                "last_event_id": self.problem_last_event_id,
                "is_downloading": getattr(self, "is_downloading", None),
                "cancel_requested": (
                    self.cancel_flag.is_set()
                    if hasattr(self, "cancel_flag") else None
                ),
                "session_dir": str(self.problem_session_dir),
                "session_summary": str(self.problem_session_summary_file),
                "context": self._compact_problem_context(context or {}),
            }
            self._atomic_write_json_file(
                self.problem_session_active_state_file, payload
            )
            self._atomic_write_json_file(self.problem_active_run_state_file, payload)
            if terminal:
                self.problem_runtime_terminal = True
        except Exception as e:
            self._write_emergency_problem_log(
                "active_run_state_write_failed", e, {"status": status, "stage": stage}
            )


    def _write_emergency_problem_log(self, operation: str,
                                     exception: BaseException,
                                     context: Optional[Dict] = None) -> None:
        """Минимальный резервный лог, если основная диагностика сама дала сбой."""
        try:
            self.problem_log_dir.mkdir(parents=True, exist_ok=True)
            item = {
                "schema_version": PROBLEM_LOG_SCHEMA_VERSION,
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "level": "CRITICAL",
                "operation": operation,
                "exception_type": type(exception).__name__,
                "message": self._sanitize_text_for_log(str(exception)),
                "context": self._compact_problem_context(context or {}),
                "problem_log_session_id": getattr(
                    self, "problem_log_session_id", None
                ),
            }
            self._append_jsonl_file(self.problem_emergency_log_file, item)
        except Exception:
            pass


    def _problem_emergency_snapshot(self) -> Dict:
        """Возвращает компактный итог аварийных записей только текущего запуска."""
        path = getattr(self, "problem_emergency_log_file", None)
        session_id = str(getattr(self, "problem_log_session_id", "") or "")
        result = {
            "status": "clean",
            "path": str(path or ""),
            "count": 0,
            "critical_count": 0,
            "operation_counts": {},
            "first_at": None,
            "last_at": None,
            "latest": None,
        }
        if not path or not Path(path).exists() or not session_id:
            return result
        try:
            lock = getattr(self, "problem_jsonl_write_lock", None)
            if lock is None:
                lock = threading.RLock()
                self.problem_jsonl_write_lock = lock
            with lock:
                lines = Path(path).read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()
        except OSError:
            return result

        records: List[Dict] = []
        for raw_line in lines:
            if not raw_line.strip():
                continue
            try:
                item = json.loads(raw_line)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if (
                isinstance(item, dict)
                and str(item.get("problem_log_session_id") or "") == session_id
            ):
                records.append(item)
        if not records:
            return result

        operations = Counter(
            str(item.get("operation") or "unknown") for item in records
        )
        latest = records[-1]
        result.update({
            "status": "degraded",
            "count": len(records),
            "critical_count": sum(
                1 for item in records
                if str(item.get("level") or "").upper() == "CRITICAL"
            ),
            "operation_counts": dict(operations.most_common()),
            "first_at": records[0].get("timestamp"),
            "last_at": latest.get("timestamp"),
            "latest": {
                "timestamp": latest.get("timestamp"),
                "operation": latest.get("operation"),
                "exception_type": latest.get("exception_type"),
                "message": latest.get("message"),
                "context": latest.get("context"),
            },
        })
        return result


    def _record_uncaught_exception(self, source: str, exc_type,
                                   exc_value, exc_traceback,
                                   context: Optional[Dict] = None) -> None:
        if exc_type in (KeyboardInterrupt, SystemExit):
            return
        if not self.problem_exception_hook_lock.acquire(blocking=False):
            return
        try:
            fatal = source in {"sys.excepthook", "main"}
            exception = exc_value or RuntimeError(str(exc_type))
            if exc_traceback is not None:
                exception = exception.with_traceback(exc_traceback)
            self.record_problem(
                f"Необработанное исключение: {source}",
                "CRITICAL",
                "uncaught_exception",
                {
                    "source": source,
                    "thread_name": threading.current_thread().name,
                    **(context or {}),
                },
                exception=exception,
            )
            self.write_problem_session_summary(
                "crashed" if fatal else "failed",
                {
                    "source": source,
                    "thread_name": threading.current_thread().name,
                    **(context or {}),
                },
            )
            self._write_active_run_state(
                "crashed" if fatal else "running",
                "uncaught_exception",
                {"source": source, **(context or {})},
                terminal=fatal,
            )
        except Exception as hook_error:
            self._write_emergency_problem_log(
                "uncaught_exception_hook_failed",
                hook_error,
                {"source": source},
            )
        finally:
            self.problem_exception_hook_lock.release()


    def _install_problem_exception_hooks(self) -> None:
        self.problem_original_sys_excepthook = sys.excepthook
        self.problem_original_threading_excepthook = getattr(
            threading, "excepthook", None
        )
        self.problem_original_unraisablehook = getattr(
            sys, "unraisablehook", None
        )
        self.problem_original_tk_report_callback_exception = getattr(
            self.root, "report_callback_exception", None
        )

        def sys_hook(exc_type, exc_value, exc_traceback):
            self._record_uncaught_exception(
                "sys.excepthook", exc_type, exc_value, exc_traceback
            )
            original = self.problem_original_sys_excepthook
            if original and original is not sys_hook:
                original(exc_type, exc_value, exc_traceback)

        def thread_hook(args):
            self._record_uncaught_exception(
                "threading.excepthook",
                args.exc_type,
                args.exc_value,
                args.exc_traceback,
                {"thread_name": getattr(args.thread, "name", None)},
            )
            original = self.problem_original_threading_excepthook
            if original and original is not thread_hook:
                original(args)

        def unraisable_hook(args):
            exc_value = getattr(args, "exc_value", None)
            self._record_uncaught_exception(
                "sys.unraisablehook",
                getattr(args, "exc_type", type(exc_value)),
                exc_value,
                getattr(args, "exc_traceback", None),
                {
                    "object_repr": self._sanitize_text_for_log(
                        repr(getattr(args, "object", None))
                    )[:1000],
                    "err_msg": getattr(args, "err_msg", None),
                },
            )
            original = self.problem_original_unraisablehook
            if original and original is not unraisable_hook:
                original(args)

        def tk_hook(exc_type, exc_value, exc_traceback):
            self._record_uncaught_exception(
                "tkinter.report_callback_exception",
                exc_type,
                exc_value,
                exc_traceback,
            )
            original = self.problem_original_tk_report_callback_exception
            if original and original is not tk_hook:
                original(exc_type, exc_value, exc_traceback)

        sys.excepthook = sys_hook
        if hasattr(threading, "excepthook"):
            threading.excepthook = thread_hook
        if hasattr(sys, "unraisablehook"):
            sys.unraisablehook = unraisable_hook
        self.root.report_callback_exception = tk_hook


    def initialize_problem_diagnostics(self) -> None:
        """Создаёт диагностический контракт и проверяет прошлое завершение."""
        if not self.should_write_problem_logs():
            return
        try:
            self.ensure_problem_log_dirs()
            if self.problem_diagnostics_initialized:
                self._write_problem_format_files()
                self._write_problem_manifest()
                self._write_active_run_state("running", "diagnostics_reenabled")
                return
            previous = self.problem_previous_active_state
            if previous and previous.get("problem_log_session_id") == self.problem_log_session_id:
                self.problem_previous_active_state = None
            self._write_problem_format_files()
            self._write_problem_manifest()
            self._install_problem_exception_hooks()
            self.problem_diagnostics_initialized = True
            self._write_active_run_state("running", "ui_ready")

            if self.problem_previous_active_state:
                previous_status = str(
                    self.problem_previous_active_state.get("status") or ""
                ).casefold()
                previous_pid = self.problem_previous_active_state.get("pid")
                if previous_status in {"starting", "running"}:
                    still_running = self._is_pid_running(previous_pid)
                    self.record_problem(
                        (
                            "Обнаружена другая активная сессия программы"
                            if still_running
                            else "Предыдущая сессия завершилась без штатного финального состояния"
                        ),
                        "INFO" if still_running else "WARNING",
                        (
                            "concurrent_app_session_detected"
                            if still_running
                            else "previous_session_abnormal_termination"
                        ),
                        {
                            "previous_state": self.problem_previous_active_state,
                            "previous_pid_still_running": still_running,
                        },
                    )
            self.write_problem_session_summary(
                "ready",
                {"diagnostics_initialized": True},
            )
        except Exception as e:
            self._write_emergency_problem_log(
                "initialize_problem_diagnostics_failed", e
            )


    def finalize_problem_diagnostics(self, status: str = "finished",
                                     stage: str = "application_closed",
                                     context: Optional[Dict] = None) -> None:
        if self.problem_runtime_terminal or not self.should_write_problem_logs():
            return
        try:
            final_context = {
                "runtime_elapsed_sec": round(
                    time.time() - self.problem_runtime_started_unix, 3
                ),
                "last_event_id": self.problem_last_event_id,
                **(context or {}),
            }
            self.write_problem_session_summary(status, final_context)
            self._write_active_run_state(
                status, stage, final_context, terminal=True
            )
        except Exception as e:
            self._write_emergency_problem_log(
                "finalize_problem_diagnostics_failed", e,
                {"status": status, "stage": stage},
            )
