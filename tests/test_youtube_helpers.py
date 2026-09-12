import unittest
from unittest.mock import patch


from src.downloads.youtube_strategy_catalog import build_ytdlp_strategy_plan
from src.downloads.youtube_command import video_format_args
from src.downloads.youtube_failure_diagnostics import (
    analyze_yt_dlp_failure,
    diagnostic_landmarks_from_output,
    is_postprocessing_conversion_failure,
)
from src.downloads.youtube_retry import (
    cookie_read_failed,
    extract_googlevideo_hosts,
    has_auth_error,
    has_critical_error,
    has_youtube_retry_error,
)


class _FakeLogger:
    def __init__(self):
        self.messages = []

    def info(self, message):
        self.messages.append(message)


class _FakeYouTubeStrategyApp:
    def __init__(self):
        self.aria2c_youtube_skip_logged = False
        self.file_logger = _FakeLogger()

    def get_ytdlp_fast_fragments(self):
        return "2"

    def is_youtube_url(self, url):
        return "youtube.com" in url or "youtu.be" in url

    def _apply_adaptive_youtube_strategy_order(self, strategies):
        return strategies


class YouTubeStrategyCatalogTests(unittest.TestCase):
    @patch("src.downloads.youtube_strategy_catalog.shutil.which", return_value=r"C:\\tools\\aria2c.exe")
    def test_youtube_strategy_plan_with_aria2c_does_not_reference_missing_self(self, _which):
        app = _FakeYouTubeStrategyApp()

        strategies, default_network_args, fast_fragments = build_ytdlp_strategy_plan(
            app, "https://www.youtube.com/watch?v=test", "1080p"
        )

        self.assertTrue(strategies)
        self.assertEqual(fast_fragments, "2")
        self.assertTrue(default_network_args)
        self.assertTrue(app.aria2c_youtube_skip_logged)
        self.assertTrue(app.file_logger.messages)
        self.assertFalse(any("aria2c" in s.get("name", "").lower() for s in strategies))



class YouTubeFormatSelectionTests(unittest.TestCase):
    def test_default_1080p_selector_prefers_direct_http_dash(self):
        args = video_format_args()
        selector = args[args.index("-f") + 1]
        self.assertIn("bestvideo[height<=1080][protocol^=http][protocol!*=dash]", selector)
        self.assertIn("bestaudio[protocol^=http][protocol!*=dash]", selector)
        self.assertNotIn("bestvideo*[height<=1080]+bestaudio", selector)

    def test_any_protocol_selector_is_reserved_for_explicit_fallback(self):
        selector = video_format_args(fallback_any=True)[1]
        self.assertIn("bestvideo*[height<=1080]+bestaudio", selector)

    def test_progressive_prefers_single_file_http(self):
        selector = video_format_args(progressive=True)[1]
        self.assertTrue(selector.startswith("best[height<=1080][protocol^=http][protocol!*=dash]/"))

    def test_same_height_prefers_mp4_video_and_m4a_audio(self):
        args = video_format_args()
        sort_order = args[args.index("-S") + 1]
        self.assertTrue(sort_order.startswith("height,vext,aext,"))


class YouTubeFailureDiagnosticsTests(unittest.TestCase):
    def test_generic_conversion_failure_after_fragments_is_structured(self):
        output = "\n".join([
            "[DOWNLOAD_PLAN] id=x format_id=270+251 protocol=m3u8_native+https ext=mkv height=1080",
            "[download] 99.0% of 10MiB (frag 204/205)",
            "[download] 100% of 10MiB",
            "ERROR: Postprocessing: Conversion failed!",
        ])
        analysis = analyze_yt_dlp_failure(
            output, strategy={"fallback_format": True}, returncode=1
        )
        self.assertTrue(is_postprocessing_conversion_failure(output))
        self.assertEqual(analysis["kind"], "yt_dlp_postprocessing_conversion_failed")
        self.assertEqual(analysis["stage"], "postprocessing_merge")
        self.assertTrue(analysis["fragmented_progress_seen"])
        self.assertEqual(analysis["format_profile"], "any")
        self.assertTrue(analysis["download_plan_lines"])

    def test_verbose_timestamp_failure_is_high_quality_and_extracts_landmarks(self):
        output = "\n".join([
            "[debug] exe versions: ffmpeg 8.0.1, ffprobe 8.0.1",
            "[debug] Invoking hlsnative downloader on https://example.invalid/signed",
            "[debug] ffmpeg command line: ffmpeg -i video -i audio -c copy out.mkv",
            "[matroska] Timestamps are unset in a packet for stream 0",
            "[matroska] Can't write packet with unknown timestamp",
            "[out] Error muxing a packet",
            "Conversion failed!",
            "ERROR: Postprocessing: Conversion failed!",
        ])
        landmarks = diagnostic_landmarks_from_output(output)
        analysis = analyze_yt_dlp_failure(
            output, strategy={}, returncode=1, diagnostic_landmarks=landmarks
        )
        self.assertEqual(analysis["kind"], "ffmpeg_timestamp_mux_failure")
        self.assertEqual(analysis["diagnostic_quality"], "high")
        self.assertIn("hlsnative", analysis["downloader_names"])
        self.assertIsNotNone(analysis["ffmpeg_command_line"])



class YouTubeRetryHelperTests(unittest.TestCase):
    def test_googlevideo_hosts_are_deduplicated(self):
        text = (
            "host='rr1---sn-a.googlevideo.com' timeout "
            "https://rr1---sn-a.googlevideo.com/videoplayback "
            "https://rr2---sn-b.googlevideo.com/videoplayback"
        )
        self.assertEqual(
            extract_googlevideo_hosts(text),
            ["rr1---sn-a.googlevideo.com", "rr2---sn-b.googlevideo.com"],
        )

    def test_retry_classifiers_keep_existing_semantics(self):
        self.assertTrue(has_auth_error("private video - sign in to confirm"))
        self.assertTrue(cookie_read_failed("cookie database is locked"))
        self.assertTrue(has_critical_error("this video is unavailable"))
        self.assertTrue(has_youtube_retry_error("http error 403: forbidden"))
        self.assertFalse(has_critical_error("read timed out"))


if __name__ == "__main__":
    unittest.main()
