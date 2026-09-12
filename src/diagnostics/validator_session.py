"""Валидация целой diagnostic session и разрешение CLI targets/examples."""

from __future__ import annotations

import gzip
import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.diagnostics.validator_core import (
    SUPPORTED_SOURCE_SCHEMA_VERSIONS, VALIDATOR_VERSION, MAX_REPORTED_ERRORS,
    _read_schema, _sha256_file, normalize_event_record, validate_jsonl_file,
)
from src.diagnostics.validator_integrity import (
    _read_jsonl_objects, validate_event_integrity, validate_incidents_file,
    validate_incident_relationships,
)

def validate_emergency_log(
    path: Optional[Path],
    problem_log_session_id: Optional[str],
) -> Dict[str, Any]:
    if path is None:
        return {"status": "not_configured", "count": 0, "critical_count": 0}
    path = Path(path)
    records, read_errors = _read_jsonl_objects(path)
    if read_errors:
        return {
            "status": "failed",
            "path": str(path),
            "count": 0,
            "critical_count": 0,
            "errors": read_errors,
        }
    matching = [
        record for _, record in records
        if str(record.get("problem_log_session_id") or "")
        == str(problem_log_session_id or "")
    ]
    critical_count = sum(
        1 for record in matching
        if str(record.get("level") or "").upper() == "CRITICAL"
    )
    return {
        "status": "degraded" if matching else "clean",
        "path": str(path),
        "count": len(matching),
        "critical_count": critical_count,
        "operation_counts": dict(Counter(
            str(record.get("operation") or "unknown") for record in matching
        )),
    }


def validate_summary_counts(
    expected_summary: Optional[Dict[str, Any]],
    event_report: Dict[str, Any],
) -> Dict[str, Any]:
    if not isinstance(expected_summary, dict):
        return {"status": "not_configured", "errors": []}
    errors: List[str] = []
    expected_event_count = expected_summary.get("event_count")
    actual_event_count = event_report.get("valid_record_count")
    if expected_event_count is not None and expected_event_count != actual_event_count:
        errors.append(
            f"event_count_mismatch:expected_{expected_event_count}:actual_{actual_event_count}"
        )
    expected_levels = expected_summary.get("event_counts")
    if isinstance(expected_levels, dict):
        actual_levels = event_report.get("level_counts") or {}
        for level in set(expected_levels) | set(actual_levels):
            expected = int(expected_levels.get(level) or 0)
            actual = int(actual_levels.get(level) or 0)
            if expected != actual:
                errors.append(
                    f"level_count_mismatch:{level}:expected_{expected}:actual_{actual}"
                )
    return {
        "status": "failed" if errors else "passed",
        "errors": errors[:MAX_REPORTED_ERRORS],
    }


def _mirror_report(primary: Path, legacy: Optional[Path]) -> Dict[str, Any]:
    if legacy is None:
        return {"status": "not_configured"}
    if not primary.exists() and not legacy.exists():
        return {"status": "both_missing"}
    primary_hash = _sha256_file(primary)
    legacy_hash = _sha256_file(legacy)
    return {
        "status": (
            "identical"
            if primary_hash is not None and primary_hash == legacy_hash
            else "different"
        ),
        "primary_path": str(primary),
        "legacy_path": str(legacy),
        "primary_sha256": primary_hash,
        "legacy_sha256": legacy_hash,
    }


def validate_session_logs(
    events_file: Path,
    incidents_file: Path,
    schema_file: Path,
    *,
    legacy_events_file: Optional[Path] = None,
    emergency_file: Optional[Path] = None,
    problem_log_session_id: Optional[str] = None,
    expected_summary: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    events_file = Path(events_file)
    incidents_file = Path(incidents_file)
    schema_file = Path(schema_file)
    legacy_events_file = (
        Path(legacy_events_file) if legacy_events_file is not None else None
    )
    schema, schema_errors = _read_schema(schema_file)
    if schema is None:
        return {
            "validator_version": VALIDATOR_VERSION,
            "status": "failed",
            "validated_at": datetime.now().isoformat(timespec="seconds"),
            "schema_file": str(schema_file),
            "schema_errors": schema_errors,
        }

    selected_events = events_file
    used_legacy_filename = False
    if not selected_events.exists() and legacy_events_file is not None:
        selected_events = legacy_events_file
        used_legacy_filename = selected_events.exists()

    event_report = validate_jsonl_file(
        selected_events, schema, allow_legacy=True
    )
    incident_report = validate_incidents_file(incidents_file)
    integrity = validate_event_integrity(selected_events)
    event_ids = set()
    for _, record in _read_jsonl_objects(selected_events)[0]:
        normalized, _ = normalize_event_record(record)
        if normalized.get("event_id"):
            event_ids.add(str(normalized["event_id"]))
    incident_relationships = validate_incident_relationships(
        incidents_file, event_ids
    )
    if not problem_log_session_id and len(integrity.get("session_ids") or []) == 1:
        problem_log_session_id = integrity["session_ids"][0]
    emergency = validate_emergency_log(
        emergency_file, problem_log_session_id
    )
    if expected_summary is None:
        summary_path = selected_events.with_name("session_summary.json")
        if summary_path.exists():
            try:
                candidate = json.loads(summary_path.read_text(encoding="utf-8"))
                if isinstance(candidate, dict):
                    expected_summary = candidate
            except (OSError, UnicodeError, json.JSONDecodeError):
                pass
    summary_counts = validate_summary_counts(expected_summary, event_report)
    mirror = _mirror_report(events_file, legacy_events_file)
    failed = bool(
        schema_errors
        or event_report["invalid_record_count"]
        or incident_report["invalid_record_count"]
        or integrity.get("status") == "failed"
        or incident_relationships.get("status") == "failed"
        or emergency.get("status") == "failed"
        or summary_counts.get("status") == "failed"
        or mirror.get("status") == "different"
    )
    degraded = emergency.get("status") == "degraded"
    return {
        "validator_version": VALIDATOR_VERSION,
        "status": "failed" if failed else ("degraded" if degraded else "passed"),
        "validated_at": datetime.now().isoformat(timespec="seconds"),
        "schema_file": str(schema_file),
        "schema_sha256": _sha256_file(schema_file),
        "used_legacy_filename": used_legacy_filename,
        "supported_source_schema_versions": list(
            SUPPORTED_SOURCE_SCHEMA_VERSIONS
        ),
        "events": event_report,
        "event_integrity": integrity,
        "incidents": incident_report,
        "incident_relationships": incident_relationships,
        "summary_counts": summary_counts,
        "emergency": emergency,
        "mirror": mirror,
    }


def _resolve_latest_session(project_dir: Path) -> Tuple[Path, Path, Path, Path]:
    log_dir = project_dir / "Логи проблем"
    latest_path = log_dir / "latest_run.json"
    latest = json.loads(latest_path.read_text(encoding="utf-8"))
    session_dir = Path(latest["session_dir"])
    return (
        Path(latest.get("event_log") or session_dir / "events.jsonl"),
        Path(latest.get("incidents_file") or session_dir / "incidents.jsonl"),
        log_dir / "log_schema.json",
        Path(
            latest.get("legacy_event_log")
            or session_dir / "ai_problem_log.jsonl"
        ),
    )


def _resolve_target(
    project_dir: Path,
    target: Optional[str],
) -> Tuple[Path, Path, Path, Optional[Path]]:
    if not target:
        return _resolve_latest_session(project_dir)
    path = Path(target).expanduser().resolve()
    schema = project_dir / "Логи проблем" / "log_schema.json"
    if path.is_dir():
        return (
            path / "events.jsonl",
            path / "incidents.jsonl",
            schema,
            path / "ai_problem_log.jsonl",
        )
    return path, path.with_name("incidents.jsonl"), schema, (
        path.with_name("ai_problem_log.jsonl")
        if path.name != "ai_problem_log.jsonl" else None
    )


def _example_reports(project_dir: Path) -> Dict[str, Any]:
    examples_root = project_dir / "Логи проблем" / "Примеры"
    reports = {}
    for scenario_dir in sorted(
        path for path in examples_root.iterdir() if path.is_dir()
    ):
        reports[scenario_dir.name] = validate_session_logs(
            scenario_dir / "events.jsonl",
            scenario_dir / "incidents.jsonl",
            project_dir / "Логи проблем" / "log_schema.json",
            legacy_events_file=scenario_dir / "ai_problem_log.jsonl",
        )
    return {
        "validator_version": VALIDATOR_VERSION,
        "status": (
            "passed"
            if reports and all(
                item.get("status") == "passed" for item in reports.values()
            )
            else "failed"
        ),
        "examples_root": str(examples_root),
        "scenarios": reports,
    }


def _atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    try:
        temp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp.replace(path)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
