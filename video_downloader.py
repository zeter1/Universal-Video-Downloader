import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog, messagebox
import threading
import queue
import io
import os
import re
import subprocess
import json
import time
import sys
import shutil
import webbrowser
import traceback
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
import urllib.request
import socket
import platform
import logging
from logging.handlers import RotatingFileHandler
from typing import List, Dict, Tuple, Optional, Set
import hashlib
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock, Event
import urllib.parse
import html as html_module
from collections import Counter
import gzip
import zipfile
from problem_log_validator import validate_session_logs


class CommandCancelledError(Exception):
    """Команда остановлена пользователем через кнопку отмены."""
    pass


class ProblematicDownloadSkipped(Exception):
    """Видео пропущено как проблемно/слишком медленно скачиваемое."""

    def __init__(self, url: str, reason: str, context: Optional[Dict] = None):
        super().__init__(reason)
        self.url = url
        self.reason = reason
        self.context = context or {}

    def to_log_item(self) -> Dict:
        return {
            "url": self.url,
            "reason": self.reason,
            "context": self.context,
        }


# ─────────────────────────────────────────────
# КОНСТАНТЫ (FIX #1: убраны магические числа)
# ─────────────────────────────────────────────
MAX_LOG_LINES        = 1000
APP_VERSION          = "5.8 VPN SMART"
MAX_VIDEOS_PER_LIST  = 1000
MAX_CONCURRENT_DL    = 2
MAX_SPLIT_HOURS      = 10
MAX_URLS_PER_SESSION = 500
DOWNLOAD_TIMEOUT_SEC = 3600   # 1 час
MERGE_TIMEOUT_SEC    = 3600   # 1 час
SPLIT_TIMEOUT_SEC    = 1800   # 30 минут
CONVERT_TIMEOUT_SEC  = 600    # 10 минут
FETCH_TITLE_TIMEOUT  = 10
LOG_FLUSH_INTERVAL   = 50     # мс — интервал батч-сброса лога в UI
DOWNLOAD_HISTORY_RETENTION_DAYS = 60
PROBLEM_LOG_RETENTION_DAYS = 120
MAX_DOWNLOAD_LINK_SESSIONS = 40  # сколько последних сессий очереди ссылок хранить
LOG_ENTRY_SPACING_PX = 4      # вертикальный отступ между строками лога
AUDIO_DURATION_TOLERANCE_SEC = 1.0  # если mp3 короче исходника на 1+ сек — считаем проблемой
MIN_AUDIO_FILE_SIZE_BYTES = 1000
MIN_VIDEO_FILE_SIZE_BYTES = 1000
SUPPORTED_VIDEO_EXTENSIONS = ("mp4", "webm", "mkv", "m4v", "mov")
PROBLEM_LOG_SCHEMA_VERSION = 4
PROBLEM_LOG_HEAD_CHARS = 1200
PROBLEM_LOG_TAIL_CHARS = 4000
PROBLEM_LOG_RECENT_LINES = 30
PROBLEM_LOG_MAX_DETAILS_PER_SIGNATURE = 20
PROBLEM_LOG_MAX_COMMAND_ARGS = 160
PROBLEM_LOG_MAX_TOTAL_BYTES = 250 * 1024 * 1024
PROBLEM_LOG_MIN_SESSIONS_TO_KEEP = 10
PROBLEM_HEALTH_HISTORY_MAX_LINES = 500
PROBLEM_LOG_FORMAT_VERSION = "4.0"
PROBLEM_LOG_COMPRESSION_AFTER_DAYS = 30
PROBLEM_LOG_COMPRESSION_MIN_BYTES = 4096
PROBLEM_LOG_SESSION_ARCHIVE_AFTER_DAYS = 60
PROBLEM_LOG_ATOMIC_REPLACE_RETRIES = 5
PROBLEM_LOG_ATOMIC_RETRY_BASE_DELAY_SEC = 0.04
DEBUG_LOG_MAX_BYTES = 5 * 1024 * 1024
DEBUG_LOG_BACKUP_COUNT = 2

# Если видео через VPN/CDN YouTube качается часами рывками, не мучаем всю очередь:
# пропускаем такой ролик и записываем ссылку в отдельную папку
# «Обработать вручную». Эти пороги специально мягкие: обычное
# короткое зависание уйдёт в fallback, а реально безнадёжные 5-7% за 30 минут
# будут быстро вынесены в отдельный список для повторной докачки позже.
PROBLEMATIC_DOWNLOAD_MIN_ELAPSED_SEC = 15 * 60
PROBLEMATIC_DOWNLOAD_LOW_PROGRESS_PERCENT = 12.0
PROBLEMATIC_DOWNLOAD_LOW_FRAGMENT_RATIO = 0.12
PROBLEMATIC_DOWNLOAD_ZERO_SPEED_SKIP_SEC = 5 * 60

# Устойчивый режим yt-dlp для нестабильной сети/YouTube CDN.
# По логам проблема была в Read timed out с googlevideo.com: раньше программа
# давала всего 1 retry, 10 секунд socket-timeout и прекращала перебор после
# 4 сетевых сбоев. Из-за этого даже временные обрывы провайдера превращались
# в окончательную ошибку скачивания.
# Быстрый режим сначала, устойчивые fallback-стратегии потом.
# В прошлой версии первая же стратегия была слишком осторожной:
# chunk=1M + concurrent-fragments=1 + общий YouTube-lock на 1 поток.
# Из-за этого 76 роликов выглядели как «зависли на ожидании слота».
# Важно: при проблемах с googlevideo.com длинные retry внутри одной попытки
# держали видео по 2-3 минуты до перехода к следующей стратегии.
# Лучше быстрее переключать стратегию, чем долго долбить один и тот же CDN-хост.
YT_DLP_RETRIES = 3
YT_DLP_FRAGMENT_RETRIES = 3
YT_DLP_EXTRACTOR_RETRIES = 2
YT_DLP_SOCKET_TIMEOUT = 30
# Медленный fallback для случаев, когда googlevideo.com не заблокирован полностью,
# а отдаёт данные с очень большой задержкой. Обычные стратегии остаются быстрыми,
# а эта включается только ближе к концу перебора.
YT_DLP_SLOW_SOCKET_TIMEOUT = 90
YT_DLP_SLOW_RETRIES = 2
YT_DLP_SLOW_FRAGMENT_RETRIES = 2
DEFAULT_PROXY_EXAMPLE = "socks5://127.0.0.1:1080"
YT_DLP_NETWORK_RETRY_PAUSE_SEC = 2
YT_DLP_SLOW_NETWORK_FAILURE_SEC = 90
YT_DLP_MAX_NETWORK_FAILURES_PER_VIDEO = 999
YT_DLP_MAX_SLOW_NETWORK_FAILURES_PER_VIDEO = 999
YT_DLP_FAST_FAIL_ENABLED = False
YT_DLP_ADAPTIVE_PRIMARY_FAILURE_THRESHOLD = 3
# По свежим логам параллельные фрагменты + chunk=8M на YouTube давали
# Connection timed out / SSL EOF от googlevideo.com. Поэтому первая попытка
# теперь тоже щадящая: 1 fragment, маленький chunk. Параллельность остаётся
# на уровне видео через настройку «Потоков», а не внутри каждого видео.
YT_DLP_HTTP_CHUNK_SIZE = "4M"
YT_DLP_SAFE_HTTP_CHUNK_SIZE = "1M"
YT_DLP_TINY_HTTP_CHUNK_SIZE = "256K"
YT_DLP_FAST_CONCURRENT_FRAGMENTS = "2"
YT_DLP_SAFE_CONCURRENT_FRAGMENTS = "1"
YT_DLP_EJS_GITHUB_COMPONENT = "ejs:github"
YT_DLP_EJS_NPM_COMPONENT = "ejs:npm"
# Диагностика зависаний yt-dlp: теперь программа пишет подробные
# heartbeat-снимки даже если yt-dlp не вернул ошибку и просто долго молчит.
# Чтобы папка «Логи проблем» не разрасталась на десятки мегабайт,
# подробные снимки пишутся только для WARNING/ERROR. Информационные события
# yt-dlp сохраняются компактно: без повторения окружения, PATH, всех папок и
# огромных хвостов прогресса.
YT_DLP_HEARTBEAT_INTERVAL_SEC = 60
YT_DLP_INACTIVITY_TIMEOUT_SEC = 240
# Дополнительный watchdog реального прогресса. yt-dlp может постоянно писать
# строки с 0.00B/s и ETA Unknown, из-за чего старый watchdog считал процесс
# живым. Теперь попытка перезапускается, если процент/фрагмент не меняется
# слишком долго, даже если вывод продолжает идти.
YT_DLP_PROGRESS_STALL_TIMEOUT_SEC = 240
YT_DLP_ZERO_SPEED_STALL_TIMEOUT_SEC = 180
YT_DLP_PROGRESS_MIN_PERCENT_DELTA = 0.05
YT_DLP_PROGRESS_LOG_INTERVAL_SEC = 25
YT_DLP_RUNTIME_TAIL_LINES = 25
YT_DLP_RUNTIME_MAX_CAPTURED_LINES = 250
PROBLEM_LOG_COMPACT_INFO = True
PROBLEM_LOG_WRITE_INFO_TEXT_REPORTS = False
PROBLEM_LOG_INFO_STDOUT_TAIL_CHARS = 1200
PROBLEM_LOG_INFO_CONTEXT_TEXT_CHARS = 1200
PROBLEM_LOG_INFO_LIST_LIMIT = 10
PROBLEM_LOG_INFO_FILE_SNAPSHOT_LIMIT = 5


class ThreadSafeList:
    """Потокобезопасный список"""
    def __init__(self):
        self._list = []
        self._lock = Lock()

    def append(self, item):
        with self._lock:
            self._list.append(item)

    def remove(self, item):
        with self._lock:
            if item in self._list:
                self._list.remove(item)

    def __len__(self):
        with self._lock:
            return len(self._list)

    def __iter__(self):
        with self._lock:
            return iter(self._list.copy())

    def clear(self):
        with self._lock:
            self._list.clear()

    def __getitem__(self, index):
        with self._lock:
            return self._list[index]

    def copy(self):
        with self._lock:
            return self._list.copy()


class SafeCounter:
    """Потокобезопасный счётчик"""
    def __init__(self, initial=0):
        self._value = initial
        self._lock = Lock()

    def increment(self, amount=1):
        with self._lock:
            self._value += amount
            return self._value

    def value(self):
        with self._lock:
            return self._value

    def reset(self, val=0):
        with self._lock:
            self._value = val


class VideoDownloader:
    def __init__(self, root):
        self.root = root
        self.root.title(f"Универсальный видео-загрузчик v{APP_VERSION}")
        self.root.state('zoomed')

        # Пути к файлам всегда считаются от папки программы, а не от cwd запуска.
        self.app_dir = Path(__file__).resolve().parent
        self.settings_file  = self.app_dir / "Настройки" / "settings.json"
        self.log_dir        = self.app_dir / "Логи скачивания"
        self.log_file       = self.log_dir / "download_log.txt"
        self.video_ids_file = self.log_dir / "video_ids.txt"
        self.downloaded_sessions_dir = self.log_dir / "Сессии скачанных"
        self.history_dir    = self.app_dir / "История ссылок"
        self.download_links_dir = self.app_dir / "Ссылки на скачивания"
        self.manual_processing_dir = self.app_dir / "Обработать вручную"
        self.history_file   = self.history_dir / "downloaded_history.txt"
        self.problem_log_dir = self.app_dir / "Логи проблем"
        self.app_session_timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        # Подробные логи пишутся в «Логи проблем/Сессии/<дата_запуска>».
        # В корне остаются только два маленьких атомарных индекса: последняя
        # сессия и последняя нерешённая проблема.
        self.problem_sessions_dir = self.problem_log_dir / "Сессии"
        problem_session_name = f"{self.app_session_timestamp}_pid{os.getpid()}"
        self.problem_session_dir = self.problem_sessions_dir / problem_session_name
        session_suffix = 2
        while self.problem_session_dir.exists():
            self.problem_session_dir = self.problem_sessions_dir / (
                f"{problem_session_name}_{session_suffix}"
            )
            session_suffix += 1
        self.problem_log_file = self.problem_session_dir / "ai_problem_log.jsonl"
        self.problem_events_file = self.problem_session_dir / "events.jsonl"
        self.problem_report_file = self.problem_session_dir / "problem_report.txt"
        self.problem_latest_json_file = self.problem_session_dir / "latest_problem_snapshot.json"
        self.problem_session_summary_file = self.problem_session_dir / "session_summary.json"
        self.problem_session_summary_md_file = self.problem_session_dir / "session_summary.md"
        self.problem_incidents_file = self.problem_session_dir / "incidents.jsonl"
        self.problem_manifest_file = self.problem_session_dir / "manifest.json"
        self.problem_validation_report_file = (
            self.problem_session_dir / "validation_report.json"
        )
        self.problem_attachments_dir = self.problem_session_dir / "attachments"
        self.problem_session_active_state_file = (
            self.problem_session_dir / "active_run_state.json"
        )
        self.problem_latest_session_index_file = self.problem_log_dir / "latest_session.json"
        self.problem_latest_run_index_file = self.problem_log_dir / "latest_run.json"
        self.problem_latest_unresolved_index_file = (
            self.problem_log_dir / "latest_unresolved_problem.json"
        )
        self.problem_active_run_state_file = self.problem_log_dir / "active_run_state.json"
        self.problem_health_history_file = self.problem_log_dir / "health_history.jsonl"
        self.problem_readme_file = self.problem_log_dir / "README_FOR_CODEX.md"
        self.problem_schema_file = self.problem_log_dir / "log_schema.json"
        self.problem_emergency_log_file = self.problem_log_dir / "emergency_problem_log.jsonl"
        self.problem_session_log_file = self.problem_log_file
        self.problem_session_report_file = self.problem_report_file
        self.problem_session_latest_json_file = self.problem_latest_json_file
        self.debug_sessions_dir = self.log_dir / "Сессии запусков"
        self.debug_session_log_file = self.debug_sessions_dir / (
            f"app_debug_{self.problem_session_dir.name}.log"
        )
        self.problem_debug_session_log_file = self.problem_session_dir / "app_debug.log"
        # Галочка "Писать логи проблем" должна работать ещё до полной загрузки UI.
        # Поэтому читаем только этот флаг напрямую из settings.json как можно раньше.
        self.problem_logging_enabled = self.read_problem_logging_setting_default(True)

        # Совместимость со старыми версиями: служебные папки переименовываем,
        # а три старые папки с проблемными элементами безопасно объединяем
        # в единую папку «Обработать вручную».
        self.legacy_folder_migrations = [
            (self.app_dir / "Settings", self.settings_file.parent),
            (self.app_dir / "Log_download", self.log_dir),
            (self.log_dir / "Downloaded_Sessions", self.downloaded_sessions_dir),
            (self.app_dir / "History_Links", self.history_dir),
            (self.app_dir / "Failed_Downloads", self.manual_processing_dir),
            (self.app_dir / "Неудачные загрузки", self.manual_processing_dir),
            (self.app_dir / "Не конвертировалось", self.manual_processing_dir),
            (self.app_dir / "Проблемно скачиваемые видео", self.manual_processing_dir),
            (self.app_dir / "Problem_Logs", self.problem_log_dir),
        ]
        self.migration_notes: List[str] = []
        self.migrate_legacy_folders()

        # Создание директорий (FIX #2: parents=True — не падаем если нет промежуточных)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.downloaded_sessions_dir.mkdir(parents=True, exist_ok=True)
        self.history_dir.mkdir(parents=True, exist_ok=True)
        self.download_links_dir.mkdir(parents=True, exist_ok=True)
        self.manual_processing_dir.mkdir(parents=True, exist_ok=True)
        self.settings_file.parent.mkdir(parents=True, exist_ok=True)
        if self.problem_logging_enabled:
            self.ensure_problem_log_dirs()
        self.debug_sessions_dir.mkdir(parents=True, exist_ok=True)
        self.problem_log_lock = Lock()
        self.problem_atomic_write_lock = threading.RLock()
        self.problem_jsonl_write_lock = threading.RLock()
        self.problem_log_session_id = (
            f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_pid{os.getpid()}"
        )
        self.problem_log_sequence = 0
        self.problem_unresolved_entries: Dict[str, Dict] = {}
        self.problem_resolution_count = 0
        self.problem_last_resolution: Optional[Dict] = None
        self.problem_log_environment_snapshot = None
        self.problem_incident_records: Dict[str, Dict] = {}
        self.problem_signature_counts: Counter = Counter()
        self.problem_error_signature_counts: Counter = Counter()
        self.problem_level_counts: Counter = Counter()
        self.problem_operation_counts: Counter = Counter()
        self.problem_strategy_metrics: Dict[str, Counter] = {}
        self.problem_duration_samples: List[float] = []
        self.problem_full_detail_count = 0
        self.problem_suppressed_detail_count = 0
        self.problem_unique_attachment_blob_count = 0
        self.problem_reused_attachment_blob_count = 0
        self.problem_schema_validation_failures = 0
        self.problem_last_disk_validation_status: Optional[str] = None
        self.problem_health_written_keys: Set[str] = set()
        self.problem_last_completion_context: Dict = {}
        self.problem_last_event_id: Optional[str] = None
        self.problem_last_event_by_task: Dict[str, str] = {}
        self.problem_runtime_started_at = datetime.now().isoformat(timespec="seconds")
        self.problem_runtime_started_unix = time.time()
        self.problem_runtime_terminal = False
        self.problem_diagnostics_initialized = False
        self.problem_previous_active_state: Optional[Dict] = self._read_json_object(
            self.problem_active_run_state_file
        )
        self.problem_original_sys_excepthook = None
        self.problem_original_threading_excepthook = None
        self.problem_original_unraisablehook = None
        self.problem_original_tk_report_callback_exception = None
        self.problem_exception_hook_lock = Lock()
        self.retention_cleanup_errors: List[str] = []
        self.retention_cleanup_count = self.cleanup_old_logs()

        # FIX #3: subprocess_flags вынесен в одно место вместо 10+ дублирований
        self.subprocess_flags = (
            subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0
        )

        # Настройка логгера
        self.setup_logger()

        # FIX #4: лог-очередь для батч-сброса в UI (предотвращает flooding root.after)
        self._log_queue: List[Tuple[str, str, bool]] = []
        self._log_flush_pending = False
        self._log_lock = Lock()

        # Потокобезопасные структуры данных
        self.download_queue = queue.Queue()
        self.is_downloading = False
        self.cancel_flag = Event()
        self.download_threads = ThreadSafeList()
        self.active_processes: Dict[int, subprocess.Popen] = {}
        self.ytdlp_runtime: Dict[int, Dict] = {}
        self.ytdlp_runtime_lock = Lock()
        self.max_concurrent = 2
        self.active_downloads = {}
        self.total_files = SafeCounter(0)
        self.completed_files = SafeCounter(0)

        # Хранение скачанных: URL-хэши + video_id
        self.downloaded_url_hashes: Set[str] = set()
        self.downloaded_video_ids: Set[str] = set()

        self.thread_lock  = Lock()
        self.file_lock    = Lock()
        self.progress_lock = Lock()
        self.list_lock    = Lock()
        self.process_lock = Lock()
        self.youtube_download_lock = Lock()
        self.youtube_strategy_state_lock = Lock()
        self.youtube_primary_auth_failure_count = 0
        self.youtube_primary_success_count = 0
        self.youtube_adaptive_strategy_logged = False
        self.aria2c_youtube_skip_logged = False

        self.current_session_start = None
        self.current_history_session_file: Optional[Path] = None
        self.current_download_log_session_file: Optional[Path] = None
        self.current_session_download_count = 0
        self.download_start_time   = None

        self.video_list = []
        self.video_checkboxes = []
        self.show_downloaded = tk.BooleanVar(value=True)

        self._write_active_run_state("starting", "loading_settings_and_ui")
        self.load_settings()
        self.load_download_log()
        self.check_dependencies()
        self.setup_ui()
        self.initialize_problem_diagnostics()

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        try:
            import signal
            signal.signal(signal.SIGINT, self.signal_handler)
        except ImportError:
            pass

    # ─────────────────────────────────────────────
    # ИНИЦИАЛИЗАЦИЯ
    # ─────────────────────────────────────────────

    def _unique_path_for_migration(self, path: Path) -> Path:
        """Возвращает свободный путь, чтобы при переносе старых файлов не затереть новые."""
        if not path.exists():
            return path
        stem = path.stem
        suffix = path.suffix
        parent = path.parent
        counter = 2
        while True:
            candidate = parent / f"{stem}_из_старой_папки_{counter}{suffix}"
            if not candidate.exists():
                return candidate
            counter += 1

    def _merge_directories_for_migration(self, src: Path, dst: Path) -> None:
        """Аккуратно переносит содержимое старой папки в новую русскую папку."""
        dst.mkdir(parents=True, exist_ok=True)
        for item in src.iterdir():
            target = dst / item.name
            if item.is_dir() and target.exists() and target.is_dir():
                self._merge_directories_for_migration(item, target)
                try:
                    item.rmdir()
                except OSError:
                    pass
                continue

            if target.exists():
                target = self._unique_path_for_migration(target)
            shutil.move(str(item), str(target))

        try:
            src.rmdir()
        except OSError:
            pass

    def migrate_legacy_folders(self) -> None:
        """Переносит старые английские служебные папки в новые русские названия."""
        for old_path, new_path in getattr(self, "legacy_folder_migrations", []):
            try:
                if not old_path.exists():
                    continue
                if old_path.resolve() == new_path.resolve():
                    continue

                if old_path.is_dir():
                    if new_path.exists():
                        self._merge_directories_for_migration(old_path, new_path)
                        self.migration_notes.append(f"{old_path.name} → {new_path.name} (объединено)")
                    else:
                        new_path.parent.mkdir(parents=True, exist_ok=True)
                        old_path.rename(new_path)
                        self.migration_notes.append(f"{old_path.name} → {new_path.name}")
                else:
                    new_path.parent.mkdir(parents=True, exist_ok=True)
                    target = new_path if not new_path.exists() else self._unique_path_for_migration(new_path)
                    shutil.move(str(old_path), str(target))
                    self.migration_notes.append(f"{old_path.name} → {target.name}")
            except Exception as e:
                self.migration_notes.append(f"Не удалось перенести {old_path}: {e}")

    def retention_cutoff(self, days: int = DOWNLOAD_HISTORY_RETENTION_DAYS) -> datetime:
        return datetime.now() - timedelta(days=max(1, int(days)))

    def _remember_retention_error(self, path: Path, error: Exception) -> None:
        errors = getattr(self, "retention_cleanup_errors", None)
        if errors is not None:
            errors.append(f"{path}: {error}")

    def _parse_iso_datetime(self, value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)
            return dt
        except (TypeError, ValueError):
            return None

    def _parse_session_datetime_from_name(self, path: Path) -> Optional[datetime]:
        match = re.search(
            r'(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-(\d{2})',
            path.name
        )
        if not match:
            return None
        try:
            return datetime.strptime(
                f"{match.group(1)} {match.group(2)}:{match.group(3)}:{match.group(4)}",
                "%Y-%m-%d %H:%M:%S"
            )
        except ValueError:
            return None

    def _path_datetime_for_retention(self, path: Path) -> Optional[datetime]:
        parsed = self._parse_session_datetime_from_name(path)
        if parsed:
            return parsed
        try:
            return datetime.fromtimestamp(path.stat().st_mtime)
        except OSError:
            return None

    def is_recent_log_file(self, path: Path, cutoff: Optional[datetime] = None) -> bool:
        cutoff = cutoff or self.retention_cutoff()
        file_dt = self._path_datetime_for_retention(path)
        return file_dt is None or file_dt >= cutoff

    def _delete_old_files(self, directory: Path, patterns: List[str],
                          cutoff: datetime) -> int:
        cleaned = 0
        if not directory.exists():
            return cleaned
        for pattern in patterns:
            for path in directory.glob(pattern):
                if not path.is_file() or self.is_recent_log_file(path, cutoff):
                    continue
                try:
                    path.unlink()
                    cleaned += 1
                except OSError as e:
                    self._remember_retention_error(path, e)
        return cleaned

    def _delete_old_dirs(self, directory: Path, cutoff: datetime) -> int:
        cleaned = 0
        if not directory.exists():
            return cleaned
        for path in directory.iterdir():
            if (
                not path.is_dir()
                or not self._is_recognized_problem_session_dir(path)
                or self.is_recent_log_file(path, cutoff)
            ):
                continue
            try:
                shutil.rmtree(path)
                cleaned += 1
            except OSError as e:
                self._remember_retention_error(path, e)
        return cleaned

    def _is_recognized_problem_session_dir(self, path: Path) -> bool:
        """Не позволяет очистке затронуть посторонние каталоги пользователя."""
        try:
            session_root = self.problem_sessions_dir.resolve()
            resolved = path.resolve()
            if resolved.parent != session_root:
                return False
            # Новые сессии содержат PID, старые имена тоже распознаются.
            return bool(
                re.fullmatch(
                    r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}"
                    r"(?:_pid\d+(?:_\d+)?|_\d+)?",
                    path.name,
                )
            )
        except Exception:
            return False

    def _problem_session_size(self, path: Path) -> int:
        total = 0
        try:
            for item in path.rglob("*"):
                if item.is_file():
                    try:
                        total += item.stat().st_size
                    except OSError:
                        continue
        except OSError:
            return total
        return total

    def _compress_old_problem_attachments(self, cutoff: datetime) -> int:
        """Сжимает старые полные снимки, оставляя по прежнему пути JSON-указатель."""
        root = getattr(self, "problem_sessions_dir", None)
        if not root or not root.exists():
            return 0
        compressed = 0
        for session_dir in root.iterdir():
            if (
                not session_dir.is_dir()
                or session_dir == getattr(self, "problem_session_dir", None)
                or not self._is_recognized_problem_session_dir(session_dir)
            ):
                continue
            try:
                session_dt = datetime.strptime(
                    session_dir.name[:19], "%Y-%m-%d_%H-%M-%S"
                )
            except ValueError:
                continue
            if session_dt >= cutoff:
                continue
            attachments_dir = session_dir / "attachments"
            if not attachments_dir.is_dir():
                continue
            for path in attachments_dir.rglob("*.json"):
                gzip_path = path.with_suffix(path.suffix + ".gz")
                temp_gzip = gzip_path.with_name(
                    f".{gzip_path.name}.{os.getpid()}.tmp"
                )
                try:
                    raw = path.read_bytes()
                    if len(raw) < PROBLEM_LOG_COMPRESSION_MIN_BYTES:
                        continue
                    try:
                        parsed = json.loads(raw.decode("utf-8"))
                        if isinstance(parsed, dict) and parsed.get("compressed_attachment"):
                            continue
                    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
                        continue
                    gzip_matches = False
                    if gzip_path.exists():
                        try:
                            with gzip.open(gzip_path, "rb") as stream:
                                gzip_matches = (
                                    hashlib.sha256(stream.read()).digest()
                                    == hashlib.sha256(raw).digest()
                                )
                        except OSError:
                            gzip_matches = False
                    if not gzip_matches:
                        with gzip.open(temp_gzip, "wb", compresslevel=6) as stream:
                            stream.write(raw)
                        with gzip.open(temp_gzip, "rb") as stream:
                            restored = stream.read()
                        if hashlib.sha256(restored).digest() != hashlib.sha256(raw).digest():
                            raise OSError("gzip verification failed")
                        os.replace(temp_gzip, gzip_path)
                    stub = {
                        "schema_version": PROBLEM_LOG_SCHEMA_VERSION,
                        "compressed_attachment": True,
                        "compression": "gzip",
                        "gzip_path": str(gzip_path),
                        "relative_gzip_path": str(
                            gzip_path.relative_to(session_dir)
                        ),
                        "original_size_bytes": len(raw),
                        "sha256": hashlib.sha256(raw).hexdigest(),
                        "hint_for_codex": (
                            "Полный JSON сжат. Откройте gzip_path и распакуйте как UTF-8 JSON."
                        ),
                    }
                    self._atomic_write_json_file(path, stub)
                    compressed += 1
                except OSError as e:
                    self._remember_retention_error(path, e)
                finally:
                    try:
                        if temp_gzip.exists():
                            temp_gzip.unlink()
                    except OSError:
                        pass
        return compressed

    def _session_archive_source_files(self, session_dir: Path) -> List[Path]:
        """Возвращает только распознанные файлы, которые разрешено архивировать."""
        names = {
            "ai_problem_log.jsonl",
            "events.jsonl",
            "incidents.jsonl",
            "latest_problem_snapshot.json",
            "problem_report.txt",
            "active_run_state.json",
            "app_debug.log",
        }
        files: List[Path] = []
        for name in names:
            path = session_dir / name
            if path.is_file():
                files.append(path)
        files.extend(
            path for path in session_dir.glob("app_debug.log.*")
            if path.is_file()
        )
        attachments = session_dir / "attachments"
        if attachments.is_dir():
            files.extend(
                path for path in attachments.rglob("*") if path.is_file()
            )
        return sorted(
            {str(path.resolve()): path for path in files}.values(),
            key=lambda path: str(path.relative_to(session_dir)).casefold(),
        )

    def _remove_verified_archive_sources(self, session_dir: Path,
                                         files: List[Path]) -> None:
        session_root = session_dir.resolve()
        for path in files:
            try:
                resolved = path.resolve()
                if session_root not in resolved.parents:
                    raise OSError("archive source escaped session directory")
                path.unlink()
            except OSError as e:
                self._remember_retention_error(path, e)
        attachments = session_dir / "attachments"
        try:
            if attachments.exists():
                attachments_resolved = attachments.resolve()
                if (
                    attachments_resolved.parent == session_root
                    and not any(
                        path.is_file() for path in attachments.rglob("*")
                    )
                ):
                    shutil.rmtree(attachments)
        except OSError as e:
            self._remember_retention_error(attachments, e)

    def _archive_old_problem_sessions(self, cutoff: datetime) -> int:
        """Архивирует старую сессию только после проверки каждого SHA-256."""
        root = getattr(self, "problem_sessions_dir", None)
        if not root or not root.exists():
            return 0
        archived_count = 0
        for session_dir in root.iterdir():
            if (
                not session_dir.is_dir()
                or session_dir == getattr(self, "problem_session_dir", None)
                or not self._is_recognized_problem_session_dir(session_dir)
            ):
                continue
            try:
                session_dt = datetime.strptime(
                    session_dir.name[:19], "%Y-%m-%d_%H-%M-%S"
                )
            except ValueError:
                continue
            if session_dt >= cutoff:
                continue

            sources = self._session_archive_source_files(session_dir)
            archive_path = session_dir / "session_archive.zip"
            archive_manifest_path = session_dir / "archive_manifest.json"
            if not sources:
                continue

            # Если архив уже был успешно создан, проверяем оставшиеся после
            # прерванной очистки исходники и только затем удаляем их.
            if archive_path.is_file() and archive_manifest_path.is_file():
                try:
                    with zipfile.ZipFile(archive_path, "r") as archive:
                        if archive.testzip() is not None:
                            raise OSError("existing session archive has bad CRC")
                        for source in sources:
                            member = str(
                                source.relative_to(session_dir)
                            ).replace("\\", "/")
                            archived_digest = hashlib.sha256()
                            with archive.open(member, "r") as archived_stream:
                                for chunk in iter(
                                    lambda: archived_stream.read(1024 * 1024), b""
                                ):
                                    archived_digest.update(chunk)
                            if archived_digest.hexdigest() != self._file_sha256(
                                source
                            ):
                                raise OSError(
                                    f"existing archive mismatch: {member}"
                                )
                    self._remove_verified_archive_sources(
                        session_dir, sources
                    )
                    archived_count += 1
                except (OSError, KeyError, zipfile.BadZipFile) as e:
                    self._remember_retention_error(archive_path, e)
                continue

            temp_archive = archive_path.with_name(
                f".{archive_path.name}.{os.getpid()}.tmp"
            )
            try:
                file_manifest = []
                with zipfile.ZipFile(
                    temp_archive,
                    "w",
                    compression=zipfile.ZIP_DEFLATED,
                    compresslevel=6,
                ) as archive:
                    for source in sources:
                        member = str(
                            source.relative_to(session_dir)
                        ).replace("\\", "/")
                        archive.write(source, member)
                        file_manifest.append({
                            "path": member,
                            "size_bytes": source.stat().st_size,
                            "sha256": self._file_sha256(source),
                        })

                with zipfile.ZipFile(temp_archive, "r") as archive:
                    bad_member = archive.testzip()
                    if bad_member is not None:
                        raise OSError(
                            f"archive CRC verification failed: {bad_member}"
                        )
                    for item in file_manifest:
                        archived_digest = hashlib.sha256()
                        with archive.open(item["path"], "r") as archived_stream:
                            for chunk in iter(
                                lambda: archived_stream.read(1024 * 1024), b""
                            ):
                                archived_digest.update(chunk)
                        if archived_digest.hexdigest() != item["sha256"]:
                            raise OSError(
                                f"archive SHA-256 verification failed: {item['path']}"
                            )

                os.replace(temp_archive, archive_path)
                archive_manifest = {
                    "schema_version": PROBLEM_LOG_SCHEMA_VERSION,
                    "archive_format": "zip",
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                    "session_dir": str(session_dir),
                    "archive_path": str(archive_path),
                    "archive_sha256": self._file_sha256(archive_path),
                    "file_count": len(file_manifest),
                    "files": file_manifest,
                    "kept_outside_archive": [
                        "session_summary.json",
                        "session_summary.md",
                        "manifest.json",
                        "validation_report.json",
                    ],
                    "hint_for_codex": (
                        "Сводка оставлена рядом. Полные старые события, инциденты, "
                        "вложения и debug-лог находятся в session_archive.zip."
                    ),
                }
                self._atomic_write_json_file(
                    archive_manifest_path, archive_manifest
                )
                self._remove_verified_archive_sources(session_dir, sources)
                archived_count += 1
            except (OSError, KeyError, zipfile.BadZipFile) as e:
                self._remember_retention_error(archive_path, e)
            finally:
                try:
                    if temp_archive.exists():
                        temp_archive.unlink()
                except OSError:
                    pass
        return archived_count

    def _enforce_problem_log_size_limit(self) -> int:
        """Удаляет только самые старые распознанные сессии сверх общего лимита."""
        root = getattr(self, "problem_sessions_dir", None)
        if not root or not root.exists():
            return 0
        sessions = [
            path for path in root.iterdir()
            if path.is_dir() and self._is_recognized_problem_session_dir(path)
        ]
        sessions.sort(
            key=lambda path: path.stat().st_mtime if path.exists() else 0,
            reverse=True,
        )
        sizes = {path: self._problem_session_size(path) for path in sessions}
        total = sum(sizes.values())
        removed = 0
        for path in reversed(sessions[PROBLEM_LOG_MIN_SESSIONS_TO_KEEP:]):
            if total <= PROBLEM_LOG_MAX_TOTAL_BYTES:
                break
            if path == getattr(self, "problem_session_dir", None):
                continue
            try:
                shutil.rmtree(path)
                total -= sizes.get(path, 0)
                removed += 1
            except OSError as e:
                self._remember_retention_error(path, e)
        return removed

    def _trim_health_history(self) -> int:
        path = getattr(self, "problem_health_history_file", None)
        if not path or not path.exists():
            return 0
        try:
            raw_lines = [
                line for line in path.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()
                if line.strip()
            ]
            cutoff = self.retention_cutoff(PROBLEM_LOG_RETENTION_DAYS)
            lines = []
            for line in raw_lines:
                try:
                    item = json.loads(line)
                    timestamp = self._parse_iso_datetime(item.get("timestamp"))
                except (TypeError, ValueError, json.JSONDecodeError):
                    timestamp = None
                if timestamp is None or timestamp >= cutoff:
                    lines.append(line)
            lines = lines[-PROBLEM_HEALTH_HISTORY_MAX_LINES:]
            if lines == raw_lines:
                return 0
            self._atomic_write_text_file(
                path,
                "\n".join(lines[-PROBLEM_HEALTH_HISTORY_MAX_LINES:]) + "\n",
            )
            return max(0, len(raw_lines) - len(lines))
        except OSError as e:
            self._remember_retention_error(path, e)
            return 0

    def _delete_legacy_flat_logs(self, paths: List[Path]) -> int:
        cleaned = 0
        for path in paths:
            if not path.exists() or not path.is_file():
                continue
            try:
                path.unlink()
                cleaned += 1
            except OSError as e:
                self._remember_retention_error(path, e)
        return cleaned

    def _rewrite_text_file_if_changed(self, path: Path, old_lines: List[str],
                                      new_lines: List[str]) -> int:
        if old_lines == new_lines:
            return 0
        try:
            if new_lines:
                temp_path = path.with_name(path.name + ".tmp")
                temp_path.write_text(''.join(new_lines), encoding='utf-8')
                temp_path.replace(path)
            else:
                path.unlink()
            return 1
        except OSError as e:
            self._remember_retention_error(path, e)
            return 0

    def _problem_atomic_lock(self):
        """Возвращает общий re-entrant lock даже для облегчённых test-double объектов."""
        lock = getattr(self, "problem_atomic_write_lock", None)
        if lock is None:
            lock = threading.RLock()
            self.problem_atomic_write_lock = lock
        return lock

    def _replace_atomic_temp_file(self, temp_path: Path, path: Path) -> None:
        """Учитывает кратковременную блокировку файла Windows индексатором/другим потоком."""
        for attempt in range(PROBLEM_LOG_ATOMIC_REPLACE_RETRIES):
            try:
                os.replace(temp_path, path)
                return
            except OSError as error:
                winerror = getattr(error, "winerror", None)
                retryable = isinstance(error, PermissionError) or winerror in {5, 32}
                if not retryable or attempt + 1 >= PROBLEM_LOG_ATOMIC_REPLACE_RETRIES:
                    raise
                time.sleep(
                    PROBLEM_LOG_ATOMIC_RETRY_BASE_DELAY_SEC * (attempt + 1)
                )

    def _atomic_write_json_file(self, path: Path, data: Dict) -> None:
        """Потокобезопасно и атомарно записывает JSON рядом с целевым файлом."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._problem_atomic_lock():
            temp_path = path.with_name(
                f".{path.name}.{os.getpid()}.{threading.get_ident()}."
                f"{time.time_ns()}.{random.getrandbits(32):08x}.tmp"
            )
            try:
                with open(temp_path, "w", encoding="utf-8", newline="\n") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                    f.write("\n")
                    f.flush()
                    os.fsync(f.fileno())
                self._replace_atomic_temp_file(temp_path, path)
            finally:
                try:
                    if temp_path.exists():
                        temp_path.unlink()
                except OSError:
                    pass

    def _atomic_write_text_file(self, path: Path, text: str) -> None:
        """Потокобезопасно и атомарно записывает небольшой текстовый отчёт."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._problem_atomic_lock():
            temp_path = path.with_name(
                f".{path.name}.{os.getpid()}.{threading.get_ident()}."
                f"{time.time_ns()}.{random.getrandbits(32):08x}.tmp"
            )
            try:
                with open(temp_path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(text)
                    f.flush()
                    os.fsync(f.fileno())
                self._replace_atomic_temp_file(temp_path, path)
            finally:
                try:
                    if temp_path.exists():
                        temp_path.unlink()
                except OSError:
                    pass

    def _trim_jsonl_log_by_timestamp(self, path: Path, cutoff: datetime) -> int:
        if not path.exists():
            return 0
        try:
            old_lines = path.read_text(encoding='utf-8', errors='replace').splitlines(True)
        except OSError as e:
            self._remember_retention_error(path, e)
            return 0

        keep_unknown = self.is_recent_log_file(path, cutoff)
        new_lines: List[str] = []
        for line in old_lines:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entry = json.loads(stripped)
                entry_dt = self._parse_iso_datetime(entry.get("timestamp"))
            except (TypeError, ValueError, json.JSONDecodeError):
                entry_dt = None

            if (entry_dt and entry_dt >= cutoff) or (entry_dt is None and keep_unknown):
                new_lines.append(line)

        return self._rewrite_text_file_if_changed(path, old_lines, new_lines)

    def _trim_report_log_by_timestamp(self, path: Path, cutoff: datetime) -> int:
        if not path.exists():
            return 0
        try:
            old_lines = path.read_text(encoding='utf-8', errors='replace').splitlines(True)
        except OSError as e:
            self._remember_retention_error(path, e)
            return 0

        blocks: List[List[str]] = []
        current: List[str] = []
        for line in old_lines:
            if line.startswith("=" * 20) and current:
                blocks.append(current)
                current = [line]
            else:
                current.append(line)
        if current:
            blocks.append(current)

        keep_unknown = self.is_recent_log_file(path, cutoff)
        new_lines: List[str] = []
        for block in blocks:
            block_text = ''.join(block)
            if not block_text.strip():
                continue
            match = re.search(r'(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})', block_text)
            block_dt = self._parse_iso_datetime(match.group(1)) if match else None
            if (block_dt and block_dt >= cutoff) or (block_dt is None and keep_unknown):
                new_lines.extend(block)

        return self._rewrite_text_file_if_changed(path, old_lines, new_lines)

    def _trim_debug_log_by_timestamp(self, path: Path, cutoff: datetime) -> int:
        if not path.exists():
            return 0
        try:
            old_lines = path.read_text(encoding='utf-8', errors='replace').splitlines(True)
        except OSError as e:
            self._remember_retention_error(path, e)
            return 0

        new_lines: List[str] = []
        keep_current = self.is_recent_log_file(path, cutoff)
        for line in old_lines:
            match = re.match(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
            if match:
                try:
                    line_dt = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
                    keep_current = line_dt >= cutoff
                except ValueError:
                    keep_current = self.is_recent_log_file(path, cutoff)
            if keep_current:
                new_lines.append(line)

        return self._rewrite_text_file_if_changed(path, old_lines, new_lines)

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

    def read_problem_logging_setting_default(self, default: bool = True) -> bool:
        """Раннее чтение флага логов проблем до полной загрузки настроек и UI."""
        try:
            if self.settings_file.exists():
                with open(self.settings_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                return bool(data.get("write_problem_logs", default))
        except Exception:
            return default
        return default

    def should_write_problem_logs(self) -> bool:
        """Единая проверка: можно ли сейчас писать в папку «Логи проблем»."""
        try:
            var = getattr(self, "write_problem_logs", None)
            if var is not None and hasattr(var, "get"):
                return bool(var.get())
        except Exception:
            pass
        try:
            settings = getattr(self, "settings", None)
            if isinstance(settings, dict) and "write_problem_logs" in settings:
                return bool(settings.get("write_problem_logs", True))
        except Exception:
            pass
        return bool(getattr(self, "problem_logging_enabled", True))

    def ensure_problem_log_dirs(self) -> None:
        """Создаёт папки логов проблем только когда они реально включены."""
        try:
            self.problem_log_dir.mkdir(parents=True, exist_ok=True)
            self.problem_sessions_dir.mkdir(parents=True, exist_ok=True)
            self.problem_session_dir.mkdir(parents=True, exist_ok=True)
            self.problem_attachments_dir.mkdir(parents=True, exist_ok=True)
            for jsonl_path in (
                self.problem_events_file,
                self.problem_log_file,
                self.problem_incidents_file,
            ):
                with open(jsonl_path, "a", encoding="utf-8"):
                    pass
        except Exception:
            pass

    def _is_problem_log_handler(self, handler: logging.Handler) -> bool:
        try:
            filename = Path(getattr(handler, "baseFilename", "")).resolve()
            problem_root = self.problem_log_dir.resolve()
            return str(filename).lower().startswith(str(problem_root).lower())
        except Exception:
            return False

    def _remove_problem_log_handlers(self) -> None:
        """Убирает файловые обработчики, которые пишут внутрь «Логи проблем»."""
        logger = getattr(self, "file_logger", logging.getLogger('VideoDownloader'))
        for handler in list(logger.handlers):
            if self._is_problem_log_handler(handler):
                logger.removeHandler(handler)
                try:
                    handler.close()
                except Exception:
                    pass

    def _add_file_logger_handler(self, log_path: Path, formatter: logging.Formatter) -> None:
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            target = str(log_path.resolve())
            for handler in self.file_logger.handlers:
                try:
                    if str(Path(getattr(handler, "baseFilename", "")).resolve()) == target:
                        return
                except Exception:
                    continue
            fh = RotatingFileHandler(
                log_path,
                maxBytes=DEBUG_LOG_MAX_BYTES,
                backupCount=DEBUG_LOG_BACKUP_COUNT,
                encoding="utf-8",
            )
            fh.setFormatter(formatter)
            self.file_logger.addHandler(fh)
        except Exception:
            pass

    def setup_logger(self) -> None:
        self.file_logger = logging.getLogger('VideoDownloader')
        self.file_logger.setLevel(logging.DEBUG)
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

        # Обычный debug-лог программы всегда остаётся в «Логи скачивания».
        for log_path in (
            self.log_dir / "app_debug.log",
            self.debug_session_log_file,
        ):
            self._add_file_logger_handler(log_path, formatter)

        # Логи в папку «Логи проблем» добавляются только при включённой галочке.
        if self.should_write_problem_logs():
            self.ensure_problem_log_dirs()
            for log_path in (self.problem_debug_session_log_file,):
                self._add_file_logger_handler(log_path, formatter)
        else:
            self._remove_problem_log_handlers()

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
        return self._file_sha256(Path(__file__).resolve())

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

    def _tail_text(self, text: Optional[str], limit: int = PROBLEM_LOG_TAIL_CHARS) -> str:
        if not text:
            return ""
        text = str(text)
        return text[-limit:]

    def _utf8_subprocess_env(self) -> Dict[str, str]:
        """Согласует кодировку Python-утилит с UTF-8-декодированием pipe."""
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        return env

    def mask_proxy_url(self, proxy_url: Optional[str]) -> str:
        """Маскирует логин/пароль в proxy URL перед записью в логи."""
        if not proxy_url:
            return ""
        proxy_url = str(proxy_url).strip()
        try:
            parsed = urllib.parse.urlsplit(proxy_url)
            if not parsed.netloc or "@" not in parsed.netloc:
                return proxy_url
            userinfo, hostinfo = parsed.netloc.rsplit("@", 1)
            if ":" in userinfo:
                username = userinfo.split(":", 1)[0]
                masked_userinfo = f"{username}:***"
            else:
                masked_userinfo = "***"
            return urllib.parse.urlunsplit(
                (parsed.scheme, f"{masked_userinfo}@{hostinfo}", parsed.path, parsed.query, parsed.fragment)
            )
        except Exception:
            return re.sub(r"://([^/@:]+):[^/@]+@", r"://\1:***@", proxy_url)

    def _redacted_value_marker(self, value) -> str:
        digest = hashlib.sha256(
            str(value).encode("utf-8", errors="replace")
        ).hexdigest()[:12]
        return f"<redacted sha256:{digest}>"

    def _is_sensitive_log_key(self, key: str) -> bool:
        normalized = re.sub(r"[^a-z0-9]+", "_", str(key).casefold()).strip("_")
        if normalized.endswith(("_masked", "_hash", "_sha256", "_md5")):
            return False
        return bool(re.search(
            r"(?:^|_)(?:password|passwd|passphrase|secret|token|"
            r"authorization|auth_header|api_key|apikey|cookie|cookies|"
            r"session_cookie|client_secret)(?:_|$)",
            normalized,
        ))

    def _sanitize_url_for_log(self, url: str) -> str:
        """Сохраняет диагностическую часть URL, но удаляет учётные данные и секреты."""
        raw = str(url)
        try:
            parsed = urllib.parse.urlsplit(raw)
            if parsed.scheme not in {"http", "https", "ftp", "socks4", "socks5"}:
                return raw
            host = parsed.hostname or ""
            if parsed.port:
                host = f"{host}:{parsed.port}"
            if parsed.username or parsed.password:
                host = f"***@{host}"
            query_items = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
            safe_query = []
            for key, value in query_items:
                if self._is_sensitive_log_key(key) or key.casefold() in {
                    "sig", "signature", "key", "credential", "access_token",
                }:
                    safe_query.append((
                        key,
                        value if str(value).startswith("<redacted ")
                        else self._redacted_value_marker(value),
                    ))
                else:
                    safe_query.append((key, value))
            fragment = "<redacted>" if parsed.fragment else ""
            return urllib.parse.urlunsplit(
                (
                    parsed.scheme,
                    host,
                    parsed.path,
                    urllib.parse.urlencode(safe_query, doseq=True),
                    fragment,
                )
            )
        except Exception:
            return self.mask_proxy_url(raw)

    def _sanitize_text_for_log(self, text: str) -> str:
        protected_urls: List[str] = []

        def protect_url(match) -> str:
            protected_urls.append(self._sanitize_url_for_log(match.group(0)))
            return f"__CODEX_SAFE_URL_{len(protected_urls) - 1}__"

        safe = re.sub(
            r"(?i)\b(?:https?|ftp|socks[45])://[^\s\"'<>]+",
            protect_url,
            str(text),
        )
        safe = self.mask_proxy_url(safe)
        safe = re.sub(
            r"(?i)\b(authorization\s*[:=]\s*(?:bearer\s+|basic\s+)?)[^\s,;]+",
            r"\1<redacted>",
            safe,
        )
        safe = re.sub(
            r"(?i)\b(password|passwd|passphrase|secret|token|api[_-]?key|cookie)"
            r"(\s*[:=]\s*)[^\s,;]+",
            r"\1\2<redacted>",
            safe,
        )
        for index, safe_url in enumerate(protected_urls):
            safe = safe.replace(f"__CODEX_SAFE_URL_{index}__", safe_url)
        return safe

    def _redact_for_log(self, value, key_hint: str = "", depth: int = 0):
        """Единая рекурсивная очистка данных перед записью в любой диагностический файл."""
        if self._is_sensitive_log_key(key_hint):
            return self._redacted_value_marker(value)
        if depth > 10:
            return self._sanitize_text_for_log(repr(value)[:1000])
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            return self._sanitize_text_for_log(value)
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, dict):
            return {
                str(key): self._redact_for_log(item, str(key), depth + 1)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple, set)):
            return [
                self._redact_for_log(item, key_hint, depth + 1)
                for item in value
            ]
        return value

    def _sanitize_command_args_for_log(self, command: Optional[List[str]]) -> List[str]:
        """Убирает секреты из команды перед попаданием в JSON/текстовые логи."""
        if not command:
            return []
        args = [str(part) for part in command[:PROBLEM_LOG_MAX_COMMAND_ARGS]]
        sanitized: List[str] = []
        value_mode = ""
        secret_flags = {
            "--password", "--video-password", "--username",
            "--cookies", "--cookies-from-browser",
            "--netrc-location", "--client-certificate-key",
        }
        for part in args:
            if value_mode == "proxy":
                sanitized.append(self.mask_proxy_url(part))
                value_mode = ""
                continue
            if value_mode == "secret":
                sanitized.append(self._redacted_value_marker(part))
                value_mode = ""
                continue
            sanitized.append(self._sanitize_text_for_log(part))
            if part == "--proxy":
                value_mode = "proxy"
            elif part in secret_flags:
                value_mode = "secret"
        if len(command) > PROBLEM_LOG_MAX_COMMAND_ARGS:
            sanitized.append(
                f"<omitted {len(command) - PROBLEM_LOG_MAX_COMMAND_ARGS} args>"
            )
        return sanitized

    def _command_to_string(self, command: Optional[List[str]]) -> str:
        if not command:
            return ""
        try:
            return subprocess.list2cmdline(self._sanitize_command_args_for_log(command))
        except Exception:
            return " ".join(self._sanitize_command_args_for_log(command))

    def _json_safe_value(self, value, depth: int = 0, key_hint: str = ""):
        value = self._redact_for_log(value, key_hint)
        if depth > 7:
            return self._sanitize_text_for_log(repr(value))
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            if len(value) <= PROBLEM_LOG_TAIL_CHARS * 2:
                return value
            return {
                "truncated": True,
                "char_count": len(value),
                "head": value[:PROBLEM_LOG_HEAD_CHARS],
                "tail": value[-PROBLEM_LOG_TAIL_CHARS:],
            }
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, datetime):
            return value.isoformat(timespec="seconds")
        if isinstance(value, BaseException):
            return {
                "type": type(value).__name__,
                "message": self._sanitize_text_for_log(str(value)),
            }
        if isinstance(value, dict):
            return {
                str(k): self._json_safe_value(v, depth + 1, str(k))
                for k, v in value.items()
            }
        if isinstance(value, (list, tuple, set)):
            return [self._json_safe_value(v, depth + 1, key_hint) for v in value]
        return self._sanitize_text_for_log(repr(value))

    def _stream_snapshot(self, text: Optional[str]) -> Dict:
        raw = "" if text is None else self._sanitize_text_for_log(str(text))
        lines = raw.splitlines()
        return {
            "char_count": len(raw),
            "line_count": len(lines),
            "was_truncated": len(raw) > (PROBLEM_LOG_HEAD_CHARS + PROBLEM_LOG_TAIL_CHARS),
            "head": raw[:PROBLEM_LOG_HEAD_CHARS],
            "tail": self._tail_text(raw),
            "last_lines": lines[-PROBLEM_LOG_RECENT_LINES:],
        }

    def _path_snapshot(self, path) -> Dict:
        info = {"path": None, "exists": False}
        if path is None:
            return info
        try:
            p = Path(path)
            info["path"] = str(p)
            info["exists"] = p.exists()
            info["is_dir"] = p.is_dir() if info["exists"] else False
            info["is_file"] = p.is_file() if info["exists"] else False
            if info["exists"]:
                stat = p.stat()
                info["size_bytes"] = stat.st_size
                info["mtime"] = datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")
            usage_target = p if p.exists() and p.is_dir() else p.parent
            if usage_target and usage_target.exists():
                usage = shutil.disk_usage(str(usage_target))
                info["disk_total_bytes"] = usage.total
                info["disk_free_bytes"] = usage.free
        except Exception as e:
            info["snapshot_error"] = f"{type(e).__name__}: {e}"
        return info

    def _recent_files_snapshot(self, directory, patterns: Optional[List[str]] = None,
                               limit: int = 20) -> List[Dict]:
        try:
            p = Path(directory)
            if not p.exists() or not p.is_dir():
                return []
            patterns = patterns or ["*"]
            files = []
            for pattern in patterns:
                files.extend(p.glob(pattern))
            unique_files = {str(f.resolve()): f for f in files}
            ordered = sorted(
                unique_files.values(),
                key=lambda f: f.stat().st_mtime if f.exists() else 0,
                reverse=True
            )
            result = []
            for f in ordered[:limit]:
                try:
                    stat = f.stat()
                    result.append({
                        "name": f.name,
                        "path": str(f),
                        "is_dir": f.is_dir(),
                        "size_bytes": stat.st_size,
                        "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                    })
                except OSError as e:
                    result.append({"path": str(f), "snapshot_error": str(e)})
            return result
        except Exception as e:
            return [{"snapshot_error": f"{type(e).__name__}: {e}"}]

    def _version_command_snapshot(self, args: List[str]) -> Dict:
        try:
            r = subprocess.run(
                args, capture_output=True, timeout=5, text=True,
                encoding='utf-8', errors='replace',
                creationflags=getattr(self, "subprocess_flags", 0)
            )
            stdout = r.stdout or ""
            stderr = r.stderr or ""
            first_line = ""
            for line in (stdout + "\n" + stderr).splitlines():
                if line.strip():
                    first_line = line.strip()
                    break
            return {
                "args": args,
                "returncode": r.returncode,
                "first_line": first_line,
                "stdout_tail": self._tail_text(stdout, 1200),
                "stderr_tail": self._tail_text(stderr, 1200),
            }
        except Exception as e:
            return {
                "args": args,
                "error_type": type(e).__name__,
                "error": str(e),
            }

    def _environment_snapshot_for_problem_log(self) -> Dict:
        cached = getattr(self, "problem_log_environment_snapshot", None)
        if cached:
            return cached

        path_entries = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
        tool_commands = {
            "yt-dlp": ["yt-dlp", "--version"],
            "aria2c": ["aria2c", "--version"],
            "ffmpeg": ["ffmpeg", "-version"],
            "ffprobe": ["ffprobe", "-version"],
            "python": ["python", "--version"],
            "py": ["py", "--version"],
            "node": ["node", "--version"],
            "deno": ["deno", "--version"],
        }
        tools = {}
        for name, args in tool_commands.items():
            resolved_path = shutil.which(name)
            tools[name] = {
                "resolved_path": resolved_path,
                "version_check": (
                    self._version_command_snapshot(args)
                    if resolved_path else {"skipped": "not found in PATH"}
                ),
            }

        snapshot = {
            "app_name": "Skachat_video_online",
            "diagnostic_schema_version": PROBLEM_LOG_SCHEMA_VERSION,
            "diagnostic_format_version": PROBLEM_LOG_FORMAT_VERSION,
            "pid": os.getpid(),
            "cwd": os.getcwd(),
            "python_executable": sys.executable,
            "python_version": sys.version,
            "platform": platform.platform(),
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "argv": self._sanitize_command_args_for_log(sys.argv),
            "path_env_entries": self._json_safe_value(path_entries),
            "tools": tools,
            "source_file": str(Path(__file__).resolve()),
        }
        self.problem_log_environment_snapshot = snapshot
        return snapshot

    def _safe_counter_value(self, attr_name: str) -> Optional[int]:
        try:
            counter = getattr(self, attr_name, None)
            return counter.value() if counter else None
        except Exception:
            return None

    def _safe_var_value(self, attr_name: str, fallback=None):
        try:
            var = getattr(self, attr_name)
            return var.get() if hasattr(var, "get") else var
        except Exception:
            return fallback

    def _settings_snapshot_for_problem_log(self) -> Dict:
        loaded_settings = self._json_safe_value(getattr(self, "settings", {}))
        save_path = self._safe_var_value(
            "save_path", loaded_settings.get("save_path") if isinstance(loaded_settings, dict) else None
        )
        effective = {
            "save_path": save_path,
            "quality": self._safe_var_value(
                "quality", loaded_settings.get("quality") if isinstance(loaded_settings, dict) else None
            ),
            "audio_quality": self._safe_var_value(
                "audio_quality", loaded_settings.get("audio_quality") if isinstance(loaded_settings, dict) else None
            ),
            "concurrent_downloads": self._safe_var_value(
                "concurrent_var",
                loaded_settings.get("concurrent_downloads") if isinstance(loaded_settings, dict) else None
            ),
            "download_subtitles": self._safe_var_value(
                "download_subtitles",
                loaded_settings.get("download_subtitles") if isinstance(loaded_settings, dict) else None
            ),
            "proxy_enabled": self._safe_var_value(
                "proxy_enabled",
                loaded_settings.get("proxy_enabled") if isinstance(loaded_settings, dict) else False
            ),
            "proxy_url_masked": self.mask_proxy_url(
                self._safe_var_value(
                    "proxy_url",
                    loaded_settings.get("proxy_url") if isinstance(loaded_settings, dict) else ""
                )
            ),
            "merge_audio": self._safe_var_value(
                "merge_audio", loaded_settings.get("merge_audio") if isinstance(loaded_settings, dict) else None
            ),
            "split_hours": self._safe_var_value(
                "split_hours", loaded_settings.get("split_hours") if isinstance(loaded_settings, dict) else None
            ),
        }
        dirs = {}
        if save_path:
            save_dir = Path(str(save_path))
            dirs = {
                "save_dir": self._path_snapshot(save_dir),
                "video_dir": self._path_snapshot(save_dir / "Видео"),
                "audio_dir": self._path_snapshot(save_dir / "Аудио"),
            }
        return {
            "loaded_settings": loaded_settings,
            "effective_ui_settings": self._json_safe_value(effective),
            "settings_file": self._path_snapshot(getattr(self, "settings_file", None)),
            "target_directories": dirs,
        }

    def _active_processes_snapshot(self) -> List[Dict]:
        try:
            lock = getattr(self, "process_lock", None)
            if lock:
                with lock:
                    items = list(getattr(self, "active_processes", {}).items())
            else:
                items = list(getattr(self, "active_processes", {}).items())
            result = []
            runtime_by_pid = {}
            try:
                with getattr(self, "ytdlp_runtime_lock", Lock()):
                    runtime_by_pid = dict(getattr(self, "ytdlp_runtime", {}))
            except Exception:
                runtime_by_pid = {}
            now = time.time()
            for pid, proc in items:
                runtime = runtime_by_pid.get(pid, {})
                runtime_snapshot = {}
                if runtime:
                    runtime_snapshot = {
                        "operation": runtime.get("operation"),
                        "url": runtime.get("url"),
                        "attempt": runtime.get("attempt"),
                        "strategy": runtime.get("strategy"),
                        "quality": runtime.get("quality"),
                        "started_at": runtime.get("started_at"),
                        "elapsed_sec": round(now - runtime.get("started_monotonic", now), 1),
                        "last_output_age_sec": round(now - runtime.get("last_output_monotonic", now), 1),
                        "last_output_line": runtime.get("last_output_line"),
                        "recent_output_tail": runtime.get("recent_output_tail"),
                        "last_progress_percent": runtime.get("last_progress_percent"),
                        "last_progress_fragment": runtime.get("last_progress_fragment"),
                        "last_progress_fragment_total": runtime.get("last_progress_fragment_total"),
                        "last_progress_speed": runtime.get("last_progress_speed"),
                        "last_real_progress_age_sec": runtime.get("last_real_progress_age_sec"),
                        "target_dir": runtime.get("target_dir"),
                        "expected_video_id": runtime.get("expected_video_id"),
                    }
                raw_proc_args = getattr(proc, "args", "")
                if isinstance(raw_proc_args, list):
                    logged_proc_args = self._sanitize_command_args_for_log(raw_proc_args)
                    args_pretty = self._command_to_string(raw_proc_args)
                else:
                    logged_proc_args = raw_proc_args
                    args_pretty = str(raw_proc_args)
                result.append({
                    "pid": pid,
                    "poll": proc.poll(),
                    "args": self._json_safe_value(logged_proc_args),
                    "args_pretty": args_pretty,
                    "sensitive_values_masked": logged_proc_args != raw_proc_args,
                    "yt_dlp_runtime": runtime_snapshot,
                })
            return result
        except Exception as e:
            return [{"snapshot_error": f"{type(e).__name__}: {e}"}]

    def _app_state_for_problem_log(self) -> Dict:
        return {
            "is_downloading": getattr(self, "is_downloading", None),
            "cancel_requested": (
                self.cancel_flag.is_set() if hasattr(self, "cancel_flag") else None
            ),
            "active_process_count": len(getattr(self, "active_processes", {})),
            "total_files": self._safe_counter_value("total_files"),
            "completed_files": self._safe_counter_value("completed_files"),
            "max_concurrent": getattr(self, "max_concurrent", None),
            "active_processes": self._active_processes_snapshot(),
        }

    def _session_state_for_problem_log(self) -> Dict:
        try:
            threads = []
            if hasattr(self, "download_threads"):
                for thread in self.download_threads.copy():
                    threads.append({
                        "name": thread.name,
                        "ident": thread.ident,
                        "is_alive": thread.is_alive(),
                    })
            return {
                "app_session_timestamp": getattr(self, "app_session_timestamp", None),
                "problem_session_dir": str(getattr(self, "problem_session_dir", "") or ""),
                "debug_session_log_file": str(getattr(self, "debug_session_log_file", "") or ""),
                "problem_log_session_id": getattr(self, "problem_log_session_id", None),
                "current_session_start": getattr(self, "current_session_start", None),
                "download_start_time": getattr(self, "download_start_time", None),
                "current_session_download_count": getattr(self, "current_session_download_count", None),
                "current_history_session_file": str(getattr(self, "current_history_session_file", "") or ""),
                "current_download_log_session_file": str(
                    getattr(self, "current_download_log_session_file", "") or ""
                ),
                "download_threads": threads,
                "python_threads": [
                    {
                        "name": t.name,
                        "ident": t.ident,
                        "daemon": t.daemon,
                        "is_alive": t.is_alive(),
                    }
                    for t in threading.enumerate()
                ],
                "downloaded_url_hash_count": len(getattr(self, "downloaded_url_hashes", set())),
                "downloaded_video_id_count": len(getattr(self, "downloaded_video_ids", set())),
            }
        except Exception as e:
            return {"snapshot_error": f"{type(e).__name__}: {e}"}

    def _paths_snapshot_for_problem_log(self) -> Dict:
        return {
            "app_dir": self._path_snapshot(getattr(self, "app_dir", None)),
            "settings_file": self._path_snapshot(getattr(self, "settings_file", None)),
            "log_dir": self._path_snapshot(getattr(self, "log_dir", None)),
            "problem_log_dir": self._path_snapshot(getattr(self, "problem_log_dir", None)),
            "problem_log_file": self._path_snapshot(getattr(self, "problem_log_file", None)),
            "problem_events_file": self._path_snapshot(
                getattr(self, "problem_events_file", None)
            ),
            "problem_report_file": self._path_snapshot(getattr(self, "problem_report_file", None)),
            "problem_latest_json_file": self._path_snapshot(
                getattr(self, "problem_latest_json_file", None)
            ),
            "problem_session_dir": self._path_snapshot(getattr(self, "problem_session_dir", None)),
            "problem_session_log_file": self._path_snapshot(
                getattr(self, "problem_session_log_file", None)
            ),
            "problem_session_report_file": self._path_snapshot(
                getattr(self, "problem_session_report_file", None)
            ),
            "problem_session_latest_json_file": self._path_snapshot(
                getattr(self, "problem_session_latest_json_file", None)
            ),
            "debug_session_log_file": self._path_snapshot(
                getattr(self, "debug_session_log_file", None)
            ),
            "download_links_dir": self._path_snapshot(getattr(self, "download_links_dir", None)),
            "manual_processing_dir": self._path_snapshot(
                getattr(self, "manual_processing_dir", None)
            ),
            "recent_manual_processing_files": self._recent_files_snapshot(
                getattr(self, "manual_processing_dir", None),
                limit=15
            ),
            "recent_problem_files": self._recent_files_snapshot(
                getattr(self, "problem_log_dir", None), limit=15
            ),
            "recent_download_link_files": self._recent_files_snapshot(
                getattr(self, "download_links_dir", None), ["links_*.txt"], limit=10
            ),
        }

    def _command_snapshot(self, command: Optional[List[str]]) -> Dict:
        if not command:
            return {}
        raw_args = [str(part) for part in command]
        args = self._sanitize_command_args_for_log(raw_args)
        executable = raw_args[0] if raw_args else ""
        return {
            "args": args,
            "pretty": self._command_to_string(raw_args),
            "executable": self._sanitize_text_for_log(executable),
            "resolved_executable": shutil.which(executable) if executable else None,
            "working_directory": os.getcwd(),
            "sensitive_values_masked": args != raw_args,
        }

    def _url_diagnostics_for_problem_log(self, context: Dict) -> Optional[Dict]:
        url = context.get("url") or context.get("source_url")
        if not url:
            return None
        try:
            raw_url = str(url)
            safe_url = self._sanitize_url_for_log(raw_url)
            parsed = urllib.parse.urlparse(safe_url)
            normalized = self._sanitize_url_for_log(
                self.normalize_url_for_history(raw_url)
            )
            return {
                "url": safe_url,
                "scheme": parsed.scheme,
                "netloc": parsed.netloc,
                "hostname": parsed.hostname,
                "path": parsed.path,
                "query_keys": sorted(urllib.parse.parse_qs(parsed.query).keys()),
                "video_id": self.extract_video_id(str(url)),
                "normalized_url": normalized,
                "normalized_url_md5": hashlib.md5(normalized.encode()).hexdigest(),
                "raw_url_md5": hashlib.md5(raw_url.strip().encode()).hexdigest(),
            }
        except Exception as e:
            return {
                "url": self._sanitize_url_for_log(str(url)),
                "snapshot_error": f"{type(e).__name__}: {e}",
            }

    def _source_location_for_problem_log(self) -> Dict:
        try:
            frames = traceback.extract_stack()
            useful = []
            for frame in frames:
                if frame.name in ("_source_location_for_problem_log", "record_problem"):
                    continue
                useful.append({
                    "file": frame.filename,
                    "line": frame.lineno,
                    "function": frame.name,
                    "code": frame.line,
                })
            return {
                "caller": useful[-1] if useful else None,
                "stack_tail": useful[-15:],
            }
        except Exception as e:
            return {"snapshot_error": f"{type(e).__name__}: {e}"}

    def _terminal_error_text_for_problem_log(
        self,
        stdout: Optional[str],
        stderr: Optional[str],
        exception: Optional[BaseException],
        context: Optional[Dict],
    ) -> str:
        """Выбирает последнюю причину сбоя, не смешивая её с ранними retry."""
        context = context if isinstance(context, dict) else {}
        marked_candidates: List[str] = []
        fallback_candidates: List[str] = []
        for value in (
            str(exception) if exception else "",
            stdout,
            stderr,
            context.get("last_error"),
        ):
            if not value:
                continue
            lines = [line.strip() for line in str(value).splitlines() if line.strip()]
            marked = [
                line for line in lines
                if any(marker in line.casefold() for marker in (
                    "error:", "got error:", "timed out", "timeout",
                    "connection reset", "ssl:", "forbidden", "sign in",
                    "not a bot", "requested format is not available",
                ))
            ]
            if marked:
                marked_candidates.append(marked[-1])
            elif lines:
                fallback_candidates.append(lines[-1])
        selected = (
            marked_candidates[-1] if marked_candidates
            else (fallback_candidates[-1] if fallback_candidates else "")
        )
        return self._sanitize_text_for_log(selected)[-2000:]

    def _canonical_problem_error_code(self, text: str) -> str:
        normalized = str(text or "").casefold()
        if any(marker in normalized for marker in (
            "sign in", "not a bot", "private video", "members-only",
            "age-restricted", "login required", "cookies",
        )):
            return "youtube_auth_required"
        if "http error 403" in normalized or "forbidden" in normalized:
            return "youtube_http_403"
        if "decryption_failed_or_bad_record_mac" in normalized or "bad record mac" in normalized:
            return "tls_bad_record_mac"
        if any(marker in normalized for marker in (
            "timed out", "timeout", "read timed out", "connect timeout",
        )):
            return "network_timeout"
        if any(marker in normalized for marker in (
            "connection reset", "connectionreseterror", "winerror 10054",
            "remote end closed", "incomplete read",
        )):
            return "network_connection_reset"
        if any(marker in normalized for marker in (
            "ssl:", "unexpected_eof", "eof occurred", "handshake failure",
        )):
            return "network_tls_error"
        if any(marker in normalized for marker in (
            "requested format is not available", "format is not available",
            "only images are available",
        )):
            return "yt_dlp_format_unavailable"
        if any(marker in normalized for marker in (
            "permission", "access is denied", "отказано в доступе",
        )):
            return "filesystem_permission_denied"
        if any(marker in normalized for marker in ("cancel", "отмен")):
            return "user_cancelled"
        return "unknown"

    def _ai_debug_hint_for_problem_log(self, operation: str, message: str,
                                       stderr: Optional[str],
                                       exception: Optional[BaseException],
                                       stdout: Optional[str] = None,
                                       context: Optional[Dict] = None) -> Dict:
        try:
            context_text = json.dumps(context or {}, ensure_ascii=False, default=str)
        except Exception:
            context_text = str(context or {})
        full_text = "\n".join([
            operation or "",
            message or "",
            stdout or "",
            stderr or "",
            str(exception) if exception else "",
            context_text,
        ]).lower()
        terminal_error = self._terminal_error_text_for_problem_log(
            stdout, stderr, exception, context
        )
        error_code = self._canonical_problem_error_code(terminal_error)
        # Для категории сначала используем последнюю причину. Полный вывод нужен
        # только как fallback, иначе ранний SSL retry маскирует итоговый HTTP 403.
        text = "\n".join((operation or "", message or "", terminal_error)).lower()
        if error_code == "unknown":
            text = full_text
        category = "unknown"
        next_checks = [
            f"В коде сначала искать operation: {operation}",
            "Сверить context, command_details.args, stderr.tail и app_state.",
            "Если ошибка воспроизводится, сравнить эту запись с предыдущими по problem_fingerprint.",
        ]

        if any(x in text for x in (
            "ssl:", "unexpected_eof", "eof occurred", "connection reset",
            "connectionreseterror", "connection aborted", "timed out", "timeout",
            "handshake failure", "aria2c exited",
            "удаленный хост принудительно разорвал",
            "удалённый хост принудительно разорвал"
        )):
            category = "network_or_tls"
            next_checks.extend([
                "Проверить сетевые аргументы yt-dlp: chunk size, force-ipv4, retries, socket-timeout.",
                "Проверить, есть ли в command_details.args флаг --proxy и корректный proxy URL.",
                "Если youtube.com отвечает, а googlevideo.com повторно даёт Read timed out/SSL/10054 на разных стратегиях, проверить VPN/proxy или другого провайдера.",
                "Проверить, на какой стратегии yt-dlp произошел сбой и была ли следующая fallback-стратегия.",
            ])
        elif any(x in text for x in ("http error 403", "forbidden")):
            category = "youtube_forbidden_or_client_blocked"
            next_checks.extend([
                "Проверить порядок fallback-стратегий: другой player_client, cookies, внешний downloader.",
                "Если 403 повторяется на всех стратегиях, проверить cookies/авторизацию и обновление yt-dlp.",
            ])
        elif any(x in text for x in (
            "requested format is not available",
            "format is not available",
            "only images are available",
        )):
            category = "yt_dlp_format_selection"
            next_checks.extend([
                "Проверить строку -f и сортировку -S в command_details.args.",
                "Сравнить requested quality с доступными форматами YouTube для этого video_id.",
            ])
        elif any(x in text for x in ("sign in", "private video", "members-only", "age-restricted", "cookies", "not a bot")):
            category = "auth_or_cookies"
            next_checks.extend([
                "Проверить, нужна ли авторизация YouTube или cookies браузера.",
                "Смотреть ошибки --cookies-from-browser и доступность браузера.",
            ])
        elif any(x in text for x in ("ffmpeg", "ffprobe", "moov atom", "invalid data found")):
            category = "media_validation_or_conversion"
            next_checks.extend([
                "Проверить ffmpeg/ffprobe версию и stderr команды конвертации.",
                "Проверить размер и длительность скачанного файла.",
            ])
        elif any(x in text for x in ("permission", "access is denied", "не удалось записать", "no such file", "cannot find the path")):
            category = "filesystem_or_permissions"
            next_checks.extend([
                "Проверить paths, свободное место на диске и права записи в целевые папки.",
                "Проверить русские пути и длину итогового имени файла.",
            ])
        elif any(x in text for x in ("отмен", "cancel", "commandcancellederror")):
            category = "user_cancelled"
            next_checks.append("Это может быть штатная отмена; искать проблему только если состояние UI осталось некорректным.")

        return {
            "category": category,
            "error_code": error_code,
            "terminal_error": terminal_error,
            "operation_to_search": operation,
            "source_file": str(Path(__file__).resolve()),
            "important_log_files": {
                "jsonl": str(getattr(self, "problem_events_file", "")),
                "legacy_jsonl": str(getattr(self, "problem_log_file", "")),
                "latest_json": str(getattr(self, "problem_latest_json_file", "")),
                "text_report": str(getattr(self, "problem_report_file", "")),
                "session_jsonl": str(getattr(self, "problem_session_log_file", "")),
                "session_latest_json": str(getattr(self, "problem_session_latest_json_file", "")),
                "session_text_report": str(getattr(self, "problem_session_report_file", "")),
                "session_debug_log": str(getattr(self, "problem_debug_session_log_file", "")),
                "debug_log": str(getattr(self, "problem_debug_session_log_file", "")),
            },
            "next_checks": next_checks,
        }

    def _problem_fingerprint(self, operation: str, message: str,
                             context: Dict, exception_type: Optional[str],
                             stderr_tail: str) -> str:
        raw = json.dumps(
            {
                "operation": operation,
                "message": message,
                "context": context,
                "exception_type": exception_type,
                "stderr_tail": stderr_tail[-2000:],
            },
            ensure_ascii=False, sort_keys=True, default=str
        )
        return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]

    def _normalize_error_signature_text(self, value: str) -> str:
        """Убирает переменные значения, чтобы одинаковые сбои имели одну сигнатуру."""
        text = self._sanitize_text_for_log(str(value or "")).casefold()
        text = re.sub(r"\b(?:https?|ftp|socks[45])://\S+", "<url>", text)
        text = re.sub(
            r"(\[youtube\]\s+)[a-z0-9_-]{6,20}",
            r"\1<video_id>",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(
            r"[a-z0-9.-]*googlevideo\.com",
            "<googlevideo_host>",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(r"\b0x[0-9a-f]+\b", "<address>", text)
        text = re.sub(
            r"(?i)(?:[a-z]:\\|\\\\)[^\r\n\"']+",
            "<path>",
            text,
        )
        text = re.sub(r"(?i)\b[0-9a-f]{32,64}\b", "<hash>", text)
        text = re.sub(
            r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
            r"[0-9a-f]{4}-[0-9a-f]{12}\b",
            "<uuid>",
            text,
        )
        text = re.sub(r"\b\d{4}-\d{2}-\d{2}[t _]\d{2}[:-]\d{2}[:-]\d{2}\b", "<time>", text)
        text = re.sub(r"\b\d+(?:[.,]\d+)?\s*(?:ms|sec|secs|seconds|s|min|mins|minutes|%)\b", "<value>", text)
        text = re.sub(
            r"\b(pid|thread|attempt|попытка)\s*[:=#-]?\s*\d+\b",
            r"\1=<n>",
            text,
        )
        text = re.sub(r"\b\d{3,}\b", "<n>", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:4000]

    def _signature_context(self, context: Dict) -> Dict:
        volatile_parts = {
            "timestamp", "updated_at", "started_at", "finished_at",
            "timestamp_unix", "pid", "thread", "thread_id", "sequence",
            "elapsed", "elapsed_sec", "elapsed_min", "duration", "duration_sec",
            "attempt", "attempt_index", "progress", "percent", "speed",
            "eta", "last_output_age_sec", "last_real_progress_age_sec",
            "url", "source_url", "save_path", "target_dir", "path",
            "video_file", "audio_file", "original_video_path",
            "captured_output_tail", "stdout_tail", "stderr_tail",
            "network_failure_count", "slow_network_failure_count",
            "strategy", "quality", "format_mode", "returncode",
        }

        def clean(value, key: str = "", depth: int = 0):
            if depth > 4:
                return None
            normalized_key = str(key).casefold()
            if self._is_sensitive_log_key(normalized_key):
                return None
            if normalized_key in volatile_parts or any(
                part in normalized_key
                for part in ("timestamp", "elapsed", "progress", "duration")
            ):
                return None
            if isinstance(value, dict):
                result = {}
                for child_key, child_value in value.items():
                    cleaned = clean(child_value, str(child_key), depth + 1)
                    if cleaned not in (None, "", [], {}):
                        result[str(child_key)] = cleaned
                return result
            if isinstance(value, (list, tuple, set)):
                result = [clean(item, key, depth + 1) for item in list(value)[:10]]
                return [item for item in result if item not in (None, "", [], {})]
            if isinstance(value, str):
                return self._normalize_error_signature_text(value)
            if value is None or isinstance(value, (bool, int, float)):
                return value
            return self._normalize_error_signature_text(repr(value))

        return clean(context or {}) or {}

    def _stable_error_signature(self, operation: str, message: str,
                                context: Dict, exception_type: Optional[str],
                                stderr_tail: str,
                                category: Optional[str] = None,
                                error_code: Optional[str] = None) -> str:
        normalized = {
            "operation": str(operation or "unknown").casefold(),
            "message": self._normalize_error_signature_text(message),
            "exception_type": str(exception_type or "").casefold(),
            "category": str(category or "").casefold(),
            "error_code": str(error_code or "").casefold(),
            "stderr": self._normalize_error_signature_text(stderr_tail)[-2000:],
            "context": self._signature_context(context),
        }
        digest = hashlib.sha256(
            json.dumps(
                normalized, ensure_ascii=False, sort_keys=True, default=str
            ).encode("utf-8")
        ).hexdigest()[:16]
        operation_slug = re.sub(
            r"[^a-z0-9_]+", "_", str(operation or "unknown").casefold()
        ).strip("_")[:48] or "unknown"
        return f"{operation_slug}.{digest}"

    def _event_id(self, sequence: int) -> str:
        return f"{self.problem_log_session_id}:evt-{sequence:06d}"

    def _incident_id(self, incident_key: str) -> str:
        digest = hashlib.sha256(
            str(incident_key).encode("utf-8", errors="replace")
        ).hexdigest()[:16]
        return f"{self.problem_log_session_id}:inc-{digest}"

    def _correlation_fields(self, context: Dict,
                            command: Optional[List[str]] = None) -> Dict:
        context = context if isinstance(context, dict) else {}
        raw_identity = (
            context.get("task_id")
            or context.get("expected_video_id")
            or context.get("video_id")
            or context.get("url")
            or context.get("source_url")
            or context.get("video_file")
            or context.get("audio_file")
        )
        task_id = context.get("task_id")
        if not task_id and raw_identity:
            identity_for_hash = str(raw_identity)
            if re.match(r"(?i)^https?://", identity_for_hash):
                try:
                    parsed = urllib.parse.urlsplit(identity_for_hash)
                    query = [
                        (key, value)
                        for key, value in urllib.parse.parse_qsl(
                            parsed.query, keep_blank_values=True
                        )
                        if not self._is_sensitive_log_key(key)
                        and key.casefold() not in {
                            "sig", "signature", "key", "credential",
                            "access_token",
                        }
                    ]
                    identity_for_hash = urllib.parse.urlunsplit(
                        (
                            parsed.scheme.casefold(),
                            parsed.netloc.casefold(),
                            parsed.path,
                            urllib.parse.urlencode(query, doseq=True),
                            "",
                        )
                    )
                except Exception:
                    identity_for_hash = self._sanitize_url_for_log(
                        identity_for_hash
                    )
            digest = hashlib.sha256(
                identity_for_hash.encode("utf-8", errors="replace")
            ).hexdigest()[:16]
            task_id = f"task-{digest}"
        attempt = context.get("attempt") or context.get("attempt_index")
        attempt_id = context.get("attempt_id")
        if not attempt_id and attempt is not None:
            attempt_id = f"{task_id or 'session'}:attempt-{attempt}"
        command_id = context.get("command_id")
        if not command_id and command:
            digest = hashlib.sha256(
                json.dumps(
                    self._sanitize_command_args_for_log(command),
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest()[:16]
            command_id = f"cmd-{digest}"
        return {
            "parent_event_id": context.get("parent_event_id"),
            "task_id": task_id,
            "attempt_id": attempt_id,
            "command_id": command_id,
        }

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
        strategy = context.get("strategy") or context.get("strategy_name")
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

    def write_problem_session_summary(self, status: str,
                                      context: Optional[Dict] = None) -> None:
        """Пишет итог сессии, метрики, регрессии и стабильные корневые индексы."""
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
                disk_validation = self._validate_problem_session_files_locked(
                    expected_summary=summary
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
                "source_file": str(Path(__file__).resolve()),
                "ai_debug_hint": ai_debug_hint,
                "hint_for_ai": (
                    "Это подробная диагностическая запись приложения для Codex. "
                    "Начните с error_signature, operation, ai_debug_hint.category, source_location.caller, "
                    "context, command_details.args, stderr.tail, app_state, settings и paths. "
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

    def safe_int_setting(self, key: str, default: int,
                         min_value: int, max_value: int) -> int:
        raw_value = self.settings.get(key, default)
        try:
            value = int(raw_value)
        except (TypeError, ValueError) as e:
            self.record_problem(
                f"Некорректное числовое значение настройки {key}: {raw_value!r}",
                "WARNING", "load_settings",
                {"key": key, "raw_value": raw_value, "default": default},
                e
            )
            value = default
        return max(min_value, min(value, max_value))

    def make_safe_session_timestamp(self, session_ts: str) -> str:
        return session_ts.replace(':', '-').replace(' ', '_')

    def make_unique_session_file(self, directory: Path, prefix: str,
                                 session_ts: str) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        safe_ts = self.make_safe_session_timestamp(session_ts)
        candidate = directory / f"{prefix}_{safe_ts}.txt"
        counter = 2
        while candidate.exists():
            candidate = directory / f"{prefix}_{safe_ts}_{counter}.txt"
            counter += 1
        return candidate

    def ensure_download_session_files(self) -> Tuple[Path, Path]:
        if not self.current_session_start:
            self.current_session_start = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        if self.current_history_session_file is None:
            self.current_history_session_file = self.make_unique_session_file(
                self.history_dir, "downloaded", self.current_session_start
            )
            with open(self.current_history_session_file, 'w', encoding='utf-8') as f:
                f.write("Скачанные видео\n")
                f.write(f"Сессия: {self.current_session_start}\n")
                f.write("=" * 80 + "\n\n")

        if self.current_download_log_session_file is None:
            self.current_download_log_session_file = self.make_unique_session_file(
                self.downloaded_sessions_dir, "downloaded_log",
                self.current_session_start
            )
            with open(self.current_download_log_session_file, 'w', encoding='utf-8') as f:
                f.write(f"# Session: {self.current_session_start}\n")
                f.write("# Columns: canonical_url_hash\tvideo_id\tcanonical_url\toriginal_url\n")

        return self.current_history_session_file, self.current_download_log_session_file

    def _register_process(self, proc: subprocess.Popen) -> None:
        with self.process_lock:
            self.active_processes[proc.pid] = proc

    def _unregister_process(self, proc: subprocess.Popen) -> None:
        with self.process_lock:
            self.active_processes.pop(proc.pid, None)

    def terminate_process(self, proc: subprocess.Popen) -> None:
        if proc.poll() is not None:
            return
        try:
            if platform.system() == "Windows":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True, timeout=10,
                    creationflags=self.subprocess_flags
                )
            else:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        except Exception as e:
            self.record_problem(
                "Не удалось штатно остановить процесс, пробую proc.kill()",
                "WARNING", "terminate_process",
                {"pid": proc.pid}, e
            )
            try:
                proc.kill()
            except Exception:
                pass

    def terminate_active_processes(self) -> int:
        with self.process_lock:
            processes = list(self.active_processes.values())
        if processes:
            self.record_problem(
                "Запрошена остановка активных процессов: снимок перед taskkill/terminate",
                "WARNING", "terminate_active_processes_snapshot",
                {
                    "process_count": len(processes),
                    "active_processes_before_terminate": self._active_processes_snapshot(),
                    "reason": "cancel/close",
                }
            )
        for proc in processes:
            self.terminate_process(proc)
        return len(processes)

    def _recent_output_tail(self, lines: List[str], max_lines: int = YT_DLP_RUNTIME_TAIL_LINES) -> str:
        return "\n".join(lines[-max_lines:])

    def _snapshot_download_target_files(self, target_dir: Optional[str],
                                        expected_video_id: Optional[str] = None,
                                        limit: int = 40) -> List[Dict]:
        """Снимок файлов в папке загрузки: готовые, .part, .temp и совпадающие с video_id."""
        if not target_dir:
            return []
        try:
            directory = Path(target_dir)
            if not directory.exists() or not directory.is_dir():
                return [{"path": str(directory), "exists": directory.exists(), "is_dir": False}]
            candidates = []
            patterns = ["*.part", "*.ytdl", "*.temp.*"]
            patterns.extend([f"*.{ext}" for ext in SUPPORTED_VIDEO_EXTENSIONS])
            if expected_video_id:
                patterns.append(f"*{expected_video_id}*")
            seen = set()
            for pattern in patterns:
                for file_path in directory.glob(pattern):
                    try:
                        key = str(file_path.resolve())
                    except OSError:
                        key = str(file_path)
                    if key in seen or not file_path.exists():
                        continue
                    seen.add(key)
                    try:
                        stat = file_path.stat()
                        candidates.append({
                            "name": file_path.name,
                            "path": str(file_path),
                            "size_bytes": stat.st_size,
                            "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                            "suffix": file_path.suffix,
                        })
                    except OSError as e:
                        candidates.append({"path": str(file_path), "stat_error": str(e)})
            candidates.sort(key=lambda x: x.get("mtime", ""), reverse=True)
            return candidates[:limit]
        except Exception as e:
            return [{"snapshot_error": f"{type(e).__name__}: {e}"}]

    def cleanup_download_temp_files(self, target_dir, expected_video_id: Optional[str] = None,
                                    final_video_path: Optional[str] = None,
                                    reason: str = "") -> List[str]:
        """Удаляет мусор yt-dlp после успешной/неудачной попытки.

        Чистим только временные хвосты: *.part, *.ytdl, *.temp.* и фрагменты
        вида .f137.mp4/.f140.m4a, которые остались от оборванных DASH-попыток.
        Готовый итоговый файл не трогаем.
        """
        deleted: List[str] = []
        try:
            directory = Path(target_dir)
            if not directory.exists() or not directory.is_dir():
                return deleted

            final_path = Path(final_video_path).resolve() if final_video_path else None
            final_stem = Path(final_video_path).stem if final_video_path else ""
            candidate_patterns = ["*.part", "*.ytdl", "*.temp.*", "*.f*.mp4", "*.f*.webm", "*.f*.m4a"]
            seen: Set[str] = set()
            candidates: List[Path] = []
            for pattern in candidate_patterns:
                for item in directory.glob(pattern):
                    try:
                        key = str(item.resolve())
                    except OSError:
                        key = str(item)
                    if key in seen:
                        continue
                    seen.add(key)
                    candidates.append(item)

            for item in candidates:
                try:
                    if not item.exists() or not item.is_file():
                        continue
                    if final_path is not None and item.resolve() == final_path:
                        continue
                    name = item.name
                    stem = item.stem
                    should_delete = False

                    # Явные временные файлы yt-dlp безопасно удалить после завершения ролика.
                    if name.endswith(".part") or name.endswith(".ytdl") or ".temp." in name:
                        should_delete = True

                    # Фрагменты DASH без .part: title.f137.mp4 / title.f140.m4a.
                    if re.search(r"\.f\d+\.(mp4|webm|m4a)$", name, re.IGNORECASE):
                        if reason == "start_new_download_session":
                            should_delete = True
                        elif expected_video_id and expected_video_id in name:
                            should_delete = True
                        elif final_stem and (name.startswith(final_stem + ".f") or stem.startswith(final_stem + ".f")):
                            should_delete = True

                    # Если в старой версии в имени был video_id, чистим только хвосты этого ролика.
                    if expected_video_id and expected_video_id in name and (
                        name.endswith(".part") or name.endswith(".ytdl") or ".temp." in name
                        or re.search(r"\.f\d+\.(mp4|webm|m4a)$", name, re.IGNORECASE)
                    ):
                        should_delete = True

                    if should_delete:
                        size = item.stat().st_size
                        item.unlink()
                        deleted.append(f"{name} ({size} байт)")
                except OSError as e:
                    self.record_problem(
                        "Не удалось удалить временный мусор yt-dlp",
                        "WARNING", "cleanup_download_temp_files",
                        {
                            "file": str(item),
                            "expected_video_id": expected_video_id,
                            "final_video_path": final_video_path,
                            "reason": reason,
                        },
                        e
                    )

            if deleted:
                self.log(f"🧹 Удалён временный мусор yt-dlp: {len(deleted)} файл(ов)", "INFO")
                self.record_problem(
                    "Удалён временный мусор yt-dlp после скачивания",
                    "INFO", "cleanup_download_temp_files",
                    {
                        "target_dir": str(directory),
                        "expected_video_id": expected_video_id,
                        "final_video_path": final_video_path,
                        "reason": reason,
                        "deleted_files": deleted[:20],
                        "deleted_count": len(deleted),
                    }
                )
        except Exception as e:
            self.record_problem(
                "Ошибка при очистке временного мусора yt-dlp",
                "WARNING", "cleanup_download_temp_files",
                {
                    "target_dir": str(target_dir),
                    "expected_video_id": expected_video_id,
                    "final_video_path": final_video_path,
                    "reason": reason,
                },
                e
            )
        return deleted

    def cleanup_stale_download_temp_files(self, target_dir) -> int:
        """Чистит старые хвосты .part/.ytdl/.temp перед новой сессией."""
        deleted = self.cleanup_download_temp_files(
            target_dir, expected_video_id=None, final_video_path=None,
            reason="start_new_download_session"
        )
        return len(deleted)

    def _set_ytdlp_runtime(self, pid: int, updates: Dict) -> None:
        try:
            with self.ytdlp_runtime_lock:
                current = self.ytdlp_runtime.get(pid, {})
                current.update(updates)
                self.ytdlp_runtime[pid] = current
        except Exception:
            pass

    def _remove_ytdlp_runtime(self, pid: int) -> None:
        try:
            with self.ytdlp_runtime_lock:
                self.ytdlp_runtime.pop(pid, None)
        except Exception:
            pass

    def _record_active_ytdlp_snapshot(self, message: str, level: str,
                                      operation: str, command: List[str],
                                      context: Optional[Dict], output_lines: List[str],
                                      exception: Optional[BaseException] = None) -> None:
        context = dict(context or {})
        is_info = str(level).upper() == "INFO"
        if is_info:
            useful_keys = {
                "url", "attempt", "strategy", "quality", "expected_video_id",
                "returncode", "elapsed_sec", "no_output_for_sec", "last_output_line",
                "process_poll", "progress_watchdog", "proxy_enabled", "format_mode",
            }
            context = {key: value for key, value in context.items() if key in useful_keys}
            context["captured_output_line_count"] = len(output_lines)
            if output_lines:
                context["captured_output_tail"] = self._recent_output_tail(output_lines, 5)
        else:
            context.update({
                "captured_output_line_count": len(output_lines),
                "captured_output_tail": self._recent_output_tail(
                    output_lines, YT_DLP_RUNTIME_TAIL_LINES
                ),
                "diagnostic_note": (
                    "Смотрите captured_output_tail, command_details.args, "
                    "target_files_snapshot и timestamps heartbeat."
                ),
            })
            context["target_files_snapshot"] = self._snapshot_download_target_files(
                context.get("target_dir"), context.get("expected_video_id"),
                limit=40
            )
        self.record_problem(
            message, level, operation, context, exception,
            command=None if is_info else command,
            stdout="" if is_info else "\n".join(output_lines[-40:]),
            stderr=""
        )

    def _handle_streamed_yt_dlp_line(self, line: str, context: Optional[Dict],
                                     last_progress_log: float) -> float:
        stripped = line.replace("\r", "").strip()
        if not stripped:
            return last_progress_log
        lower = stripped.lower()
        now = time.time()

        important_markers = (
            "[download] destination:",
            "[download] downloading item",
            "[download] finished downloading playlist",
            "[merger] merging formats",
            "deleting original file",
            "[extractor]",
            "[youtube]",
            "error:",
            "warning:",
            "requested format is not available",
            "sign in",
            "cookies",
            "403",
            "429",
            "timed out",
            "retrying",
        )
        if "[download]" in lower and "%" in stripped:
            if now - last_progress_log >= YT_DLP_PROGRESS_LOG_INTERVAL_SEC:
                self.log(f"📊 yt-dlp: {stripped[:220]}", "INFO", update_only=True)
                return now
            return last_progress_log
        if any(marker in lower for marker in important_markers):
            self.log(f"🔎 yt-dlp: {stripped[:280]}", "INFO")
        return last_progress_log

    def parse_ytdlp_progress_line(self, line: str) -> Optional[Dict]:
        """Достаёт реальный прогресс из строки yt-dlp.

        Нужен не для красоты логов, а для watchdog: yt-dlp иногда бесконечно
        пишет 0.00B/s ETA Unknown, и простой контроль "есть новый вывод" не
        замечает зависание фрагмента.
        """
        if not line or "[download]" not in line or "%" not in line:
            return None
        info: Dict = {
            "raw_line": line[-400:],
            "percent": None,
            "fragment": None,
            "fragment_total": None,
            "speed_text": "",
            "speed_is_zero_or_unknown": False,
        }
        try:
            percent_match = re.search(r"\[download\]\s+([0-9]+(?:\.[0-9]+)?)%", line)
            if percent_match:
                info["percent"] = float(percent_match.group(1))

            frag_match = re.search(r"\(frag\s+(\d+)\s*/\s*(\d+)\)", line, re.IGNORECASE)
            if frag_match:
                info["fragment"] = int(frag_match.group(1))
                info["fragment_total"] = int(frag_match.group(2))

            speed_match = re.search(r"\bat\s+(.+?)\s+ETA\b", line, re.IGNORECASE)
            if speed_match:
                speed_text = speed_match.group(1).strip()
                info["speed_text"] = speed_text
                speed_l = speed_text.lower().replace(" ", "")
                speed_value = None
                speed_num_match = re.match(r"([0-9]+(?:\.[0-9]+)?)", speed_l)
                if speed_num_match:
                    try:
                        speed_value = float(speed_num_match.group(1))
                    except ValueError:
                        speed_value = None
                info["speed_is_zero_or_unknown"] = (
                    "unknown" in speed_l
                    or speed_l in {"0b/s", "0.00b/s", "0.0b/s"}
                    or speed_value == 0.0
                )

            return info
        except Exception:
            return None

    def should_skip_problematic_slow_download(self, progress_state: Dict, started: float, now: float) -> Tuple[bool, str, Dict]:
        """Определяет, что ролик слишком медленный для текущей сессии.

        Это не обычный timeout. Смысл функции — не держать очередь часами на
        видео, которое через текущий VPN/CDN идёт 5-10% за десятки минут.
        Такой URL лучше записать отдельно и попробовать позже/на другом VPN.
        """
        elapsed = now - started
        if not progress_state.get("seen") or elapsed < PROBLEMATIC_DOWNLOAD_MIN_ELAPSED_SEC:
            return False, "", {}

        percent = progress_state.get("last_percent")
        fragment = progress_state.get("last_fragment")
        fragment_total = progress_state.get("last_fragment_total")
        zero_started = progress_state.get("last_zero_speed_time")
        zero_speed_for = now - float(zero_started) if zero_started else 0.0

        fragment_ratio = None
        if fragment and fragment_total:
            try:
                fragment_ratio = float(fragment) / max(1.0, float(fragment_total))
            except Exception:
                fragment_ratio = None

        low_percent = percent is not None and float(percent) < PROBLEMATIC_DOWNLOAD_LOW_PROGRESS_PERCENT
        low_fragment = (
            fragment_ratio is not None
            and fragment_ratio < PROBLEMATIC_DOWNLOAD_LOW_FRAGMENT_RATIO
        )
        long_zero_speed = zero_speed_for >= PROBLEMATIC_DOWNLOAD_ZERO_SPEED_SKIP_SEC

        if not (low_percent or low_fragment or long_zero_speed):
            return False, "", {}

        diagnostic = {
            "elapsed_sec": round(elapsed, 2),
            "elapsed_min": round(elapsed / 60, 2),
            "last_percent": percent,
            "last_fragment": fragment,
            "last_fragment_total": fragment_total,
            "fragment_ratio": round(fragment_ratio, 4) if fragment_ratio is not None else None,
            "last_speed_text": progress_state.get("last_speed_text"),
            "zero_speed_for_sec": round(zero_speed_for, 2),
            "last_real_progress_age_sec": round(
                now - float(progress_state.get("last_real_progress_time", now)), 2
            ),
            "last_progress_line": progress_state.get("last_progress_line"),
            "thresholds": {
                "min_elapsed_sec": PROBLEMATIC_DOWNLOAD_MIN_ELAPSED_SEC,
                "low_progress_percent": PROBLEMATIC_DOWNLOAD_LOW_PROGRESS_PERCENT,
                "low_fragment_ratio": PROBLEMATIC_DOWNLOAD_LOW_FRAGMENT_RATIO,
                "zero_speed_skip_sec": PROBLEMATIC_DOWNLOAD_ZERO_SPEED_SKIP_SEC,
            },
        }
        reason = (
            "слишком медленное скачивание: "
            f"{percent if percent is not None else '?'}% / "
            f"frag {fragment or '?'} из {fragment_total or '?'} "
            f"за {int(elapsed // 60)} мин, скорость {progress_state.get('last_speed_text') or 'неизвестна'}"
        )
        return True, reason, diagnostic

    def run_command_streamed(self, command: List[str], timeout: int,
                             operation: str, context: Optional[Dict] = None,
                             allow_cancel: bool = True) -> subprocess.CompletedProcess:
        """
        Потоковый запуск yt-dlp.
        Важное отличие от communicate(): stdout/stderr читаются в реальном времени,
        поэтому yt-dlp не может зависнуть из-за заполненной pipe, а в «Логи проблем»
        попадают heartbeat-снимки до того, как пользователь нажмёт отмену.
        """
        started = time.time()
        last_heartbeat = started
        last_output_time = started
        last_progress_log = 0.0
        proc: Optional[subprocess.Popen] = None
        output_lines: List[str] = []
        output_queue: queue.Queue = queue.Queue()
        context = dict(context or {})
        progress_state = {
            "seen": False,
            "last_real_progress_time": started,
            "last_percent": None,
            "last_fragment": None,
            "last_fragment_total": None,
            "last_progress_line": "",
            "last_speed_text": "",
            "last_zero_speed_time": None,
        }

        def append_output(raw_line: str) -> None:
            nonlocal last_output_time, last_progress_log
            clean = raw_line.rstrip("\r\n")
            if clean:
                output_lines.append(clean)
                if len(output_lines) > YT_DLP_RUNTIME_MAX_CAPTURED_LINES:
                    del output_lines[:len(output_lines) - YT_DLP_RUNTIME_MAX_CAPTURED_LINES]
                last_output_time = time.time()
                progress_info = self.parse_ytdlp_progress_line(clean)
                if progress_info:
                    progress_state["seen"] = True
                    progress_state["last_progress_line"] = clean
                    progress_state["last_speed_text"] = progress_info.get("speed_text", "")
                    pct = progress_info.get("percent")
                    frag = progress_info.get("fragment")
                    frag_total = progress_info.get("fragment_total")
                    zero_or_unknown = bool(progress_info.get("speed_is_zero_or_unknown"))
                    real_progress = False

                    if pct is not None:
                        prev_pct = progress_state.get("last_percent")
                        if prev_pct is None or pct >= float(prev_pct) + YT_DLP_PROGRESS_MIN_PERCENT_DELTA:
                            real_progress = True
                        if prev_pct is None or pct > float(prev_pct):
                            progress_state["last_percent"] = pct

                    if frag is not None:
                        prev_frag = progress_state.get("last_fragment")
                        if prev_frag is None or frag > int(prev_frag):
                            real_progress = True
                        if prev_frag is None or frag > int(prev_frag):
                            progress_state["last_fragment"] = frag
                        progress_state["last_fragment_total"] = frag_total

                    if real_progress or not zero_or_unknown:
                        progress_state["last_real_progress_time"] = time.time()
                        progress_state["last_zero_speed_time"] = None
                    elif zero_or_unknown and progress_state.get("last_zero_speed_time") is None:
                        progress_state["last_zero_speed_time"] = time.time()

                if proc is not None:
                    runtime_update = {
                        "last_output_monotonic": last_output_time,
                        "last_output_line": clean,
                        "recent_output_tail": self._recent_output_tail(output_lines),
                    }
                    if progress_state.get("seen"):
                        runtime_update.update({
                            "last_progress_percent": progress_state.get("last_percent"),
                            "last_progress_fragment": progress_state.get("last_fragment"),
                            "last_progress_fragment_total": progress_state.get("last_fragment_total"),
                            "last_progress_speed": progress_state.get("last_speed_text"),
                            "last_real_progress_age_sec": round(
                                time.time() - float(progress_state.get("last_real_progress_time", time.time())), 1
                            ),
                        })
                    self._set_ytdlp_runtime(proc.pid, runtime_update)
                last_progress_log = self._handle_streamed_yt_dlp_line(
                    clean, context, last_progress_log
                )

        try:
            proc = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding='utf-8',
                errors='replace',
                bufsize=1,
                env=self._utf8_subprocess_env(),
                creationflags=self.subprocess_flags
            )
            self._register_process(proc)
            self._set_ytdlp_runtime(proc.pid, {
                "operation": operation,
                "url": context.get("url"),
                "attempt": context.get("attempt"),
                "strategy": context.get("strategy"),
                "quality": context.get("quality"),
                "target_dir": context.get("target_dir"),
                "expected_video_id": context.get("expected_video_id"),
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "started_monotonic": started,
                "last_output_monotonic": started,
                "last_output_line": None,
                "recent_output_tail": "",
                "command_pretty": self._command_to_string(command),
            })
            self._record_active_ytdlp_snapshot(
                "Запущена попытка yt-dlp: стартовый диагностический снимок",
                "INFO", "yt_dlp_attempt_started", command, context, output_lines
            )

            def reader() -> None:
                try:
                    if proc and proc.stdout:
                        for line in proc.stdout:
                            output_queue.put(line)
                except Exception as e:
                    output_queue.put(f"[reader-error] {type(e).__name__}: {e}\n")

            reader_thread = threading.Thread(target=reader, daemon=True)
            reader_thread.start()

            while True:
                drained = False
                while True:
                    try:
                        line = output_queue.get_nowait()
                    except queue.Empty:
                        break
                    append_output(line)
                    drained = True

                returncode = proc.poll()
                now = time.time()
                if returncode is not None:
                    reader_thread.join(timeout=2)
                    while True:
                        try:
                            line = output_queue.get_nowait()
                        except queue.Empty:
                            break
                        append_output(line)
                    stdout = "\n".join(output_lines)
                    if returncode == 0:
                        self._record_active_ytdlp_snapshot(
                            "yt-dlp успешно завершил попытку",
                            "INFO", "yt_dlp_attempt_succeeded", command,
                            {
                                **context,
                                "returncode": returncode,
                                "elapsed_sec": round(now - started, 2),
                            },
                            output_lines
                        )
                    return subprocess.CompletedProcess(command, returncode, stdout, "")

                if allow_cancel and self.cancel_flag.is_set():
                    self._record_active_ytdlp_snapshot(
                        "yt-dlp остановлен пользователем: снимок перед завершением процесса",
                        "WARNING", "yt_dlp_cancel_before_terminate", command,
                        {**context, "elapsed_sec": round(now - started, 2)}, output_lines
                    )
                    self.terminate_process(proc)
                    reader_thread.join(timeout=3)
                    while True:
                        try:
                            line = output_queue.get_nowait()
                        except queue.Empty:
                            break
                        append_output(line)
                    raise CommandCancelledError("Операция отменена пользователем")

                if now - last_heartbeat >= YT_DLP_HEARTBEAT_INTERVAL_SEC:
                    no_output_for = now - last_output_time
                    last_line = output_lines[-1] if output_lines else "вывода от yt-dlp ещё не было"
                    self.log(
                        f"⏳ yt-dlp работает {int(now - started)} сек | "
                        f"нет нового вывода {int(no_output_for)} сек | {last_line[:120]}",
                        "INFO"
                    )
                    heartbeat_context = {
                        **context,
                        "elapsed_sec": round(now - started, 2),
                        "no_output_for_sec": round(no_output_for, 2),
                        "last_output_line": last_line,
                        "process_poll": returncode,
                    }
                    if progress_state.get("seen"):
                        heartbeat_context["progress_watchdog"] = {
                            "last_percent": progress_state.get("last_percent"),
                            "last_fragment": progress_state.get("last_fragment"),
                            "last_fragment_total": progress_state.get("last_fragment_total"),
                            "last_speed_text": progress_state.get("last_speed_text"),
                            "last_real_progress_age_sec": round(now - float(progress_state.get("last_real_progress_time", now)), 2),
                            "zero_speed_age_sec": (
                                round(now - float(progress_state["last_zero_speed_time"]), 2)
                                if progress_state.get("last_zero_speed_time") else 0
                            ),
                            "last_progress_line": progress_state.get("last_progress_line"),
                        }
                    self._record_active_ytdlp_snapshot(
                        "Heartbeat yt-dlp: процесс ещё работает",
                        "INFO", "yt_dlp_heartbeat", command,
                        heartbeat_context,
                        output_lines
                    )
                    last_heartbeat = now

                if progress_state.get("seen"):
                    should_skip, skip_reason, skip_diag = self.should_skip_problematic_slow_download(
                        progress_state, started, now
                    )
                    if should_skip:
                        skip_context = {
                            **context,
                            "skip_reason": skip_reason,
                            "slow_download_diagnostic": skip_diag,
                        }
                        self._record_active_ytdlp_snapshot(
                            "Видео скачивается слишком медленно — пропускаю его и записываю в отдельный список",
                            "WARNING", "yt_dlp_problematic_slow_download_skip", command,
                            skip_context, output_lines
                        )
                        self.terminate_process(proc)
                        raise ProblematicDownloadSkipped(
                            str(context.get("url") or ""), skip_reason, skip_context
                        )

                    progress_stalled_for = now - float(progress_state.get("last_real_progress_time", now))
                    zero_started = progress_state.get("last_zero_speed_time")
                    zero_speed_for = now - float(zero_started) if zero_started else 0
                    if (
                        progress_stalled_for >= YT_DLP_PROGRESS_STALL_TIMEOUT_SEC
                        or zero_speed_for >= YT_DLP_ZERO_SPEED_STALL_TIMEOUT_SEC
                    ):
                        err = subprocess.TimeoutExpired(command, int(progress_stalled_for),
                                                        output="\n".join(output_lines), stderr="")
                        self._record_active_ytdlp_snapshot(
                            "yt-dlp пишет вывод, но реальный прогресс скачивания застыл — перезапускаю попытку",
                            "ERROR", "yt_dlp_progress_stalled", command,
                            {
                                **context,
                                "elapsed_sec": round(now - started, 2),
                                "progress_stalled_for_sec": round(progress_stalled_for, 2),
                                "zero_speed_for_sec": round(zero_speed_for, 2),
                                "progress_stall_timeout_sec": YT_DLP_PROGRESS_STALL_TIMEOUT_SEC,
                                "zero_speed_stall_timeout_sec": YT_DLP_ZERO_SPEED_STALL_TIMEOUT_SEC,
                                "last_percent": progress_state.get("last_percent"),
                                "last_fragment": progress_state.get("last_fragment"),
                                "last_fragment_total": progress_state.get("last_fragment_total"),
                                "last_speed_text": progress_state.get("last_speed_text"),
                                "last_progress_line": progress_state.get("last_progress_line"),
                            },
                            output_lines, err
                        )
                        self.terminate_process(proc)
                        raise err

                if now - last_output_time >= YT_DLP_INACTIVITY_TIMEOUT_SEC:
                    err = subprocess.TimeoutExpired(command, YT_DLP_INACTIVITY_TIMEOUT_SEC,
                                                    output="\n".join(output_lines), stderr="")
                    self._record_active_ytdlp_snapshot(
                        f"yt-dlp не отдавал новый вывод {YT_DLP_INACTIVITY_TIMEOUT_SEC} сек — считаю попытку зависшей",
                        "ERROR", "yt_dlp_inactivity_timeout", command,
                        {
                            **context,
                            "elapsed_sec": round(now - started, 2),
                            "inactivity_timeout_sec": YT_DLP_INACTIVITY_TIMEOUT_SEC,
                            "last_output_line": output_lines[-1] if output_lines else None,
                        },
                        output_lines, err
                    )
                    self.terminate_process(proc)
                    raise err

                if now - started >= timeout:
                    err = subprocess.TimeoutExpired(command, timeout,
                                                    output="\n".join(output_lines), stderr="")
                    self._record_active_ytdlp_snapshot(
                        f"Тайм-аут команды после {timeout} сек.",
                        "ERROR", operation, command,
                        {**context, "elapsed_sec": round(now - started, 2)},
                        output_lines, err
                    )
                    self.terminate_process(proc)
                    raise err

                time.sleep(0.2 if drained else 0.5)

        except ProblematicDownloadSkipped:
            raise
        except CommandCancelledError as e:
            self.record_problem(
                "Команда остановлена по запросу пользователя",
                "WARNING", operation, context, e,
                command, "\n".join(output_lines), ""
            )
            raise
        except Exception as e:
            if not isinstance(e, subprocess.TimeoutExpired):
                self.record_problem(
                    "Ошибка запуска или выполнения потоковой команды",
                    "ERROR", operation, context, e,
                    command, "\n".join(output_lines), ""
                )
            raise
        finally:
            if proc is not None:
                self._remove_ytdlp_runtime(proc.pid)
                self._unregister_process(proc)

    def run_command(self, command: List[str], timeout: int,
                    operation: str, context: Optional[Dict] = None,
                    allow_cancel: bool = True) -> subprocess.CompletedProcess:
        if operation == "yt_dlp_download":
            return self.run_command_streamed(command, timeout, operation, context, allow_cancel)

        started = time.time()
        last_heartbeat = started
        proc: Optional[subprocess.Popen] = None
        stdout = ""
        stderr = ""
        try:
            proc = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8', errors='replace',
                env=self._utf8_subprocess_env(),
                creationflags=self.subprocess_flags
            )
            self._register_process(proc)

            while True:
                if allow_cancel and self.cancel_flag.is_set():
                    self.terminate_process(proc)
                    try:
                        stdout, stderr = proc.communicate(timeout=5)
                    except Exception:
                        stdout, stderr = "", ""
                    raise CommandCancelledError("Операция отменена пользователем")

                try:
                    stdout, stderr = proc.communicate(timeout=0.5)
                    return subprocess.CompletedProcess(command, proc.returncode, stdout, stderr)
                except subprocess.TimeoutExpired:
                    now = time.time()
                    if operation == "yt_dlp_download" and now - last_heartbeat >= 60:
                        elapsed = int(now - started)
                        strategy = (context or {}).get("strategy", "")
                        self.log(
                            f"⏳ yt-dlp ещё работает: {elapsed} сек"
                            + (f" | {strategy}" if strategy else ""),
                            "INFO"
                        )
                        last_heartbeat = now
                    if now - started >= timeout:
                        self.terminate_process(proc)
                        try:
                            stdout, stderr = proc.communicate(timeout=5)
                        except Exception:
                            stdout, stderr = "", ""
                        err = subprocess.TimeoutExpired(command, timeout, output=stdout, stderr=stderr)
                        self.record_problem(
                            f"Тайм-аут команды после {timeout} сек.",
                            "ERROR", operation, context, err,
                            command, stdout, stderr
                        )
                        raise err
        except CommandCancelledError as e:
            self.record_problem(
                "Команда остановлена по запросу пользователя",
                "WARNING", operation, context, e,
                command, stdout, stderr
            )
            raise
        except Exception as e:
            if not isinstance(e, subprocess.TimeoutExpired):
                self.record_problem(
                    "Ошибка запуска или выполнения команды",
                    "ERROR", operation, context, e,
                    command, stdout, stderr
                )
            raise
        finally:
            if proc is not None:
                self._unregister_process(proc)

    def signal_handler(self, signum, frame) -> None:
        self.on_closing()

    def load_settings(self) -> None:
        default_settings = {
            "save_path": str(Path.home() / "Downloads"),
            "quality": "1080p",
            "audio_quality": "320k",
            "concurrent_downloads": 2,
            "download_subtitles": False,
            "write_problem_logs": True,
            "proxy_enabled": False,
            "proxy_url": "",
            "merge_audio": False,
            "split_hours": MAX_SPLIT_HOURS,
            "urls": []
        }
        try:
            if self.settings_file.exists():
                with open(self.settings_file, 'r', encoding='utf-8') as f:
                    self.settings = json.load(f)
                for key, value in default_settings.items():
                    if key not in self.settings:
                        self.settings[key] = value
            else:
                self.settings = default_settings
        except Exception as e:
            self.file_logger.error(f"load_settings: {e}")
            self.settings = default_settings

        # FIX #5: корректный clamp + защита от битого JSON/ручного ввода.
        self.settings["split_hours"] = self.safe_int_setting(
            "split_hours", MAX_SPLIT_HOURS, 0, MAX_SPLIT_HOURS
        )
        self.settings["concurrent_downloads"] = self.safe_int_setting(
            "concurrent_downloads", 2, 1, MAX_CONCURRENT_DL
        )
        # Качество видео больше не выбирается пользователем: всегда авто до 1080p.
        self.settings["quality"] = "1080p"
        self.settings["proxy_enabled"] = bool(self.settings.get("proxy_enabled", False))
        self.settings["proxy_url"] = str(self.settings.get("proxy_url", "") or "").strip()
        self.settings["write_problem_logs"] = bool(
            self.settings.get("write_problem_logs", True)
        )
        self.problem_logging_enabled = self.settings["write_problem_logs"]
        # После полной загрузки настроек синхронизируем файловые обработчики логгера.
        if hasattr(self, "file_logger"):
            self.setup_logger()

    def save_settings(self) -> None:
        try:
            self.settings_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.settings_file, 'w', encoding='utf-8') as f:
                json.dump(self.settings, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.log(f"Ошибка сохранения настроек: {str(e)}", "ERROR")

    # ─────────────────────────────────────────────
    # ОТСЛЕЖИВАНИЕ СКАЧАННЫХ ВИДЕО
    # ─────────────────────────────────────────────

    def load_download_log(self) -> None:
        """Загрузка лога скачанных видео за срок хранения."""
        cutoff = self.retention_cutoff()

        # Основной машинный лог: Логи скачивания/Сессии скачанных/downloaded_log_*.txt
        try:
            if self.downloaded_sessions_dir.exists():
                for session_file in sorted(self.downloaded_sessions_dir.glob("downloaded_log_*.txt")):
                    if not self.is_recent_log_file(session_file, cutoff):
                        continue
                    with open(session_file, 'r', encoding='utf-8') as f:
                        for line in f:
                            line = line.strip()
                            if not line or line.startswith("#"):
                                continue
                            parts = line.split('\t')
                            if parts:
                                url_hash = parts[0].strip()
                                if url_hash:
                                    self.downloaded_url_hashes.add(url_hash)
                            if len(parts) > 1:
                                video_id = parts[1].strip()
                                if video_id and video_id != "-":
                                    self.downloaded_video_ids.add(video_id)
                            if len(parts) > 2:
                                self.remember_downloaded_url_in_memory(parts[2].strip())
        except Exception as e:
            self.file_logger.error(f"load_download_log (session files): {e}")

        # Дополнительная страховка: человекочитаемые История ссылок/downloaded_*.txt.
        # Если пользователь перенёс только История ссылок или старый машинный лог был очищен,
        # защита от повторного добавления всё равно сработает.
        try:
            if self.history_dir.exists():
                history_patterns = ["downloaded_*.txt", "downloaded_history.txt"]
                for pattern in history_patterns:
                    for history_file in sorted(self.history_dir.glob(pattern)):
                        if not self.is_recent_log_file(history_file, cutoff):
                            continue
                        text = history_file.read_text(encoding='utf-8', errors='replace')
                        for found_url in self.extract_urls_from_text(text):
                            self.remember_downloaded_url_in_memory(found_url)
        except Exception as e:
            self.file_logger.error(f"load_download_log (История ссылок): {e}")

    def save_download_log(self, url: str) -> None:
        """
        Сохранение URL и video_id в сессионные логи.
        Новые записи идут по одному файлу на сессию и хранятся
        DOWNLOAD_HISTORY_RETENTION_DAYS дней.
        Старые общие download_log.txt/video_ids.txt не используются: в них нет дат по строкам.
        """
        normalized_url = self.normalize_url_for_history(url)
        url_hash = hashlib.md5(normalized_url.encode()).hexdigest()
        video_id = self.extract_video_id(url)

        with self.file_lock:
            self.downloaded_url_hashes.add(url_hash)
            # Совместимость со старыми точными hash по сырой ссылке.
            self.downloaded_url_hashes.add(hashlib.md5(url.strip().encode()).hexdigest())
            if video_id:
                self.downloaded_video_ids.add(video_id)

            try:
                history_file, session_log_file = self.ensure_download_session_files()
                self.current_session_download_count += 1

                with open(history_file, 'a', encoding='utf-8') as f:
                    f.write(f"{self.current_session_download_count}. {url}\n")

                with open(session_log_file, 'a', encoding='utf-8') as f:
                    f.write(f"{url_hash}\t{video_id or '-'}\t{normalized_url}\t{url}\n")
            except Exception as e:
                self.file_logger.error(f"save_download_log (session): {e}")
                self.record_problem(
                    "Не удалось записать скачанное видео в сессионные логи",
                    "ERROR", "save_download_log",
                    {
                        "url": url,
                        "url_hash": url_hash,
                        "normalized_url": normalized_url,
                        "video_id": video_id,
                        "history_file": str(self.current_history_session_file),
                        "session_log_file": str(self.current_download_log_session_file),
                    },
                    e
                )

    def is_url_downloaded(self, url: str) -> bool:
        if not url:
            return False
        normalized_url = self.normalize_url_for_history(url)
        url_hash = hashlib.md5(normalized_url.encode()).hexdigest()
        if url_hash in self.downloaded_url_hashes:
            return True
        # Совместимость со старыми hash, где считали прямо от введённой строки.
        raw_hash = hashlib.md5(url.strip().encode()).hexdigest()
        if raw_hash in self.downloaded_url_hashes:
            return True
        video_id = self.extract_video_id(url)
        if video_id and video_id in self.downloaded_video_ids:
            return True
        return False

    def is_youtube_url(self, url: str) -> bool:
        try:
            host = (urllib.parse.urlparse(url.strip()).hostname or "").lower().rstrip(".")
            return host == "youtube.com" or host.endswith(".youtube.com") or host == "youtu.be"
        except Exception:
            return False

    def run_yt_dlp_with_download_slot(self, url: str) -> Optional[str]:
        # Раньше здесь был глобальный Lock на YouTube: даже если в настройках стояло
        # 2-3 потока, реально качался только 1 ролик, а остальные висели на строке
        # «Жду свободный слот YouTube». Это делало большие очереди очень медленными.
        # Теперь параллельность контролирует только ThreadPoolExecutor через настройку
        # «Потоков», а устойчивость даёт список fallback-стратегий внутри method_yt_dlp.
        return self.method_yt_dlp(url)

    def clear_download_log(self) -> None:
        try:
            if self.log_file.exists():
                self.log_file.unlink()
            if self.video_ids_file.exists():
                self.video_ids_file.unlink()
            if self.downloaded_sessions_dir.exists():
                for session_file in self.downloaded_sessions_dir.glob("downloaded_log_*.txt"):
                    try:
                        session_file.unlink()
                    except OSError as e:
                        self.file_logger.error(f"clear_download_log (session file): {e}")

            # Так как защита теперь умеет читать История ссылок, кнопка очистки должна
            # очищать и человекочитаемую историю скачанных, иначе после перезапуска
            # программа снова будет считать эти видео уже скачанными.
            if self.history_dir.exists():
                for pattern in ("downloaded_*.txt", "downloaded_history.txt"):
                    for history_file in self.history_dir.glob(pattern):
                        try:
                            history_file.unlink()
                        except OSError as e:
                            self.file_logger.error(f"clear_download_log (history file): {e}")

            self.downloaded_url_hashes.clear()
            self.downloaded_video_ids.clear()
            self.current_history_session_file = None
            self.current_download_log_session_file = None
            self.current_session_download_count = 0
            self.log("✓ Лог скачанных видео очищен. Теперь видео можно скачивать повторно.", "SUCCESS")

            if self.video_list:
                for video in self.video_list:
                    video['is_downloaded'] = False
                self.display_video_list()

        except Exception as e:
            self.log(f"Ошибка очистки лога: {str(e)}", "ERROR")

    def extract_video_id(self, url: str) -> Optional[str]:
        if not url:
            return None
        patterns = [
            r'(?:youtube\.com/watch\?(?:.*&)?v=|youtu\.be/)([A-Za-z0-9_-]{11})',
            r'youtube\.com/embed/([A-Za-z0-9_-]{11})',
            r'youtube\.com/v/([A-Za-z0-9_-]{11})',
            r'youtube\.com/shorts/([A-Za-z0-9_-]{11})',
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None

    def extract_urls_from_text(self, text: str) -> List[str]:
        """Достаёт URL из человекочитаемых history-файлов и логов."""
        if not text:
            return []
        urls: List[str] = []
        for match in re.findall(r'https?://[^\s<>"\']+', text):
            urls.append(match.rstrip('.,);]'))
        return urls

    def normalize_url_for_history(self, url: str) -> str:
        """
        Канонический ключ истории.
        Для YouTube разные варианты одной ссылки (watch, youtu.be, shorts, лишние
        параметры плейлиста/таймкода) считаются одним и тем же видео.
        """
        url = (url or "").strip()
        video_id = self.extract_video_id(url)
        if video_id:
            return f"https://www.youtube.com/watch?v={video_id}"

        try:
            parsed = urllib.parse.urlparse(url)
            scheme = (parsed.scheme or "https").lower()
            host = (parsed.hostname or "").lower().rstrip(".")
            path = urllib.parse.urlunparse(("", "", parsed.path or "", "", "", ""))

            # Для не-YouTube сохраняем полезные параметры, но убираем мусорные трекеры.
            drop_prefixes = ("utm_",)
            drop_exact = {"fbclid", "gclid", "si", "feature", "pp", "ab_channel"}
            query_pairs = []
            for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True):
                key_l = key.lower()
                if key_l in drop_exact or any(key_l.startswith(p) for p in drop_prefixes):
                    continue
                query_pairs.append((key, value))
            query = urllib.parse.urlencode(query_pairs, doseq=True)
            return urllib.parse.urlunparse((scheme, host, path, "", query, ""))
        except Exception:
            return url

    def url_identity(self, url: str) -> str:
        video_id = self.extract_video_id(url)
        if video_id:
            return f"youtube:{video_id}"
        return f"url:{self.normalize_url_for_history(url)}"

    def deduplicate_urls_by_identity(self, urls: List[str]) -> Tuple[List[str], List[str]]:
        """Убирает повторы внутри очереди, включая разные YouTube-ссылки на один video_id."""
        unique: List[str] = []
        duplicates: List[str] = []
        seen: Set[str] = set()
        for url in urls:
            clean = (url or "").strip()
            if not clean:
                continue
            key = self.url_identity(clean)
            if key in seen:
                duplicates.append(clean)
                continue
            seen.add(key)
            unique.append(clean)
        return unique, duplicates

    def remember_downloaded_url_in_memory(self, url: str) -> None:
        """Добавляет URL в память истории без записи на диск. Вызывать можно при загрузке логов."""
        if not url:
            return
        normalized = self.normalize_url_for_history(url)
        if normalized:
            self.downloaded_url_hashes.add(hashlib.md5(normalized.encode()).hexdigest())
        raw = url.strip()
        if raw and raw != normalized:
            # Совместимость со старыми session-логами, где hash считался от сырой строки.
            self.downloaded_url_hashes.add(hashlib.md5(raw.encode()).hexdigest())
        video_id = self.extract_video_id(url)
        if video_id:
            self.downloaded_video_ids.add(video_id)

    # ─────────────────────────────────────────────
    # ДОБАВЛЕНИЕ URL С ПРОВЕРКОЙ
    # ─────────────────────────────────────────────

    def add_urls_with_check(self, urls: List[str]) -> int:
        already_downloaded: List[str] = []
        new_urls: List[str] = []
        duplicate_in_batch: List[str] = []
        # Дедупликация внутри входного списка: одинаковый YouTube video_id = один ролик,
        # даже если ссылки выглядят по-разному.
        seen_in_batch: Set[str] = set()

        for url in urls:
            url = url.strip()
            if not url:
                continue
            identity = self.url_identity(url)
            if identity in seen_in_batch:
                duplicate_in_batch.append(url)
                continue
            seen_in_batch.add(identity)

            if self.is_url_downloaded(url):
                already_downloaded.append(url)
            else:
                new_urls.append(url)

        if duplicate_in_batch:
            self.log(f"ℹ️ Убрано {len(duplicate_in_batch)} дублей внутри добавляемого списка", "INFO")

        if already_downloaded:
            msg = f"⚠️ {len(already_downloaded)} видео уже были скачаны и НЕ добавлены в очередь:\n\n"
            for u in already_downloaded[:8]:
                msg += f"• {u[:90]}\n"
            if len(already_downloaded) > 8:
                msg += f"... и ещё {len(already_downloaded) - 8} видео\n"
            msg += "\nЕсли хотите скачать их повторно — сначала нажмите «Очистить лог скачанных»."
            messagebox.showwarning("Видео уже скачаны", msg)
            self.log(f"⚠️ Заблокировано {len(already_downloaded)} уже скачанных URL", "WARNING")

        if new_urls:
            current_text = self.url_text.get("1.0", tk.END).strip()
            if current_text:
                self.url_text.insert(tk.END, "\n" + "\n".join(new_urls))
            else:
                self.url_text.insert(tk.END, "\n".join(new_urls))
            self.log(f"✅ Добавлено {len(new_urls)} новых URL", "SUCCESS")

        return len(new_urls)

    # ─────────────────────────────────────────────
    # ВАЛИДАЦИЯ URL
    # ─────────────────────────────────────────────

    def validate_url(self, url: str) -> bool:
        if not url or not url.strip():
            return False
        # FIX #8: только управляющие символы — переносы строк и табы,
        # которые реально не могут быть частью URL и ломают subprocess.
        # & % = ? # — нормальные части YouTube-URL, НЕ блокируем.
        if any(c in url for c in ('\n', '\r', '\t')):
            self.log(f"⚠️ URL содержит управляющие символы: {url[:60]}", "WARNING")
            return False
        try:
            result = urllib.parse.urlparse(url.strip())
            if not all([result.scheme in ('http', 'https'), result.netloc]):
                return False
            allowed_domains = ['youtube.com', 'youtu.be', 'vimeo.com', 'dailymotion.com']
            host = (result.hostname or "").lower().rstrip(".")
            if not any(host == domain or host.endswith("." + domain)
                       for domain in allowed_domains):
                self.log(f"⚠️ Неподдерживаемый домен: {result.netloc}", "WARNING")
                return False
            return True
        except Exception:
            return False

    # ─────────────────────────────────────────────
    # ЗАВИСИМОСТИ
    # ─────────────────────────────────────────────

    def check_dependencies(self) -> None:
        self.missing_deps = []
        self.missing_pip_packages = []
        self.optional_missing_deps = []

        try:
            r = subprocess.run(
                ["yt-dlp", "--version"], capture_output=True,
                timeout=5, creationflags=self.subprocess_flags
            )
            if r.returncode != 0:
                self.missing_deps.append("yt-dlp")
                self.missing_pip_packages.append("yt-dlp")
        except (subprocess.SubprocessError, FileNotFoundError):
            self.missing_deps.append("yt-dlp")
            self.missing_pip_packages.append("yt-dlp")

        try:
            r = subprocess.run(
                ["ffmpeg", "-version"], capture_output=True,
                timeout=5, creationflags=self.subprocess_flags
            )
            if r.returncode != 0:
                self.missing_deps.append("ffmpeg")
        except (subprocess.SubprocessError, FileNotFoundError):
            self.missing_deps.append("ffmpeg")

        js_runtime_found = False
        js_runtime_name  = None

        for runtime, args in [("deno", ["deno", "--version"]),
                               ("node", ["node", "--version"])]:
            try:
                r = subprocess.run(
                    args, capture_output=True, timeout=5, text=True,
                    encoding='utf-8', errors='replace',
                    creationflags=self.subprocess_flags
                )
                if r.returncode == 0:
                    js_runtime_found = True
                    js_runtime_name = ("Deno" if runtime == "deno"
                                       else f"Node.js {r.stdout.strip()}")
                    break
            except Exception:
                pass

        self.js_runtime_found = js_runtime_found
        self.js_runtime_name  = js_runtime_name
        if not js_runtime_found:
            self.optional_missing_deps.append("JavaScript Runtime (Deno или Node.js)")

    # ─────────────────────────────────────────────
    # ЗАГРУЗКА ВИДЕО — МЕТОД YT-DLP
    # ─────────────────────────────────────────────

    def is_supported_video_file(self, path: Path) -> bool:
        return path.suffix.lower().lstrip(".") in SUPPORTED_VIDEO_EXTENSIONS

    def snapshot_video_files(self, directory: Path) -> Set[Path]:
        if not directory.exists():
            return set()
        files: Set[Path] = set()
        for ext in SUPPORTED_VIDEO_EXTENSIONS:
            files.update(directory.glob(f"*.{ext}"))
        return {
            f for f in files
            if ".temp." not in f.name
            and ".part" not in f.name
            and not re.search(r"\.f\d+\.(mp4|webm|m4a)$", f.name, re.IGNORECASE)
        }

    def normalize_downloaded_path(self, path: str) -> str:
        for ext in SUPPORTED_VIDEO_EXTENSIONS:
            temp_suffix = f".temp.{ext}"
            if path.endswith(temp_suffix):
                return path[:-len(temp_suffix)] + f".{ext}"
        return path

    def make_unique_media_path(self, desired_path: Path) -> Path:
        if not desired_path.exists():
            return desired_path
        for counter in range(2, 1000):
            candidate = desired_path.with_name(
                f"{desired_path.stem} ({counter}){desired_path.suffix}"
            )
            if not candidate.exists():
                return candidate
        return desired_path

    def move_video_to_manual_processing(self, video_path: str, reason: str) -> str:
        """Безопасно переносит готовый проблемный видеофайл для ручной обработки."""
        source = Path(video_path)
        try:
            if not source.exists() or not source.is_file():
                return str(source)

            self.manual_processing_dir.mkdir(parents=True, exist_ok=True)
            if source.parent.resolve() == self.manual_processing_dir.resolve():
                return str(source)

            target = self.make_unique_media_path(self.manual_processing_dir / source.name)
            with self.file_lock:
                shutil.move(str(source), str(target))

            self.log(
                f"📦 Видео перенесено в Обработать вручную: {target.name}",
                "MANUAL_FOLDER_LINK"
            )
            return str(target)
        except Exception as e:
            self.log(
                f"❌ Не удалось перенести видео в «Обработать вручную»: {source.name} — {e}",
                "ERROR"
            )
            self.record_problem(
                "Не удалось перенести проблемный видеофайл для ручной обработки",
                "ERROR", "move_video_to_manual_processing",
                {
                    "source_video": str(source),
                    "manual_processing_dir": str(self.manual_processing_dir),
                    "reason": reason,
                },
                e
            )
            return str(source)

    def remove_video_id_suffix_from_filename(self, path: str,
                                             expected_video_id: Optional[str]) -> str:
        if not path or not expected_video_id:
            return path

        try:
            current_path = Path(path)
            if not current_path.exists() or not self.is_supported_video_file(current_path):
                return path

            id_suffix = f" [{expected_video_id}]"
            if not current_path.stem.endswith(id_suffix):
                return path

            clean_stem = current_path.stem[:-len(id_suffix)].rstrip()
            if not clean_stem:
                return path

            with self.file_lock:
                desired_path = current_path.with_name(f"{clean_stem}{current_path.suffix}")
                target_path = self.make_unique_media_path(desired_path)
                if target_path == current_path:
                    return path
                current_path.rename(target_path)

            self.log(f"📝 Имя файла очищено: {target_path.name}", "INFO")
            return str(target_path)
        except Exception as e:
            self.record_problem(
                "Не удалось убрать YouTube ID из имени готового файла",
                "WARNING", "cleanup_downloaded_filename",
                {
                    "path": path,
                    "expected_video_id": expected_video_id,
                },
                e
            )
            return path

    def build_youtube_ejs_args(self, url: str, ejs_mode: str = "github") -> List[str]:
        """
        Аргументы для новых требований YouTube/yt-dlp.

        Свежие логи показали предупреждение:
        "Remote components challenge solver script ... were skipped" и
        "n challenge solving failed". Это значит, что yt-dlp видит Deno/Node,
        но не получает EJS-скрипты для решения YouTube n-challenge. Без этого
        могут пропадать форматы или выдаваться CDN-ссылки, которые потом
        падают на googlevideo.com с timeout/SSL EOF.
        """
        if not self.is_youtube_url(url):
            # Ветка нужна только чтобы статические анализаторы не ругались:
            # метод ниже вызывается только для YouTube. Возвращаем пусто для
            # любых будущих не-YouTube вызовов.
            return []

        args: List[str] = []
        deno_path = shutil.which("deno")
        node_path = shutil.which("node")

        # Deno включён в yt-dlp по умолчанию, но указываем явно: так в логах
        # сразу видно, каким runtime программа пытается пользоваться.
        if deno_path:
            args.extend(["--js-runtimes", "deno"])
        if node_path:
            # Опция может повторяться. Node — запасной runtime, если Deno
            # найден, но не подходит конкретному окружению.
            args.extend(["--js-runtimes", "node"])

        if ejs_mode == "none":
            return args
        if ejs_mode == "npm" and deno_path:
            args.extend(["--remote-components", YT_DLP_EJS_NPM_COMPONENT])
        else:
            args.extend(["--remote-components", YT_DLP_EJS_GITHUB_COMPONENT])
        return args

    def youtube_ejs_status_text(self, ejs_mode: str) -> str:
        deno_found = bool(shutil.which("deno"))
        node_found = bool(shutil.which("node"))
        if ejs_mode == "npm":
            component = YT_DLP_EJS_NPM_COMPONENT
        elif ejs_mode == "none":
            component = "без remote-components"
        else:
            component = YT_DLP_EJS_GITHUB_COMPONENT
        return (
            f"EJS={component}, Deno={'есть' if deno_found else 'нет'}, "
            f"Node={'есть' if node_found else 'нет'}"
        )

    def normalize_proxy_url(self, proxy_url: Optional[str]) -> str:
        """Нормализует proxy URL для yt-dlp.

        Поддерживаются:
        - socks5://127.0.0.1:1080
        - socks5h://127.0.0.1:1080
        - http://127.0.0.1:7890
        - https://host:port
        Если пользователь ввёл только host:port, считаем это socks5://host:port.
        """
        proxy_url = str(proxy_url or "").strip()
        if not proxy_url:
            return ""
        if "://" not in proxy_url:
            proxy_url = "socks5://" + proxy_url
        parsed = urllib.parse.urlsplit(proxy_url)
        if parsed.scheme.lower() not in {"http", "https", "socks4", "socks4a", "socks5", "socks5h"}:
            return ""
        if not parsed.netloc:
            return ""
        return proxy_url

    def get_proxy_url(self) -> str:
        try:
            enabled = bool(self.proxy_enabled.get()) if hasattr(self, "proxy_enabled") else bool(
                self.settings.get("proxy_enabled", False)
            )
        except Exception:
            enabled = bool(getattr(self, "settings", {}).get("proxy_enabled", False))
        if not enabled:
            return ""
        try:
            raw_proxy = self.proxy_url.get() if hasattr(self, "proxy_url") else self.settings.get("proxy_url", "")
        except Exception:
            raw_proxy = self.settings.get("proxy_url", "")
        return self.normalize_proxy_url(raw_proxy)

    def build_proxy_args(self) -> List[str]:
        proxy_url = self.get_proxy_url()
        return ["--proxy", proxy_url] if proxy_url else []

    def proxy_status_text(self) -> str:
        proxy_url = self.get_proxy_url()
        if proxy_url:
            return f"proxy={self.mask_proxy_url(proxy_url)}"
        try:
            enabled = bool(self.proxy_enabled.get()) if hasattr(self, "proxy_enabled") else bool(
                self.settings.get("proxy_enabled", False)
            )
        except Exception:
            enabled = False
        if enabled:
            return "proxy включён, но URL пустой или некорректный"
        return "proxy выключен"

    def on_proxy_settings_change(self, event=None) -> None:
        """Сохраняет proxy-настройки без перезапуска программы."""
        if hasattr(self, "settings"):
            self.settings["proxy_enabled"] = bool(self.proxy_enabled.get()) if hasattr(self, "proxy_enabled") else False
            self.settings["proxy_url"] = (
                str(self.proxy_url.get()).strip() if hasattr(self, "proxy_url") else ""
            )
        self.save_current_settings()

    def test_proxy_with_ytdlp(self) -> None:
        """Быстрый тест: сможет ли yt-dlp через proxy получить служебную страницу YouTube."""
        proxy_url = self.get_proxy_url()
        if not proxy_url:
            messagebox.showwarning(
                "Прокси не задан",
                "Включите галочку «Прокси yt-dlp» и укажите адрес, например:\n"
                f"{DEFAULT_PROXY_EXAMPLE}"
            )
            return

        def worker():
            masked_proxy = self.mask_proxy_url(proxy_url)
            self.log(f"🌐 Проверяю proxy для yt-dlp: {masked_proxy}", "INFO")
            cmd = [
                "yt-dlp", "--skip-download", "--no-playlist",
                "--encoding", "utf-8", "--socket-timeout", "20",
                "--proxy", proxy_url, "--get-title", "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            ]
            try:
                r = self.run_command(
                    cmd, 45, "test_ytdlp_proxy",
                    {"proxy_enabled": True, "proxy_url_masked": masked_proxy},
                    allow_cancel=False
                )
                if r.returncode == 0 and (r.stdout or "").strip():
                    self.log("✅ Proxy работает: yt-dlp получил ответ от YouTube", "SUCCESS")
                    self.root.after(0, lambda: messagebox.showinfo("Прокси работает", "✅ yt-dlp получил ответ от YouTube через proxy."))
                else:
                    tail = ((r.stderr or "") + "\n" + (r.stdout or ""))[-1000:]
                    self.log(f"❌ Proxy-тест не прошёл: {tail[:300]}", "ERROR")
                    self.record_problem(
                        "Proxy-тест yt-dlp не прошёл",
                        "ERROR", "test_ytdlp_proxy_failed",
                        {"proxy_enabled": True, "proxy_url_masked": masked_proxy},
                        command=cmd, stdout=r.stdout, stderr=r.stderr
                    )
                    self.root.after(0, lambda: messagebox.showerror("Прокси не работает", f"yt-dlp не смог пройти тест через proxy.\n\n{tail[:700]}"))
            except Exception as e:
                self.log(f"❌ Ошибка proxy-теста: {e}", "ERROR")
                self.record_problem(
                    "Исключение во время proxy-теста yt-dlp",
                    "ERROR", "test_ytdlp_proxy_exception",
                    {"proxy_enabled": True, "proxy_url_masked": masked_proxy},
                    e, command=cmd
                )
                self.root.after(0, lambda: messagebox.showerror("Прокси не работает", str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def get_ytdlp_fast_fragments(self) -> str:
        """Через VPN/TUN два видео в два потока уже дают параллельность.

        Если оставить ещё и --concurrent-fragments=2 внутри каждого yt-dlp,
        получается до четырёх одновременных соединений к googlevideo.com. По
        логам это быстрее на старте, но чаще заканчивается timeout/0.00B/s.
        """
        try:
            return "1" if int(getattr(self, "max_concurrent", 1)) >= 2 else YT_DLP_FAST_CONCURRENT_FRAGMENTS
        except Exception:
            return "1"

    def _is_youtube_auth_or_forbidden_error(self, text: str) -> bool:
        lowered = str(text or "").lower()
        markers = (
            "sign in to confirm",
            "not a bot",
            "login required",
            "http error 403",
            "forbidden",
            "unable to download api page",
        )
        return any(marker in lowered for marker in markers)

    def _note_youtube_primary_strategy_result(self, success: bool,
                                              error_text: str = "") -> None:
        with self.youtube_strategy_state_lock:
            if success:
                self.youtube_primary_success_count += 1
                self.youtube_primary_auth_failure_count = 0
            elif self._is_youtube_auth_or_forbidden_error(error_text):
                self.youtube_primary_auth_failure_count += 1

    def _apply_adaptive_youtube_strategy_order(self,
                                               strategies: List[Dict]) -> List[Dict]:
        """После серии блокировок web-клиента пробует Android до 1080p первым."""
        with self.youtube_strategy_state_lock:
            enabled = (
                self.youtube_primary_auth_failure_count
                >= YT_DLP_ADAPTIVE_PRIMARY_FAILURE_THRESHOLD
            )
            should_log = enabled and not self.youtube_adaptive_strategy_logged
            if should_log:
                self.youtube_adaptive_strategy_logged = True
        if not enabled:
            return strategies

        preferred_name = "Android Client small chunks"
        preferred = [item for item in strategies if item.get("name") == preferred_name]
        remaining = [item for item in strategies if item.get("name") != preferred_name]
        if should_log:
            self.log(
                "🧭 После повторных 403/проверок «не робот» сначала пробую "
                "Android-клиент с качеством до 1080p",
                "INFO"
            )
        return preferred + remaining

    def make_video_download_temp_dir(self, video_dir: Path, url: str, expected_video_id: Optional[str]) -> Path:
        """Отдельная временная папка для одного ролика.

        Так параллельные загрузки не видят .part/.f137/.f140 друг друга, а
        очистка мусора не может случайно удалить активный файл другого потока.
        """
        key = expected_video_id or hashlib.md5(url.strip().encode('utf-8')).hexdigest()[:12]
        safe_key = re.sub(r"[^A-Za-z0-9_.-]+", "_", key).strip("._") or "video"
        return video_dir / "_temp_downloads" / safe_key

    def cleanup_stale_download_temp_dirs(self, video_dir: Path) -> int:
        """Удаляет старые временные папки перед новой сессией."""
        temp_root = video_dir / "_temp_downloads"
        if not temp_root.exists():
            return 0
        try:
            count = sum(1 for _ in temp_root.iterdir())
        except OSError:
            count = 0
        try:
            shutil.rmtree(temp_root, ignore_errors=True)
            return count
        except Exception as e:
            self.record_problem(
                "Не удалось очистить старую папку _temp_downloads",
                "WARNING", "cleanup_stale_download_temp_dirs",
                {"temp_root": str(temp_root)}, e
            )
            return 0

    def finalize_downloaded_video_file(self, temp_video_path: str, final_video_dir: Path,
                                       expected_video_id: Optional[str]) -> Optional[str]:
        """Переносит готовое видео из временной папки в основную папку Видео."""
        if not temp_video_path:
            return None
        try:
            src = Path(temp_video_path)
            if not src.exists() or not src.is_file():
                return temp_video_path
            final_video_dir.mkdir(parents=True, exist_ok=True)
            clean_name = src.name
            if expected_video_id:
                id_suffix = f" [{expected_video_id}]"
                if src.stem.endswith(id_suffix):
                    clean_name = f"{src.stem[:-len(id_suffix)].rstrip()}{src.suffix}"
            dst = self.make_unique_media_path(final_video_dir / clean_name)
            if src.resolve() == dst.resolve():
                return str(src)
            with self.file_lock:
                shutil.move(str(src), str(dst))
            self.log(f"📦 Готовый файл перенесён в Видео: {dst.name}", "INFO")
            return str(dst)
        except Exception as e:
            self.record_problem(
                "Не удалось перенести готовое видео из временной папки",
                "ERROR", "finalize_downloaded_video_file",
                {
                    "temp_video_path": temp_video_path,
                    "final_video_dir": str(final_video_dir),
                    "expected_video_id": expected_video_id,
                },
                e
            )
            return temp_video_path

    def method_yt_dlp(self, url: str) -> Optional[str]:
        save_dir  = Path(self.save_path.get())
        # FIX #9: parents=True — создаём всю цепочку директорий
        video_dir = save_dir / "Видео"
        video_dir.mkdir(parents=True, exist_ok=True)
        quality = "1080p"

        video_path        = None
        expected_video_id = self.extract_video_id(url)
        download_temp_dir = self.make_video_download_temp_dir(video_dir, url, expected_video_id)
        download_temp_dir.mkdir(parents=True, exist_ok=True)
        video_files_before = self.snapshot_video_files(download_temp_dir)
        proxy_url = self.get_proxy_url()
        proxy_args = self.build_proxy_args()
        proxy_enabled = bool(proxy_args)
        proxy_url_masked = self.mask_proxy_url(proxy_url)

        # Быстрая первая попытка: несколько фрагментов и крупнее chunk.
        # Если сеть/YouTube начнут рвать соединение — ниже идут более осторожные
        # fallback-стратегии с мелкими chunk и 1 фрагментом.
        fast_fragments = self.get_ytdlp_fast_fragments()
        default_network_args = [
            "--http-chunk-size", YT_DLP_HTTP_CHUNK_SIZE,
            "--concurrent-fragments", fast_fragments,
        ]
        default_ipv4_network_args = [
            "--http-chunk-size", YT_DLP_HTTP_CHUNK_SIZE,
            "--concurrent-fragments", fast_fragments,
            "--force-ipv4",
        ]
        default_ipv6_network_args = [
            "--http-chunk-size", YT_DLP_SAFE_HTTP_CHUNK_SIZE,
            "--concurrent-fragments", YT_DLP_SAFE_CONCURRENT_FRAGMENTS,
            "--force-ipv6",
        ]
        safe_chunk_network_args = [
            "--http-chunk-size", YT_DLP_SAFE_HTTP_CHUNK_SIZE,
            "--concurrent-fragments", YT_DLP_SAFE_CONCURRENT_FRAGMENTS,
            "--force-ipv4",
        ]
        no_chunk_network_args = [
            "--concurrent-fragments", YT_DLP_SAFE_CONCURRENT_FRAGMENTS,
            "--force-ipv4",
        ]
        tiny_chunk_network_args = [
            "--http-chunk-size", YT_DLP_TINY_HTTP_CHUNK_SIZE,
            "--concurrent-fragments", YT_DLP_SAFE_CONCURRENT_FRAGMENTS,
            "--force-ipv4",
        ]
        no_force_ipv4_network_args = [
            "--concurrent-fragments", YT_DLP_SAFE_CONCURRENT_FRAGMENTS,
        ]
        neutral_network_args = [
            "--http-chunk-size", YT_DLP_SAFE_HTTP_CHUNK_SIZE,
            "--concurrent-fragments", YT_DLP_SAFE_CONCURRENT_FRAGMENTS,
        ]
        neutral_tiny_chunk_network_args = [
            "--http-chunk-size", YT_DLP_TINY_HTTP_CHUNK_SIZE,
            "--concurrent-fragments", YT_DLP_SAFE_CONCURRENT_FRAGMENTS,
        ]
        aria2_downloader_args = []
        if shutil.which("aria2c"):
            aria2_downloader_args = [
                "--downloader", "aria2c",
                "--downloader-args",
                (
                    "aria2c:-x 1 -s 1 -j 1 -k 1M --max-tries=8 "
                    "--retry-wait=2 --connect-timeout=20 --timeout=30 "
                    "--summary-interval=0 --console-log-level=warn"
                ),
            ]

        strategies = [
            # Сначала пробуем 1080p/лучшее до 1080p, но только одной осторожной DASH-попыткой.
            # В свежих логах две параллельные DASH-загрузки стабильно ловили Read timed out
            # с rr*.googlevideo.com. Поэтому после первой сетевой ошибки быстро уходим
            # на цельный MP4-поток и другие клиенты YouTube, а не тратим минуты на тот же CDN.
            {'name': 'EJS GitHub stable auto quality', 'fallback_format': False,
             'args': [], 'network_args': default_network_args, 'ejs_mode': 'github'},
            {'name': 'Early progressive MP4 no force IPv4', 'fallback_format': False,
             'progressive_format': True, 'args': [], 'network_args': no_force_ipv4_network_args,
             'ejs_mode': 'github'},
            {'name': 'Android Progressive MP4 early', 'fallback_format': False,
             'progressive_format': True,
             'args': ["--extractor-args", "youtube:player_client=android"],
             'network_args': no_force_ipv4_network_args, 'ejs_mode': 'github'},
            {'name': 'EJS GitHub stable auto quality IPv4', 'fallback_format': False,
             'args': [], 'network_args': default_ipv4_network_args, 'ejs_mode': 'github'},
            {'name': 'EJS GitHub stable auto quality IPv6', 'fallback_format': False,
             'args': [], 'network_args': default_ipv6_network_args, 'ejs_mode': 'github'},
            {'name': 'Early progressive MP4 IPv4 tiny chunks', 'fallback_format': False,
             'progressive_format': True, 'args': [], 'network_args': tiny_chunk_network_args,
             'ejs_mode': 'github'},
            {'name': 'Slow direct progressive MP4 90 sec timeout', 'fallback_format': False,
             'progressive_format': True, 'args': [], 'network_args': tiny_chunk_network_args,
             'ejs_mode': 'github', 'socket_timeout': YT_DLP_SLOW_SOCKET_TIMEOUT,
             'retries': YT_DLP_SLOW_RETRIES, 'fragment_retries': YT_DLP_SLOW_FRAGMENT_RETRIES},
            {'name': 'Slow direct any format 90 sec timeout', 'fallback_format': True,
             'args': [], 'network_args': tiny_chunk_network_args,
             'ejs_mode': 'github', 'socket_timeout': YT_DLP_SLOW_SOCKET_TIMEOUT,
             'retries': YT_DLP_SLOW_RETRIES, 'fragment_retries': YT_DLP_SLOW_FRAGMENT_RETRIES},
            # Если GitHub release assets недоступны, Deno умеет тянуть EJS через npm.
            {'name': 'EJS npm stable auto quality', 'fallback_format': False,
             'args': [], 'network_args': neutral_network_args, 'ejs_mode': 'npm'},
            {'name': 'EJS GitHub safe tiny chunks', 'fallback_format': False,
             'args': [], 'network_args': tiny_chunk_network_args, 'ejs_mode': 'github'},
            # Быстрый практичный fallback: один MP4-поток со звуком. Часто это 720p/360p,
            # зато он обходит часть проблем с раздельными DASH audio/video.
            {'name': 'Progressive MP4 single file', 'fallback_format': False,
             'progressive_format': True, 'args': [], 'network_args': no_chunk_network_args,
             'ejs_mode': 'github'},
            {'name': 'Android Progressive MP4 single file', 'fallback_format': False,
             'progressive_format': True,
             'args': ["--extractor-args", "youtube:player_client=android"],
             'network_args': no_chunk_network_args, 'ejs_mode': 'github'},
            {'name': 'Android Client small chunks', 'fallback_format': False,
             'args': ["--extractor-args", "youtube:player_client=android"],
             'network_args': safe_chunk_network_args, 'ejs_mode': 'github'},
            {'name': 'Android Client tiny chunks', 'fallback_format': False,
             'args': ["--extractor-args", "youtube:player_client=android"],
             'network_args': tiny_chunk_network_args, 'ejs_mode': 'github'},
            {'name': 'iOS Client without HTTP chunks', 'fallback_format': False,
             'args': ["--extractor-args", "youtube:player_client=ios"],
             'network_args': no_chunk_network_args, 'ejs_mode': 'github'},
            {'name': 'TV Embedded Client without HTTP chunks', 'fallback_format': False,
             'args': ["--extractor-args", "youtube:player_client=tv_embedded"],
             'network_args': no_chunk_network_args, 'ejs_mode': 'github'},
            {'name': 'Web Embedded Client without HTTP chunks', 'fallback_format': False,
             'args': ["--extractor-args", "youtube:player_client=web_embedded"],
             'network_args': no_chunk_network_args, 'ejs_mode': 'github'},
            {'name': 'Default Client without HTTP chunks', 'fallback_format': False,
             'args': [], 'network_args': no_chunk_network_args, 'ejs_mode': 'github'},
            {'name': 'Default Client no force IPv4', 'fallback_format': False,
             'args': [], 'network_args': no_force_ipv4_network_args, 'ejs_mode': 'github'},
            {'name': 'Default Client small chunks no force IPv4', 'fallback_format': False,
             'args': [], 'network_args': neutral_network_args, 'ejs_mode': 'github'},
            {'name': 'Any Format iOS without HTTP chunks', 'fallback_format': True,
             'args': ["--extractor-args", "youtube:player_client=ios"],
             'network_args': no_chunk_network_args, 'ejs_mode': 'github'},
            {'name': 'Any Format Mobile Web without HTTP chunks', 'fallback_format': True,
             'args': ["--extractor-args", "youtube:player_client=mweb"],
             'network_args': no_chunk_network_args, 'ejs_mode': 'github'},
            {'name': 'Default + Chrome Cookies without HTTP chunks', 'fallback_format': False,
             'args': ["--cookies-from-browser", "chrome"],
             'network_args': no_chunk_network_args, 'ejs_mode': 'github'},
            {'name': 'Any Format Default small chunks', 'fallback_format': True,
             'args': [], 'network_args': neutral_network_args, 'ejs_mode': 'github'},
            {'name': 'Any Format Default tiny chunks no force IPv4', 'fallback_format': True,
             'args': [], 'network_args': neutral_tiny_chunk_network_args, 'ejs_mode': 'github'},
            {'name': 'Any Format Chrome Cookies small chunks', 'fallback_format': True,
             'args': ["--cookies-from-browser", "chrome"],
             'network_args': neutral_network_args, 'ejs_mode': 'github'},
            # Последняя страховка: без EJS, чтобы скачать хотя бы доступный
            # progressive/низкий формат, если remote-components не скачиваются.
            {'name': 'Emergency progressive without EJS remote components', 'fallback_format': False,
             'progressive_format': True,
             'args': ["--extractor-args", "youtube:player_client=default"],
             'network_args': no_chunk_network_args, 'ejs_mode': 'none'},
        ]
        if quality == "360p":
            # 360p обычно нужен для быстрого и лёгкого скачивания.
            # В прошлой логике даже при 360p первые 4 попытки качали раздельные DASH-потоки
            # video+audio с googlevideo.com. В свежих логах именно эти DASH-ссылки стабильно
            # рвались с ConnectionResetError(10054), а программа до цельного MP4-потока
            # просто не успевала дойти до ручной остановки.
            #
            # Поэтому для 360p сначала пробуем progressive MP4: один готовый поток со звуком
            # (обычно itag 18). Это меньше запросов к CDN, быстрее стартует и чаще проходит
            # при нестабильном соединении. DASH/лучшее качество остаются ниже как fallback.
            low_quality_first = [
                {'name': '360p Progressive MP4 first no force IPv4', 'fallback_format': False,
                 'progressive_format': True, 'args': [],
                 'network_args': no_force_ipv4_network_args, 'ejs_mode': 'github'},
                {'name': '360p Progressive MP4 first tiny chunks no force IPv4', 'fallback_format': False,
                 'progressive_format': True, 'args': [],
                 'network_args': neutral_tiny_chunk_network_args, 'ejs_mode': 'github'},
                {'name': '360p Progressive MP4 first IPv4', 'fallback_format': False,
                 'progressive_format': True, 'args': [],
                 'network_args': no_chunk_network_args, 'ejs_mode': 'github'},
            ]
            existing_strategy_names = {s.get('name') for s in low_quality_first}
            strategies = low_quality_first + [
                s for s in strategies if s.get('name') not in existing_strategy_names
            ]

        if aria2_downloader_args:
            if self.is_youtube_url(url):
                if not getattr(self, "aria2c_youtube_skip_logged", False):
                    self.aria2c_youtube_skip_logged = True
                    self.file_logger.info(
                        "aria2c найден, но пропущен для YouTube: свежие логи показывают "
                        "повторные SSL/TLS разрывы googlevideo.com через внешний загрузчик"
                    )
            else:
                strategies.append(
                    {'name': 'Default Client via aria2c', 'fallback_format': False,
                     'args': aria2_downloader_args, 'network_args': neutral_network_args}
                )

        if self.is_youtube_url(url):
            strategies = self._apply_adaptive_youtube_strategy_order(strategies)

        success_video = False
        last_error    = None
        network_failure_count = 0
        slow_network_failure_count = 0
        attempt_summaries: List[Dict] = []

        for attempt, strategy in enumerate(strategies, 1):
            if success_video or self.cancel_flag.is_set():
                break

            merge_format = "mkv" if strategy.get('fallback_format') else "mp4"
            strategy_retries = int(strategy.get('retries', YT_DLP_RETRIES))
            strategy_fragment_retries = int(strategy.get('fragment_retries', YT_DLP_FRAGMENT_RETRIES))
            strategy_socket_timeout = int(strategy.get('socket_timeout', YT_DLP_SOCKET_TIMEOUT))
            cmd_v = [
                "yt-dlp",
                "--user-agent",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "--no-playlist",
                "--encoding", "utf-8",
                "--merge-output-format", merge_format,
                "--retries", str(strategy_retries),
                "--fragment-retries", str(strategy_fragment_retries),
                "--extractor-retries", str(YT_DLP_EXTRACTOR_RETRIES),
                "--retry-sleep", str(YT_DLP_NETWORK_RETRY_PAUSE_SEC),
                "--socket-timeout", str(strategy_socket_timeout),
                "--file-access-retries", "3",
                "--progress",
                "--newline",
                "--throttled-rate", "100K",
                "--continue",
                "--no-mtime",
                "--print", "after_move:filepath",
            ]
            if proxy_args:
                cmd_v.extend(proxy_args)
            cmd_v.extend(strategy.get('network_args', default_network_args))
            ejs_mode = strategy.get('ejs_mode', 'github')
            ejs_args = self.build_youtube_ejs_args(url, ejs_mode) if self.is_youtube_url(url) else []
            cmd_v.extend(ejs_args)
            cmd_v.extend(strategy['args'])

            if attempt > 1:
                self.log(f"🔄 Попытка {attempt}/{len(strategies)}: {strategy['name']}", "INFO")

            if strategy.get('progressive_format'):
                self.log(
                    f"⚠️ Пробую один MP4-поток со звуком ({strategy['name']})",
                    "WARNING"
                )
                if quality == "360p":
                    progressive_format = (
                        "18/"
                        "best[height<=360][ext=mp4][vcodec!=none][acodec!=none]/"
                        "best[height<=360]"
                    )
                    sort_order = "res:360,vbr,abr"
                else:
                    progressive_format = (
                        "best[height<=1080][ext=mp4][vcodec!=none][acodec!=none]/"
                        "22/18/"
                        "best[height<=720][ext=mp4][vcodec!=none][acodec!=none]/"
                        "best[height<=720]/best"
                    )
                    sort_order = "res:1080,vbr,abr"
                cmd_v.extend(["-f", progressive_format, "-S", sort_order])
            elif strategy.get('fallback_format'):
                self.log(
                    f"⚠️ Попытка без строгого MP4/M4A-фильтра ({strategy['name']})",
                    "WARNING"
                )
                if quality == "Лучшее":
                    cmd_v.extend(["-f", "bestvideo*+bestaudio/best", "-S", "vbr,res,abr"])
                else:
                    h = quality.replace("p", "")
                    cmd_v.extend([
                        "-f", (
                            f"bestvideo*[height<={h}]+bestaudio/"
                            f"best[height<={h}]/"
                            f"bestvideo*+bestaudio/best"
                        ),
                        "-S", f"res:{h},vbr,abr",
                    ])
            elif quality == "Лучшее":
                cmd_v.extend([
                    "-f", "bestvideo*[ext=mp4]+bestaudio[ext=m4a]/bestvideo*+bestaudio/best",
                    "-S", "vbr,res,abr",
                ])
            else:
                h = quality.replace("p", "")
                # Сначала предпочитаем mp4+m4a, потом разрешаем любые контейнеры.
                # Это уменьшает риск битых merge/remux, но не делает формат слишком строгим.
                cmd_v.extend([
                    "-f", (
                        f"bestvideo*[height<={h}][ext=mp4]+bestaudio[ext=m4a]/"
                        f"best[height<={h}][ext=mp4]/"
                        f"bestvideo*[height<={h}]+bestaudio/"
                        f"best[height<={h}]/best"
                    ),
                    # Сначала качество/разрешение до выбранного лимита, потом битрейт.
                    # Если 1080p нет, yt-dlp сам возьмёт 720p/480p/360p ниже лимита.
                    "-S", f"res:{h},vbr,abr",
                ])

            if self.download_subtitles.get():
                cmd_v.extend(["--write-sub", "--write-auto-sub", "--sub-lang", "ru,en"])

            # Названия файлов делаем максимально полными и чистыми: без YouTube ID в конце.
            # --trim-filenames срабатывает только для экстремально длинных названий, чтобы Windows не падал по длине пути.
            output_template = download_temp_dir / "%(title)s.%(ext)s"
            cmd_v.extend(["--windows-filenames", "--trim-filenames", "240", "-o", str(output_template), url])

            try:
                if attempt == 1:
                    self.log(f"⬇️ Загрузка видео ({quality})...", "INFO")
                    if quality != "Лучшее":
                        self.log(
                            f"🎯 Авто-качество: до {quality}. Если такого качества нет, "
                            "скачаю ближайшее доступное ниже.",
                            "INFO"
                        )
                        if quality == "360p":
                            self.log(
                                "⚡ 360p быстрый режим: сначала пробую цельный MP4-поток со звуком, "
                                "а раздельные DASH-потоки оставляю как запасные стратегии.",
                                "INFO"
                            )
                    self.log(
                        "⚡ Устойчивый режим YouTube + EJS fallback: "
                        f"потоков={getattr(self, 'max_concurrent', '?')}, "
                        f"fragments={fast_fragments}, "
                        f"retries={strategy_retries}, fragment-retries={strategy_fragment_retries}, "
                        f"socket-timeout={strategy_socket_timeout} сек, chunk={YT_DLP_HTTP_CHUNK_SIZE}, "
                        f"{self.youtube_ejs_status_text(strategy.get('ejs_mode', 'github'))}, "
                        f"{self.proxy_status_text()}",
                        "INFO"
                    )
                    if proxy_enabled:
                        self.log(
                            f"🌐 Proxy для yt-dlp включён: {proxy_url_masked}. "
                            "Сам видеопоток googlevideo.com пойдёт через proxy.",
                            "SUCCESS"
                        )
                    else:
                        self.log(
                            "🌐 Proxy для yt-dlp выключен. Если googlevideo.com постоянно даёт Read timed out, "
                            "включите proxy/VPN и укажите proxy в настройках программы.",
                            "INFO"
                        )

                attempt_context = {
                    "url": url,
                    "attempt": attempt,
                    "strategy": strategy["name"],
                    "quality": quality,
                    "expected_video_id": expected_video_id,
                    "target_dir": str(download_temp_dir),
                    "final_video_dir": str(video_dir),
                    "strategy_count": len(strategies),
                    "network_args": strategy.get('network_args', default_network_args),
                    "socket_timeout": strategy_socket_timeout,
                    "retries": strategy_retries,
                    "fragment_retries": strategy_fragment_retries,
                    "proxy_enabled": proxy_enabled,
                    "proxy_url_masked": proxy_url_masked,
                    "ejs_mode": strategy.get('ejs_mode', 'github'),
                    "ejs_args": ejs_args,
                    "extra_args": strategy.get('args', []),
                    "format_mode": (
                        "progressive" if strategy.get('progressive_format')
                        else "fallback_any_format" if strategy.get('fallback_format')
                        else "normal"
                    ),
                    "target_files_before_attempt": self._snapshot_download_target_files(
                        str(download_temp_dir), expected_video_id,
                        limit=PROBLEM_LOG_INFO_FILE_SNAPSHOT_LIMIT
                    ),
                }
                attempt_started = time.time()
                res_v = self.run_command(
                    cmd_v, DOWNLOAD_TIMEOUT_SEC, "yt_dlp_download", attempt_context
                )
                attempt_elapsed = time.time() - attempt_started

                if self.cancel_flag.is_set():
                    self.log("🛑 Загрузка остановлена пользователем", "WARNING")
                    break

                if res_v.returncode == 0:
                    temp_video_path = self.parse_yt_dlp_output(
                        res_v.stdout, url, download_temp_dir, video_files_before,
                        expected_video_id
                    )
                    if temp_video_path:
                        video_path = self.finalize_downloaded_video_file(
                            temp_video_path, video_dir, expected_video_id
                        ) or temp_video_path
                        success_video = True
                        if strategy["name"] == "EJS GitHub stable auto quality":
                            self._note_youtube_primary_strategy_result(True)
                        self.cleanup_download_temp_files(
                            download_temp_dir, expected_video_id=expected_video_id,
                            final_video_path=None, reason="download_success_temp_dir"
                        )
                        try:
                            shutil.rmtree(download_temp_dir, ignore_errors=True)
                        except Exception:
                            pass
                        if attempt > 1:
                            self.log(f"✅ Успешно через: {strategy['name']}", "SUCCESS")
                            self.record_problem(
                                "Видео успешно скачано резервной стратегией",
                                "INFO", "yt_dlp_recovered_after_fallback",
                                {
                                    "url": url,
                                    "expected_video_id": expected_video_id,
                                    "successful_attempt": attempt,
                                    "successful_strategy": strategy["name"],
                                    "failed_attempt_count": len(attempt_summaries),
                                    "attempt_summaries": attempt_summaries[-8:],
                                    "result_path": video_path,
                                },
                                resolved=True
                            )
                        break
                    self.record_problem(
                        "yt-dlp завершился успешно, но программа не нашла скачанный видеофайл",
                        "ERROR", "yt_dlp_success_without_file",
                        {
                            "url": url,
                            "attempt": attempt,
                            "strategy": strategy["name"],
                            "target_dir": str(download_temp_dir),
                            "final_video_dir": str(video_dir),
                            "expected_video_id": expected_video_id,
                            "files_before_count": len(video_files_before),
                            "supported_extensions": SUPPORTED_VIDEO_EXTENSIONS,
                        },
                        command=cmd_v, stdout=res_v.stdout, stderr=res_v.stderr
                    )
                else:
                    combined_error = "\n".join(
                        part for part in (res_v.stderr, res_v.stdout) if part
                    )
                    last_error = combined_error[-3000:] if combined_error else "Неизвестная ошибка"
                    stderr_lower = last_error.lower()
                    if strategy["name"] == "EJS GitHub stable auto quality":
                        self._note_youtube_primary_strategy_result(
                            False, last_error
                        )
                    googlevideo_hosts = sorted(set(re.findall(r"host='([^']*googlevideo\.com)'|https?://([^/\s]*googlevideo\.com)", last_error)))
                    googlevideo_hosts = sorted({h for pair in googlevideo_hosts for h in pair if h})
                    attempt_summaries.append({
                        "attempt": attempt,
                        "strategy": strategy["name"],
                        "returncode": res_v.returncode,
                        "elapsed_sec": round(attempt_elapsed, 2),
                        "socket_timeout": strategy_socket_timeout,
                        "proxy_enabled": proxy_enabled,
                        "proxy_url_masked": proxy_url_masked,
                        "googlevideo_hosts": googlevideo_hosts,
                        "error_tail": last_error[-700:],
                    })
                    will_retry = attempt < len(strategies)
                    self.record_yt_dlp_attempt(
                        url, attempt, strategy["name"], cmd_v, res_v,
                        "yt-dlp вернул ненулевой код", "WARNING" if will_retry else "ERROR",
                        {
                            "attempt_elapsed_sec": round(attempt_elapsed, 2),
                            "network_failure_count": network_failure_count,
                            "slow_network_failure_count": slow_network_failure_count,
                        }
                    )

                    critical = ["This video is unavailable", "Video unavailable",
                                "has been removed", "copyright",
                                "This live event will begin"]
                    auth_errors = [
                        "private video", "members-only", "sign in to confirm",
                        "age-restricted", "confirm your age", "inappropriate",
                        "login required", "cookies", "cookie database",
                        "could not copy chrome cookie", "not a bot",
                    ]
                    if any(e in stderr_lower for e in auth_errors):
                        if attempt < len(strategies):
                            cookie_strategy_used = any(
                                arg in cmd_v for arg in (
                                    "--cookies-from-browser", "--cookies"
                                )
                            )
                            cookie_read_failed = any(marker in stderr_lower for marker in (
                                "cookie database",
                                "could not copy chrome cookie",
                                "failed to decrypt",
                                "permission denied",
                                "database is locked",
                            ))
                            if cookie_strategy_used and cookie_read_failed:
                                self.log(
                                    "🔐 Не удалось прочитать cookies браузера; "
                                    "пробую следующую стратегию...",
                                    "WARNING"
                                )
                            else:
                                self.log(
                                    "🔐 YouTube запросил авторизацию или проверку «не робот»; "
                                    "пробую другую стратегию...",
                                    "WARNING"
                                )
                            if self.cancel_flag.is_set():
                                break
                            continue
                        self.log(
                            "❌ Не удалось скачать: нужна авторизация или cookies браузера",
                            "ERROR"
                        )
                        break

                    if "gvs po token" in stderr_lower or "po token" in stderr_lower:
                        if attempt < len(strategies):
                            self.log(
                                "⚠️ YouTube GVS PO Token warning: это проблема конкретного клиента/формата, "
                                "а не доказательство недоступности видео. Пробую следующую стратегию...",
                                "WARNING"
                            )
                            if self.cancel_flag.is_set():
                                break
                            continue

                    if any(e.lower() in stderr_lower for e in critical):
                        self.log(f"❌ Видео недоступно: {last_error[:200]}", "ERROR")
                        break

                    network_errors = [
                        "Connection aborted", "ConnectionResetError",
                        "Connection reset by peer", "timeout", "timed out",
                        "Unable to download webpage", "Read timed out",
                        "HTTP Error 416", "HTTP Error 429", "HTTP Error 500",
                        "HTTP Error 502", "HTTP Error 503", "HTTP Error 504",
                        "SSL:", "UNEXPECTED_EOF", "EOF occurred",
                        "handshake failure", "aria2c exited",
                        "connect timeout", "connection to", "remote end closed", "incomplete read",
                        "temporarily unavailable", "WinError 10054",
                        "удаленный хост принудительно разорвал",
                        "удалённый хост принудительно разорвал",
                    ]
                    if any(e.lower() in stderr_lower for e in network_errors):
                        network_failure_count += 1
                        if attempt_elapsed >= YT_DLP_SLOW_NETWORK_FAILURE_SEC:
                            slow_network_failure_count += 1

                        if network_failure_count == 3 and not proxy_enabled:
                            self.log(
                                "🌐 Уже 3 одинаковых сетевых сбоя googlevideo.com без proxy. "
                                "Это похоже не на качество/формат, а на сетевой путь до CDN. "
                                "Попробуйте включить VPN/proxy и прописать proxy в настройках.",
                                "WARNING"
                            )
                            self.record_problem(
                                "Повторные Read timed out/SSL/10054 от googlevideo.com без proxy",
                                "WARNING", "yt_dlp_repeated_googlevideo_network_errors_no_proxy",
                                {
                                    "url": url,
                                    "attempt": attempt,
                                    "network_failure_count": network_failure_count,
                                    "attempt_summaries": attempt_summaries[-5:],
                                    "recommendation": (
                                        "Добавить/включить proxy/VPN. Для локального proxy обычно подходят "
                                        "socks5://127.0.0.1:1080 или http://127.0.0.1:7890."
                                    ),
                                },
                                command=cmd_v, stdout=res_v.stdout, stderr=res_v.stderr
                            )

                        too_many_network_failures = (
                            YT_DLP_FAST_FAIL_ENABLED
                            and (
                                network_failure_count >= YT_DLP_MAX_NETWORK_FAILURES_PER_VIDEO
                                or slow_network_failure_count >= YT_DLP_MAX_SLOW_NETWORK_FAILURES_PER_VIDEO
                            )
                        )
                        if too_many_network_failures:
                            self.log(
                                "⚠️ Сеть несколько раз долго рвала соединение; пропускаю остальные попытки для этого видео",
                                "WARNING"
                            )
                            self.record_problem(
                                "Остановил перебор стратегий yt-dlp после повторных сетевых разрывов",
                                "WARNING", "yt_dlp_network_fast_fail",
                                {
                                    "url": url,
                                    "attempt": attempt,
                                    "strategy": strategy["name"],
                                    "attempt_elapsed_sec": round(attempt_elapsed, 2),
                                    "network_failure_count": network_failure_count,
                                    "slow_network_failure_count": slow_network_failure_count,
                                    "max_network_failures": YT_DLP_MAX_NETWORK_FAILURES_PER_VIDEO,
                                    "max_slow_network_failures": YT_DLP_MAX_SLOW_NETWORK_FAILURES_PER_VIDEO,
                                    "last_error": last_error,
                                },
                                command=cmd_v, stdout=res_v.stdout, stderr=res_v.stderr
                            )
                            break

                        if attempt < len(strategies):
                            self.log(
                                "⚠️ Сетевая ошибка/тайм-аут googlevideo.com. "
                                f"Пауза {YT_DLP_NETWORK_RETRY_PAUSE_SEC} сек, затем следующая стратегия "
                                f"({network_failure_count} сетевых сбоев по этому видео)...",
                                "WARNING"
                            )
                            if self.cancel_flag.wait(YT_DLP_NETWORK_RETRY_PAUSE_SEC):
                                break
                            continue
                        break

                    youtube_errors = ["no longer supported", "HTTP Error 401", "HTTP Error 403", "Forbidden",
                                      "unable to extract", "Failed to extract",
                                      "This video is not available", "Premieres in",
                                      "nsig extraction failed", "Unable to download API page",
                                      "Requested format is not available", "format is not available",
                                      "Only images are available",
                                      "n challenge solving failed", "remote components challenge solver",
                                      "gvs po token", "po token"]
                    if any(e.lower() in stderr_lower for e in youtube_errors):
                        if attempt < len(strategies):
                            self.log("⚠️ YouTube блокировка, пробую другой метод...", "WARNING")
                            if self.cancel_flag.is_set():
                                break
                            continue
                        else:
                            self.log(
                                f"❌ Все {len(strategies)} стратегий заблокированы YouTube", "ERROR"
                            )
                            self.log("💡 Попробуйте обновить yt-dlp: pip install -U yt-dlp", "INFO")
                            break
                    else:
                        self.log(f"❌ Ошибка (код {res_v.returncode}): {last_error[:250]}", "ERROR")
                        if attempt < len(strategies):
                            if self.cancel_flag.wait(3):
                                break
                            continue
                        break

            except ProblematicDownloadSkipped:
                # Это не ошибка всей программы: ролик слишком медленный на текущем VPN/CDN.
                # Прекращаем перебор стратегий для него и отдаём в download_single_video,
                # чтобы ссылка попала в отдельный txt внутри «Обработать вручную».
                self.cleanup_download_temp_files(
                    download_temp_dir, expected_video_id=expected_video_id,
                    final_video_path=None, reason="problematic_slow_download_skip"
                )
                try:
                    shutil.rmtree(download_temp_dir, ignore_errors=True)
                except Exception:
                    pass
                raise
            except CommandCancelledError:
                self.log("🛑 Загрузка остановлена пользователем", "WARNING")
                break
            except subprocess.TimeoutExpired:
                # run_command уже остановил процесс и записал подробности в Логи проблем.
                self.log(f"⏰ Тайм-аут загрузки (>{DOWNLOAD_TIMEOUT_SEC // 60} мин)", "ERROR")
                if attempt < len(strategies):
                    continue
                break
            except KeyboardInterrupt:
                self.log("🛑 Загрузка прервана пользователем", "WARNING")
                raise
            except Exception as e:
                self.log(f"❌ Неожиданная ошибка: {str(e)}", "ERROR")
                self.file_logger.error(f"method_yt_dlp: {traceback.format_exc()}")
                if attempt < len(strategies):
                    if self.cancel_flag.wait(2):
                        break
                    continue
                break

        if not success_video and not self.cancel_flag.is_set():
            self.cleanup_download_temp_files(
                download_temp_dir, expected_video_id=expected_video_id,
                final_video_path=None, reason="download_failed_all_strategies"
            )
            try:
                shutil.rmtree(download_temp_dir, ignore_errors=True)
            except Exception:
                pass
            self.record_problem(
                "Не удалось скачать видео после перебора стратегий yt-dlp",
                "ERROR", "yt_dlp_download_failed_all_strategies",
                {
                    "url": url,
                    "quality": quality,
                    "expected_video_id": expected_video_id,
                    "last_error": last_error,
                    "strategy_count": len(strategies),
                    "timeout_sec": DOWNLOAD_TIMEOUT_SEC,
                    "proxy_enabled": proxy_enabled,
                    "proxy_url_masked": proxy_url_masked,
                    "attempt_summaries": attempt_summaries[-12:],
                    "network_diagnosis": (
                        "Если во всех попытках повторяется Read timed out/SSL/10054 именно от googlevideo.com, "
                        "а youtube.com метаданные получает, проблема почти наверняка в сетевом пути до CDN. "
                        "Включите VPN/proxy и проверьте, что в command_details.args появился --proxy."
                    ),
                    "network_tuning": {
                        "yt_dlp_retries": YT_DLP_RETRIES,
                        "fragment_retries": YT_DLP_FRAGMENT_RETRIES,
                        "extractor_retries": YT_DLP_EXTRACTOR_RETRIES,
                        "socket_timeout_sec": YT_DLP_SOCKET_TIMEOUT,
                        "slow_socket_timeout_sec": YT_DLP_SLOW_SOCKET_TIMEOUT,
                        "slow_retries": YT_DLP_SLOW_RETRIES,
                        "slow_fragment_retries": YT_DLP_SLOW_FRAGMENT_RETRIES,
                        "retry_pause_sec": YT_DLP_NETWORK_RETRY_PAUSE_SEC,
                        "fast_fail_enabled": YT_DLP_FAST_FAIL_ENABLED,
                        "http_chunk_size": YT_DLP_HTTP_CHUNK_SIZE,
                        "safe_http_chunk_size": YT_DLP_SAFE_HTTP_CHUNK_SIZE,
                        "tiny_http_chunk_size": YT_DLP_TINY_HTTP_CHUNK_SIZE,
                        "fast_concurrent_fragments": fast_fragments,
                        "safe_concurrent_fragments": YT_DLP_SAFE_CONCURRENT_FRAGMENTS,
                    },
                }
            )

        return video_path if success_video else None

    def record_yt_dlp_attempt(self, url: str, attempt: int, strategy_name: str,
                              command: List[str], result: subprocess.CompletedProcess,
                              message: str, level: str = "WARNING",
                              extra_context: Optional[Dict] = None) -> None:
        context = {
            "url": url,
            "attempt": attempt,
            "strategy": strategy_name,
            "returncode": result.returncode,
        }
        if extra_context:
            context.update(extra_context)
        self.record_problem(
            message, level, "yt_dlp_download_attempt", context,
            command=command, stdout=result.stdout, stderr=result.stderr
        )

    def parse_yt_dlp_output(self, output: str, url: str, target_dir: Path,
                             files_before: Set[Path],
                             expected_video_id: Optional[str] = None) -> Optional[str]:
        found_path = None

        for line in output.split('\n'):
            stripped = line.strip().strip('"\'')
            if (stripped and not stripped.startswith('[')
                    and Path(stripped).suffix.lower().lstrip(".") in SUPPORTED_VIDEO_EXTENSIONS):
                found_path = self.normalize_downloaded_path(stripped)
            elif '[Merger] Merging formats into' in line:
                path = line.split('into')[-1].strip().strip('"\'')
                path = self.normalize_downloaded_path(path)
                if Path(path).suffix.lower().lstrip(".") in SUPPORTED_VIDEO_EXTENSIONS:
                    found_path = path
            elif '[download] Destination:' in line:
                path = line.split('Destination:')[-1].strip()
                path = self.normalize_downloaded_path(path)
                if Path(path).suffix.lower().lstrip(".") in SUPPORTED_VIDEO_EXTENSIONS:
                    found_path = path
            elif 'has already been downloaded' in line:
                self.log("ℹ️ Файл уже существовал", "INFO")

        if found_path and os.path.exists(found_path) and os.path.getsize(found_path) > 1000:
            found_path = self.remove_video_id_suffix_from_filename(
                found_path, expected_video_id
            )
            self._log_video_info(found_path)
            return found_path

        found_path = self.find_newest_file(
            target_dir, files_before, expected_video_id
        )
        if found_path:
            found_path = self.remove_video_id_suffix_from_filename(
                found_path, expected_video_id
            )
            self._log_video_info(found_path)
            return found_path

        self.log("❌ Новых видеофайлов не обнаружено", "ERROR")
        return None

    def _log_video_info(self, path: str) -> None:
        # FIX #10: проверяем существование файла перед логированием
        if not path or not os.path.exists(path):
            self.log("⚠️ Файл не найден для логирования", "WARNING")
            return
        duration   = self.get_media_duration(path)
        resolution = self.get_video_resolution(path)
        bitrate    = self.get_video_bitrate(path)
        size = os.path.getsize(path) / (1024 * 1024)
        self.log(f"✅ Видео: {os.path.basename(path)}", "SUCCESS")
        self.log(
            f"   📊 {resolution} | {bitrate} | {size:.1f} MB | {self.format_duration(duration)}",
            "INFO"
        )

    def find_newest_file(self, directory: Path, files_before: Set[Path],
                          expected_video_id: Optional[str] = None) -> Optional[str]:
        if not directory.exists():
            return None
        # FIX #11: сначала ищем файл с video_id, чтобы параллельные загрузки не путались.
        with self.file_lock:
            try:
                # FIX temp: исключаем .temp-файлы yt-dlp — они ещё не готовы
                current_files = self.snapshot_video_files(directory)
                if expected_video_id:
                    id_matches = []
                    for f in current_files:
                        if expected_video_id not in f.stem:
                            continue
                        try:
                            _ = f.stat().st_ctime
                            id_matches.append(f)
                        except OSError:
                            pass
                    if id_matches:
                        newest = max(id_matches, key=lambda f: f.stat().st_ctime)
                        return str(newest)

                new_files = current_files - files_before
                if not new_files:
                    return None

                if len(new_files) > 1 and not expected_video_id:
                    self.record_problem(
                        "Найдено несколько новых файлов без ожидаемого video_id; выбран самый новый",
                        "WARNING", "find_newest_file",
                        {
                            "directory": str(directory),
                            "extensions": SUPPORTED_VIDEO_EXTENSIONS,
                            "new_files": [str(f) for f in new_files],
                        }
                    )

                # FIX temp: safe stat — .temp файлы могут исчезнуть в любой момент
                valid_new = []
                for f in new_files:
                    try:
                        _ = f.stat().st_ctime
                        valid_new.append(f)
                    except OSError:
                        pass
                if not valid_new:
                    return None
                newest = max(valid_new, key=lambda f: f.stat().st_ctime)
                return str(newest)
            except Exception as e:
                self.file_logger.error(f"find_newest_file: {e}")
                return None

    def wait_for_file(self, file_path: str, timeout: int = 15) -> bool:
        if not file_path:
            return False
        # FIX temp: yt-dlp мог передать путь к .temp.*, который уже переименован
        # Проверяем оба варианта: финальный и .temp
        alt_path: Optional[str] = None
        for ext in SUPPORTED_VIDEO_EXTENSIONS:
            temp_suffix = f'.temp.{ext}'
            final_suffix = f'.{ext}'
            if file_path.endswith(temp_suffix):
                alt_path = file_path[:-len(temp_suffix)] + final_suffix
                break
            if file_path.endswith(final_suffix):
                alt_path = file_path[:-len(final_suffix)] + temp_suffix
                break

        start = time.time()
        while time.time() - start < timeout:
            if self.cancel_flag.is_set():
                return False
            for p in filter(None, [file_path, alt_path]):
                try:
                    if os.path.exists(p) and os.path.getsize(p) > 1000:
                        return True
                except OSError:
                    pass
            time.sleep(1)
        return False

    # ─────────────────────────────────────────────
    # ИНФОРМАЦИЯ О МЕДИАФАЙЛАХ
    # ─────────────────────────────────────────────

    def get_media_duration(self, media_file: str) -> float:
        try:
            cmd = ["ffprobe", "-v", "error", "-show_entries",
                   "format=duration", "-of", "csv=p=0", media_file]
            r = subprocess.run(
                cmd, capture_output=True, text=True,
                encoding='utf-8', errors='replace',
                timeout=30, creationflags=self.subprocess_flags
            )
            return float(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else 0
        except Exception as e:
            self.record_problem(
                "Не удалось получить длительность медиафайла",
                "WARNING", "ffprobe_duration",
                {"media_file": media_file}, e
            )
            return 0

    def get_audio_duration(self, audio_file: str) -> float:
        return self.get_media_duration(audio_file)

    def has_audio_stream(self, media_file: str) -> bool:
        try:
            cmd = ["ffprobe", "-v", "error", "-select_streams", "a:0",
                   "-show_entries", "stream=index", "-of", "csv=p=0", media_file]
            r = subprocess.run(
                cmd, capture_output=True, text=True,
                encoding='utf-8', errors='replace',
                timeout=30, creationflags=self.subprocess_flags
            )
            return r.returncode == 0 and bool(r.stdout.strip())
        except Exception as e:
            self.record_problem(
                "Не удалось проверить наличие аудиодорожки",
                "WARNING", "ffprobe_audio_stream",
                {"media_file": media_file}, e
            )
            return False

    def has_video_stream(self, media_file: str) -> bool:
        try:
            cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0",
                   "-show_entries", "stream=index", "-of", "csv=p=0", media_file]
            r = subprocess.run(
                cmd, capture_output=True, text=True,
                encoding='utf-8', errors='replace',
                timeout=30, creationflags=self.subprocess_flags
            )
            return r.returncode == 0 and bool(r.stdout.strip())
        except Exception as e:
            self.record_problem(
                "Не удалось проверить наличие видеодорожки",
                "WARNING", "ffprobe_video_stream",
                {"media_file": media_file}, e
            )
            return False

    def validate_downloaded_video(self, video_path: str, url: str) -> Tuple[bool, str]:
        path = Path(video_path)
        if not path.exists():
            return False, "файл не найден после скачивания"
        if not self.is_supported_video_file(path):
            return False, f"неподдерживаемое расширение: {path.suffix}"

        try:
            size = path.stat().st_size
        except OSError as e:
            self.record_problem(
                "Не удалось проверить размер скачанного видео",
                "ERROR", "validate_downloaded_video",
                {"url": url, "video_path": str(path)}, e
            )
            return False, "не удалось проверить размер файла"

        if size < MIN_VIDEO_FILE_SIZE_BYTES:
            return False, f"файл слишком маленький: {size} байт"

        duration = self.get_media_duration(str(path))
        if duration <= 0:
            return False, "ffprobe не смог прочитать длительность видео"

        if not self.has_video_stream(str(path)):
            return False, "в файле нет видеодорожки или она не читается"

        if not self.has_audio_stream(str(path)):
            self.log(
                f"⚠️ Видео скачано, но аудиодорожка не обнаружена: {path.name}",
                "WARNING"
            )
            self.record_problem(
                "Скачанное видео не содержит аудиодорожку; конвертация в mp3 может быть невозможна",
                "WARNING", "validate_downloaded_video_no_audio",
                {
                    "url": url,
                    "video_path": str(path),
                    "duration": duration,
                    "size_bytes": size,
                }
            )

        return True, f"проверено ffprobe: {self.format_duration(duration)}, {size / (1024 * 1024):.1f} MB"

    def validate_converted_audio(self, video_file: Path,
                                 audio_file: Path) -> Tuple[bool, str, float, float]:
        source_duration = self.get_media_duration(str(video_file))

        if not audio_file.exists():
            return False, "mp3-файл не создан", source_duration, 0

        try:
            if audio_file.stat().st_size < MIN_AUDIO_FILE_SIZE_BYTES:
                return False, "mp3-файл слишком маленький или пустой", source_duration, 0
        except OSError as e:
            self.record_problem(
                "Не удалось проверить размер mp3 после конвертации",
                "ERROR", "validate_converted_audio",
                {"video_file": str(video_file), "audio_file": str(audio_file)}, e
            )
            return False, "не удалось проверить размер mp3", source_duration, 0

        audio_duration = self.get_audio_duration(str(audio_file))

        if audio_duration <= 0:
            return False, "ffprobe не смог прочитать длительность mp3", source_duration, audio_duration

        if source_duration <= 0:
            return True, "исходная длительность неизвестна, проверен только факт создания mp3", source_duration, audio_duration

        lost_seconds = source_duration - audio_duration
        if lost_seconds >= AUDIO_DURATION_TOLERANCE_SEC:
            return (
                False,
                f"mp3 короче исходного видео на {lost_seconds:.2f} сек",
                source_duration,
                audio_duration,
            )

        return (
            True,
            f"длительность проверена, расхождение {abs(lost_seconds):.2f} сек",
            source_duration,
            audio_duration,
        )

    def _normalized_process_returncode(self, returncode: Optional[int]) -> Optional[int]:
        if returncode is None:
            return None
        value = int(returncode)
        if platform.system() == "Windows" and value >= 2 ** 31:
            return value - 2 ** 32
        return value

    def _diagnostic_stderr_excerpt(self, stderr: Optional[str],
                                   head_chars: int = 240,
                                   tail_chars: int = 2000) -> str:
        text = str(stderr or "")
        if len(text) <= head_chars + tail_chars:
            return text
        return (
            text[:head_chars]
            + f"\n... пропущено {len(text) - head_chars - tail_chars} символов ...\n"
            + text[-tail_chars:]
        )

    def _conversion_temp_path(self, audio_file: Path) -> Path:
        return audio_file.with_name(
            f".{audio_file.stem}.{os.getpid()}.{threading.get_ident()}.converting.mp3"
        )

    def move_audio_to_manual_processing(self, audio_path: Path,
                                        reason: str) -> Optional[Path]:
        """Сохраняет плохой или неполный MP3 вместо его удаления."""
        try:
            if not audio_path.exists() or not audio_path.is_file():
                return None
            target_dir = self.manual_processing_dir / "Неполные MP3"
            target_dir.mkdir(parents=True, exist_ok=True)
            target = self.make_unique_media_path(target_dir / audio_path.name.lstrip("."))
            with self.file_lock:
                shutil.move(str(audio_path), str(target))
            self.log(
                f"📦 Неполный MP3 сохранён для проверки: {target.name}",
                "INFO"
            )
            return target
        except Exception as e:
            self.record_problem(
                "Не удалось сохранить проблемный MP3 для ручной проверки",
                "ERROR", "move_audio_to_manual_processing",
                {
                    "audio_path": str(audio_path),
                    "reason": reason,
                },
                e
            )
            return None

    def get_video_resolution(self, video_file: str) -> str:
        try:
            cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0",
                   "-show_entries", "stream=width,height", "-of", "csv=p=0", video_file]
            r = subprocess.run(
                cmd, capture_output=True, text=True,
                encoding='utf-8', errors='replace',
                timeout=30, creationflags=self.subprocess_flags
            )
            if r.returncode == 0 and r.stdout.strip():
                parts = r.stdout.strip().split(',')
                if len(parts) == 2:
                    w, h = parts
                    return f"{w}x{h} ({h}p)"
            return "Неизвестно"
        except Exception:
            return "Неизвестно"

    def get_video_bitrate(self, video_file: str) -> str:
        try:
            cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0",
                   "-show_entries", "stream=bit_rate", "-of", "csv=p=0", video_file]
            r = subprocess.run(
                cmd, capture_output=True, text=True,
                encoding='utf-8', errors='replace',
                timeout=30, creationflags=self.subprocess_flags
            )
            if r.returncode == 0 and r.stdout.strip():
                bps  = int(r.stdout.strip())
                mbps = bps / 1_000_000
                return f"{mbps:.1f} Mbps" if mbps >= 1 else f"{bps / 1000:.0f} kbps"
            return "Неизвестно"
        except Exception:
            return "Неизвестно"

    def get_audio_info(self, audio_path: str) -> str:
        try:
            cmd = ["ffprobe", "-v", "quiet",
                   "-show_entries", "stream=codec_name,sample_rate,channels,bit_rate",
                   "-of", "csv=p=0", audio_path]
            r = subprocess.run(
                cmd, capture_output=True, text=True,
                encoding='utf-8', errors='replace',
                timeout=10, creationflags=self.subprocess_flags
            )
            return r.stdout.strip() if r.returncode == 0 else "Неизвестно"
        except Exception:
            return "Ошибка"

    def get_total_audio_duration(self, audio_files: List[str]) -> float:
        return sum(self.get_audio_duration(f) for f in audio_files)

    def format_duration(self, seconds: float) -> str:
        # FIX #12: защита от отрицательных значений (ffprobe может вернуть -1)
        seconds = max(0.0, seconds)
        h, remainder = divmod(int(seconds), 3600)
        m, s = divmod(remainder, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    def sanitize_filename(self, filename: str) -> str:
        for ch in '<>:"/\\|?*':
            filename = filename.replace(ch, '_')
        return filename[:200]

    # ─────────────────────────────────────────────
    # КОНВЕРТАЦИЯ И ОБЪЕДИНЕНИЕ АУДИО
    # ─────────────────────────────────────────────

    def convert_videos_to_audio(self, video_paths: List[str], audio_dir: Path) -> List[str]:
        video_files: List[Path] = []
        seen: Set[Path] = set()
        for video_path in video_paths:
            path = Path(video_path)
            try:
                resolved = path.resolve()
            except OSError:
                resolved = path
            if resolved in seen:
                continue
            if path.exists() and self.is_supported_video_file(path):
                video_files.append(path)
                seen.add(resolved)

        if not video_files:
            self.log("⚠️ Нет видео текущей сессии для конвертации", "WARNING")
            return []

        self.log(f"🎵 КОНВЕРТАЦИЯ {len(video_files)} ВИДЕО ТЕКУЩЕЙ СЕССИИ", "INFO")
        converted: List[str] = []
        failed_conversions: List[Dict] = []
        audio_quality_setting = self.audio_quality.get()

        for idx, video_file in enumerate(video_files, 1):
            if self.cancel_flag.is_set():
                self.log("🛑 Конвертация отменена", "WARNING")
                break

            audio_file = audio_dir / f"{video_file.stem}.mp3"
            if audio_file.exists():
                ok, reason, src_dur, out_dur = self.validate_converted_audio(video_file, audio_file)
                if ok:
                    self.log(
                        f"[{idx}/{len(video_files)}] ⏭️ Уже существует и проверен: "
                        f"{audio_file.name} | видео {self.format_duration(src_dur)}, "
                        f"mp3 {self.format_duration(out_dur)} | {reason}",
                        "SUCCESS"
                    )
                    converted.append(str(audio_file))
                    continue

                self.log(
                    f"[{idx}/{len(video_files)}] ⚠️ Существующий mp3 плохой, пересоздаю: "
                    f"{audio_file.name} | {reason}",
                    "WARNING"
                )
                self.record_problem(
                    "Существующий mp3 не прошёл проверку длительности/целостности",
                    "WARNING", "convert_video_to_audio_existing_invalid",
                    {
                        "video_file": str(video_file),
                        "audio_file": str(audio_file),
                        "source_duration": src_dur,
                        "audio_duration": out_dur,
                        "reason": reason,
                    }
                )
                preserved_existing = self.move_audio_to_manual_processing(
                    audio_file, "existing_audio_failed_validation"
                )
                if audio_file.exists():
                    self.log(
                        f"[{idx}/{len(video_files)}] ❌ Не удалось безопасно перенести "
                        f"плохой mp3: {audio_file.name}",
                        "ERROR"
                    )
                    failed_conversions.append({
                        "video_file": str(video_file),
                        "audio_file": str(audio_file),
                        "reason": "не удалось безопасно перенести существующий плохой mp3",
                    })
                    continue
                if preserved_existing:
                    self.log(
                        f"[{idx}/{len(video_files)}] Старый плохой MP3 сохранён: "
                        f"{preserved_existing.name}",
                        "INFO"
                    )

            source_duration = self.get_media_duration(str(video_file))
            if not self.has_audio_stream(str(video_file)):
                reason = "в исходном видео нет аудиодорожки"
                self.log(f"[{idx}/{len(video_files)}] ❌ {reason}: {video_file.name}", "ERROR")
                failed_conversions.append({
                    "video_file": str(video_file),
                    "audio_file": str(audio_file),
                    "reason": reason,
                    "source_duration": source_duration,
                })
                self.record_problem(
                    "Конвертация невозможна: в видео нет аудиодорожки",
                    "ERROR", "convert_video_to_audio_no_stream",
                    failed_conversions[-1]
                )
                continue

            self.log(
                f"[{idx}/{len(video_files)}] 🔄 {video_file.name} "
                f"(длительность видео: {self.format_duration(source_duration)})",
                "INFO"
            )

            temp_audio_file = self._conversion_temp_path(audio_file)
            if temp_audio_file.exists():
                self.move_audio_to_manual_processing(
                    temp_audio_file, "stale_conversion_temp_before_retry"
                )

            audio_filter = "aresample=async=1:first_pts=0"
            cmd = [
                "ffmpeg", "-nostdin", "-y", "-i", str(video_file),
                "-map", "0:a:0", "-vn",
            ]
            if source_duration > 0:
                audio_filter += ",apad"
                cmd.extend(["-af", audio_filter, "-t", f"{source_duration:.3f}"])
            else:
                cmd.extend(["-af", audio_filter])
            cmd.extend(["-acodec", "libmp3lame"])
            if audio_quality_setting == "VBR-0 (лучшее)":
                cmd.extend(["-q:a", "0"])
            else:
                cmd.extend(["-b:a", audio_quality_setting])
            cmd.extend(["-ar", "48000", "-ac", "2", str(temp_audio_file)])

            try:
                r = self.run_command(
                    cmd, CONVERT_TIMEOUT_SEC, "convert_video_to_audio",
                    {
                        "video_file": str(video_file),
                        "audio_file": str(audio_file),
                        "temp_audio_file": str(temp_audio_file),
                        "audio_quality": audio_quality_setting,
                        "source_duration": source_duration,
                        "duration_tolerance_sec": AUDIO_DURATION_TOLERANCE_SEC,
                    }
                )
                ok, reason, src_dur, out_dur = self.validate_converted_audio(
                    video_file, temp_audio_file
                )
                if r.returncode == 0 and ok:
                    temp_audio_file.replace(audio_file)
                    sz  = audio_file.stat().st_size / (1024 * 1024)
                    self.log(
                        f"[{idx}/{len(video_files)}] ✅ {audio_file.name} "
                        f"({sz:.1f} MB) | видео {self.format_duration(src_dur)}, "
                        f"mp3 {self.format_duration(out_dur)} | {reason}",
                        "SUCCESS"
                    )
                    converted.append(str(audio_file))
                else:
                    err = self._diagnostic_stderr_excerpt(r.stderr) or reason
                    partial_audio = self.move_audio_to_manual_processing(
                        temp_audio_file, "ffmpeg_failed_or_audio_validation_failed"
                    )
                    normalized_returncode = self._normalized_process_returncode(
                        r.returncode
                    )
                    self.log(
                        f"[{idx}/{len(video_files)}] ❌ Ошибка/потеря при конвертации: "
                        f"{video_file.name} | {reason} | {err[-500:]}",
                        "ERROR"
                    )
                    failed_conversions.append({
                        "video_file": str(video_file),
                        "audio_file": str(audio_file),
                        "partial_audio_file": str(partial_audio) if partial_audio else None,
                        "reason": reason,
                        "source_duration": src_dur,
                        "audio_duration": out_dur,
                        "ffmpeg_returncode": normalized_returncode,
                        "ffmpeg_returncode_raw": r.returncode,
                        "ffmpeg_stderr_excerpt": err,
                    })
                    self.record_problem(
                        "Конвертация завершилась ошибкой или mp3 короче исходника",
                        "ERROR", "convert_video_to_audio_failed_validation",
                        failed_conversions[-1],
                        command=cmd, stdout=r.stdout, stderr=r.stderr
                    )
            except subprocess.TimeoutExpired:
                reason = f"таймаут >{CONVERT_TIMEOUT_SEC // 60} мин"
                partial_audio = self.move_audio_to_manual_processing(
                    temp_audio_file, "ffmpeg_conversion_timeout"
                )
                self.log(
                    f"[{idx}/{len(video_files)}] ⏰ Таймаут конвертации: {video_file.name}",
                    "ERROR"
                )
                failed_conversions.append({
                    "video_file": str(video_file),
                    "audio_file": str(audio_file),
                    "partial_audio_file": str(partial_audio) if partial_audio else None,
                    "reason": reason,
                    "source_duration": source_duration,
                })
                self.record_problem(
                    "Таймаут конвертации видео в аудио",
                    "ERROR", "convert_video_to_audio_timeout",
                    failed_conversions[-1]
                )
            except CommandCancelledError:
                self.move_audio_to_manual_processing(
                    temp_audio_file, "conversion_cancelled"
                )
                self.log("🛑 Конвертация остановлена пользователем", "WARNING")
                break
            except KeyboardInterrupt:
                self.move_audio_to_manual_processing(
                    temp_audio_file, "conversion_keyboard_interrupt"
                )
                break
            except Exception as e:
                partial_audio = self.move_audio_to_manual_processing(
                    temp_audio_file, "conversion_exception"
                )
                self.log(f"[{idx}/{len(video_files)}] ❌ {str(e)}", "ERROR")
                failed_conversions.append({
                    "video_file": str(video_file),
                    "audio_file": str(audio_file),
                    "partial_audio_file": str(partial_audio) if partial_audio else None,
                    "reason": str(e),
                    "source_duration": source_duration,
                })
                self.record_problem(
                    "Непредвиденная ошибка при конвертации видео в аудио",
                    "ERROR", "convert_video_to_audio_exception",
                    failed_conversions[-1], e
                )

        if self.cancel_flag.is_set():
            self.log(
                f"🛑 Конвертация прервана: готово {len(converted)}/{len(video_files)}",
                "WARNING"
            )
        elif failed_conversions:
            self.log(
                f"❌ Конвертация завершена с проблемами: "
                f"готово {len(converted)}/{len(video_files)}, ошибок {len(failed_conversions)}",
                "ERROR"
            )
            for item in failed_conversions:
                original_video = str(item.get("video_file", "") or "")
                if not original_video:
                    continue
                manual_video = self.move_video_to_manual_processing(
                    original_video, str(item.get("reason", "conversion_failed"))
                )
                item["original_video_file"] = original_video
                item["video_file"] = manual_video
                if manual_video != original_video:
                    item["manual_processing_video"] = manual_video

            failed_conversion_file = self.save_failed_conversions_session(failed_conversions)
            failed_location = (failed_conversion_file.name if failed_conversion_file
                               else "Обработать вручную")
            self.log(
                f"⚠️ Некоторые видео не удалось конвертировать в аудио. "
                f"Исходные видео и файл сессии помещены в «Обработать вручную»: "
                f"{failed_location}",
                "WARNING"
            )
            self.log(
                f"📁 Открыть папку Обработать вручную: {self.manual_processing_dir}",
                "MANUAL_FOLDER_LINK"
            )
            self.record_problem(
                "Итог конвертации: часть видео не сконвертировалась или потеряла длительность",
                "ERROR", "convert_videos_to_audio_summary",
                {
                    "converted_count": len(converted),
                    "total_video_count": len(video_files),
                    "failed_conversions": failed_conversions,
                }
            )
        else:
            self.log(
                f"🎵 Конвертация завершена и проверена: {len(converted)}/{len(video_files)}",
                "SUCCESS"
            )
        return converted

    def merge_audio_files(self, audio_files: List[str], output_path: str,
                           folder_name: str) -> None:
        if not audio_files:
            self.log("❌ Нет файлов для объединения", "ERROR")
            return

        split_dir = Path(output_path) / "Разбивка аудио"
        split_dir.mkdir(parents=True, exist_ok=True)
        temp_out  = split_dir / f"temp_merge_{int(time.time())}.mp3"

        valid_files = [f for f in audio_files if Path(f).exists()]
        if not valid_files:
            self.log("❌ Нет валидных аудиофайлов для объединения", "ERROR")
            return

        temp_audio_dir = split_dir / "temp_audio"
        temp_audio_dir.mkdir(parents=True, exist_ok=True)
        list_file = split_dir / "filelist.txt"

        try:
            with open(list_file, 'w', encoding='utf-8') as f:
                for idx, audio_file in enumerate(valid_files, 1):
                    simple_name = f"audio_{idx:03d}.mp3"
                    temp_file   = temp_audio_dir / simple_name
                    shutil.copy2(audio_file, temp_file)
                    # FIX #13: абсолютный путь — предотвращает сбой при пробелах/кириллице
                    abs_path = temp_file.resolve().as_posix()
                    f.write(f"file '{abs_path}'\n")

            total_duration = self.get_total_audio_duration(valid_files)
            self.log(
                f"🔄 Объединение {len(valid_files)} файлов "
                f"({self.format_duration(total_duration)})...", "INFO"
            )

            audio_quality_setting = self.audio_quality.get()
            cmd = ["ffmpeg", "-f", "concat", "-safe", "0", "-i", str(list_file),
                   "-c:a", "libmp3lame"]
            if audio_quality_setting == "VBR-0 (лучшее)":
                cmd.extend(["-q:a", "0"])
            else:
                cmd.extend(["-b:a", audio_quality_setting])
            cmd.extend(["-ar", "48000", "-ac", "2", "-y", str(temp_out)])
            try:
                requested_split_hours = self.split_hours.get()
            except tk.TclError:
                requested_split_hours = MAX_SPLIT_HOURS

            r = self.run_command(
                cmd, MERGE_TIMEOUT_SEC, "merge_audio_files",
                {
                    "output_path": output_path,
                    "folder_name": folder_name,
                    "audio_count": len(valid_files),
                    "split_hours": requested_split_hours,
                }
            )
            if r.returncode != 0:
                self.log(
                    f"❌ Ошибка FFmpeg: {r.stderr[:500] if r.stderr else 'Неизвестно'}", "ERROR"
                )
                self.record_problem(
                    "FFmpeg не смог объединить аудиофайлы",
                    "ERROR", "merge_audio_files",
                    {
                        "output_path": output_path,
                        "folder_name": folder_name,
                        "audio_count": len(valid_files),
                    },
                    command=cmd, stdout=r.stdout, stderr=r.stderr
                )
                return

            merged_dur = self.get_audio_duration(str(temp_out))
            self.log(f"   После merge: {self.format_duration(merged_dur)}", "INFO")

            if requested_split_hours > 0:
                self.split_audio_file(temp_out, split_dir, folder_name, requested_split_hours)
            else:
                final = split_dir / f"{folder_name}_Объединенное.mp3"
                temp_out.replace(final)
                self.log(f"✅ Создан: {final.name}", "SUCCESS")

            if temp_out.exists():
                temp_out.unlink()
            self.log("✅ Объединение завершено!", "SUCCESS")

        except subprocess.TimeoutExpired:
            self.log(f"❌ Таймаут объединения (>{MERGE_TIMEOUT_SEC // 3600} час)", "ERROR")
        except CommandCancelledError:
            self.log("🛑 Объединение остановлено пользователем", "WARNING")
        except Exception as e:
            self.log(f"❌ Ошибка при объединении: {e}", "ERROR")
            self.file_logger.error(f"merge_audio_files: {traceback.format_exc()}")
        finally:
            if temp_audio_dir.exists():
                shutil.rmtree(temp_audio_dir, ignore_errors=True)
            if list_file.exists():
                try:
                    list_file.unlink()
                except Exception:
                    pass

    def split_audio_file(self, temp_file: Path, split_dir: Path,
                          folder_name: str, hours: int) -> None:
        sec = hours * 3600
        for old_part in split_dir.glob("part_*.mp3"):
            try:
                old_part.unlink()
            except OSError:
                pass
        cmd = [
            "ffmpeg", "-y", "-i", str(temp_file),
            "-f", "segment", "-segment_time", str(sec),
            "-c", "copy", "-reset_timestamps", "1",
            str(split_dir / "part_%03d.mp3")
        ]
        try:
            r = self.run_command(
                cmd, SPLIT_TIMEOUT_SEC, "split_audio_file",
                {
                    "temp_file": str(temp_file),
                    "split_dir": str(split_dir),
                    "folder_name": folder_name,
                    "hours": hours,
                }
            )
            if r.returncode == 0:
                parts = sorted(split_dir.glob("part_*.mp3"))
                for i, p in enumerate(parts, 1):
                    p.replace(split_dir / f"{folder_name}_Часть_{i:03d}.mp3")
                self.log(f"✅ Создано {len(parts)} частей", "SUCCESS")
            else:
                self.log(
                    f"❌ Ошибка разбиения: {r.stderr[:200] if r.stderr else 'Неизвестно'}",
                    "ERROR"
                )
            try:
                temp_file.unlink()
            except Exception:
                pass
        except subprocess.TimeoutExpired:
            self.log(f"❌ Таймаут разбиения (>{SPLIT_TIMEOUT_SEC // 60} мин)", "ERROR")
        except CommandCancelledError:
            self.log("🛑 Разбиение остановлено пользователем", "WARNING")
        except Exception as e:
            self.log(f"❌ Ошибка split: {e}", "ERROR")

    # ─────────────────────────────────────────────
    # УПРАВЛЕНИЕ ЗАГРУЗКОЙ
    # ─────────────────────────────────────────────

    def download_single_video(self, url: str, index: int,
                               downloaded_videos: ThreadSafeList,
                               failed_urls: ThreadSafeList,
                               problematic_downloads: ThreadSafeList) -> None:
        try:
            self.log(f"📥 [{index + 1}/{self.total_files.value()}]: {url[:80]}...")

            if not self.validate_url(url):
                self.log(f"❌ Неверный формат URL: {url}", "ERROR")
                failed_urls.append(url)
                return

            if self.is_url_downloaded(url):
                self.log("⏭️ Пропущено (уже скачано)", "WARNING")
                return

            if index > 0:
                delay = random.uniform(2, 5)
                if self.cancel_flag.wait(delay):
                    return

            result = self.run_yt_dlp_with_download_slot(url)

            if result and self.wait_for_file(result):
                is_valid, reason = self.validate_downloaded_video(result, url)
                if is_valid:
                    self.log(f"✅ Проверка скачанного видео: {reason}", "SUCCESS")
                    # После успешного файла удаляем .part/.ytdl/.f137/.f140 хвосты этого ролика.
                    self.cleanup_download_temp_files(
                        Path(result).parent,
                        expected_video_id=self.extract_video_id(url),
                        final_video_path=result,
                        reason="download_success_validated"
                    )
                    downloaded_videos.append(result)
                    self.save_download_log(url)
                else:
                    failed_urls.append(url)
                    manual_video = self.move_video_to_manual_processing(
                        result, "downloaded_video_validation_failed"
                    )
                    self.log(f"❌ Скачанный файл не прошёл проверку: {reason}", "ERROR")
                    self.record_problem(
                        "Скачанный файл не прошёл проверку ffprobe и не записан в историю",
                        "ERROR", "downloaded_video_validation_failed",
                        {
                            "url": url,
                            "video_path": manual_video,
                            "original_video_path": result,
                            "reason": reason,
                        }
                    )
            else:
                if not self.cancel_flag.is_set():
                    failed_urls.append(url)
                    self.log(f"❌ Не удалось скачать: {url[:80]}", "ERROR")
                    self.record_problem(
                        "Видео не скачалось или файл не появился после yt-dlp",
                        "ERROR", "download_single_video_no_result",
                        {
                            "url": url,
                            "result_path": result,
                            "result_returned": bool(result),
                        }
                    )

        except ProblematicDownloadSkipped as e:
            item = e.to_log_item()
            item["index_in_session"] = index + 1
            item["total_in_session"] = self.total_files.value()
            problematic_downloads.append(item)
            self.log(
                f"⏭️ Пропущено проблемно скачиваемое видео: {e.reason}",
                "WARNING"
            )
            self.record_problem(
                "Видео пропущено как проблемно скачиваемое и будет записано в отдельный txt",
                "WARNING", "download_single_video_problematic_skip",
                item
            )
        except KeyboardInterrupt:
            self.log("🛑 Загрузка прервана пользователем", "WARNING")
            raise
        except Exception as e:
            self.log(f"❌ Ошибка: {str(e)}", "ERROR")
            self.file_logger.error(f"download_single_video: {traceback.format_exc()}")
            failed_urls.append(url)
        finally:
            if not self.cancel_flag.is_set():
                self.update_progress()

    def download_single_video_task(self, url: str, index: int,
                                    downloaded_videos: ThreadSafeList,
                                    failed_urls: ThreadSafeList,
                                    problematic_downloads: ThreadSafeList) -> None:
        thread = threading.current_thread()
        self.download_threads.append(thread)
        try:
            self.download_single_video(url, index, downloaded_videos, failed_urls, problematic_downloads)
        finally:
            self.download_threads.remove(thread)

    def download_manager(self, urls: List[str]) -> None:
        # FIX #14: ThreadSafeList вместо plain list — устраняет гонку без list_lock
        downloaded_videos: ThreadSafeList = ThreadSafeList()
        failed_urls: ThreadSafeList = ThreadSafeList()
        problematic_downloads: ThreadSafeList = ThreadSafeList()
        converted: List[str] = []

        save_dir  = Path(self.save_path.get())
        audio_dir = save_dir / "Аудио"
        audio_dir.mkdir(parents=True, exist_ok=True)
        video_dir = save_dir / "Видео"
        video_dir.mkdir(parents=True, exist_ok=True)

        stale_count = self.cleanup_stale_download_temp_files(video_dir)
        stale_temp_dirs = self.cleanup_stale_download_temp_dirs(video_dir)
        if stale_count:
            self.log(f"🧹 Перед новой сессией очищены старые временные хвосты yt-dlp: {stale_count}", "INFO")
        if stale_temp_dirs:
            self.log(f"🧹 Перед новой сессией очищены старые временные папки yt-dlp: {stale_temp_dirs}", "INFO")

        self.log(f"📺 СКАЧИВАНИЕ {len(urls)} ВИДЕО", "INFO")
        self.log(
            f"🚀 Параллельные загрузки: {self.max_concurrent}. "
            "YouTube больше не блокируется одним общим слотом.",
            "INFO"
        )
        self.download_threads.clear()

        with ThreadPoolExecutor(max_workers=self.max_concurrent) as executor:
            futures = []
            for i, url in enumerate(urls):
                if self.cancel_flag.is_set():
                    self.log("🛑 Загрузка отменена пользователем", "WARNING")
                    break
                if self.is_url_downloaded(url):
                    self.log(f"⚠️ Пропущено (уже скачано ранее): {url[:80]}", "WARNING")
                    self.update_progress()
                    continue
                future = executor.submit(
                    self.download_single_video_task, url, i,
                    downloaded_videos, failed_urls, problematic_downloads
                )
                futures.append(future)

            for future in as_completed(futures):
                if self.cancel_flag.is_set():
                    for f in futures:
                        f.cancel()
                    break

        if not self.cancel_flag.is_set():
            current_downloaded = downloaded_videos.copy()
            converted = self.convert_videos_to_audio(current_downloaded, audio_dir)

            if self.cancel_flag.is_set():
                self.log("🛑 Объединение аудио не запускается: операция отменена", "WARNING")
            elif self.merge_audio.get():
                # Важное поведение: объединяем ВСЕ успешно созданные MP3,
                # даже если часть видео не скачалась или часть роликов не
                # сконвертировалась. Раньше программа пропускала merge при
                # неполной конвертации, из-за чего пользователь оставался без
                # общего аудио даже при 70+ успешных файлах.
                if converted:
                    if len(converted) < len(current_downloaded):
                        self.log(
                            "⚠️ Объединяю только успешно сконвертированные аудио: "
                            f"{len(converted)}/{len(current_downloaded)}. "
                            "Итоговый файл будет неполным относительно всей очереди, "
                            "но все успешные MP3 будут включены.",
                            "WARNING"
                        )
                        self.record_problem(
                            "Объединение аудио запущено по успешным MP3, несмотря на неполную конвертацию",
                            "WARNING", "merge_audio_partial_success",
                            {
                                "downloaded_video_count": len(current_downloaded),
                                "converted_audio_count": len(converted),
                                "missing_audio_count": max(0, len(current_downloaded) - len(converted)),
                                "converted_audio_files": converted,
                            }
                        )
                    else:
                        self.log(
                            f"🔗 ОБЪЕДИНЕНИЕ АУДИО: {len(converted)} успешных MP3",
                            "INFO"
                        )

                    self.merge_audio_files(
                        converted, str(save_dir),
                        self.sanitize_filename(save_dir.name)
                    )
                else:
                    self.log(
                        "⚠️ Объединение аудио не запускается: нет успешно сконвертированных MP3",
                        "WARNING"
                    )
                    self.record_problem(
                        "Объединение аудио не запущено: список успешных MP3 пуст",
                        "WARNING", "merge_audio_no_converted_files",
                        {
                            "downloaded_video_count": len(current_downloaded),
                            "converted_audio_count": 0,
                        }
                    )

        current_problematic = problematic_downloads.copy()
        if current_problematic:
            problematic_file = self.save_problematic_downloads_session(current_problematic)
            problematic_location = (
                problematic_file.name if problematic_file else "Обработать вручную"
            )
            self.log(
                f"⚠️ Пропущено {len(current_problematic)} проблемно скачиваемых видео. "
                f"Ссылки сохранены для ручной обработки: {problematic_location}",
                "WARNING"
            )
            self.log(
                f"📁 Открыть папку Обработать вручную: {self.manual_processing_dir}",
                "MANUAL_FOLDER_LINK"
            )

        current_failed = failed_urls.copy()

        if current_failed:
            failed_file = self.save_failed_downloads_session(current_failed)
            failed_location = failed_file.name if failed_file else "Обработать вручную"
            self.log(
                f"⚠️ Не удалось скачать {len(current_failed)} видео. "
                f"Ссылки сохранены для ручной обработки: {failed_location}", "WARNING"
            )
            self.log(
                f"📁 Открыть папку Обработать вручную: {self.manual_processing_dir}",
                "MANUAL_FOLDER_LINK"
            )

        self.write_problem_session_summary(
            "cancelled" if self.cancel_flag.is_set() else "completed",
            {
                "queued_url_count": len(urls),
                "downloaded_video_count": len(downloaded_videos),
                "failed_download_count": len(current_failed),
                "problematic_download_count": len(current_problematic),
                "converted_audio_count": len(converted),
                "cancel_requested": self.cancel_flag.is_set(),
                "elapsed_sec": round(
                    time.time() - self.download_start_time, 2
                ) if self.download_start_time else 0,
            },
        )
        self.finish_download()

    def start_download(self) -> None:
        if self.missing_deps:
            messagebox.showerror(
                "Ошибка",
                "Невозможно начать загрузку!\n\n"
                f"Не установлены: {', '.join(self.missing_deps)}"
            )
            return

        self.save_current_settings()

        if hasattr(self, "proxy_enabled") and self.proxy_enabled.get() and not self.get_proxy_url():
            messagebox.showwarning(
                "Прокси указан неправильно",
                "Галочка «Прокси yt-dlp» включена, но адрес proxy пустой или некорректный.\n\n"
                f"Пример: {DEFAULT_PROXY_EXAMPLE}\n"
                "Можно либо исправить адрес, либо временно выключить галочку."
            )
            return

        raw = self.url_text.get("1.0", tk.END).strip().split("\n")
        all_urls = [u.strip() for u in raw if u.strip()]

        valid_urls: List[str] = []
        invalid_urls: List[str] = []
        for url in all_urls:
            if self.validate_url(url):
                valid_urls.append(url)
            else:
                if url:
                    invalid_urls.append(url)

        # Дедупликация URL в текстовом поле: preserve order + одинаковый YouTube video_id.
        valid_urls, duplicate_urls = self.deduplicate_urls_by_identity(valid_urls)
        if duplicate_urls:
            self.log(f"ℹ️ Убрано {len(duplicate_urls)} дублей внутри очереди", "INFO")

        if invalid_urls:
            self.log(f"⚠️ Пропущено {len(invalid_urls)} невалидных URL", "WARNING")

        already_done = [u for u in valid_urls if self.is_url_downloaded(u)]
        urls = [u for u in valid_urls if not self.is_url_downloaded(u)]

        if already_done:
            self.log(f"⏭️ Пропущено {len(already_done)} уже скачанных видео", "WARNING")

        if len(urls) > MAX_URLS_PER_SESSION:
            messagebox.showwarning(
                "Слишком много URL",
                f"Максимум {MAX_URLS_PER_SESSION} видео за раз.\n"
                f"Вы добавили {len(urls)}.\nРазделите на несколько очередей."
            )
            return

        if not urls:
            messagebox.showwarning(
                "Предупреждение",
                "Нет новых видео для скачивания.\n\nВсе URL уже скачаны или список пуст."
            )
            return

        if not self.check_internet_connection():
            messagebox.showerror("Ошибка", "Нет подключения к интернету!")
            return

        session_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.current_session_start = session_time
        self.current_history_session_file = None
        self.current_download_log_session_file = None
        self.current_session_download_count = 0

        self.is_downloading = True
        self.cancel_flag.clear()
        try:
            self.max_concurrent = max(1, min(self.concurrent_var.get(), MAX_CONCURRENT_DL))
        except tk.TclError:
            self.max_concurrent = 2

        # VPN/TUN-режим: если пользователь выбрал 2 потока, не понижаем YouTube до 1.
        # При включённом системном VPN два параллельных ролика обычно качаются стабильно,
        # а ограничение до 1 потока сильно замедляло большие очереди.
        if any(self.is_youtube_url(u) for u in urls):
            self.log(
                f"🌐 VPN/TUN-режим: YouTube будет качаться в {self.max_concurrent} поток(а/ов). "
                "Если VPN выключен и снова пойдут Read timed out, поставьте 1 поток.",
                "INFO"
            )

        self.total_files.reset(len(urls))
        self.completed_files.reset(0)
        self.download_start_time = time.time()
        with self.youtube_strategy_state_lock:
            self.youtube_primary_auth_failure_count = 0
            self.youtube_primary_success_count = 0
            self.youtube_adaptive_strategy_logged = False

        self.download_btn.config(state=tk.DISABLED)
        self.cancel_btn.config(state=tk.NORMAL)
        self.main_progress_frame.grid()
        self.main_progress['value'] = 0
        self.main_progress['maximum'] = len(urls)
        self.update_main_progress(0)

        self.log(f"🚀 Начало загрузки {len(urls)} видео", "INFO")
        self.record_problem(
            "Старт сессии скачивания: полный снимок очереди и настроек",
            "INFO", "download_session_started",
            {
                "url_count": len(urls),
                "urls": urls[:MAX_URLS_PER_SESSION],
                "quality": "1080p",
                "audio_quality": self.audio_quality.get(),
                "max_concurrent": self.max_concurrent,
                "merge_audio": self.merge_audio.get(),
                "download_subtitles": self.download_subtitles.get(),
                "proxy_enabled": bool(self.get_proxy_url()),
                "proxy_url_masked": self.mask_proxy_url(self.get_proxy_url()),
                "save_path": self.save_path.get(),
            }
        )
        self.write_problem_session_summary(
            "running",
            {
                "queued_url_count": len(urls),
                "quality": "1080p",
                "max_concurrent": self.max_concurrent,
                "merge_audio": self.merge_audio.get(),
                "proxy_enabled": bool(self.get_proxy_url()),
            },
        )

        thread = threading.Thread(
            target=self.download_manager, args=(urls,), daemon=True
        )
        thread.start()

    def save_problematic_downloads_session(self, problematic_items: List[Dict]) -> Optional[Path]:
        """Сохраняет ссылки на видео, которые качались слишком долго/рывками.

        Это отдельный список, не смешиваем его с «Неудачными загрузками»: такие
        ролики часто скачиваются нормально позже после смены VPN-сервера.
        """
        if not problematic_items:
            return None
        try:
            self.manual_processing_dir.mkdir(parents=True, exist_ok=True)
            session_ts = (self.current_session_start
                          or datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
            problematic_file = self.make_unique_session_file(
                self.manual_processing_dir, "problematic", session_ts
            )

            with open(problematic_file, 'w', encoding='utf-8') as f:
                f.write("Обработать вручную — слишком медленно скачивалось\n")
                f.write(f"Сессия: {session_ts}\n")
                f.write(f"Количество: {len(problematic_items)}\n")
                f.write("Причина: видео качалось слишком медленно через текущий VPN/CDN YouTube.\n")
                f.write("Рекомендация: сменить VPN-сервер и повторно вставить эти ссылки в программу.\n")
                f.write("=" * 80 + "\n\n")

                for idx, item in enumerate(problematic_items, 1):
                    url = item.get("url") or item.get("context", {}).get("url") or "-"
                    reason = item.get("reason", "слишком медленное скачивание")
                    context = item.get("context", {}) or {}
                    diag = context.get("slow_download_diagnostic", {}) or {}
                    f.write(f"{idx}. URL: {url}\n")
                    f.write(f"   Причина: {reason}\n")
                    if item.get("index_in_session"):
                        f.write(
                            f"   Номер в очереди: {item.get('index_in_session')}/"
                            f"{item.get('total_in_session', '-')}\n"
                        )
                    if context.get("strategy"):
                        f.write(f"   Стратегия yt-dlp: {context.get('strategy')}\n")
                    if diag:
                        f.write(f"   Прошло минут: {diag.get('elapsed_min', '-')}\n")
                        f.write(f"   Прогресс: {diag.get('last_percent', '-')}%\n")
                        f.write(
                            f"   Фрагмент: {diag.get('last_fragment', '-')}"
                            f"/{diag.get('last_fragment_total', '-')}\n"
                        )
                        f.write(f"   Скорость: {diag.get('last_speed_text', '-')}\n")
                        f.write(f"   Последняя строка: {diag.get('last_progress_line', '-')}\n")
                    f.write("   JSON для ИИ: ")
                    f.write(json.dumps(item, ensure_ascii=False, default=str))
                    f.write("\n\n")

            self.log(
                f"💾 Ссылки на медленные загрузки сохранены для ручной обработки: {problematic_file.name}",
                "INFO"
            )
            return problematic_file
        except Exception as e:
            self.log(f"Ошибка сохранения проблемно скачиваемых видео: {str(e)}", "ERROR")
            self.record_problem(
                "Не удалось сохранить файл проблемно скачиваемых видео текущей сессии",
                "ERROR", "save_problematic_downloads_session",
                {"problematic_items": problematic_items}, e
            )
            return None

    def save_failed_downloads_session(self, failed_urls: List[str]) -> Optional[Path]:
        if not failed_urls:
            return None
        try:
            self.manual_processing_dir.mkdir(parents=True, exist_ok=True)
            session_ts = (self.current_session_start
                          or datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
            safe_ts = session_ts.replace(':', '-').replace(' ', '_')
            failed_file = self.manual_processing_dir / f"failed_{safe_ts}.txt"

            counter = 2
            while failed_file.exists():
                failed_file = self.manual_processing_dir / f"failed_{safe_ts}_{counter}.txt"
                counter += 1

            with open(failed_file, 'w', encoding='utf-8') as f:
                f.write("Обработать вручную — видео не скачалось\n")
                f.write(f"Сессия: {session_ts}\n")
                f.write(f"Количество: {len(failed_urls)}\n")
                f.write("=" * 80 + "\n\n")
                for idx, url in enumerate(failed_urls, 1):
                    f.write(f"{idx}. {url}\n")

            self.log(f"💾 Неудачные загрузки сохранены для ручной обработки: {failed_file.name}", "INFO")
            return failed_file
        except Exception as e:
            self.log(f"Ошибка сохранения неудачных загрузок: {str(e)}", "ERROR")
            self.record_problem(
                "Не удалось сохранить файл неудачных загрузок текущей сессии",
                "ERROR", "save_failed_downloads_session",
                {"failed_urls": failed_urls}, e
            )
            return None

    def save_failed_conversions_session(self, failed_conversions: List[Dict]) -> Optional[Path]:
        if not failed_conversions:
            return None
        try:
            self.manual_processing_dir.mkdir(parents=True, exist_ok=True)
            session_ts = (self.current_session_start
                          or datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
            failed_file = self.make_unique_session_file(
                self.manual_processing_dir, "not_converted", session_ts
            )

            def duration_text(value) -> Optional[str]:
                if value is None:
                    return None
                if isinstance(value, (int, float)):
                    return f"{self.format_duration(float(value))} ({float(value):.3f} сек)"
                return str(value)

            with open(failed_file, 'w', encoding='utf-8') as f:
                f.write("Обработать вручную — не конвертировалось в аудио\n")
                f.write(f"Сессия: {session_ts}\n")
                f.write(f"Количество проблемных видео: {len(failed_conversions)}\n")
                f.write("Важно: остальные скачанные видео программа продолжила конвертировать.\n")
                f.write("=" * 80 + "\n\n")

                for idx, item in enumerate(failed_conversions, 1):
                    f.write(f"{idx}. Видео: {item.get('video_file', '-')}\n")
                    if item.get("original_video_file") != item.get("video_file"):
                        f.write(f"   Исходное расположение: {item.get('original_video_file', '-')}\n")
                    f.write(f"   MP3: {item.get('audio_file', '-')}\n")
                    if item.get("partial_audio_file"):
                        f.write(
                            f"   Сохранён неполный MP3: "
                            f"{item.get('partial_audio_file')}\n"
                        )
                    f.write(f"   Причина: {item.get('reason', '-')}\n")

                    source_duration = duration_text(item.get("source_duration"))
                    if source_duration:
                        f.write(f"   Длительность видео: {source_duration}\n")

                    audio_duration = duration_text(item.get("audio_duration"))
                    if audio_duration:
                        f.write(f"   Длительность mp3: {audio_duration}\n")

                    if "ffmpeg_returncode" in item:
                        f.write(f"   Код FFmpeg: {item.get('ffmpeg_returncode')}\n")

                    if item.get("ffmpeg_stderr_excerpt"):
                        f.write(f"   FFmpeg stderr: {item.get('ffmpeg_stderr_excerpt')}\n")

                    f.write("   JSON для ИИ: ")
                    f.write(json.dumps(item, ensure_ascii=False, default=str))
                    f.write("\n\n")

            self.log(
                f"💾 Проблемы конвертации сохранены в отдельный файл: {failed_file.name}",
                "INFO"
            )
            return failed_file
        except Exception as e:
            self.log(f"Ошибка сохранения лога не сконвертированных видео: {str(e)}", "ERROR")
            self.record_problem(
                "Не удалось сохранить файл проблемных конвертаций текущей сессии",
                "ERROR", "save_failed_conversions_session",
                {"failed_conversions": failed_conversions}, e
            )
            return None

    def save_current_settings(self) -> None:
        try:
            concurrent_downloads = self.concurrent_var.get()
        except tk.TclError:
            concurrent_downloads = self.settings.get("concurrent_downloads", 2)
        try:
            split_hours = self.split_hours.get()
        except tk.TclError:
            split_hours = self.settings.get("split_hours", MAX_SPLIT_HOURS)
        try:
            concurrent_downloads = int(concurrent_downloads)
        except (TypeError, ValueError):
            concurrent_downloads = 2
        try:
            split_hours = int(split_hours)
        except (TypeError, ValueError):
            split_hours = MAX_SPLIT_HOURS

        self.settings.update({
            "save_path": self.save_path.get(),
            "quality": "1080p",
            "audio_quality": self.audio_quality.get(),
            "concurrent_downloads": max(1, min(int(concurrent_downloads), MAX_CONCURRENT_DL)),
            "download_subtitles": self.download_subtitles.get(),
            "write_problem_logs": (
                self.write_problem_logs.get()
                if hasattr(self, "write_problem_logs") else self.problem_logging_enabled
            ),
            "proxy_enabled": (
                bool(self.proxy_enabled.get()) if hasattr(self, "proxy_enabled") else self.settings.get("proxy_enabled", False)
            ),
            "proxy_url": (
                str(self.proxy_url.get()).strip() if hasattr(self, "proxy_url") else self.settings.get("proxy_url", "")
            ),
            "merge_audio": self.merge_audio.get(),
            "split_hours": max(0, min(int(split_hours), MAX_SPLIT_HOURS)),
            "urls": []
        })
        self.save_settings()

    def check_internet_connection(self) -> bool:
        # Если пользователь включил proxy для yt-dlp, прямой TCP-тест до YouTube может
        # быть нерелевантен: сам yt-dlp будет ходить через proxy. Поэтому не блокируем
        # старт загрузки, а оставляем проверку реальной доступности на yt-dlp.
        if self.get_proxy_url():
            self.log(
                f"🌐 Прямую проверку YouTube пропускаю: yt-dlp будет использовать proxy {self.proxy_status_text()}",
                "INFO"
            )
            return True

        targets = [("www.youtube.com", 443), ("8.8.8.8", 53), ("1.1.1.1", 53)]
        last_error = None
        for host, port in targets:
            try:
                with socket.create_connection((host, port), timeout=5):
                    return True
            except OSError as e:
                last_error = e
        self.record_problem(
            "Проверка интернета не смогла подключиться ни к одному тестовому адресу",
            "WARNING", "check_internet_connection",
            {"targets": targets}, last_error
        )
        return False

    # ─────────────────────────────────────────────
    # ПРОГРЕСС
    # ─────────────────────────────────────────────

    def update_main_progress(self, current: int) -> None:
        def _update():
            total = self.total_files.value()
            if total > 0:
                self.main_progress['value'] = current
                p = int((current / total) * 100)
                self.progress_percent.config(text=f"{p}%")
        self.root.after(0, _update)

    def update_progress(self) -> None:
        current = self.completed_files.increment(1)
        self.update_main_progress(current)

    def cancel_download(self) -> None:
        self.cancel_flag.set()
        stopped = self.terminate_active_processes()
        if stopped:
            self.log(f"🛑 Остановлено активных процессов: {stopped}", "WARNING")
        self.log("🛑 Отмена загрузки...", "WARNING")

    def finish_download(self) -> None:
        self.root.after(0, self._finish_download_ui)

    def _finish_download_ui(self) -> None:
        self.is_downloading = False
        self.download_btn.config(state=tk.NORMAL)
        self.cancel_btn.config(state=tk.DISABLED)
        try:
            self.main_progress_frame.grid_remove()
        except Exception:
            pass
        elapsed = (time.time() - self.download_start_time
                   if self.download_start_time else 0)
        self.log(f"✅ Завершено! Время: {self.format_duration(elapsed)}", "SUCCESS")

    # ─────────────────────────────────────────────
    # СОХРАНЕНИЕ ССЫЛОК ИЗ ОЧЕРЕДИ
    # ─────────────────────────────────────────────

    def get_urls_from_textbox(self) -> List[str]:
        """Возвращает все непустые строки из поля ссылок без изменения порядка."""
        try:
            raw_text = self.url_text.get("1.0", tk.END)
        except Exception as e:
            self.record_problem(
                "Не удалось прочитать поле ссылок для сохранения сессии",
                "WARNING", "get_urls_from_textbox", exception=e
            )
            return []

        urls: List[str] = []
        for line in raw_text.splitlines():
            clean = line.strip()
            if clean:
                urls.append(clean)
        return urls

    def trim_download_link_sessions(self) -> int:
        """
        Оставляет только MAX_DOWNLOAD_LINK_SESSIONS последних файлов очереди ссылок.
        Сортировка идёт по дате из имени файла, а если её нет — по времени изменения.
        """
        if not self.download_links_dir.exists():
            return 0

        files = [
            p for p in self.download_links_dir.glob("links_*.txt")
            if p.is_file()
        ]

        def sort_key(path: Path) -> datetime:
            parsed = self._parse_session_datetime_from_name(path)
            if parsed:
                return parsed
            try:
                return datetime.fromtimestamp(path.stat().st_mtime)
            except OSError:
                return datetime.min

        files.sort(key=sort_key, reverse=True)
        old_files = files[MAX_DOWNLOAD_LINK_SESSIONS:]
        removed = 0

        for old_file in old_files:
            try:
                old_file.unlink()
                removed += 1
            except OSError as e:
                self.record_problem(
                    "Не удалось удалить старый файл сессии ссылок",
                    "WARNING", "trim_download_link_sessions",
                    {"file": str(old_file), "max_sessions": MAX_DOWNLOAD_LINK_SESSIONS},
                    e
                )

        return removed

    def save_download_links_session(self, reason: str = "закрытие программы") -> Optional[Path]:
        """
        Сохраняет текущую очередь ссылок в отдельный .txt-файл.
        Это страховка на случай случайного закрытия программы: введённые ссылки
        можно восстановить из папки «Ссылки на скачивания».
        """
        urls = self.get_urls_from_textbox()
        if not urls:
            return None

        try:
            session_ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            links_file = self.make_unique_session_file(
                self.download_links_dir, "links", session_ts
            )

            with open(links_file, 'w', encoding='utf-8') as f:
                f.write("Ссылки на скачивания\n")
                f.write(f"Сессия: {session_ts}\n")
                f.write(f"Причина сохранения: {reason}\n")
                f.write(f"Количество строк: {len(urls)}\n")
                f.write(
                    f"Хранение: автоматически остаются только последние "
                    f"{MAX_DOWNLOAD_LINK_SESSIONS} сессий\n"
                )
                f.write("=" * 80 + "\n\n")
                for url in urls:
                    f.write(url + "\n")

            removed = self.trim_download_link_sessions()
            self.log(
                f"💾 Ссылки из очереди сохранены: {links_file.name} "
                f"(строк: {len(urls)})",
                "DOWNLOAD_LINKS_FOLDER_LINK"
            )
            if removed:
                self.log(
                    f"🧹 Старые сессии ссылок удалены: {removed}. "
                    f"Оставлено последних {MAX_DOWNLOAD_LINK_SESSIONS}.",
                    "INFO"
                )
            return links_file

        except Exception as e:
            self.log(f"❌ Не удалось сохранить ссылки из очереди: {e}", "ERROR")
            self.record_problem(
                "Не удалось сохранить текущие ссылки на скачивания в отдельный файл",
                "ERROR", "save_download_links_session",
                {
                    "reason": reason,
                    "url_count": len(urls),
                    "download_links_dir": str(self.download_links_dir),
                },
                e
            )
            return None

    # ─────────────────────────────────────────────
    # ЗАКРЫТИЕ ПРИЛОЖЕНИЯ
    # ─────────────────────────────────────────────

    def on_closing(self) -> None:
        if self.is_downloading:
            if messagebox.askyesno("Подтверждение", "Идёт загрузка. Остановить и выйти?"):
                self.save_download_links_session("закрытие программы во время загрузки")
                self.cancel_flag.set()
                self.terminate_active_processes()
                self.log("🛑 Остановка...", "WARNING")
                self.save_current_settings()
                self.wait_for_threads(timeout=30)
                self.finalize_problem_diagnostics(
                    "finished",
                    "application_closed_after_cancel",
                    {"download_was_active": True},
                )
                self.root.destroy()
        else:
            self.save_download_links_session("закрытие программы")
            self.save_current_settings()
            self.finalize_problem_diagnostics(
                "finished",
                "application_closed",
                {"download_was_active": False},
            )
            self.root.destroy()

    def wait_for_threads(self, timeout: int = 30) -> None:
        # ThreadSafeList.__iter__ возвращает копию — безопасно при модификации списка потоками
        start = time.time()
        for thread in self.download_threads:
            if thread.is_alive():
                remaining = timeout - (time.time() - start)
                if remaining > 0:
                    thread.join(timeout=remaining)

    # ─────────────────────────────────────────────
    # UI — РАБОТА С URL
    # ─────────────────────────────────────────────

    def paste_from_clipboard(self) -> None:
        try:
            clipboard_text = self.root.clipboard_get().strip()
            if not clipboard_text:
                self.log("⚠️ Буфер обмена пуст", "WARNING")
                return
            urls = [u.strip() for u in clipboard_text.split("\n") if u.strip()]
            if not urls:
                return
            self.add_urls_with_check(urls)
        except tk.TclError:
            self.log("⚠️ Буфер обмена недоступен", "WARNING")
        except Exception as e:
            self.log(f"❌ Ошибка вставки: {str(e)}", "ERROR")

    def clear_urls(self) -> None:
        self.url_text.delete("1.0", tk.END)

    def show_context_menu(self, event) -> None:
        try:
            self.context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.context_menu.grab_release()

    def copy_text(self) -> None:
        try:
            text = self.url_text.get(tk.SEL_FIRST, tk.SEL_LAST)
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
        except tk.TclError:
            pass

    def paste_text(self) -> None:
        try:
            text = self.root.clipboard_get().strip()
            if text:
                self.url_text.insert(tk.INSERT, text)
        except tk.TclError:
            pass

    def delete_text(self) -> None:
        try:
            self.url_text.delete(tk.SEL_FIRST, tk.SEL_LAST)
        except tk.TclError:
            pass

    def check_duplicates_in_textbox(self) -> None:
        content = self.url_text.get("1.0", tk.END)
        if not content.strip():
            messagebox.showinfo("Информация", "Список пуст")
            return

        self.url_text.tag_remove("DUPLICATE", "1.0", tk.END)
        self.url_text.tag_remove("NEW", "1.0", tk.END)

        # FIX #16: отслеживаем реальный номер строки в виджете,
        #          а не порядковый индекс непустых URL (фикс смещения при пустых строках)
        dup_count = 0
        new_count = 0
        lines_raw = content.split("\n")
        for line_num, raw_line in enumerate(lines_raw, start=1):
            url = raw_line.strip()
            if not url:
                continue
            start = f"{line_num}.0"
            end   = f"{line_num}.end"
            if self.is_url_downloaded(url):
                self.url_text.tag_add("DUPLICATE", start, end)
                dup_count += 1
            else:
                self.url_text.tag_add("NEW", start, end)
                new_count += 1

        if dup_count > 0:
            messagebox.showwarning(
                "Найдены дубликаты!",
                f"🔴 Уже скачано: {dup_count}\n"
                f"🟢 Новых: {new_count}\n\n"
                f"Дубликаты подсвечены красным."
            )
            self.log(f"⚠️ Найдено {dup_count} дубликатов", "WARNING")
        else:
            messagebox.showinfo("Отлично!", f"✅ Все {new_count} видео новые!")
            self.log(f"✅ Дубликатов нет: {new_count} новых видео", "SUCCESS")

    # ─────────────────────────────────────────────
    # UI — НАСТРОЙКИ
    # ─────────────────────────────────────────────

    def on_problem_logging_change(self) -> None:
        enabled = bool(self.write_problem_logs.get())
        self.problem_logging_enabled = enabled
        if hasattr(self, "settings"):
            self.settings["write_problem_logs"] = enabled

        if enabled:
            self.problem_runtime_terminal = False
            self.ensure_problem_log_dirs()
            self.setup_logger()
            self.initialize_problem_diagnostics()
            self.log(
                "✅ Логи проблем включены. При ошибках будет создана подробная диагностика для ИИ.",
                "SUCCESS"
            )
            self.record_problem(
                "Пользователь включил запись логов проблем",
                "INFO", "problem_logging_toggle", {"enabled": True}
            )
        else:
            self._write_active_run_state(
                "disabled",
                "problem_logging_disabled",
                {"disabled_by_user": True},
                terminal=True,
                force=True,
            )
            self._remove_problem_log_handlers()
            self.log(
                "ℹ️ Логи проблем отключены. Окно программы продолжит показывать ход загрузки, но папка «Логи проблем» пополняться не будет.",
                "INFO"
            )

        self.save_current_settings()

    def on_merge_change(self) -> None:
        if self.merge_audio.get():
            self.split_frame.grid()
        else:
            self.split_frame.grid_remove()

    def check_split_warning(self) -> None:
        # FIX #17: при нечисловом вводе сбрасываем в MAX вместо молчаливого ignore
        try:
            val = self.split_hours.get()
            if val > MAX_SPLIT_HOURS:
                self.split_hours.set(MAX_SPLIT_HOURS)
                messagebox.showwarning(
                    "Ограничение", f"Максимум {MAX_SPLIT_HOURS} часов на часть!"
                )
        except tk.TclError:
            self.split_hours.set(MAX_SPLIT_HOURS)

    def browse_path(self) -> None:
        path = filedialog.askdirectory()
        if path:
            self.save_path.set(path)

    # ─────────────────────────────────────────────
    # ЗАВИСИМОСТИ — UI
    # ─────────────────────────────────────────────

    def log_retention_status(self) -> None:
        self.log(
            f"🧹 История скачиваний хранится {DOWNLOAD_HISTORY_RETENTION_DAYS} дней, "
            f"логи проблем — {PROBLEM_LOG_RETENTION_DAYS} дней",
            "INFO"
        )
        if self.should_write_problem_logs():
            self.log(
                "📉 Логи проблем включены: компактная история, итог сессии "
                "и индекс последней нерешённой проблемы",
                "INFO"
            )
            self.log(
                f"📄 Логи этого запуска: Логи проблем/Сессии/{self.app_session_timestamp}",
                "PROBLEM_FOLDER_LINK"
            )
        else:
            self.log(
                "📄 Логи проблем отключены галочкой «Писать логи проблем». Папка «Логи проблем» пополняться не будет.",
                "INFO"
            )
        self.log(
            f"📄 Debug этого запуска: Логи скачивания/Сессии запусков/{self.debug_session_log_file.name}",
            "INFO"
        )
        if getattr(self, "retention_cleanup_count", 0):
            self.log(
                f"🧹 Старые логи удалены или сжаты: {self.retention_cleanup_count}",
                "INFO"
            )
        errors = getattr(self, "retention_cleanup_errors", [])
        if errors:
            self.log(
                f"⚠️ Не удалось очистить часть старых логов: {len(errors)}. "
                "Подробности записаны в app_debug.log",
                "WARNING"
            )
            for error in errors[:10]:
                self.file_logger.warning(f"log retention cleanup: {error}")

    def show_dependency_warning(self) -> None:
        optional_missing = getattr(self, "optional_missing_deps", [])
        self.log("⚠️ ПРОВЕРЬТЕ ЗАВИСИМОСТИ!", "CRITICAL" if self.missing_deps else "WARNING")
        self.log_retention_status()

        if "JavaScript Runtime (Deno или Node.js)" in optional_missing:
            self.log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "WARNING")
            self.log("📢 Deno/Node.js не найден. Для YouTube сейчас это важно: без JS runtime могут пропадать форматы.", "WARNING")
            self.log("   Базовую загрузку не блокирую, но 1080p может работать хуже.", "WARNING")
            self.log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "WARNING")
        else:
            self.log("✅ JS runtime найден. Для YouTube включаю EJS remote-components автоматически.", "SUCCESS")
        if "yt-dlp" in self.missing_deps:
            self.log("⚠️ yt-dlp не установлен", "WARNING")
        if "ffmpeg" in self.missing_deps:
            self.log("⚠️ ffmpeg не установлен", "WARNING")
        self.log(
            "📄 Ручная обработка: Обработать вручную — ссылки на несостоявшиеся загрузки "
            "и видео с ошибками конвертации",
            "MANUAL_FOLDER_LINK"
        )
        if self.should_write_problem_logs():
            self.log(
                "📄 Диагностика проблем для ИИ: Логи проблем/Сессии/<запуск>/events.jsonl "
                "+ incidents.jsonl + attachments; начинать с корневого latest_run.json "
                "и README_FOR_CODEX.md",
                "PROBLEM_FOLDER_LINK"
            )
        else:
            self.log(
                "📄 Диагностика проблем для ИИ отключена галочкой «Писать логи проблем»",
                "INFO"
            )

        dep_frame = ttk.Frame(self.root, relief="solid", borderwidth=2, padding="10")
        dep_frame.pack(fill=tk.X, padx=10, pady=10)

        title = ("⚠️ Установите недостающие компоненты:"
                 if self.missing_deps else
                 "⚠️ Рекомендуемые компоненты:")
        ttk.Label(dep_frame, text=title,
                  font=("Arial", 10, "bold"), foreground="red").pack(pady=5)

        btn_frame = ttk.Frame(dep_frame)
        btn_frame.pack(fill=tk.X, pady=5)

        if "yt-dlp" in self.missing_deps:
            ttk.Button(btn_frame, text="Установить yt-dlp",
                       command=self.auto_install_packages).pack(side=tk.LEFT, padx=5)
        if "JavaScript Runtime (Deno или Node.js)" in optional_missing:
            ttk.Button(
                btn_frame, text="Сайт Deno",
                command=lambda: webbrowser.open("https://deno.com/")
            ).pack(side=tk.LEFT, padx=5)
            ttk.Button(
                btn_frame, text="Сайт Node.js",
                command=lambda: webbrowser.open("https://nodejs.org/")
            ).pack(side=tk.LEFT, padx=5)
        if "ffmpeg" in self.missing_deps:
            ttk.Button(
                btn_frame, text="Сайт FFmpeg",
                command=lambda: webbrowser.open("https://ffmpeg.org/download.html")
            ).pack(side=tk.LEFT, padx=5)

        ttk.Button(btn_frame, text="Перепроверить",
                   command=self.recheck_dependencies).pack(side=tk.LEFT, padx=5)

    def recheck_dependencies(self) -> None:
        self.log("🔄 Перепроверка зависимостей...", "INFO")
        try:
            r = subprocess.run(
                ["yt-dlp", "--version"], capture_output=True,
                timeout=5, creationflags=self.subprocess_flags
            )
            if r.returncode == 0:
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp[default]"],
                    capture_output=True, timeout=60,
                    creationflags=self.subprocess_flags
                )
                self.log("✅ yt-dlp обновлён", "SUCCESS")
        except Exception as e:
            self.record_problem("Ошибка при перепроверке/обновлении yt-dlp",
                                "WARNING", "recheck_dependencies", exception=e)

        self.check_dependencies()
        optional_missing = getattr(self, "optional_missing_deps", [])
        if not self.missing_deps and not optional_missing:
            self.log("✅ Все зависимости установлены!", "SUCCESS")
            messagebox.showinfo("Успех", "✅ Все зависимости установлены!")
        elif not self.missing_deps:
            self.log("✅ Обязательные зависимости установлены. Есть только рекомендации.", "SUCCESS")
            messagebox.showinfo(
                "Можно скачивать",
                "✅ Обязательные зависимости установлены.\n\n"
                "Рекомендовано, но не обязательно:\n• " + "\n• ".join(optional_missing)
            )
        else:
            messagebox.showwarning(
                "Внимание",
                "Ещё отсутствуют:\n• " + "\n• ".join(self.missing_deps)
            )

    def auto_install_packages(self) -> None:
        self.log("📦 Установка yt-dlp...", "INFO")
        for p in self.missing_pip_packages:
            try:
                r = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "--upgrade", ("yt-dlp[default]" if p == "yt-dlp" else p)],
                    capture_output=True, timeout=120,
                    creationflags=self.subprocess_flags
                )
                if r.returncode == 0:
                    self.log(f"✅ {p} установлен", "SUCCESS")
                else:
                    self.log(f"❌ Ошибка установки {p}", "ERROR")
            except subprocess.TimeoutExpired:
                self.log(f"❌ Таймаут установки {p}", "ERROR")
        self.recheck_dependencies()

    # ─────────────────────────────────────────────
    # HTML ПЛЕЙЛИСТ
    # ─────────────────────────────────────────────

    def load_from_html(self) -> None:
        file_path = filedialog.askopenfilename(
            title="Выберите HTML файл плейлиста YouTube",
            filetypes=[("HTML файлы", "*.html *.htm"), ("Все файлы", "*.*")]
        )
        if not file_path:
            return

        win = tk.Toplevel(self.root)
        win.title("Загрузка плейлиста")
        win.geometry("450x180")
        win.transient(self.root)
        win.grab_set()

        ttk.Label(win, text="⚡ Парсинг HTML файла...",
                  font=("Arial", 11, "bold")).pack(pady=20)
        progress = ttk.Progressbar(win, mode='indeterminate', length=350)
        progress.pack(pady=10)
        progress.start(8)
        status_label = ttk.Label(win, text="Чтение файла...")
        status_label.pack(pady=10)

        start_time = time.time()

        def parse_thread():
            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    html_content = f.read()
                self.root.after(0, lambda: status_label.config(text="⚡ Извлечение видео..."))
                videos     = self.parse_youtube_playlist_html_fast(html_content)
                parse_time = time.time() - start_time
                for video in videos:
                    video['is_downloaded'] = self.is_url_downloaded(video['url'])
                self.root.after(
                    0, lambda: self.finish_html_loading(videos, win, parse_time)
                )
            except Exception as e:
                self.root.after(0, lambda: self.handle_html_error(str(e), win))

        threading.Thread(target=parse_thread, daemon=True).start()

    def parse_youtube_playlist_html_fast(self, html_content: str) -> List[Dict]:
        videos: List[Dict] = []
        seen_ids: Set[str] = set()

        json_data = None
        for pattern in [r'var ytInitialData\s*=\s*(\{.*?\});',
                         r'window\["ytInitialData"\]\s*=\s*(\{.*?\});',
                         r'ytInitialData\s*=\s*(\{.*?\});']:
            try:
                m = re.search(pattern, html_content, re.DOTALL)
                if m:
                    json_data = json.loads(m.group(1))
                    break
            except Exception:
                continue

        if json_data:
            videos = self.extract_all_videos_from_json(json_data)
            if videos:
                unique: List[Dict] = []
                for v in videos:
                    if v['video_id'] not in seen_ids:
                        seen_ids.add(v['video_id'])
                        unique.append(v)
                self.log(f"⚡ JSON: {len(unique)} видео", "SUCCESS")
                self.fetch_missing_titles(unique)
                return unique

        self.log("⚠️ JSON не найден, резервный метод", "WARNING")
        vid_ids = re.findall(r'"videoId":"([A-Za-z0-9_-]{11})"', html_content)
        titles  = re.findall(r'"title":{"runs":\[{"text":"([^"]+)"', html_content)

        for i, vid in enumerate(vid_ids):
            if vid not in seen_ids and len(vid) == 11:
                seen_ids.add(vid)
                title = titles[i] if i < len(titles) else 'Без названия'
                videos.append({
                    'url': f'https://www.youtube.com/watch?v={vid}',
                    'title': self.clean_title(title)[:150],
                    'video_id': vid
                })

        if videos:
            self.log(f"⚡ Резервный: {len(videos)} видео", "SUCCESS")
            self.fetch_missing_titles(videos)
        else:
            self.log("❌ Видео не найдены", "ERROR")
        return videos

    def extract_title_from_renderer(self, renderer: dict) -> str:
        title_text = "Без названия"
        try:
            if 'title' in renderer:
                t = renderer['title']
                if 'runs' in t and t['runs']:
                    title_text = t['runs'][0].get('text', '')
                elif 'simpleText' in t:
                    title_text = t['simpleText']
        except Exception:
            pass
        return self.clean_title(title_text)

    def clean_title(self, title: str) -> str:
        if not title:
            return "Без названия"
        try:
            title = html_module.unescape(title)
        except Exception:
            pass

        title = re.sub(r'\\u([0-9a-fA-F]{4})',
                       lambda m: chr(int(m.group(1), 16)), title)
        for old, new in [('\\n', ' '), ('\\r', ' '), ('\\t', ' '),
                         ('\\"', '"'), ("\'", "'"), ('&quot;', '"'),
                         ('&amp;', '&'), ('&lt;', '<'), ('&gt;', '>'),
                         ('&#39;', "'"), ('&nbsp;', ' ')]:
            title = title.replace(old, new)
        title = ' '.join(title.split())
        return title.strip()[:150] if len(title.strip()) >= 2 else "Без названия"

    def fetch_missing_titles(self, videos: List[Dict]) -> None:
        SUSPICIOUS = {'описание', 'без названия', 'комментарии', 'playlist',
                      'description', 'overview', 'about', 'comments'}
        to_fetch = [v for v in videos
                    if (v['title'] == 'Без названия' or not v['title'].strip()
                        or any(w in v['title'].lower() for w in SUSPICIOUS)
                        or len(v['title']) < 10 or '\\u' in v['title'])]
        if not to_fetch:
            return

        self.log(f"📝 Подгрузка названий для {len(to_fetch)} видео...", "INFO")

        def fetch_one(video: Dict) -> Tuple[Dict, Optional[str]]:
            try:
                r = subprocess.run(
                    ["yt-dlp", "--skip-download", "--encoding", "utf-8", "--get-title",
                     "--no-warnings", "--quiet", video['url']],
                    capture_output=True, text=True,
                    encoding='utf-8', errors='replace',
                    env=self._utf8_subprocess_env(),
                    timeout=FETCH_TITLE_TIMEOUT,
                    creationflags=self.subprocess_flags
                )
                return video, r.stdout.strip() if r.returncode == 0 else None
            except Exception:
                return video, None

        # FIX #18: executor.map без try/except ронял весь поток при одной ошибке
        try:
            with ThreadPoolExecutor(max_workers=3) as executor:
                for video, title in executor.map(fetch_one, to_fetch):
                    if title:
                        video['title'] = self.clean_title(title)[:150]
        except Exception as e:
            self.file_logger.error(f"fetch_missing_titles: {e}")

        self.log("✅ Подгрузка завершена", "SUCCESS")

    def extract_all_videos_from_json(self, json_data: dict) -> List[Dict]:
        videos: List[Dict] = []
        # FIX #19: защита от циклических/рекурсивных структур через id() объекта
        seen_objects: Set[int] = set()

        def find(obj, depth: int = 0) -> None:
            obj_id = id(obj)
            if obj_id in seen_objects:
                return
            seen_objects.add(obj_id)

            if depth > 15 or len(videos) >= MAX_VIDEOS_PER_LIST:
                return
            if isinstance(obj, dict):
                for rtype in ['playlistVideoRenderer', 'videoRenderer',
                               'gridVideoRenderer', 'reelItemRenderer']:
                    if rtype in obj:
                        r   = obj[rtype]
                        vid = r.get('videoId')
                        if vid and len(vid) == 11:
                            videos.append({
                                'url': f'https://www.youtube.com/watch?v={vid}',
                                'title': self.extract_title_from_renderer(r),
                                'video_id': vid
                            })
                            return
                for key in obj:
                    find(obj[key], depth + 1)
            elif isinstance(obj, list):
                for item in obj[:100]:
                    find(item, depth + 1)

        try:
            find(json_data)
        except Exception as e:
            self.log(f"⚠️ Ошибка извлечения: {str(e)[:100]}", "WARNING")
        return videos

    def finish_html_loading(self, videos: List[Dict], win: tk.Toplevel,
                             parse_time: float) -> None:
        win.destroy()
        if not videos:
            messagebox.showwarning(
                "Предупреждение", "В HTML файле не найдено видео из плейлиста."
            )
            return
        self.video_list = videos
        self.display_video_list()
        self.video_list_frame.grid()

        downloaded_count = sum(1 for v in videos if v['is_downloaded'])
        new_count = len(videos) - downloaded_count

        msg = (f"⚡ Парсинг за {parse_time:.2f}с\n\n"
               f"📊 {len(videos)} видео:\n"
               f"🔴 Уже скачано: {downloaded_count}\n"
               f"🟢 Новых: {new_count}")
        self.log(
            f"⚡ Парсинг {parse_time:.2f}с: {new_count} новых, {downloaded_count} скачанных",
            "SUCCESS" if downloaded_count == 0 else "WARNING"
        )
        messagebox.showinfo("Готово!", msg)

    def handle_html_error(self, error: str, win: tk.Toplevel) -> None:
        win.destroy()
        messagebox.showerror("Ошибка", f"Не удалось загрузить HTML:\n{error[:200]}")
        self.log(f"❌ Ошибка HTML: {error}", "ERROR")

    # ─────────────────────────────────────────────
    # UI — СПИСОК ВИДЕО
    # ─────────────────────────────────────────────

    def display_video_list(self) -> None:
        for widget in self.video_scrollable_frame.winfo_children():
            widget.destroy()
        self.video_checkboxes = []

        downloaded_count = 0
        new_count = 0

        for idx, video in enumerate(self.video_list):
            is_dl = video.get('is_downloaded', False)
            if is_dl:
                downloaded_count += 1
            else:
                new_count += 1

            if not self.show_downloaded.get() and is_dl:
                continue

            frame = ttk.Frame(self.video_scrollable_frame, relief="solid",
                              borderwidth=2 if is_dl else 1)
            frame.pack(fill=tk.X, padx=5, pady=2)

            var = tk.BooleanVar(value=not is_dl)
            ttk.Checkbutton(frame, variable=var).pack(side=tk.LEFT, padx=5)
            self.video_checkboxes.append((var, idx))

            ttk.Label(frame, text=f"{idx + 1}.", font=("Arial", 9),
                      width=4).pack(side=tk.LEFT)

            bg = "#ff4444" if is_dl else "#44cc44"
            fg = "white" if is_dl else "black"
            label_text   = "✓ УЖЕ СКАЧАНО" if is_dl else "🆕 НОВОЕ"
            status_frame = tk.Frame(frame, bg=bg, padx=8, pady=3)
            status_frame.pack(side=tk.LEFT, padx=5)
            tk.Label(status_frame, text=label_text,
                     font=("Arial", 8, "bold"), bg=bg, fg=fg).pack()

            info_frame = ttk.Frame(frame)
            info_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)
            color = "#888888" if is_dl else "blue"
            ttk.Label(info_frame, text=video['title'],
                      font=("Arial", 9, "bold"), foreground=color).pack(anchor=tk.W)
            ttk.Label(info_frame, text=video['url'],
                      font=("Arial", 8), foreground="gray").pack(anchor=tk.W)

            ttk.Button(frame, text="🗑️", width=3,
                       command=lambda i=idx: self.delete_video(i)).pack(side=tk.RIGHT, padx=5)

        self.update_video_stats(len(self.video_list), new_count, downloaded_count)

    def update_video_stats(self, total: int, new: int, downloaded: int) -> None:
        self.video_stats_label.config(
            text=f"Всего: {total} | 🟢 Новых: {new} | 🔴 Скачанных: {downloaded}"
        )

    def select_only_new(self) -> None:
        count = 0
        for var, idx in self.video_checkboxes:
            if idx < len(self.video_list):
                is_dl = self.video_list[idx].get('is_downloaded', False)
                var.set(not is_dl)
                if not is_dl:
                    count += 1
        self.log(f"✅ Выбрано {count} новых видео", "SUCCESS")

    def delete_video(self, index: int) -> None:
        if 0 <= index < len(self.video_list):
            del self.video_list[index]
            self.display_video_list()

    def delete_selected_videos(self) -> None:
        indices = [idx for var, idx in self.video_checkboxes
                   if var.get() and idx < len(self.video_list)]
        if not indices:
            messagebox.showwarning("Предупреждение", "Не выбрано ни одного видео")
            return
        if messagebox.askyesno("Подтверждение", f"Удалить {len(indices)} видео из списка?"):
            for idx in sorted(indices, reverse=True):
                if idx < len(self.video_list):
                    del self.video_list[idx]
            self.display_video_list()
            self.log(f"🗑️ Удалено {len(indices)} видео", "SUCCESS")

    def select_all_videos(self) -> None:
        for var, _ in self.video_checkboxes:
            var.set(True)

    def deselect_all_videos(self) -> None:
        for var, _ in self.video_checkboxes:
            var.set(False)

    def add_selected_to_queue(self) -> None:
        selected = [self.video_list[idx]['url']
                    for var, idx in self.video_checkboxes
                    if var.get() and idx < len(self.video_list)]
        if not selected:
            messagebox.showwarning("Предупреждение", "Не выбрано ни одного видео")
            return
        added = self.add_urls_with_check(selected)
        if added > 0:
            messagebox.showinfo("Готово", f"✅ Добавлено {added} видео в очередь")

    # ─────────────────────────────────────────────
    # ЛОГИРОВАНИЕ
    # FIX #20: батч-очередь — все потоки кладут сообщения в список,
    #          UI читает их пачкой раз в LOG_FLUSH_INTERVAL мс.
    #          Устраняет flooding root.after и утечку замыканий.
    # ─────────────────────────────────────────────

    def log(self, message: str, level: str = "INFO", update_only: bool = False,
            record_as_problem: bool = False) -> None:
        timestamp   = datetime.now().strftime("%H:%M:%S")
        log_message = f"[{timestamp}] {message}"

        # Дублируем ошибки в файловый лог
        if level in ("ERROR", "CRITICAL"):
            self.file_logger.error(message)
        if level in ("ERROR", "CRITICAL") or (
            level == "WARNING" and record_as_problem
        ):
            self.record_problem(
                message, level, "app_log",
                {"update_only": update_only}
            )

        with self._log_lock:
            self._log_queue.append((log_message, level, update_only))
            if not self._log_flush_pending:
                self._log_flush_pending = True
                self.root.after(LOG_FLUSH_INTERVAL, self._flush_log_queue)

    def _insert_log_message(self, log_message: str, level: str) -> None:
        folder_link_text = {
            "HISTORY_FOLDER_LINK": "История ссылок",
            "DOWNLOAD_LINKS_FOLDER_LINK": "Ссылки на скачивания",
            "DOWNLOADED_SESSIONS_FOLDER_LINK": "Логи скачивания/Сессии скачанных",
            "MANUAL_FOLDER_LINK": "Обработать вручную",
            "PROBLEM_FOLDER_LINK": "Логи проблем",
        }.get(level)

        if not folder_link_text or folder_link_text not in log_message:
            self.log_text.insert(tk.END, log_message + "\n", level)
            return

        start = log_message.find(folder_link_text)
        end = start + len(folder_link_text)
        self.log_text.insert(tk.END, log_message[:start], "INFO")
        self.log_text.insert(tk.END, log_message[start:end], level)
        self.log_text.insert(tk.END, log_message[end:] + "\n", "INFO")

    def _flush_log_queue(self) -> None:
        """Сброс накопленных лог-сообщений в UI (вызывается только из main thread)."""
        with self._log_lock:
            items = self._log_queue[:]
            self._log_queue.clear()
            self._log_flush_pending = False

        try:
            keep_at_bottom = self._is_log_scrolled_to_bottom()
            for log_message, level, update_only in items:
                if update_only:
                    idx = self.log_text.index('end-1c').split('.')[0]
                    if int(idx) > 1:
                        self.log_text.delete(f"{idx}.0", "end")
                self._insert_log_message(log_message, level)
            lines = int(self.log_text.index('end-1c').split('.')[0])
            if lines > MAX_LOG_LINES:
                self.log_text.delete("1.0", f"{lines - MAX_LOG_LINES}.0")
            if keep_at_bottom:
                self.log_text.see(tk.END)
        except Exception:
            pass

    def _is_log_scrolled_to_bottom(self) -> bool:
        try:
            return self.log_text.yview()[1] >= 0.995
        except Exception:
            return True

    def open_folder_path(self, folder: Path, error_label: str) -> None:
        try:
            folder.mkdir(parents=True, exist_ok=True)
            if platform.system() == "Windows":
                os.startfile(str(folder))
            else:
                webbrowser.open(folder.resolve().as_uri())
        except Exception as e:
            self.log(f"❌ Не удалось открыть папку {error_label}: {e}", "ERROR")

    def open_history_folder(self, event=None) -> None:
        self.open_folder_path(self.history_dir, "История ссылок")

    def open_download_links_folder(self, event=None) -> None:
        self.open_folder_path(self.download_links_dir, "Ссылки на скачивания")

    def open_downloaded_sessions_folder(self, event=None) -> None:
        self.open_folder_path(self.downloaded_sessions_dir, "Логи скачивания/Сессии скачанных")

    def open_manual_processing_folder(self, event=None) -> None:
        self.open_folder_path(self.manual_processing_dir, "Обработать вручную")

    def open_problem_logs_folder(self, event=None) -> None:
        self.open_folder_path(self.problem_log_dir, "Логи проблем")

    # ─────────────────────────────────────────────
    # МЫШЬ
    # ─────────────────────────────────────────────

    def _on_mousewheel(self, event) -> None:
        # FIX #21: поддержка Linux (Button-4/Button-5) и Windows (event.delta)
        try:
            if not self.video_list_frame.winfo_ismapped():
                return
            if event.delta:
                self.video_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            elif event.num == 4:
                self.video_canvas.yview_scroll(-1, "units")
            elif event.num == 5:
                self.video_canvas.yview_scroll(1, "units")
        except Exception:
            pass

    def _on_log_mousewheel(self, event):
        try:
            if getattr(event, "num", None) == 4:
                units = -3
            elif getattr(event, "num", None) == 5:
                units = 3
            else:
                delta = getattr(event, "delta", 0)
                if not delta:
                    return "break"
                units = int(-delta / 120)
                if units == 0:
                    units = -1 if delta > 0 else 1
                units *= 3
            self.log_text.yview_scroll(units, "units")
            return "break"
        except Exception:
            return "break"

    # ─────────────────────────────────────────────
    # ПОСТРОЕНИЕ UI
    # ─────────────────────────────────────────────

    def setup_ui(self) -> None:
        style = ttk.Style()
        style.configure("TFrame",      background="#f0f0f0")
        style.configure("TLabel",      background="#f0f0f0", font=("Arial", 9))
        style.configure("TButton",     font=("Arial", 9))
        style.configure("TCheckbutton", background="#f0f0f0", font=("Arial", 9))

        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(5, weight=1)

        # ── Прогресс ──
        self.main_progress_frame = ttk.Frame(main_frame, relief="solid", borderwidth=1)
        self.main_progress_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        self.main_progress_frame.columnconfigure(0, weight=1)
        self.main_progress_frame.grid_remove()

        pb_frame = ttk.Frame(self.main_progress_frame)
        pb_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), padx=10, pady=5)
        pb_frame.columnconfigure(0, weight=1)
        ttk.Label(pb_frame, text="Общий прогресс:",
                  font=("Arial", 10, "bold")).grid(row=0, column=0, sticky=tk.W)
        self.main_progress = ttk.Progressbar(pb_frame, mode='determinate')
        self.main_progress.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=5)
        self.progress_percent = ttk.Label(pb_frame, text="0%", font=("Arial", 10, "bold"))
        self.progress_percent.grid(row=1, column=1, padx=10)

        # ── URL ──
        url_frame = ttk.LabelFrame(
            main_frame,
            text="Ссылки для скачивания (каждая с новой строки)",
            padding="5"
        )
        url_frame.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=5)
        url_frame.columnconfigure(0, weight=1)

        url_btn_frame = ttk.Frame(url_frame)
        url_btn_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 5))

        ttk.Button(url_btn_frame, text="📋 Вставить из буфера",
                   command=self.paste_from_clipboard).pack(side=tk.LEFT, padx=5)
        ttk.Button(url_btn_frame, text="🗑 Очистить список",
                   command=self.clear_urls).pack(side=tk.LEFT, padx=5)
        ttk.Button(url_btn_frame, text="📂 Загрузить плейлист из .html",
                   command=self.load_from_html).pack(side=tk.LEFT, padx=5)

        self.url_text = scrolledtext.ScrolledText(url_frame, height=4, wrap=tk.WORD)
        self.url_text.grid(row=1, column=0, sticky=(tk.W, tk.E))
        self.url_text.tag_config("DUPLICATE", background="#ffcccc", foreground="red")
        self.url_text.tag_config("NEW", background="#ccffcc")

        self.context_menu = tk.Menu(self.url_text, tearoff=0)
        self.context_menu.add_command(label="Копировать", command=self.copy_text)
        self.context_menu.add_command(label="Вставить",   command=self.paste_text)
        self.context_menu.add_command(label="Удалить",    command=self.delete_text)
        self.url_text.bind("<Button-3>", self.show_context_menu)

        # ── Список видео из HTML ──
        self.video_list_frame = ttk.LabelFrame(
            main_frame, text="Видео из HTML плейлиста", padding="5"
        )
        self.video_list_frame.grid(
            row=2, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), pady=5
        )
        self.video_list_frame.columnconfigure(0, weight=1)
        self.video_list_frame.rowconfigure(1, weight=1)
        self.video_list_frame.grid_remove()

        vl_btn = ttk.Frame(self.video_list_frame)
        vl_btn.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 5))

        ttk.Button(vl_btn, text="✅ Добавить выбранные в очередь",
                   command=self.add_selected_to_queue).pack(side=tk.LEFT, padx=5)
        ttk.Button(vl_btn, text="☑ Все",
                   command=self.select_all_videos).pack(side=tk.LEFT, padx=5)
        ttk.Button(vl_btn, text="🆕 Только новые",
                   command=self.select_only_new).pack(side=tk.LEFT, padx=5)
        ttk.Button(vl_btn, text="☐ Снять выбор",
                   command=self.deselect_all_videos).pack(side=tk.LEFT, padx=5)
        ttk.Button(vl_btn, text="🗑️ Удалить выбранные",
                   command=self.delete_selected_videos).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(vl_btn, text="Показать скачанные",
                        variable=self.show_downloaded,
                        command=self.display_video_list).pack(side=tk.LEFT, padx=15)
        self.video_stats_label = ttk.Label(vl_btn, text="Видео: 0",
                                            font=("Arial", 9, "bold"))
        self.video_stats_label.pack(side=tk.RIGHT, padx=10)

        list_container = ttk.Frame(self.video_list_frame)
        list_container.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        list_container.columnconfigure(0, weight=1)
        list_container.rowconfigure(0, weight=1)

        self.video_canvas    = tk.Canvas(list_container, height=200)
        self.video_scrollbar = ttk.Scrollbar(
            list_container, orient="vertical", command=self.video_canvas.yview
        )
        self.video_scrollable_frame = ttk.Frame(self.video_canvas)
        self.video_scrollable_frame.bind(
            "<Configure>",
            lambda e: self.video_canvas.configure(
                scrollregion=self.video_canvas.bbox("all"))
        )
        self.video_canvas.create_window(
            (0, 0), window=self.video_scrollable_frame, anchor="nw"
        )
        self.video_canvas.configure(yscrollcommand=self.video_scrollbar.set)
        self.video_canvas.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        self.video_scrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))

        # Скролл мышью — Windows и Linux (FIX #21 продолжение)
        for widget in (self.video_canvas, self.video_scrollable_frame):
            widget.bind("<MouseWheel>", self._on_mousewheel)   # Windows
            widget.bind("<Button-4>",   self._on_mousewheel)   # Linux scroll up
            widget.bind("<Button-5>",   self._on_mousewheel)   # Linux scroll down

        # ── Настройки ──
        settings_frame = ttk.LabelFrame(main_frame, text="Настройки загрузки", padding="5")
        settings_frame.grid(row=3, column=0, sticky=(tk.W, tk.E), pady=5)

        path_frame = ttk.Frame(settings_frame)
        path_frame.grid(row=0, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=2)
        ttk.Label(path_frame, text="Путь сохранения:").pack(side=tk.LEFT, padx=5)
        self.save_path = tk.StringVar(
            value=self.settings.get("save_path", str(Path.home() / "Downloads"))
        )
        ttk.Entry(path_frame, textvariable=self.save_path, width=55).pack(
            side=tk.LEFT, padx=5, fill=tk.X, expand=True
        )
        ttk.Button(path_frame, text="Обзор", command=self.browse_path).pack(
            side=tk.LEFT, padx=5
        )

        quality_frame = ttk.Frame(settings_frame)
        quality_frame.grid(row=1, column=0, sticky=tk.W, pady=2)
        # Качество видео больше не выбирается в интерфейсе: программа всегда
        # скачивает авто до 1080p, а если у ролика нет 1080p — берёт ближайшее доступное ниже.
        self.quality = tk.StringVar(value="1080p")
        ttk.Label(
            quality_frame,
            text="Качество видео: авто до 1080p (если нет 1080p — ниже)",
            foreground="gray"
        ).pack(side=tk.LEFT, padx=5)

        ttk.Label(quality_frame, text="Качество аудио:").pack(side=tk.LEFT, padx=(20, 5))
        self.audio_quality = tk.StringVar(
            value=self.settings.get("audio_quality", "320k")
        )
        aq_combo = ttk.Combobox(
            quality_frame, textvariable=self.audio_quality,
            values=["128k", "192k", "256k", "320k", "VBR-0 (лучшее)"],
            width=15, state="readonly"
        )
        aq_combo.pack(side=tk.LEFT, padx=5)
        aq_combo.bind("<<ComboboxSelected>>", lambda e: self.save_current_settings())

        adv_frame = ttk.Frame(settings_frame)
        adv_frame.grid(row=1, column=1, sticky=tk.W, pady=2, padx=20)
        self.download_subtitles = tk.BooleanVar(
            value=self.settings.get("download_subtitles", False)
        )
        ttk.Checkbutton(adv_frame, text="Субтитры",
                        variable=self.download_subtitles).pack(side=tk.LEFT, padx=5)
        self.write_problem_logs = tk.BooleanVar(
            value=self.settings.get("write_problem_logs", True)
        )
        ttk.Checkbutton(
            adv_frame, text="Писать логи проблем",
            variable=self.write_problem_logs,
            command=self.on_problem_logging_change
        ).pack(side=tk.LEFT, padx=(20, 5))
        ttk.Label(adv_frame, text="Потоков:").pack(side=tk.LEFT, padx=(20, 5))
        self.concurrent_var = tk.IntVar(
            value=self.settings.get("concurrent_downloads", 2)
        )
        ttk.Spinbox(
            adv_frame, from_=1, to=MAX_CONCURRENT_DL,
            textvariable=self.concurrent_var, width=5
        ).pack(side=tk.LEFT, padx=5)

        merge_frame = ttk.LabelFrame(settings_frame, text="Объединение аудио", padding="5")
        merge_frame.grid(row=2, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=5)
        self.merge_audio = tk.BooleanVar(value=self.settings.get("merge_audio", False))
        ttk.Checkbutton(merge_frame, text="Объединить все аудио в один файл",
                        variable=self.merge_audio,
                        command=self.on_merge_change).grid(row=0, column=0, sticky=tk.W)
        self.split_frame = ttk.Frame(merge_frame)
        self.split_frame.grid(row=1, column=0, sticky=tk.W, pady=2)
        ttk.Label(self.split_frame, text="Разбить по (часов):").pack(side=tk.LEFT, padx=5)
        self.split_hours = tk.IntVar(value=self.settings.get("split_hours", MAX_SPLIT_HOURS))
        sp = ttk.Spinbox(
            self.split_frame, from_=0, to=MAX_SPLIT_HOURS,
            textvariable=self.split_hours, width=5,
            command=self.check_split_warning
        )
        sp.pack(side=tk.LEFT, padx=5)
        sp.bind('<KeyRelease>', lambda e: self.check_split_warning())
        ttk.Label(
            self.split_frame,
            text=f"(0 = без разбивки, макс {MAX_SPLIT_HOURS})"
        ).pack(side=tk.LEFT, padx=5)

        # ── Кнопки действий ──
        btn_frame = ttk.Frame(main_frame)
        btn_frame.grid(row=4, column=0, pady=10)

        self.download_btn = ttk.Button(
            btn_frame, text="▶ Скачать все",
            command=self.start_download, width=20
        )
        self.download_btn.grid(row=0, column=0, padx=5)

        self.cancel_btn = ttk.Button(
            btn_frame, text="⏹ Отменить",
            command=self.cancel_download,
            state=tk.DISABLED, width=20
        )
        self.cancel_btn.grid(row=0, column=1, padx=5)

        ttk.Button(
            btn_frame, text="🗂 Очистить лог скачанных",
            command=self.clear_download_log
        ).grid(row=0, column=2, padx=5)

        # ── Лог ──
        log_frame = ttk.LabelFrame(main_frame, text="Лог загрузки", padding="5")
        log_frame.grid(row=5, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), pady=5)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        self.log_text = scrolledtext.ScrolledText(log_frame, height=15, wrap=tk.WORD)
        self.log_text.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        self.log_text.configure(spacing3=LOG_ENTRY_SPACING_PX)
        self.log_text.bind("<MouseWheel>", self._on_log_mousewheel)
        self.log_text.bind("<Button-4>", self._on_log_mousewheel)
        self.log_text.bind("<Button-5>", self._on_log_mousewheel)
        self.log_text.tag_config("ERROR",    foreground="red",    font=("Arial", 9, "bold"))
        self.log_text.tag_config("WARNING",  foreground="orange", font=("Arial", 9, "bold"))
        self.log_text.tag_config("SUCCESS",  foreground="green")
        self.log_text.tag_config("INFO",     foreground="black")
        self.log_text.tag_config("CRITICAL", foreground="red",    font=("Arial", 10, "bold"))
        self.log_text.tag_config("HISTORY_FOLDER_LINK", foreground="#0066cc",
                                 underline=True, font=("Arial", 9, "bold"))
        self.log_text.tag_bind("HISTORY_FOLDER_LINK", "<Button-1>", self.open_history_folder)
        self.log_text.tag_bind("HISTORY_FOLDER_LINK", "<Enter>",
                               lambda e: self.log_text.config(cursor="hand2"))
        self.log_text.tag_bind("HISTORY_FOLDER_LINK", "<Leave>",
                               lambda e: self.log_text.config(cursor=""))
        self.log_text.tag_config("DOWNLOAD_LINKS_FOLDER_LINK", foreground="#0066cc",
                                 underline=True, font=("Arial", 9, "bold"))
        self.log_text.tag_bind("DOWNLOAD_LINKS_FOLDER_LINK", "<Button-1>", self.open_download_links_folder)
        self.log_text.tag_bind("DOWNLOAD_LINKS_FOLDER_LINK", "<Enter>",
                               lambda e: self.log_text.config(cursor="hand2"))
        self.log_text.tag_bind("DOWNLOAD_LINKS_FOLDER_LINK", "<Leave>",
                               lambda e: self.log_text.config(cursor=""))
        self.log_text.tag_config("DOWNLOADED_SESSIONS_FOLDER_LINK", foreground="#0066cc",
                                 underline=True, font=("Arial", 9, "bold"))
        self.log_text.tag_bind(
            "DOWNLOADED_SESSIONS_FOLDER_LINK", "<Button-1>",
            self.open_downloaded_sessions_folder
        )
        self.log_text.tag_bind("DOWNLOADED_SESSIONS_FOLDER_LINK", "<Enter>",
                               lambda e: self.log_text.config(cursor="hand2"))
        self.log_text.tag_bind("DOWNLOADED_SESSIONS_FOLDER_LINK", "<Leave>",
                               lambda e: self.log_text.config(cursor=""))
        self.log_text.tag_config("MANUAL_FOLDER_LINK", foreground="#0066cc",
                                 underline=True, font=("Arial", 9, "bold"))
        self.log_text.tag_bind(
            "MANUAL_FOLDER_LINK", "<Button-1>",
            self.open_manual_processing_folder
        )
        self.log_text.tag_bind("MANUAL_FOLDER_LINK", "<Enter>",
                               lambda e: self.log_text.config(cursor="hand2"))
        self.log_text.tag_bind("MANUAL_FOLDER_LINK", "<Leave>",
                               lambda e: self.log_text.config(cursor=""))
        self.log_text.tag_config("PROBLEM_FOLDER_LINK", foreground="#0066cc",
                                 underline=True, font=("Arial", 9, "bold"))
        self.log_text.tag_bind("PROBLEM_FOLDER_LINK", "<Button-1>", self.open_problem_logs_folder)
        self.log_text.tag_bind("PROBLEM_FOLDER_LINK", "<Enter>",
                               lambda e: self.log_text.config(cursor="hand2"))
        self.log_text.tag_bind("PROBLEM_FOLDER_LINK", "<Leave>",
                               lambda e: self.log_text.config(cursor=""))
        for tag_name in (
            "ERROR", "WARNING", "SUCCESS", "INFO", "CRITICAL",
            "HISTORY_FOLDER_LINK", "DOWNLOAD_LINKS_FOLDER_LINK",
            "DOWNLOADED_SESSIONS_FOLDER_LINK",
            "MANUAL_FOLDER_LINK", "PROBLEM_FOLDER_LINK"
        ):
            self.log_text.tag_config(tag_name, spacing3=LOG_ENTRY_SPACING_PX)

        # Инициализация состояния
        self.on_merge_change()

        optional_missing = getattr(self, "optional_missing_deps", [])
        if self.missing_deps or optional_missing:
            self.show_dependency_warning()
        else:
            if self.js_runtime_found:
                self.log(f"✅ JavaScript Runtime: {self.js_runtime_name}", "SUCCESS")
            self.log(
                f"✅ Все зависимости установлены. Готов к работе. Версия v{APP_VERSION} активна.",
                "SUCCESS",
            )
            self.log_retention_status()
            self.log("📄 История скачанных: отдельные файлы История ссылок/downloaded_*.txt", "HISTORY_FOLDER_LINK")
            self.log(
                f"📄 Ссылки из очереди при закрытии: Ссылки на скачивания/links_*.txt "
                f"(последние {MAX_DOWNLOAD_LINK_SESSIONS} сессий)",
                "DOWNLOAD_LINKS_FOLDER_LINK"
            )
            self.log("📄 Лог уже скачанных для программы: Логи скачивания/Сессии скачанных/downloaded_log_*.txt", "DOWNLOADED_SESSIONS_FOLDER_LINK")
            self.log(
                "📄 Ручная обработка: Обработать вручную — ссылки на несостоявшиеся загрузки "
                "и видео с ошибками конвертации",
                "MANUAL_FOLDER_LINK"
            )
            if self.should_write_problem_logs():
                self.log(
                    "📄 Диагностика проблем для ИИ: Логи проблем/Сессии/<запуск>/events.jsonl "
                    "+ incidents.jsonl + attachments; начинать с корневого latest_run.json "
                    "и README_FOR_CODEX.md",
                    "PROBLEM_FOLDER_LINK"
                )
            else:
                self.log(
                    "📄 Диагностика проблем для ИИ отключена галочкой «Писать логи проблем»",
                    "INFO"
                )


# ─────────────────────────────────────────────────────────────────────────────


def _write_startup_emergency_log(exception: BaseException) -> None:
    """Пишет минимальный лог, даже если VideoDownloader не успел создаться."""
    try:
        log_dir = Path(__file__).resolve().parent / "Логи проблем"
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / "emergency_problem_log.jsonl"
        message = re.sub(
            r"(?i)\b(password|passwd|secret|token|api[_-]?key|cookie)"
            r"(\s*[:=]\s*)[^\s,;]+",
            r"\1\2<redacted>",
            str(exception),
        )
        traceback_text = "".join(traceback.format_exception(
            type(exception), exception, exception.__traceback__
        ))[-PROBLEM_LOG_TAIL_CHARS:]
        traceback_text = re.sub(
            r"(?i)\b(password|passwd|secret|token|api[_-]?key|cookie)"
            r"(\s*[:=]\s*)[^\s,;]+",
            r"\1\2<redacted>",
            traceback_text,
        )
        item = {
            "schema_version": PROBLEM_LOG_SCHEMA_VERSION,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "level": "CRITICAL",
            "operation": "application_startup_failed",
            "exception_type": type(exception).__name__,
            "message": message,
            "traceback": traceback_text,
        }
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    except Exception:
        pass


def main() -> None:
    app = None
    try:
        root = tk.Tk()
        app  = VideoDownloader(root)
        root.mainloop()
    except Exception as e:
        if app is not None:
            app._record_uncaught_exception(
                "main", type(e), e, e.__traceback__
            )
        else:
            _write_startup_emergency_log(e)
        print(f"Критическая ошибка: {e}")
        traceback.print_exc()
    finally:
        if app is not None:
            app.finalize_problem_diagnostics(
                "finished", "mainloop_finished"
            )


if __name__ == "__main__":
    main()
