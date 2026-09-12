import logging
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src.application import VideoDownloader


class DownloadResultTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = VideoDownloader.__new__(VideoDownloader)
        self.app.file_lock = threading.Lock()
        self.app.cancel_flag = threading.Event()
        self.app.log = Mock()
        self.app.record_problem = Mock()
        self.app.file_logger = logging.getLogger('result-tests')

    def media(self, name):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'x' * 2000)
        return path

    def test_dash_fragment_is_not_a_finished_download(self):
        fragment = self.media('job/title.f137.mp4')
        result = self.app.parse_yt_dlp_output(
            '[download] Destination: ' + str(fragment), 'https://example.invalid',
            fragment.parent, set())
        self.assertIsNone(result)
        self.assertTrue(fragment.exists())

    def test_output_cannot_select_file_outside_current_job(self):
        outside = self.media('other-job/video.mp4')
        job = self.root / 'job'
        job.mkdir()
        result = self.app.parse_yt_dlp_output(str(outside), 'https://example.invalid', job, set())
        self.assertIsNone(result)
        self.assertEqual(outside.stat().st_size, 2000)

    def test_existing_id_match_is_not_new_output(self):
        old = self.media('old [videoid].mp4')
        self.assertIsNone(self.app.find_newest_file(self.root, {old}, 'videoid'))

    def test_ambiguous_outputs_are_not_guessed(self):
        self.media('one.mp4')
        self.media('two.mkv')
        self.assertIsNone(self.app.find_newest_file(self.root, set(), 'id'))

    def test_exact_new_output_is_returned(self):
        output = self.media('job/видео into тест.mkv')
        result = self.app.parse_yt_dlp_output(
            '[Merger] Merging formats into "' + str(output) + '"',
            'https://example.invalid', output.parent, set())
        self.assertEqual(result, str(output))

    def test_temporary_alternative_does_not_satisfy_wait(self):
        self.media('movie.temp.mp4')
        with patch('src.downloads.ytdlp_results.time.sleep'):
            self.assertFalse(self.app.wait_for_file(str(self.root/'movie.mp4'), timeout=.001))

    def test_exhausted_names_never_return_existing_target(self):
        with patch.object(Path, 'exists', return_value=True):
            with self.assertRaises(FileExistsError):
                self.app.make_unique_media_path(self.root/'video.mp4')



    def test_download_plan_dimensions_are_available_when_ffprobe_metadata_fails(self):
        output = (
            "[DOWNLOAD_PLAN] id=x format_id=333+140 protocol=https+https "
            "ext=mp4 height=854 width=480 vcodec=vp9.2 acodec=mp4a.40.2"
        )
        self.assertEqual(self.app.parse_download_plan_dimensions(output), (480, 854))

    def test_finalize_normalizes_non_mp4_with_lossless_remux(self):
        source = self.media('job/movie.mkv')
        calls = []

        def fake_run(command, timeout, operation, context=None, allow_cancel=True):
            calls.append((operation, list(command)))
            Path(command[-1]).write_bytes(b'mp4' * 1000)
            return subprocess.CompletedProcess(command, 0, '', '')

        self.app.run_command = fake_run
        result = self.app.finalize_downloaded_video_file(str(source), self.root/'final', 'id')
        self.assertIsNotNone(result)
        self.assertEqual(Path(result).suffix.lower(), '.mp4')
        self.assertTrue(Path(result).exists())
        self.assertFalse(source.exists())
        self.assertEqual(calls[0][0], 'mp4_output_remux')
        self.assertIn('copy', calls[0][1])
        self.assertEqual(len(calls), 1)

    def test_finalize_uses_h264_aac_only_if_stream_copy_remux_fails(self):
        source = self.media('job/movie.webm')
        calls = []

        def fake_run(command, timeout, operation, context=None, allow_cancel=True):
            calls.append((operation, list(command)))
            if operation == 'mp4_output_remux':
                return subprocess.CompletedProcess(command, 1, '', 'mux unsupported')
            Path(command[-1]).write_bytes(b'mp4' * 1000)
            return subprocess.CompletedProcess(command, 0, '', '')

        self.app.run_command = fake_run
        result = self.app.finalize_downloaded_video_file(str(source), self.root/'final', 'id')
        self.assertIsNotNone(result)
        self.assertEqual(Path(result).suffix.lower(), '.mp4')
        self.assertEqual([name for name, _ in calls], ['mp4_output_remux', 'mp4_output_transcode'])
        self.assertIn('libx264', calls[1][1])
        self.assertIn('aac', calls[1][1])

    def test_download_duration_must_be_finite(self):
        output = self.media('movie.mp4')
        self.app.has_video_stream = Mock(return_value=True)
        self.app.has_audio_stream = Mock(return_value=True)
        self.app.format_duration = Mock(return_value='unknown')
        for duration in (float('nan'), float('inf')):
            self.app.get_media_duration = Mock(return_value=duration)
            self.assertFalse(self.app.validate_downloaded_video(str(output), 'url')[0])
