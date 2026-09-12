"""Atomic writes и trimming небольших persistent diagnostic files."""

from typing import Dict
from typing import List
from pathlib import Path
from datetime import datetime
import json
import os
import random
import re
import threading
import time

from src.core.constants import PROBLEM_HEALTH_HISTORY_MAX_LINES, PROBLEM_LOG_ATOMIC_REPLACE_RETRIES, PROBLEM_LOG_ATOMIC_RETRY_BASE_DELAY_SEC, PROBLEM_LOG_RETENTION_DAYS


class RetentionStorageMixin:
    def _trim_health_history(self) -> int:
        path = getattr(self, "problem_health_history_file", None)
        if not path or not path.exists():
            return 0
        try:
            raw_lines = [
                line for line in path.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()
                if line.strip()
            ]
            cutoff = self.retention_cutoff(PROBLEM_LOG_RETENTION_DAYS)
            lines = []
            for line in raw_lines:
                try:
                    item = json.loads(line)
                    timestamp = self._parse_iso_datetime(item.get("timestamp"))
                except (TypeError, ValueError, json.JSONDecodeError):
                    timestamp = None
                if timestamp is None or timestamp >= cutoff:
                    lines.append(line)
            lines = lines[-PROBLEM_HEALTH_HISTORY_MAX_LINES:]
            if lines == raw_lines:
                return 0
            self._atomic_write_text_file(
                path,
                "\n".join(lines[-PROBLEM_HEALTH_HISTORY_MAX_LINES:]) + "\n",
            )
            return max(0, len(raw_lines) - len(lines))
        except OSError as e:
            self._remember_retention_error(path, e)
            return 0


    def _delete_legacy_flat_logs(self, paths: List[Path]) -> int:
        cleaned = 0
        for path in paths:
            if not path.exists() or not path.is_file():
                continue
            try:
                path.unlink()
                cleaned += 1
            except OSError as e:
                self._remember_retention_error(path, e)
        return cleaned


    def _rewrite_text_file_if_changed(self, path: Path, old_lines: List[str],
                                      new_lines: List[str]) -> int:
        if old_lines == new_lines:
            return 0
        try:
            if new_lines:
                temp_path = path.with_name(path.name + ".tmp")
                temp_path.write_text(''.join(new_lines), encoding='utf-8')
                temp_path.replace(path)
            else:
                path.unlink()
            return 1
        except OSError as e:
            self._remember_retention_error(path, e)
            return 0


    def _problem_atomic_lock(self):
        """Возвращает общий re-entrant lock даже для облегчённых test-double объектов."""
        lock = getattr(self, "problem_atomic_write_lock", None)
        if lock is None:
            lock = threading.RLock()
            self.problem_atomic_write_lock = lock
        return lock


    def _replace_atomic_temp_file(self, temp_path: Path, path: Path) -> None:
        """Учитывает кратковременную блокировку файла Windows индексатором/другим потоком."""
        for attempt in range(PROBLEM_LOG_ATOMIC_REPLACE_RETRIES):
            try:
                os.replace(temp_path, path)
                return
            except OSError as error:
                winerror = getattr(error, "winerror", None)
                retryable = isinstance(error, PermissionError) or winerror in {5, 32}
                if not retryable or attempt + 1 >= PROBLEM_LOG_ATOMIC_REPLACE_RETRIES:
                    raise
                time.sleep(
                    PROBLEM_LOG_ATOMIC_RETRY_BASE_DELAY_SEC * (attempt + 1)
                )


    def _atomic_write_json_file(self, path: Path, data: Dict) -> None:
        """Потокобезопасно и атомарно записывает JSON рядом с целевым файлом."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._problem_atomic_lock():
            temp_path = path.with_name(
                f".{path.name}.{os.getpid()}.{threading.get_ident()}."
                f"{time.time_ns()}.{random.getrandbits(32):08x}.tmp"
            )
            try:
                with open(temp_path, "w", encoding="utf-8", newline="\n") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                    f.write("\n")
                    f.flush()
                    os.fsync(f.fileno())
                self._replace_atomic_temp_file(temp_path, path)
            finally:
                try:
                    if temp_path.exists():
                        temp_path.unlink()
                except OSError:
                    pass


    def _atomic_write_text_file(self, path: Path, text: str) -> None:
        """Потокобезопасно и атомарно записывает небольшой текстовый отчёт."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._problem_atomic_lock():
            temp_path = path.with_name(
                f".{path.name}.{os.getpid()}.{threading.get_ident()}."
                f"{time.time_ns()}.{random.getrandbits(32):08x}.tmp"
            )
            try:
                with open(temp_path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(text)
                    f.flush()
                    os.fsync(f.fileno())
                self._replace_atomic_temp_file(temp_path, path)
            finally:
                try:
                    if temp_path.exists():
                        temp_path.unlink()
                except OSError:
                    pass


    def _trim_jsonl_log_by_timestamp(self, path: Path, cutoff: datetime) -> int:
        if not path.exists():
            return 0
        try:
            old_lines = path.read_text(encoding='utf-8', errors='replace').splitlines(True)
        except OSError as e:
            self._remember_retention_error(path, e)
            return 0

        keep_unknown = self.is_recent_log_file(path, cutoff)
        new_lines: List[str] = []
        for line in old_lines:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entry = json.loads(stripped)
                entry_dt = self._parse_iso_datetime(entry.get("timestamp"))
            except (TypeError, ValueError, json.JSONDecodeError):
                entry_dt = None

            if (entry_dt and entry_dt >= cutoff) or (entry_dt is None and keep_unknown):
                new_lines.append(line)

        return self._rewrite_text_file_if_changed(path, old_lines, new_lines)


    def _trim_report_log_by_timestamp(self, path: Path, cutoff: datetime) -> int:
        if not path.exists():
            return 0
        try:
            old_lines = path.read_text(encoding='utf-8', errors='replace').splitlines(True)
        except OSError as e:
            self._remember_retention_error(path, e)
            return 0

        blocks: List[List[str]] = []
        current: List[str] = []
        for line in old_lines:
            if line.startswith("=" * 20) and current:
                blocks.append(current)
                current = [line]
            else:
                current.append(line)
        if current:
            blocks.append(current)

        keep_unknown = self.is_recent_log_file(path, cutoff)
        new_lines: List[str] = []
        for block in blocks:
            block_text = ''.join(block)
            if not block_text.strip():
                continue
            match = re.search(r'(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})', block_text)
            block_dt = self._parse_iso_datetime(match.group(1)) if match else None
            if (block_dt and block_dt >= cutoff) or (block_dt is None and keep_unknown):
                new_lines.extend(block)

        return self._rewrite_text_file_if_changed(path, old_lines, new_lines)


    def _trim_debug_log_by_timestamp(self, path: Path, cutoff: datetime) -> int:
        if not path.exists():
            return 0
        try:
            old_lines = path.read_text(encoding='utf-8', errors='replace').splitlines(True)
        except OSError as e:
            self._remember_retention_error(path, e)
            return 0

        new_lines: List[str] = []
        keep_current = self.is_recent_log_file(path, cutoff)
        for line in old_lines:
            match = re.match(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
            if match:
                try:
                    line_dt = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
                    keep_current = line_dt >= cutoff
                except ValueError:
                    keep_current = self.is_recent_log_file(path, cutoff)
            if keep_current:
                new_lines.append(line)

        return self._rewrite_text_file_if_changed(path, old_lines, new_lines)
