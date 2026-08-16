r"""Проверка диагностических JSONL-файлов Skachat_video_online.

Модуль не требует сторонних библиотек. Его можно импортировать из приложения
или запускать отдельно:

    python problem_log_validator.py
    python problem_log_validator.py "Логи проблем\Сессии\<сессия>"
    python problem_log_validator.py --examples
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


CURRENT_SCHEMA_VERSION = 4
VALIDATOR_VERSION = "1.1"
SUPPORTED_SOURCE_SCHEMA_VERSIONS = (3, 4)
MAX_REPORTED_ERRORS = 100
INCIDENT_STATUSES = {
    "detected", "retrying", "recovered", "failed", "cancelled",
}


def _sha256_file(path: Path) -> Optional[str]:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _json_type_matches(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return True


def _legacy_signature(record: Dict[str, Any]) -> str:
    fingerprint = str(record.get("problem_fingerprint") or "").strip()
    if fingerprint:
        return f"legacy.{fingerprint}"
    raw = json.dumps(
        {
            "operation": record.get("operation"),
            "message": record.get("message"),
            "level": record.get("level"),
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return f"legacy.{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"


def normalize_event_record(
    record: Dict[str, Any],
    line_number: int = 0,
) -> Tuple[Dict[str, Any], bool]:
    """Нормализует запись схемы 3 в читаемый контракт схемы 4.

    Исходный словарь не изменяется. Поля схемы 3 не удаляются.
    """
    normalized = dict(record)
    source_version = normalized.get("schema_version")
    try:
        source_version = int(source_version)
    except (TypeError, ValueError):
        return normalized, False

    if source_version != 3:
        return normalized, False

    session_id = str(
        normalized.get("problem_log_session_id") or "legacy-session"
    )
    try:
        sequence = int(normalized.get("sequence") or line_number or 0)
    except (TypeError, ValueError):
        sequence = line_number or 0
    normalized.update({
        "schema_version": CURRENT_SCHEMA_VERSION,
        "source_schema_version": 3,
        "compatibility_normalized": True,
        "sequence": sequence,
        "event_id": normalized.get("event_id")
        or f"{session_id}:legacy-evt-{sequence:06d}",
        "problem_log_session_id": session_id,
        "timestamp": str(
            normalized.get("timestamp") or "1970-01-01T00:00:00"
        ),
        "level": str(normalized.get("level") or "ERROR").upper(),
        "operation": str(normalized.get("operation") or "legacy_unknown"),
        "message": str(normalized.get("message") or ""),
        "error_signature": normalized.get("error_signature")
        or _legacy_signature(normalized),
        "problem_fingerprint": str(
            normalized.get("problem_fingerprint") or ""
        ),
        "resolved": bool(normalized.get("resolved", False)),
    })
    return normalized, True


def validate_record_against_schema(
    record: Dict[str, Any],
    schema: Dict[str, Any],
) -> List[str]:
    errors: List[str] = []
    for key in schema.get("required") or []:
        if key not in record or record.get(key) is None:
            errors.append(f"missing:{key}")

    properties = schema.get("properties") or {}
    for key, rules in properties.items():
        if key not in record:
            continue
        value = record[key]
        if "const" in rules and value != rules["const"]:
            errors.append(f"invalid_const:{key}")
        if "enum" in rules and value not in rules["enum"]:
            errors.append(f"invalid_enum:{key}")
        expected_types = rules.get("type")
        if expected_types:
            if isinstance(expected_types, str):
                expected_types = [expected_types]
            if not any(
                _json_type_matches(value, expected)
                for expected in expected_types
            ):
                errors.append(f"invalid_type:{key}")
    return errors


def _read_schema(path: Path) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, [f"schema_not_found:{path}"]
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return None, [f"schema_read_failed:{type(error).__name__}:{error}"]
    if not isinstance(value, dict):
        return None, ["schema_root_is_not_object"]
    return value, []


def validate_jsonl_file(
    path: Path,
    schema: Dict[str, Any],
    *,
    allow_legacy: bool = True,
) -> Dict[str, Any]:
    report: Dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "sha256": _sha256_file(path),
        "line_count": 0,
        "blank_line_count": 0,
        "valid_record_count": 0,
        "invalid_record_count": 0,
        "legacy_record_count": 0,
        "source_schema_versions": [],
        "level_counts": {},
        "operation_counts": {},
        "errors": [],
    }
    if not path.exists():
        report["invalid_record_count"] = 1
        report["errors"].append({"line": None, "codes": ["file_not_found"]})
        return report

    source_versions = set()
    level_counts: Counter[str] = Counter()
    operation_counts: Counter[str] = Counter()
    try:
        stream = path.open("r", encoding="utf-8")
    except (OSError, UnicodeError) as error:
        report["invalid_record_count"] = 1
        report["errors"].append({
            "line": None,
            "codes": [f"file_open_failed:{type(error).__name__}:{error}"],
        })
        return report

    with stream:
        for line_number, raw_line in enumerate(stream, 1):
            report["line_count"] += 1
            if not raw_line.strip():
                report["blank_line_count"] += 1
                continue
            try:
                record = json.loads(raw_line)
            except json.JSONDecodeError as error:
                report["invalid_record_count"] += 1
                if len(report["errors"]) < MAX_REPORTED_ERRORS:
                    report["errors"].append({
                        "line": line_number,
                        "codes": [
                            f"invalid_json:column_{error.colno}:{error.msg}"
                        ],
                    })
                continue
            if not isinstance(record, dict):
                report["invalid_record_count"] += 1
                if len(report["errors"]) < MAX_REPORTED_ERRORS:
                    report["errors"].append({
                        "line": line_number,
                        "codes": ["record_is_not_object"],
                    })
                continue

            source_version = record.get("schema_version")
            source_versions.add(source_version)
            normalized = record
            was_legacy = False
            if allow_legacy:
                normalized, was_legacy = normalize_event_record(
                    record, line_number
                )
            if was_legacy:
                report["legacy_record_count"] += 1
            level_counts[str(normalized.get("level") or "UNKNOWN").upper()] += 1
            operation_counts[str(normalized.get("operation") or "unknown")] += 1
            errors = validate_record_against_schema(normalized, schema)
            if errors:
                report["invalid_record_count"] += 1
                if len(report["errors"]) < MAX_REPORTED_ERRORS:
                    report["errors"].append({
                        "line": line_number,
                        "source_schema_version": source_version,
                        "codes": errors,
                    })
            else:
                report["valid_record_count"] += 1

    report["source_schema_versions"] = sorted(
        source_versions, key=lambda value: str(value)
    )
    report["level_counts"] = dict(level_counts)
    report["operation_counts"] = dict(operation_counts)
    return report


def _read_jsonl_objects(path: Path) -> Tuple[List[Tuple[int, Dict[str, Any]]], List[Dict[str, Any]]]:
    records: List[Tuple[int, Dict[str, Any]]] = []
    errors: List[Dict[str, Any]] = []
    if not path.exists():
        return records, errors
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        return records, [{
            "line": None,
            "codes": [f"file_read_failed:{type(error).__name__}:{error}"],
        }]
    for line_number, raw_line in enumerate(lines, 1):
        if not raw_line.strip():
            continue
        try:
            value = json.loads(raw_line)
        except json.JSONDecodeError as error:
            errors.append({
                "line": line_number,
                "codes": [f"invalid_json:column_{error.colno}:{error.msg}"],
            })
            continue
        if isinstance(value, dict):
            records.append((line_number, value))
        else:
            errors.append({"line": line_number, "codes": ["record_is_not_object"]})
    return records, errors


def _canonical_json_sha256(value: Any) -> str:
    data = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _read_attachment_json(path: Path) -> Any:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not value.get("compressed_attachment"):
        return value
    gzip_path = Path(str(value.get("gzip_path") or ""))
    if not gzip_path.is_absolute():
        gzip_path = path.parent / gzip_path
    if not gzip_path.exists():
        gzip_path = path.with_suffix(path.suffix + ".gz")
    with gzip.open(gzip_path, "rb") as stream:
        raw = stream.read()
    expected_raw_hash = str(value.get("sha256") or "")
    if expected_raw_hash and hashlib.sha256(raw).hexdigest() != expected_raw_hash:
        raise ValueError("compressed_attachment_sha256_mismatch")
    return json.loads(raw.decode("utf-8"))


def _attachment_path(
    session_dir: Path,
    attachment: Dict[str, Any],
    absolute_key: str,
    relative_key: str,
) -> Optional[Path]:
    relative = attachment.get(relative_key)
    if relative:
        return session_dir / Path(str(relative))
    absolute = attachment.get(absolute_key)
    if absolute:
        candidate = Path(str(absolute))
        return candidate if candidate.is_absolute() else session_dir / candidate
    return None


def validate_event_integrity(path: Path) -> Dict[str, Any]:
    records, read_errors = _read_jsonl_objects(path)
    errors = list(read_errors)
    warnings: List[Dict[str, Any]] = []
    seen_event_ids = set()
    session_ids = set()
    previous_sequence: Optional[int] = None
    checked_attachment_count = 0
    verified_attachment_count = 0
    legacy_unverified_attachment_count = 0

    for line_number, raw_record in records:
        record, _ = normalize_event_record(raw_record, line_number)
        codes: List[str] = []
        event_id = str(record.get("event_id") or "")
        if event_id:
            if event_id in seen_event_ids:
                codes.append("duplicate:event_id")
            seen_event_ids.add(event_id)
        session_id = str(record.get("problem_log_session_id") or "")
        if session_id:
            session_ids.add(session_id)
        try:
            sequence = int(record.get("sequence"))
        except (TypeError, ValueError):
            sequence = None
        if sequence is not None:
            if previous_sequence is not None and sequence <= previous_sequence:
                codes.append("sequence_not_strictly_increasing")
            previous_sequence = sequence
        parent_event_id = str(record.get("parent_event_id") or "")
        if parent_event_id and parent_event_id not in seen_event_ids:
            codes.append("parent_event_not_found_before_child")

        attachment = record.get("attachment")
        if isinstance(attachment, dict) and attachment.get("status") == "saved":
            checked_attachment_count += 1
            expected_hash = str(attachment.get("content_sha256") or "")
            content_path = _attachment_path(
                path.parent, attachment, "content_path", "relative_content_path"
            )
            pointer_path = _attachment_path(
                path.parent, attachment, "path", "relative_path"
            )
            if not expected_hash or content_path is None:
                legacy_unverified_attachment_count += 1
                warnings.append({
                    "line": line_number,
                    "codes": ["legacy_attachment_without_hash_metadata"],
                })
            else:
                if not content_path.exists():
                    codes.append("attachment_content_not_found")
                else:
                    try:
                        content = _read_attachment_json(content_path)
                        if _canonical_json_sha256(content) != expected_hash:
                            codes.append("attachment_content_sha256_mismatch")
                        else:
                            verified_attachment_count += 1
                    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
                        codes.append(
                            f"attachment_content_read_failed:{type(error).__name__}"
                        )
                if pointer_path is None or not pointer_path.exists():
                    codes.append("attachment_pointer_not_found")
                else:
                    try:
                        pointer = _read_attachment_json(pointer_path)
                        if str(pointer.get("content_sha256") or "") != expected_hash:
                            codes.append("attachment_pointer_sha256_mismatch")
                    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
                        codes.append(
                            f"attachment_pointer_read_failed:{type(error).__name__}"
                        )
        if codes and len(errors) < MAX_REPORTED_ERRORS:
            errors.append({"line": line_number, "codes": codes})

    if len(session_ids) > 1:
        errors.append({"line": None, "codes": ["multiple_session_ids"]})
    return {
        "status": "failed" if errors else "passed",
        "event_id_count": len(seen_event_ids),
        "session_ids": sorted(session_ids),
        "checked_attachment_count": checked_attachment_count,
        "verified_attachment_count": verified_attachment_count,
        "legacy_unverified_attachment_count": legacy_unverified_attachment_count,
        "errors": errors[:MAX_REPORTED_ERRORS],
        "warnings": warnings[:MAX_REPORTED_ERRORS],
    }


def validate_incidents_file(path: Path) -> Dict[str, Any]:
    report: Dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "sha256": _sha256_file(path),
        "line_count": 0,
        "valid_record_count": 0,
        "invalid_record_count": 0,
        "errors": [],
    }
    if not path.exists():
        # В чистой сессии incidents.jsonl может ещё не существовать.
        report["optional_empty_file"] = True
        return report

    required = {
        "schema_version", "timestamp", "problem_log_session_id",
        "incident_id", "incident_key", "event_id", "status",
        "operation", "error_signature",
    }
    try:
        lines: Iterable[str] = path.read_text(
            encoding="utf-8"
        ).splitlines()
    except (OSError, UnicodeError) as error:
        report["invalid_record_count"] = 1
        report["errors"].append({
            "line": None,
            "codes": [f"file_read_failed:{type(error).__name__}:{error}"],
        })
        return report

    for line_number, raw_line in enumerate(lines, 1):
        report["line_count"] += 1
        if not raw_line.strip():
            continue
        try:
            record = json.loads(raw_line)
        except json.JSONDecodeError as error:
            report["invalid_record_count"] += 1
            report["errors"].append({
                "line": line_number,
                "codes": [f"invalid_json:column_{error.colno}:{error.msg}"],
            })
            continue
        errors = []
        if not isinstance(record, dict):
            errors.append("record_is_not_object")
        else:
            errors.extend(
                f"missing:{key}" for key in sorted(required)
                if record.get(key) is None
            )
            if record.get("schema_version") != CURRENT_SCHEMA_VERSION:
                errors.append("invalid:schema_version")
            if record.get("status") not in INCIDENT_STATUSES:
                errors.append("invalid:status")
        if errors:
            report["invalid_record_count"] += 1
            if len(report["errors"]) < MAX_REPORTED_ERRORS:
                report["errors"].append({
                    "line": line_number,
                    "codes": errors,
                })
        else:
            report["valid_record_count"] += 1
    return report


def validate_incident_relationships(
    path: Path,
    event_ids: set,
) -> Dict[str, Any]:
    records, read_errors = _read_jsonl_objects(path)
    errors = list(read_errors)
    previous_status: Dict[str, str] = {}
    allowed = {
        "detected": {"retrying", "recovered", "failed", "cancelled"},
        "retrying": {"retrying", "recovered", "failed", "cancelled"},
        "recovered": set(),
        "failed": set(),
        "cancelled": set(),
    }
    for line_number, record in records:
        codes: List[str] = []
        event_id = str(record.get("event_id") or "")
        if event_id and event_id not in event_ids:
            codes.append("incident_event_not_found")
        incident_id = str(record.get("incident_id") or "")
        status = str(record.get("status") or "")
        prior = previous_status.get(incident_id)
        if prior and status not in allowed.get(prior, set()):
            codes.append(f"invalid_incident_transition:{prior}->{status}")
        if incident_id:
            previous_status[incident_id] = status
        if codes and len(errors) < MAX_REPORTED_ERRORS:
            errors.append({"line": line_number, "codes": codes})
    return {
        "status": "failed" if errors else "passed",
        "incident_count": len(previous_status),
        "errors": errors[:MAX_REPORTED_ERRORS],
    }


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


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Проверка событий и инцидентов из папки Логи проблем."
    )
    parser.add_argument(
        "target", nargs="?",
        help="Каталог сессии или events.jsonl. По умолчанию latest_run.json.",
    )
    parser.add_argument(
        "--examples", action="store_true",
        help="Проверить постоянные примеры схем 3 и 4.",
    )
    parser.add_argument(
        "--report", help="Сохранить JSON-отчёт в указанный файл.",
    )
    args = parser.parse_args(argv)
    project_dir = Path(__file__).resolve().parent
    try:
        if args.examples:
            report = _example_reports(project_dir)
        else:
            events, incidents, schema, legacy = _resolve_target(
                project_dir, args.target
            )
            report = validate_session_logs(
                events, incidents, schema,
                legacy_events_file=legacy,
                emergency_file=(
                    project_dir / "Логи проблем" / "emergency_problem_log.jsonl"
                    if args.target is None else None
                ),
            )
    except Exception as error:
        report = {
            "validator_version": VALIDATOR_VERSION,
            "status": "failed",
            "error_type": type(error).__name__,
            "error": str(error),
        }
    if args.report:
        _atomic_write_json(Path(args.report).expanduser().resolve(), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("status") == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
