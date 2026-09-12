"""Инициализация mutable-state приложения.

Разделено на диагностику и обычный runtime, чтобы Codex не читал большой
`VideoDownloader.__init__` при каждом изменении конкретной подсистемы.
"""

from collections import Counter
from typing import Dict
from threading import Event
from typing import List
from threading import Lock
from typing import Optional
from pathlib import Path
from typing import Set
from typing import Tuple
from datetime import datetime
import os
import platform
import queue
import subprocess
import threading
import time
import tkinter as tk

from src.core.concurrency import SafeCounter, ThreadSafeList


def initialize_diagnostics_state(app) -> None:
    app.problem_log_lock = Lock()
    app.problem_atomic_write_lock = threading.RLock()
    app.problem_jsonl_write_lock = threading.RLock()
    app.problem_log_session_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_pid{os.getpid()}"
    app.problem_log_sequence = 0
    app.problem_unresolved_entries: Dict[str, Dict] = {}
    app.problem_resolution_count = 0
    app.problem_last_resolution: Optional[Dict] = None
    app.problem_log_environment_snapshot = None
    app.problem_incident_records: Dict[str, Dict] = {}
    app.problem_signature_counts: Counter = Counter()
    app.problem_error_signature_counts: Counter = Counter()
    app.problem_level_counts: Counter = Counter()
    app.problem_operation_counts: Counter = Counter()
    app.problem_strategy_metrics: Dict[str, Counter] = {}
    app.problem_duration_samples: List[float] = []
    app.problem_full_detail_count = 0
    app.problem_suppressed_detail_count = 0
    app.problem_unique_attachment_blob_count = 0
    app.problem_reused_attachment_blob_count = 0
    app.problem_schema_validation_failures = 0
    app.problem_last_disk_validation_status: Optional[str] = None
    app.problem_health_written_keys: Set[str] = set()
    app.problem_last_completion_context: Dict = {}
    app.problem_last_event_id: Optional[str] = None
    app.problem_last_event_by_task: Dict[str, str] = {}
    app.problem_runtime_started_at = datetime.now().isoformat(timespec="seconds")
    app.problem_runtime_started_unix = time.time()
    app.problem_runtime_terminal = False
    app.problem_diagnostics_initialized = False
    app.problem_previous_active_state: Optional[Dict] = app._read_json_object(
        app.problem_active_run_state_file
    )
    app.problem_original_sys_excepthook = None
    app.problem_original_threading_excepthook = None
    app.problem_original_unraisablehook = None
    app.problem_original_tk_report_callback_exception = None
    app.problem_exception_hook_lock = Lock()
    app.retention_cleanup_errors: List[str] = []
    app.retention_cleanup_count = app.cleanup_old_logs()


def initialize_platform_state(app) -> None:
    app.subprocess_flags = (
        subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
    )


def initialize_runtime_state(app) -> None:
    # Очередь батч-сброса UI-логов.
    app._log_queue: List[Tuple[str, str, bool, Optional[str]]] = []
    app._log_flush_pending = False
    app._log_lock = Lock()

    # Загрузка и внешние процессы.
    app.download_queue = queue.Queue()
    app.is_downloading = False
    app.cancel_flag = Event()
    app.download_threads = ThreadSafeList()
    app.active_processes: Dict[int, subprocess.Popen] = {}
    app.ytdlp_runtime: Dict[int, Dict] = {}
    app.ytdlp_runtime_lock = Lock()
    app.max_concurrent = 2
    app.active_downloads = {}
    app.total_files = SafeCounter(0)
    app.completed_files = SafeCounter(0)

    app.downloaded_url_hashes: Set[str] = set()
    app.downloaded_video_ids: Set[str] = set()

    app.thread_lock = Lock()
    app.file_lock = Lock()
    app.progress_lock = Lock()
    app.list_lock = Lock()
    app.process_lock = Lock()
    app.youtube_download_lock = Lock()
    app.youtube_strategy_state_lock = Lock()
    app.youtube_primary_auth_failure_count = 0
    app.youtube_primary_success_count = 0
    app.youtube_adaptive_strategy_logged = False
    app.aria2c_youtube_skip_logged = False

    app.current_session_start = None
    app.current_history_session_file: Optional[Path] = None
    app.current_download_log_session_file: Optional[Path] = None
    app.current_session_download_count = 0
    app.download_start_time = None

    app.video_list = []
    app.video_checkboxes = []
    app.show_downloaded = tk.BooleanVar(value=True)
