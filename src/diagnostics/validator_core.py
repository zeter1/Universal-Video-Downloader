"""Базовая schema/JSONL-валидация problem logs.

Owner: schema compatibility, record normalization и проверка одного JSONL файла.
"""

from __future__ import annotations

import gzip
import hashlib
import json
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
