"""Пути, каталоги и legacy-миграции приложения.

Owner-файл для вопросов «где хранится X?» и переименования служебных папок.
Не содержит логики скачивания или UI.
"""

from typing import List
from pathlib import Path
from datetime import datetime
import os


def initialize_app_paths(app) -> None:
    """Создать все пути приложения и выполнить безопасные legacy-миграции."""
    app.app_dir = Path(__file__).resolve().parents[2]
    app.settings_file = app.app_dir / "Настройки" / "settings.json"
    app.log_dir = app.app_dir / "Логи скачивания"
    app.log_file = app.log_dir / "download_log.txt"
    app.video_ids_file = app.log_dir / "video_ids.txt"
    app.downloaded_sessions_dir = app.log_dir / "Сессии скачанных"
    app.history_dir = app.app_dir / "История ссылок"
    app.download_links_dir = app.app_dir / "Ссылки на скачивания"
    app.manual_processing_dir = app.app_dir / "Обработать вручную"
    app.history_file = app.history_dir / "downloaded_history.txt"
    app.problem_log_dir = app.app_dir / "Логи проблем"
    app.app_session_timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')

    app.problem_sessions_dir = app.problem_log_dir / "Сессии"
    problem_session_name = f"{app.app_session_timestamp}_pid{os.getpid()}"
    app.problem_session_dir = app.problem_sessions_dir / problem_session_name
    session_suffix = 2
    while app.problem_session_dir.exists():
        app.problem_session_dir = app.problem_sessions_dir / (
            f"{problem_session_name}_{session_suffix}"
        )
        session_suffix += 1

    app.problem_log_file = app.problem_session_dir / "ai_problem_log.jsonl"
    app.problem_events_file = app.problem_session_dir / "events.jsonl"
    app.problem_report_file = app.problem_session_dir / "problem_report.txt"
    app.problem_latest_json_file = app.problem_session_dir / "latest_problem_snapshot.json"
    app.problem_session_summary_file = app.problem_session_dir / "session_summary.json"
    app.problem_session_summary_md_file = app.problem_session_dir / "session_summary.md"
    app.problem_incidents_file = app.problem_session_dir / "incidents.jsonl"
    app.problem_manifest_file = app.problem_session_dir / "manifest.json"
    app.problem_validation_report_file = app.problem_session_dir / "validation_report.json"
    app.problem_attachments_dir = app.problem_session_dir / "attachments"
    app.problem_session_active_state_file = app.problem_session_dir / "active_run_state.json"
    app.problem_latest_session_index_file = app.problem_log_dir / "latest_session.json"
    app.problem_latest_run_index_file = app.problem_log_dir / "latest_run.json"
    app.problem_latest_unresolved_index_file = app.problem_log_dir / "latest_unresolved_problem.json"
    app.problem_active_run_state_file = app.problem_log_dir / "active_run_state.json"
    app.problem_health_history_file = app.problem_log_dir / "health_history.jsonl"
    app.problem_readme_file = app.problem_log_dir / "README_FOR_CODEX.md"
    app.problem_schema_file = app.problem_log_dir / "log_schema.json"
    app.problem_emergency_log_file = app.problem_log_dir / "emergency_problem_log.jsonl"
    app.problem_session_log_file = app.problem_log_file
    app.problem_session_report_file = app.problem_report_file
    app.problem_session_latest_json_file = app.problem_latest_json_file
    app.debug_sessions_dir = app.log_dir / "Сессии запусков"
    app.debug_session_log_file = app.debug_sessions_dir / (
        f"app_debug_{app.problem_session_dir.name}.log"
    )
    app.problem_debug_session_log_file = app.problem_session_dir / "app_debug.log"

    # Флаг нужен до полной загрузки UI и setup_logger().
    app.problem_logging_enabled = app.read_problem_logging_setting_default(True)

    app.legacy_folder_migrations = [
        (app.app_dir / "Settings", app.settings_file.parent),
        (app.app_dir / "Log_download", app.log_dir),
        (app.log_dir / "Downloaded_Sessions", app.downloaded_sessions_dir),
        (app.app_dir / "History_Links", app.history_dir),
        (app.app_dir / "Failed_Downloads", app.manual_processing_dir),
        (app.app_dir / "Неудачные загрузки", app.manual_processing_dir),
        (app.app_dir / "Не конвертировалось", app.manual_processing_dir),
        (app.app_dir / "Проблемно скачиваемые видео", app.manual_processing_dir),
        (app.app_dir / "Problem_Logs", app.problem_log_dir),
    ]
    app.migration_notes: List[str] = []
    app.migrate_legacy_folders()

    app.log_dir.mkdir(parents=True, exist_ok=True)
    app.downloaded_sessions_dir.mkdir(parents=True, exist_ok=True)
    app.history_dir.mkdir(parents=True, exist_ok=True)
    app.download_links_dir.mkdir(parents=True, exist_ok=True)
    app.manual_processing_dir.mkdir(parents=True, exist_ok=True)
    app.settings_file.parent.mkdir(parents=True, exist_ok=True)
    if app.problem_logging_enabled:
        app.ensure_problem_log_dirs()
    app.debug_sessions_dir.mkdir(parents=True, exist_ok=True)
