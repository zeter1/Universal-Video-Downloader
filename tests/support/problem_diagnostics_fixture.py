"""Shared fixture for problem-diagnostics tests."""

import logging
import threading
import time
from collections import Counter
from pathlib import Path

from src.application import VideoDownloader
from src.core.concurrency import SafeCounter, ThreadSafeList


class BoolVarStub:
    def get(self):
        return True


def make_diagnostic_app(root: Path):
    app = VideoDownloader.__new__(VideoDownloader)
    problem_root = root / "problem_logs"
    session_dir = problem_root / "sessions" / "2026-08-03_12-00-00_pid1"
    paths = {
        "app_dir": root,
        "problem_log_dir": problem_root,
        "problem_sessions_dir": problem_root / "sessions",
        "problem_session_dir": session_dir,
        "problem_log_file": session_dir / "ai_problem_log.jsonl",
        "problem_events_file": session_dir / "events.jsonl",
        "problem_session_log_file": session_dir / "ai_problem_log.jsonl",
        "problem_report_file": session_dir / "problem_report.txt",
        "problem_latest_json_file": session_dir / "latest_problem_snapshot.json",
        "problem_session_summary_file": session_dir / "session_summary.json",
        "problem_session_summary_md_file": session_dir / "session_summary.md",
        "problem_incidents_file": session_dir / "incidents.jsonl",
        "problem_manifest_file": session_dir / "manifest.json",
        "problem_validation_report_file": session_dir / "validation_report.json",
        "problem_attachments_dir": session_dir / "attachments",
        "problem_session_active_state_file": session_dir / "active_run_state.json",
        "problem_latest_session_index_file": problem_root / "latest_session.json",
        "problem_latest_run_index_file": problem_root / "latest_run.json",
        "problem_latest_unresolved_index_file": (
            problem_root / "latest_unresolved_problem.json"
        ),
        "problem_active_run_state_file": problem_root / "active_run_state.json",
        "problem_health_history_file": problem_root / "health_history.jsonl",
        "problem_readme_file": problem_root / "README_FOR_CODEX.md",
        "problem_schema_file": problem_root / "log_schema.json",
        "problem_emergency_log_file": problem_root / "emergency_problem_log.jsonl",
        "problem_debug_session_log_file": session_dir / "app_debug.log",
        "debug_session_log_file": root / "debug.log",
        "settings_file": root / "settings.json",
        "log_dir": root / "logs",
        "download_links_dir": root / "links",
        "manual_processing_dir": root / "manual",
    }
    state = {
        "problem_log_lock": threading.Lock(),
        "problem_atomic_write_lock": threading.RLock(),
        "problem_jsonl_write_lock": threading.RLock(),
        "problem_log_session_id": "diagnostic-test",
        "problem_log_sequence": 0,
        "problem_unresolved_entries": {},
        "problem_resolution_count": 0,
        "problem_last_resolution": None,
        "problem_log_environment_snapshot": {"test": True},
        "problem_incident_records": {},
        "problem_signature_counts": Counter(),
        "problem_error_signature_counts": Counter(),
        "problem_level_counts": Counter(),
        "problem_operation_counts": Counter(),
        "problem_strategy_metrics": {},
        "problem_duration_samples": [],
        "problem_full_detail_count": 0,
        "problem_suppressed_detail_count": 0,
        "problem_unique_attachment_blob_count": 0,
        "problem_reused_attachment_blob_count": 0,
        "problem_schema_validation_failures": 0,
        "problem_last_disk_validation_status": None,
        "problem_health_written_keys": set(),
        "problem_last_completion_context": {},
        "problem_last_event_id": None,
        "problem_last_event_by_task": {},
        "problem_runtime_started_at": "2026-08-03T12:00:00",
        "problem_runtime_started_unix": time.time(),
        "problem_runtime_terminal": False,
        "problem_previous_active_state": None,
        "problem_logging_enabled": True,
        "settings": {"write_problem_logs": True, "api_token": "TOPSECRET"},
        "write_problem_logs": BoolVarStub(),
        "current_session_start": "2026-08-03 12:00:00",
        "current_history_session_file": None,
        "current_download_log_session_file": None,
        "current_session_download_count": 0,
        "download_start_time": time.time(),
        "is_downloading": True,
        "cancel_flag": threading.Event(),
        "active_processes": {},
        "process_lock": threading.Lock(),
        "ytdlp_runtime": {},
        "ytdlp_runtime_lock": threading.Lock(),
        "download_threads": ThreadSafeList(),
        "downloaded_url_hashes": set(),
        "downloaded_video_ids": set(),
        "total_files": SafeCounter(2),
        "completed_files": SafeCounter(0),
        "max_concurrent": 2,
        "file_logger": logging.getLogger("problem-diagnostics-test"),
        "subprocess_flags": 0,
        "problem_exception_hook_lock": threading.Lock(),
        "retention_cleanup_errors": [],
    }
    for key, value in {**paths, **state}.items():
        setattr(app, key, value)
    app.ensure_problem_log_dirs()
    app._write_problem_format_files()
    return app
