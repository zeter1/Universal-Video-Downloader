"""Offline regression coverage for completion notification integration."""

import queue
import unittest
from unittest.mock import Mock

from src.ui.completion_notification import CompletionNotificationMixin
from src.downloads.progress import DownloadProgressMixin


class FakeCompletion(CompletionNotificationMixin, DownloadProgressMixin):
    pass


class CompletionIntegrationTests(unittest.TestCase):
    def test_worker_does_not_touch_tk(self):
        owner = FakeCompletion()
        owner._completion_closed = False
        owner._completion_queue = queue.Queue()
        owner.download_outcome = "completed"
        owner.download_start_time = None
        owner.root = Mock()
        owner.finish_download()
        self.assertEqual(owner._completion_queue.get_nowait(), ("completed", 0))
        owner.root.after.assert_not_called()

    def test_closed_app_does_not_enqueue(self):
        owner = FakeCompletion()
        owner._completion_closed = True
        owner._completion_queue = queue.Queue()
        owner.finish_download()
        self.assertTrue(owner._completion_queue.empty())

    def test_ui_reenables_save_path_and_notifies(self):
        owner = FakeCompletion()
        owner.is_downloading = True
        owner.set_save_path_controls_enabled = Mock()
        owner.download_btn = Mock()
        owner.cancel_btn = Mock()
        owner.main_progress_frame = Mock()
        owner.log = Mock()
        owner.show_completion_notification = Mock()
        owner.format_duration = lambda x: "0:01"
        owner._finish_download_ui("completed", 1)
        owner.set_save_path_controls_enabled.assert_called_once_with(True)
        owner.show_completion_notification.assert_called_once_with("completed", 1)
        self.assertFalse(owner.is_downloading)


if __name__ == "__main__":
    unittest.main()
