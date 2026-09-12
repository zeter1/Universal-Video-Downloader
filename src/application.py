"""Сборка главного класса приложения из маленьких тематических mixin-модулей.

Корень проекта определяется как папка над src, поэтому Настройки/Логи/Видео
остаются рядом с video_downloader.py, как и в предыдущей версии.
"""

from src.core.constants import APP_VERSION
from src.app.paths import initialize_app_paths
from src.app.state import initialize_diagnostics_state, initialize_platform_state, initialize_runtime_state
from src.core.settings import SettingsMixin
from src.core.process_control import ProcessControlMixin
from src.core.download_temp import DownloadTempMixin
from src.core.ytdlp_runtime import YtDlpRuntimeMixin
from src.core.subprocess_runner import SubprocessRunnerMixin
from src.downloads.history import DownloadHistoryMixin
from src.downloads.urls import UrlQueueMixin
from src.downloads.dependencies import DependenciesMixin
from src.downloads.files import DownloadFilesMixin
from src.downloads.youtube_access import YouTubeAccessMixin
from src.downloads.youtube_strategy import YouTubeStrategyMixin
from src.downloads.youtube_downloader import YouTubeDownloaderMixin
from src.downloads.ytdlp_results import YtDlpResultsMixin
from src.downloads.media_probe import MediaProbeMixin
from src.downloads.media_validation import MediaValidationMixin
from src.downloads.task import DownloadTaskMixin
from src.downloads.manager import DownloadManagerMixin
from src.downloads.session_reports import DownloadSessionReportsMixin
from src.downloads.progress import DownloadProgressMixin
from src.downloads.link_sessions import DownloadLinkSessionsMixin
from src.audio.convert import AudioConvertMixin
from src.audio.merge import AudioMergeMixin
from src.audio.split import AudioSplitMixin
from src.ui.window import WindowLifecycleMixin
from src.ui.clipboard import ClipboardMixin
from src.ui.settings_controls import SettingsControlsMixin
from src.ui.dependency_dialog import DependencyDialogMixin
from src.ui.playlist_import import PlaylistImportMixin
from src.ui.video_list import VideoListMixin
from src.ui.log_view import LogViewMixin
from src.ui.folders import FoldersMixin
from src.ui.layout import UILayoutMixin
from src.diagnostics.migration import DiagnosticsMigrationMixin
from src.diagnostics.retention import DiagnosticsRetentionMixin
from src.diagnostics.logger_setup import DiagnosticsLoggerMixin
from src.diagnostics.schema_manifest import DiagnosticsSchemaMixin
from src.diagnostics.lifecycle import DiagnosticsLifecycleMixin
from src.diagnostics.redaction import DiagnosticsRedactionMixin
from src.diagnostics.snapshots import DiagnosticsSnapshotsMixin
from src.diagnostics.classification import DiagnosticsClassificationMixin
from src.diagnostics.events import DiagnosticsEventsMixin
from src.diagnostics.health import DiagnosticsHealthMixin
from src.diagnostics.summary_writer import DiagnosticsSummaryMixin
from src.diagnostics.reporting import DiagnosticsReportingMixin


class VideoDownloader(
    SettingsMixin,
    ProcessControlMixin,
    DownloadTempMixin,
    YtDlpRuntimeMixin,
    SubprocessRunnerMixin,
    DownloadHistoryMixin,
    UrlQueueMixin,
    DependenciesMixin,
    DownloadFilesMixin,
    YouTubeAccessMixin,
    YouTubeStrategyMixin,
    YouTubeDownloaderMixin,
    YtDlpResultsMixin,
    MediaProbeMixin,
    MediaValidationMixin,
    DownloadTaskMixin,
    DownloadManagerMixin,
    DownloadSessionReportsMixin,
    DownloadProgressMixin,
    DownloadLinkSessionsMixin,
    AudioConvertMixin,
    AudioMergeMixin,
    AudioSplitMixin,
    WindowLifecycleMixin,
    ClipboardMixin,
    SettingsControlsMixin,
    DependencyDialogMixin,
    PlaylistImportMixin,
    VideoListMixin,
    LogViewMixin,
    FoldersMixin,
    UILayoutMixin,
    DiagnosticsMigrationMixin,
    DiagnosticsRetentionMixin,
    DiagnosticsLoggerMixin,
    DiagnosticsSchemaMixin,
    DiagnosticsLifecycleMixin,
    DiagnosticsRedactionMixin,
    DiagnosticsSnapshotsMixin,
    DiagnosticsClassificationMixin,
    DiagnosticsEventsMixin,
    DiagnosticsHealthMixin,
    DiagnosticsSummaryMixin,
    DiagnosticsReportingMixin
):
    def __init__(self, root):
        self.root = root
        self.root.title(f"Универсальный видео-загрузчик v{APP_VERSION}")
        self.root.state('zoomed')

        # Инициализация разбита по owner-модулям: Codex может читать только
        # нужную область вместо 200+ строк состояния в этом файле.
        initialize_app_paths(self)
        initialize_diagnostics_state(self)
        initialize_platform_state(self)

        self.setup_logger()
        initialize_runtime_state(self)

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
