import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


from src.downloads.youtube_strategy_catalog import build_ytdlp_strategy_plan
from src.downloads.youtube_command import (
    _with_youtube_language_preference,
    build_ytdlp_download_command,
    video_format_args,
)
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


class _FakeCommandApp:
    settings = {"download_subtitles": False}

    @staticmethod
    def is_youtube_url(url):
        return "youtube.com" in url or "youtu.be" in url

    @staticmethod
    def build_youtube_ejs_args(_url, _mode):
        return []


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
    def test_language_neutral_selector_keeps_ready_1080p_before_split_formats(self):
        selector = video_format_args()[1]
        ready = "best[height=1080][protocol^=http][protocol!*=dash]"
        split = (
            "bestvideo[height<=1080][protocol^=http][protocol!*=dash]+"
            "bestaudio[protocol^=http][protocol!*=dash]"
        )
        self.assertTrue(selector.startswith(ready + "/"))
        self.assertLess(selector.index(ready), selector.index(split))

    def test_youtube_russian_ready_1080p_is_first_when_available(self):
        selector = video_format_args(preferred_audio_language="ru")[1]
        self.assertTrue(selector.startswith(
            "best[height=1080][language^=ru][protocol^=http][protocol!*=dash]/"
        ))

    def test_default_1080p_selector_prefers_direct_http_dash(self):
        args = video_format_args(preferred_audio_language="ru")
        selector = args[args.index("-f") + 1]
        self.assertIn("bestvideo[height<=1080][protocol^=http][protocol!*=dash]", selector)
        self.assertIn("bestaudio[language^=ru][protocol^=http][protocol!*=dash]", selector)
        self.assertIn("bestaudio[protocol^=http][protocol!*=dash]", selector)
        self.assertNotIn("bestvideo*[height<=1080]+bestaudio", selector)

    def test_russian_audio_falls_back_to_any_audio_instead_of_failing(self):
        selector = video_format_args(preferred_audio_language="ru")[1]
        russian = "bestaudio[language^=ru][protocol^=http][protocol!*=dash]"
        fallback = "bestaudio[protocol^=http][protocol!*=dash]"
        self.assertLess(selector.index(russian), selector.index(fallback))

    def test_youtube_language_preference_preserves_player_client(self):
        args = _with_youtube_language_preference([
            "--extractor-args", "youtube:player_client=android"
        ])
        self.assertEqual(
            args,
            ["--extractor-args", "youtube:player_client=android;lang=ru"],
        )

    def test_youtube_language_preference_is_added_when_strategy_has_none(self):
        self.assertEqual(
            _with_youtube_language_preference(["--cookies-from-browser", "chrome"]),
            ["--cookies-from-browser", "chrome", "--extractor-args", "youtube:lang=ru"],
        )

    def test_any_protocol_selector_is_reserved_for_explicit_fallback(self):
        selector = video_format_args(fallback_any=True, preferred_audio_language="ru")[1]
        self.assertIn("bestvideo*[height<=1080]+bestaudio[language^=ru]", selector)
        self.assertIn("bestvideo*[height<=1080]+bestaudio", selector)

    def test_progressive_prefers_single_file_http(self):
        selector = video_format_args(progressive=True, preferred_audio_language="ru")[1]
        self.assertTrue(selector.startswith(
            "best[height<=1080][language^=ru][protocol^=http][protocol!*=dash]/"
        ))

    def test_youtube_sort_prefers_language_before_container_and_bitrate(self):
        args = video_format_args(preferred_audio_language="ru")
        sort_order = args[args.index("-S") + 1]
        self.assertTrue(sort_order.startswith("height,lang,vext,aext,"))

    def test_non_youtube_selector_keeps_historical_language_neutral_behavior(self):
        args = video_format_args()
        selector = args[args.index("-f") + 1]
        sort_order = args[args.index("-S") + 1]
        self.assertNotIn("language^=", selector)
        self.assertTrue(sort_order.startswith("height,vext,aext,"))

    def test_download_command_applies_russian_preference_only_to_youtube(self):
        app = _FakeCommandApp()
        strategy = {
            "args": ["--extractor-args", "youtube:player_client=android"],
            "network_args": [],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            youtube_cmd = build_ytdlp_download_command(
                app,
                "https://www.youtube.com/watch?v=test",
                strategy,
                [],
                Path(temp_dir),
                [],
            )[0]
            other_cmd = build_ytdlp_download_command(
                app,
                "https://example.com/video",
                {"args": [], "network_args": []},
                [],
                Path(temp_dir),
                [],
            )[0]

        yt_extractor_value = youtube_cmd[youtube_cmd.index("--extractor-args") + 1]
        self.assertEqual(yt_extractor_value, "youtube:player_client=android;lang=ru")
        self.assertIn("language^=ru", youtube_cmd[youtube_cmd.index("-f") + 1])
        self.assertNotIn("language^=", other_cmd[other_cmd.index("-f") + 1])
        self.assertNotIn("--extractor-args", other_cmd)


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
