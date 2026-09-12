"""Application constants only.

Keep this module dependency-free so Codex can inspect configuration without
loading Tkinter, diagnostics, subprocess helpers, or feature logic.
"""

MAX_LOG_LINES        = 1000
APP_VERSION          = "6.2 MP4 OUTPUT + 1080P PRIORITY"
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
LOG_TAB_ALL = "all"
LOG_TAB_DOWNLOAD_FAILED = "download_failed"
LOG_TAB_ERRORS = "errors"
LOG_CATEGORY_DOWNLOAD_FAILED = "download_failed"
AUDIO_DURATION_TOLERANCE_SEC = 1.0  # если mp3 короче исходника на 1+ сек — считаем проблемой
MIN_AUDIO_FILE_SIZE_BYTES = 1000
MIN_VIDEO_FILE_SIZE_BYTES = 1000
# Качество видео фиксировано: программа никогда не должна скачивать выше 1080p,
# а 1080p является приоритетной целью перед любыми 720p/360p fallback.
VIDEO_MAX_HEIGHT = 1080
VIDEO_TARGET_HEIGHT = 1080
SUPPORTED_VIDEO_EXTENSIONS = ("mp4", "webm", "mkv", "m4v", "mov")
SOURCE_CODE_FILES = (
    'video_downloader.py',
    'src/__init__.py',
    'src/app/__init__.py',
    'src/app/paths.py',
    'src/app/state.py',
    'src/application.py',
    'src/audio/__init__.py',
    'src/audio/convert.py',
    'src/audio/merge.py',
    'src/audio/split.py',
    'src/bootstrap.py',
    'src/core/__init__.py',
    'src/core/concurrency.py',
    'src/core/constants.py',
    'src/core/download_temp.py',
    'src/core/errors.py',
    'src/core/process_control.py',
    'src/core/runtime.py',
    'src/core/session_settings.py',
    'src/core/settings.py',
    'src/core/subprocess_runner.py',
    'src/core/ytdlp_runtime.py',
    'src/diagnostics/__init__.py',
    'src/diagnostics/classification.py',
    'src/diagnostics/events.py',
    'src/diagnostics/health.py',
    'src/diagnostics/lifecycle.py',
    'src/diagnostics/logger_setup.py',
    'src/diagnostics/migration.py',
    'src/diagnostics/redaction.py',
    'src/diagnostics/reporting.py',
    'src/diagnostics/retention.py',
    'src/diagnostics/retention_archive.py',
    'src/diagnostics/retention_base.py',
    'src/diagnostics/retention_storage.py',
    'src/diagnostics/schema_manifest.py',
    'src/diagnostics/snapshots.py',
    'src/diagnostics/summary_writer.py',
    'src/diagnostics/validator.py',
    'src/diagnostics/validator_core.py',
    'src/diagnostics/validator_integrity.py',
    'src/diagnostics/validator_session.py',
    'src/downloads/__init__.py',
    'src/downloads/dependencies.py',
    'src/downloads/files.py',
    'src/downloads/history.py',
    'src/downloads/link_sessions.py',
    'src/downloads/manager.py',
    'src/downloads/media_probe.py',
    'src/downloads/media_validation.py',
    'src/downloads/mp4_output.py',
    'src/downloads/progress.py',
    'src/downloads/session_reports.py',
    'src/downloads/task.py',
    'src/downloads/urls.py',
    'src/downloads/youtube_access.py',
    'src/downloads/youtube_attempt.py',
    'src/downloads/youtube_command.py',
    'src/downloads/youtube_downloader.py',
    'src/downloads/youtube_finalize.py',
    'src/downloads/youtube_retry.py',
    'src/downloads/youtube_strategy.py',
    'src/downloads/youtube_strategy_catalog.py',
    'src/downloads/youtube_success.py',
    'src/downloads/ytdlp_results.py',
    'src/ui/__init__.py',
    'src/ui/clipboard.py',
    'src/ui/dependency_dialog.py',
    'src/ui/folders.py',
    'src/ui/helpers.py',
    'src/ui/layout.py',
    'src/ui/log_view.py',
    'src/ui/playlist_import.py',
    'src/ui/settings_controls.py',
    'src/ui/video_list.py',
    'src/ui/window.py',
)
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
# Один долгий сетевой сбой ещё может восстановиться через progressive fallback.
# После двух медленных либо трёх любых сетевых сбоев по одному ролику дальнейший
# перебор десятков похожих стратегий уже только задерживает остальную очередь.
YT_DLP_MAX_NETWORK_FAILURES_PER_VIDEO = 3
YT_DLP_MAX_SLOW_NETWORK_FAILURES_PER_VIDEO = 2
YT_DLP_FAST_FAIL_ENABLED = True
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
