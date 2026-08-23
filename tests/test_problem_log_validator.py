import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from problem_log_validator import (
    _example_reports,
    normalize_event_record,
    validate_jsonl_file,
    validate_session_logs,
)


PROJECT_DIR = Path(__file__).resolve().parents[1]
LOG_DIR = PROJECT_DIR / "Логи проблем"


class ProblemLogValidatorTests(unittest.TestCase):
    @staticmethod
    def _event(event_id="session:evt-000001", sequence=1, **extra):
        event = {
            "schema_version": 4,
            "event_id": event_id,
            "parent_event_id": None,
            "sequence": sequence,
            "timestamp": "2026-01-01T00:00:00",
            "level": "INFO",
            "operation": "operation",
            "message": "message",
            "error_signature": "operation.signature",
            "problem_log_session_id": "session",
            "attachment": None,
        }
        event.update(extra)
        return event

    @staticmethod
    def _write_jsonl(path, records):
        path.write_text(
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records),
            encoding="utf-8",
        )

    def test_all_permanent_examples_match_expectations(self):
        report = _example_reports(PROJECT_DIR)
        expected = json.loads(
            (LOG_DIR / "Примеры" / "expected_results.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(report["status"], expected["validator_status"])
        for name, expected_scenario in expected["scenarios"].items():
            scenario = report["scenarios"][name]
            self.assertEqual(scenario["status"], "passed", name)
            self.assertEqual(
                scenario["events"]["valid_record_count"],
                expected_scenario["event_count"],
                name,
            )
            self.assertEqual(
                scenario["incidents"]["valid_record_count"],
                expected_scenario["incident_count"],
                name,
            )
            self.assertEqual(
                scenario["events"]["legacy_record_count"],
                expected_scenario["legacy_record_count"],
                name,
            )
            self.assertEqual(scenario["mirror"]["status"], "identical", name)
            if "incident_statuses" in expected_scenario:
                incident_path = (
                    LOG_DIR / "Примеры" / name / "incidents.jsonl"
                )
                statuses = [
                    json.loads(line)["status"]
                    for line in incident_path.read_text(
                        encoding="utf-8"
                    ).splitlines()
                    if line.strip()
                ]
                self.assertEqual(
                    statuses, expected_scenario["incident_statuses"], name
                )

    def test_schema3_record_is_normalized_without_losing_source_fields(self):
        original = {
            "schema_version": 3,
            "sequence": 7,
            "problem_log_session_id": "legacy-test",
            "timestamp": "2025-01-01T00:00:00",
            "level": "warning",
            "operation": "legacy_operation",
            "message": "legacy message",
            "problem_fingerprint": "abc123",
            "custom_old_field": {"kept": True},
        }
        normalized, changed = normalize_event_record(original, 1)
        self.assertTrue(changed)
        self.assertEqual(original["schema_version"], 3)
        self.assertEqual(normalized["schema_version"], 4)
        self.assertEqual(normalized["source_schema_version"], 3)
        self.assertEqual(
            normalized["event_id"], "legacy-test:legacy-evt-000007"
        )
        self.assertEqual(normalized["error_signature"], "legacy.abc123")
        self.assertEqual(normalized["custom_old_field"], {"kept": True})

    def test_invalid_json_line_is_reported_with_line_number(self):
        schema = json.loads(
            (LOG_DIR / "log_schema.json").read_text(encoding="utf-8")
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "events.jsonl"
            path.write_text('{"schema_version": 4}\n{broken}\n', encoding="utf-8")
            report = validate_jsonl_file(path, schema)
        self.assertEqual(report["invalid_record_count"], 2)
        self.assertEqual(report["errors"][0]["line"], 1)
        self.assertEqual(report["errors"][1]["line"], 2)

    def test_different_legacy_mirror_fails_session_validation(self):
        schema_path = LOG_DIR / "log_schema.json"
        valid_event = (
            '{"schema_version":4,"event_id":"e","timestamp":"t",'
            '"level":"INFO","operation":"o","message":"m",'
            '"error_signature":"s","problem_log_session_id":"p"}\n'
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            events = root / "events.jsonl"
            legacy = root / "ai_problem_log.jsonl"
            incidents = root / "incidents.jsonl"
            events.write_text(valid_event, encoding="utf-8")
            legacy.write_text(valid_event + "\n", encoding="utf-8")
            incidents.write_text("", encoding="utf-8")
            report = validate_session_logs(
                events,
                incidents,
                schema_path,
                legacy_events_file=legacy,
            )
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["mirror"]["status"], "different")

    def test_attachment_content_hash_corruption_fails_validation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            attachments = root / "attachments"
            blobs = attachments / "blobs"
            blobs.mkdir(parents=True)
            content = {"schema_version": 4, "diagnostic": {"value": "good"}}
            canonical = json.dumps(
                content, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            expected_hash = hashlib.sha256(canonical).hexdigest()
            content_path = blobs / f"{expected_hash}.json"
            content_path.write_text(
                json.dumps({"corrupted": True}), encoding="utf-8"
            )
            pointer_path = attachments / "event_000001.json"
            pointer_path.write_text(
                json.dumps({"content_sha256": expected_hash}), encoding="utf-8"
            )
            event = self._event(attachment={
                "status": "saved",
                "relative_path": "attachments/event_000001.json",
                "relative_content_path": f"attachments/blobs/{expected_hash}.json",
                "content_sha256": expected_hash,
            })
            events = root / "events.jsonl"
            incidents = root / "incidents.jsonl"
            self._write_jsonl(events, [event])
            incidents.write_text("", encoding="utf-8")
            report = validate_session_logs(
                events, incidents, LOG_DIR / "log_schema.json"
            )
        self.assertEqual(report["status"], "failed")
        codes = report["event_integrity"]["errors"][0]["codes"]
        self.assertIn("attachment_content_sha256_mismatch", codes)

    def test_duplicate_event_and_unknown_parent_fail_validation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            events = root / "events.jsonl"
            incidents = root / "incidents.jsonl"
            self._write_jsonl(events, [
                self._event(),
                self._event(parent_event_id="missing", sequence=2),
            ])
            incidents.write_text("", encoding="utf-8")
            report = validate_session_logs(
                events, incidents, LOG_DIR / "log_schema.json"
            )
        self.assertEqual(report["status"], "failed")
        codes = {
            code
            for error in report["event_integrity"]["errors"]
            for code in error["codes"]
        }
        self.assertIn("duplicate:event_id", codes)
        self.assertIn("parent_event_not_found_before_child", codes)

    def test_summary_count_mismatch_fails_validation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            events = root / "events.jsonl"
            incidents = root / "incidents.jsonl"
            self._write_jsonl(events, [self._event()])
            incidents.write_text("", encoding="utf-8")
            report = validate_session_logs(
                events,
                incidents,
                LOG_DIR / "log_schema.json",
                expected_summary={
                    "event_count": 2,
                    "event_counts": {"INFO": 2},
                },
            )
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["summary_counts"]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
