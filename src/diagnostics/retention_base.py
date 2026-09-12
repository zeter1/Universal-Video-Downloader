"""Базовые даты/удаление и распознавание retention paths."""

from typing import List
from typing import Optional
from pathlib import Path
from datetime import datetime
import re
import shutil
from datetime import timedelta

from src.core.constants import DOWNLOAD_HISTORY_RETENTION_DAYS


class RetentionBaseMixin:
    def retention_cutoff(self, days: int = DOWNLOAD_HISTORY_RETENTION_DAYS) -> datetime:
        return datetime.now() - timedelta(days=max(1, int(days)))


    def _remember_retention_error(self, path: Path, error: Exception) -> None:
        errors = getattr(self, "retention_cleanup_errors", None)
        if errors is not None:
            errors.append(f"{path}: {error}")


    def _parse_iso_datetime(self, value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)
            return dt
        except (TypeError, ValueError):
            return None


    def _parse_session_datetime_from_name(self, path: Path) -> Optional[datetime]:
        match = re.search(
            r'(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-(\d{2})',
            path.name
        )
        if not match:
            return None
        try:
            return datetime.strptime(
                f"{match.group(1)} {match.group(2)}:{match.group(3)}:{match.group(4)}",
                "%Y-%m-%d %H:%M:%S"
            )
        except ValueError:
            return None


    def _path_datetime_for_retention(self, path: Path) -> Optional[datetime]:
        parsed = self._parse_session_datetime_from_name(path)
        if parsed:
            return parsed
        try:
            return datetime.fromtimestamp(path.stat().st_mtime)
        except OSError:
            return None


    def is_recent_log_file(self, path: Path, cutoff: Optional[datetime] = None) -> bool:
        cutoff = cutoff or self.retention_cutoff()
        file_dt = self._path_datetime_for_retention(path)
        return file_dt is None or file_dt >= cutoff


    def _delete_old_files(self, directory: Path, patterns: List[str],
                          cutoff: datetime) -> int:
        cleaned = 0
        if not directory.exists():
            return cleaned
        for pattern in patterns:
            for path in directory.glob(pattern):
                if not path.is_file() or self.is_recent_log_file(path, cutoff):
                    continue
                try:
                    path.unlink()
                    cleaned += 1
                except OSError as e:
                    self._remember_retention_error(path, e)
        return cleaned


    def _delete_old_dirs(self, directory: Path, cutoff: datetime) -> int:
        cleaned = 0
        if not directory.exists():
            return cleaned
        for path in directory.iterdir():
            if (
                not path.is_dir()
                or not self._is_recognized_problem_session_dir(path)
                or self.is_recent_log_file(path, cutoff)
            ):
                continue
            try:
                shutil.rmtree(path)
                cleaned += 1
            except OSError as e:
                self._remember_retention_error(path, e)
        return cleaned


    def _is_recognized_problem_session_dir(self, path: Path) -> bool:
        """Не позволяет очистке затронуть посторонние каталоги пользователя."""
        try:
            session_root = self.problem_sessions_dir.resolve()
            resolved = path.resolve()
            if resolved.parent != session_root:
                return False
            # Новые сессии содержат PID, старые имена тоже распознаются.
            return bool(
                re.fullmatch(
                    r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}"
                    r"(?:_pid\d+(?:_\d+)?|_\d+)?",
                    path.name,
                )
            )
        except Exception:
            return False


    def _problem_session_size(self, path: Path) -> int:
        total = 0
        try:
            for item in path.rglob("*"):
                if item.is_file():
                    try:
                        total += item.stat().st_size
                    except OSError:
                        continue
        except OSError:
            return total
        return total
