"""Проверка целостности events/incidents/attachments problem logs."""

from __future__ import annotations

import gzip
import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.diagnostics.validator_core import (
    CURRENT_SCHEMA_VERSION, INCIDENT_STATUSES, MAX_REPORTED_ERRORS,
    _sha256_file, normalize_event_record,
)

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
