"""Публичный фасад и CLI validator problem logs.

Тяжёлая реализация разделена на `validator_core.py`, `validator_integrity.py`
и `validator_session.py`, чтобы обычный импорт/поиск не требовал чтения 800+ строк.
Совместимые импорты из `src.diagnostics.validator` сохранены.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from src.diagnostics.validator_core import (
    CURRENT_SCHEMA_VERSION, VALIDATOR_VERSION, SUPPORTED_SOURCE_SCHEMA_VERSIONS,
    MAX_REPORTED_ERRORS, INCIDENT_STATUSES,
    _sha256_file, _json_type_matches, _legacy_signature, normalize_event_record,
    validate_record_against_schema, _read_schema, validate_jsonl_file,
)
from src.diagnostics.validator_integrity import (
    _read_jsonl_objects, _canonical_json_sha256, _read_attachment_json,
    _attachment_path, validate_event_integrity, validate_incidents_file,
    validate_incident_relationships,
)
from src.diagnostics.validator_session import (
    validate_emergency_log, validate_summary_counts, _mirror_report,
    validate_session_logs, _resolve_latest_session, _resolve_target,
    _example_reports, _atomic_write_json,
)

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
    project_dir = Path(__file__).resolve().parents[2]
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
