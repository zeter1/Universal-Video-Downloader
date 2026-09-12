import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from src.application import VideoDownloader
from src.core.session_settings import session_setting


class ManagerFailures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = VideoDownloader.__new__(VideoDownloader)
        app = self.app
        app.save_path = SimpleNamespace(get=lambda: self.temp.name)
        app.merge_audio = SimpleNamespace(get=lambda: False)
        app.cancel_flag = threading.Event()
        app.max_concurrent = 1
        app.download_threads = []
        app.download_start_time = 0
        app.manual_processing_dir = Path(self.temp.name)/'manual'
        for name in ('log','record_problem','finish_download','write_problem_session_summary',
                     'update_progress','save_failed_downloads_session'):
            setattr(app,name,Mock())
        app.cleanup_stale_download_temp_files = Mock(return_value=0)
        app.cleanup_stale_download_temp_dirs = Mock(return_value=0)
        app.is_url_downloaded = Mock(return_value=False)
        app.convert_videos_to_audio = Mock(return_value=[])
        app.download_single_video_task = Mock()

    def test_directory_error_still_finishes_and_reports_failure(self):
        with patch('src.downloads.manager.Path.mkdir', side_effect=PermissionError('denied')):
            self.app.download_manager(['url'])
        self.app.finish_download.assert_called_once()
        self.assertEqual(self.app.write_problem_session_summary.call_args.args[0], 'failed')

    def test_worker_exception_is_observed_and_link_retained(self):
        self.app.download_single_video_task.side_effect = RuntimeError('worker failed')
        self.app.download_manager(['url'])
        self.app.save_failed_downloads_session.assert_called_once_with(['url'])
        self.app.finish_download.assert_called_once()
        self.assertEqual(self.app.write_problem_session_summary.call_args.args[0], 'failed')

    def test_converter_error_still_finishes(self):
        self.app.convert_videos_to_audio.side_effect = OSError('conversion failed')
        self.app.download_manager(['url'])
        self.app.finish_download.assert_called_once()
        self.assertEqual(self.app.write_problem_session_summary.call_args.args[0], 'failed')

    def test_cancelled_run_is_not_success(self):
        self.app.cancel_flag.set()
        self.app.download_manager(['url'])
        self.app.convert_videos_to_audio.assert_not_called()
        self.assertEqual(self.app.download_outcome, 'cancelled')
        self.app.finish_download.assert_called_once()

    def test_reentrant_start_is_ignored(self):
        self.app.is_downloading = True
        self.app.start_download()
        self.app.download_single_video_task.assert_not_called()

    def test_session_setting_falls_back_when_proxy_ui_control_is_absent(self):
        app = SimpleNamespace(settings={
            'proxy_enabled': True,
            'proxy_url': 'socks5://127.0.0.1:1080',
        })
        self.assertTrue(session_setting(app, 'proxy_enabled'))
        self.assertEqual(
            session_setting(app, 'proxy_url'),
            'socks5://127.0.0.1:1080',
        )

    def test_worker_session_setting_prefers_start_snapshot(self):
        app = SimpleNamespace(
            settings={'proxy_enabled': True},
            download_settings_snapshot={'proxy_enabled': False},
        )
        result = []
        worker = threading.Thread(
            target=lambda: result.append(session_setting(app, 'proxy_enabled'))
        )
        worker.start()
        worker.join(timeout=2)
        self.assertEqual(result, [False])

    def test_finish_ui_reports_cancellation(self):
        app = self.app
        app.download_outcome = 'cancelled'
        app.download_btn = Mock()
        app.cancel_btn = Mock()
        app.main_progress_frame = Mock()
        app.format_duration = Mock(return_value='0s')
        app._finish_download_ui()
        self.assertFalse(app.is_downloading)
        self.assertEqual(app.log.call_args.args[1], 'WARNING')
