import json
import logging
import os
import tempfile
import threading
import time
import unittest
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from unittest import mock

from src.application import VideoDownloader
from src.core.concurrency import SafeCounter, ThreadSafeList
from src.core.constants import (
    YT_DLP_ADAPTIVE_PRIMARY_FAILURE_THRESHOLD,
    YT_DLP_SLOW_NETWORK_FAILURE_SEC,
)


from tests.support.problem_diagnostics_fixture import make_diagnostic_app


class ProblemDiagnosticsTests(unittest.TestCase):
    def test_atomic_json_write_retries_transient_permission_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app = make_diagnostic_app(Path(temp_dir))
            target = Path(temp_dir) / "atomic.json"
            real_replace = os.replace
            calls = []

            def flaky_replace(source, destination):
                calls.append((source, destination))
                if len(calls) < 3:
                    raise PermissionError(5, "Access denied")
                return real_replace(source, destination)

            with mock.patch.object(os, "replace", side_effect=flaky_replace):
                app._atomic_write_json_file(target, {"ok": True})

            self.assertEqual(len(calls), 3)
            self.assertEqual(
                json.loads(target.read_text(encoding="utf-8")), {"ok": True}
            )

    def test_problem_event_refreshes_live_summary_immediately(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app = make_diagnostic_app(Path(temp_dir))
            app.record_problem(
                "yt-dlp postprocessing failed",
                "ERROR",
                "yt_dlp_download_attempt",
                {
                    "url": "https://www.youtube.com/watch?v=abcdefghijk",
                    "strategy": "direct-1080p",
                    "failure_analysis": {
                        "kind": "ffmpeg_timestamp_mux_failure",
                        "stage": "postprocessing_merge",
                    },
                },
                RuntimeError("Conversion failed"),
                stderr="ERROR: Postprocessing: Conversion failed!",
            )

            summary = json.loads(
                app.problem_session_summary_file.read_text(encoding="utf-8")
            )
            latest = json.loads(
                app.problem_latest_run_index_file.read_text(encoding="utf-8")
            )
            events = [
                json.loads(line)
                for line in app.problem_events_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

            self.assertEqual(len(events), 1)
            self.assertEqual(summary["status"], "running")
            self.assertTrue(summary["live_refresh"])
            self.assertEqual(summary["event_count"], 1)
            self.assertEqual(summary["event_counts"]["ERROR"], 1)
            self.assertEqual(summary["structured_error_count"], 1)
            self.assertEqual(summary["error_count"], 1)
            self.assertEqual(summary["disk_validation_status"], "deferred_live")
            self.assertEqual(summary["last_event_id"], events[0]["event_id"])
            self.assertEqual(latest["structured_error_count"], 1)
            self.assertEqual(latest["error_count"], 1)

    def test_finished_summary_preserves_completion_counts_and_one_health_row(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app = make_diagnostic_app(Path(temp_dir))
            completion = {
                "elapsed_sec": 12.5,
                "queued_url_count": 3,
                "downloaded_video_count": 3,
                "failed_download_count": 0,
                "problematic_download_count": 1,
            }
            app.write_problem_session_summary("completed", completion)
            app.write_problem_session_summary("finished", {"shutdown": True})

            summary = json.loads(
                app.problem_session_summary_file.read_text(encoding="utf-8")
            )
            health_rows = [
                json.loads(line)
                for line in app.problem_health_history_file.read_text(
                    encoding="utf-8"
                ).splitlines()
                if line.strip()
            ]
            self.assertEqual(summary["context"]["downloaded_video_count"], 3)
            self.assertTrue(summary["context"]["shutdown"])
            self.assertEqual(len(health_rows), 1)
            self.assertEqual(health_rows[0]["downloaded_video_count"], 3)

    def test_emergency_failure_is_visible_in_summary_and_validator(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app = make_diagnostic_app(Path(temp_dir))
            app._write_emergency_problem_log(
                "active_run_state_write_failed",
                PermissionError(5, "Access denied"),
            )
            app.write_problem_session_summary("completed", {})

            summary = json.loads(
                app.problem_session_summary_file.read_text(encoding="utf-8")
            )
            self.assertEqual(summary["emergency"]["critical_count"], 1)
            self.assertEqual(summary["diagnostic_integrity_status"], "degraded")
            self.assertEqual(summary["disk_validation_status"], "degraded")
            self.assertEqual(summary["error_count"], 1)

    def test_strategy_rate_uses_attempts_and_403_signature_is_stable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app = make_diagnostic_app(Path(temp_dir))
            app.problem_strategy_metrics = {
                "primary": Counter({
                    "events": 4,
                    "attempts_started": 2,
                    "attempts_succeeded": 1,
                    "attempts_failed": 1,
                })
            }
            metrics = app._problem_strategy_summary()["primary"]
            self.assertEqual(metrics["attempts"], 2)
            self.assertEqual(metrics["failure_rate"], 0.5)

            terminal = "ERROR: unable to download video data: HTTP Error 403: Forbidden"
            first = app._stable_error_signature(
                "yt_dlp_download_attempt", "yt-dlp вернул ненулевой код",
                {"url": "https://youtu.be/one", "attempt": 1, "strategy": "one"},
                None, terminal, "youtube_forbidden_or_client_blocked",
                "youtube_http_403",
            )
            second = app._stable_error_signature(
                "yt_dlp_download_attempt", "yt-dlp вернул ненулевой код",
                {"url": "https://youtu.be/two", "attempt": 2, "strategy": "two"},
                None, terminal, "youtube_forbidden_or_client_blocked",
                "youtube_http_403",
            )
            self.assertEqual(first, second)
            terminal_error = app._terminal_error_text_for_problem_log(
                "WARNING: timeout on fragment retry\n"
                "ERROR: unable to download video data: HTTP Error 403: Forbidden",
                "",
                RuntimeError("yt-dlp returned code 1"),
                {},
            )
            self.assertEqual(
                app._canonical_problem_error_code(terminal_error),
                "youtube_http_403",
            )

    def test_postprocessing_conversion_has_canonical_code_and_media_hint(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app = make_diagnostic_app(Path(temp_dir))
            terminal = "ERROR: Postprocessing: Conversion failed!"
            self.assertEqual(
                app._canonical_problem_error_code(terminal),
                "yt_dlp_postprocessing_conversion_failed",
            )
            hint = app._ai_debug_hint_for_problem_log(
                "yt_dlp_download_attempt",
                "yt-dlp вернул ненулевой код",
                "",
                None,
                terminal,
                {
                    "failure_analysis": {
                        "stage": "postprocessing_merge",
                        "format_profile": "any",
                    }
                },
            )
            self.assertEqual(hint["category"], "media_validation_or_conversion")
            self.assertEqual(
                hint["error_code"], "yt_dlp_postprocessing_conversion_failed"
            )
            self.assertTrue(any("failure_analysis" in item for item in hint["next_checks"]))

    def test_ffmpeg_unknown_timestamp_has_specific_canonical_code(self):
        app = VideoDownloader.__new__(VideoDownloader)
        self.assertEqual(
            app._canonical_problem_error_code(
                "Timestamps are unset in a packet; Can't write packet with unknown timestamp"
            ),
            "ffmpeg_timestamp_mux_failure",
        )

    def test_strategy_findings_include_bad_strategy_and_effective_fallback(self):
        app = VideoDownloader.__new__(VideoDownloader)
        findings = app._problem_strategy_findings({
            "blocked": {
                "attempts": 6,
                "attempts_failed": 6,
                "attempts_succeeded": 0,
                "failure_rate": 1.0,
                "success_rate": 0.0,
            },
            "fallback": {
                "attempts": 5,
                "attempts_failed": 0,
                "attempts_succeeded": 5,
                "failure_rate": 0.0,
                "success_rate": 1.0,
                "fallback_recoveries": 5,
            },
        })
        self.assertEqual(
            [item["kind"] for item in findings],
            ["ineffective_strategy", "effective_fallback"],
        )
        self.assertIn("6 из 6", findings[0]["message"])
        self.assertIn("восстановил 5", findings[1]["message"])

    def test_adaptive_order_promotes_both_android_strategies_and_logs_order(self):
        app = VideoDownloader.__new__(VideoDownloader)
        app.youtube_strategy_state_lock = threading.Lock()
        app.youtube_primary_auth_failure_count = (
            YT_DLP_ADAPTIVE_PRIMARY_FAILURE_THRESHOLD
        )
        app.youtube_adaptive_strategy_logged = False
        ui_messages = []
        problem_events = []
        app.log = lambda message, level: ui_messages.append((message, level))
        app.record_problem = lambda *args, **kwargs: problem_events.append(
            (args, kwargs)
        )
        strategies = [
            {"name": "EJS GitHub stable auto quality"},
            {"name": "Early progressive MP4 no force IPv4"},
            {"name": "Android Progressive MP4 early"},
            {"name": "Android Client small chunks"},
            {"name": "last"},
        ]

        reordered = app._apply_adaptive_youtube_strategy_order(strategies)

        self.assertEqual(
            [item["name"] for item in reordered[:4]],
            [
                "Android Client small chunks",
                "EJS GitHub stable auto quality",
                "Early progressive MP4 no force IPv4",
                "Android Progressive MP4 early",
            ],
        )
        self.assertTrue(ui_messages)
        self.assertEqual(
            problem_events[0][0][2],
            "youtube_adaptive_strategy_order",
        )

    def test_network_failure_counts_include_current_attempt(self):
        app = VideoDownloader.__new__(VideoDownloader)
        counts = app._updated_youtube_network_failure_counts(
            "Connection to googlevideo.com timed out",
            YT_DLP_SLOW_NETWORK_FAILURE_SEC + 1,
            0,
            0,
        )
        self.assertEqual(counts, (1, 1, True))
        self.assertEqual(
            app._updated_youtube_network_failure_counts(
                "HTTP Error 403: Forbidden", 2.0, counts[0], counts[1]
            ),
            (1, 1, False),
        )
        self.assertFalse(app._should_fast_fail_youtube_network(1, 1))
        self.assertTrue(app._should_fast_fail_youtube_network(2, 2))
        self.assertTrue(app._should_fast_fail_youtube_network(3, 1))

    def test_end_to_end_recovery_writes_mirror_and_passes_disk_validation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app = make_diagnostic_app(Path(temp_dir))
            url = "https://www.youtube.com/watch?v=abcdefghijk&token=secret"
            app.record_problem(
                "Download token=SUPERSECRET failed",
                "WARNING",
                "yt_dlp_download_attempt",
                {
                    "url": url,
                    "attempt": 1,
                    "strategy": "safe",
                    "password": "secret",
                },
                RuntimeError("authorization: Bearer ABCDEF"),
                stderr="cookie=VALUE timed out",
            )
            app.record_problem(
                "Fallback succeeded",
                "INFO",
                "yt_dlp_recovered_after_fallback",
                {"url": url, "strategy": "safe"},
                resolved=True,
            )
            app.write_problem_session_summary(
                "completed",
                {
                    "elapsed_sec": 4.2,
                    "queued_url_count": 1,
                    "downloaded_video_count": 1,
                    "failed_download_count": 0,
                },
            )

            self.assertEqual(
                app.problem_events_file.read_bytes(),
                app.problem_log_file.read_bytes(),
            )
            validation = json.loads(
                app.problem_validation_report_file.read_text(encoding="utf-8")
            )
            summary = json.loads(
                app.problem_session_summary_file.read_text(encoding="utf-8")
            )
            self.assertEqual(validation["status"], "passed")
            self.assertEqual(validation["mirror"]["status"], "identical")
            self.assertEqual(summary["disk_validation_status"], "passed")
            self.assertEqual(summary["resolved_count"], 1)
            self.assertEqual(
                summary["strategy_metrics"]["safe"]["fallback_recoveries"],
                1,
            )
            all_text = "\n".join(
                path.read_text(encoding="utf-8", errors="replace")
                for path in app.problem_session_dir.rglob("*")
                if path.is_file()
            )
            for secret in (
                "SUPERSECRET", "ABCDEF", "VALUE", "TOPSECRET"
            ):
                self.assertNotIn(secret, all_text)

    def test_identical_diagnostic_payload_reuses_sha256_blob(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app = make_diagnostic_app(Path(temp_dir))
            base = {
                "schema_version": 4,
                "level": "ERROR",
                "operation": "same_error",
                "message": "same message",
                "error_signature": "same_error.signature",
                "context": {"stable": True},
                "stderr": {"tail": "same stderr"},
            }
            app.problem_signature_counts["same_error.signature"] = 1
            first = {
                **base,
                "sequence": 1,
                "event_id": "session:evt-000001",
                "timestamp": "2026-08-03T12:00:00",
                "problem_log_session_id": "session",
            }
            first_attachment = app._write_problem_attachment_locked(first)
            app.problem_signature_counts["same_error.signature"] = 2
            second = {
                **base,
                "sequence": 2,
                "event_id": "session:evt-000002",
                "timestamp": "2026-08-03T12:00:01",
                "problem_log_session_id": "session",
            }
            second_attachment = app._write_problem_attachment_locked(second)

            self.assertEqual(
                first_attachment["content_sha256"],
                second_attachment["content_sha256"],
            )
            self.assertEqual(
                first_attachment["content_path"],
                second_attachment["content_path"],
            )
            self.assertFalse(first_attachment["reused_existing_blob"])
            self.assertTrue(second_attachment["reused_existing_blob"])
            self.assertEqual(app.problem_unique_attachment_blob_count, 1)
            self.assertEqual(app.problem_reused_attachment_blob_count, 1)
            self.assertEqual(
                len(list((app.problem_attachments_dir / "blobs").glob("*.json"))),
                1,
            )

    def test_old_session_archive_is_verified_before_sources_are_removed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            app = VideoDownloader.__new__(VideoDownloader)
            app.problem_sessions_dir = root / "sessions"
            app.problem_session_dir = app.problem_sessions_dir / (
                "2026-08-03_12-00-00_pid999"
            )
            app.retention_cleanup_errors = []
            old = app.problem_sessions_dir / "2026-01-01_00-00-00_pid1"
            attachments = old / "attachments" / "blobs"
            attachments.mkdir(parents=True)
            (old / "events.jsonl").write_text('{"event":"one"}\n', encoding="utf-8")
            (old / "ai_problem_log.jsonl").write_text(
                '{"event":"one"}\n', encoding="utf-8"
            )
            (old / "incidents.jsonl").write_text("", encoding="utf-8")
            (attachments / "blob.json").write_text(
                json.dumps({"payload": "x" * 5000}), encoding="utf-8"
            )
            (old / "session_summary.json").write_text(
                '{"status":"completed"}', encoding="utf-8"
            )

            count = app._archive_old_problem_sessions(
                datetime(2026, 7, 1)
            )
            archive_path = old / "session_archive.zip"
            manifest = json.loads(
                (old / "archive_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(count, 1)
            self.assertTrue(archive_path.exists())
            self.assertTrue((old / "session_summary.json").exists())
            self.assertFalse((old / "events.jsonl").exists())
            self.assertFalse((old / "attachments").exists())
            self.assertFalse(app.retention_cleanup_errors)
            with zipfile.ZipFile(archive_path, "r") as archive:
                self.assertIsNone(archive.testzip())
                self.assertIn("events.jsonl", archive.namelist())
                self.assertIn(
                    "attachments/blobs/blob.json", archive.namelist()
                )
            self.assertEqual(manifest["file_count"], 4)


if __name__ == "__main__":
    unittest.main()
