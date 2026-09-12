"""Сжатие, архивирование и size-limit problem-log sessions."""

from typing import List
from pathlib import Path
from datetime import datetime
import gzip
import hashlib
import json
import os
import shutil
import zipfile

from src.core.constants import PROBLEM_LOG_COMPRESSION_MIN_BYTES, PROBLEM_LOG_MAX_TOTAL_BYTES, PROBLEM_LOG_MIN_SESSIONS_TO_KEEP, PROBLEM_LOG_SCHEMA_VERSION


class RetentionArchiveMixin:
    def _compress_old_problem_attachments(self, cutoff: datetime) -> int:
        """Сжимает старые полные снимки, оставляя по прежнему пути JSON-указатель."""
        root = getattr(self, "problem_sessions_dir", None)
        if not root or not root.exists():
            return 0
        compressed = 0
        for session_dir in root.iterdir():
            if (
                not session_dir.is_dir()
                or session_dir == getattr(self, "problem_session_dir", None)
                or not self._is_recognized_problem_session_dir(session_dir)
            ):
                continue
            try:
                session_dt = datetime.strptime(
                    session_dir.name[:19], "%Y-%m-%d_%H-%M-%S"
                )
            except ValueError:
                continue
            if session_dt >= cutoff:
                continue
            attachments_dir = session_dir / "attachments"
            if not attachments_dir.is_dir():
                continue
            for path in attachments_dir.rglob("*.json"):
                gzip_path = path.with_suffix(path.suffix + ".gz")
                temp_gzip = gzip_path.with_name(
                    f".{gzip_path.name}.{os.getpid()}.tmp"
                )
                try:
                    raw = path.read_bytes()
                    if len(raw) < PROBLEM_LOG_COMPRESSION_MIN_BYTES:
                        continue
                    try:
                        parsed = json.loads(raw.decode("utf-8"))
                        if isinstance(parsed, dict) and parsed.get("compressed_attachment"):
                            continue
                    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
                        continue
                    gzip_matches = False
                    if gzip_path.exists():
                        try:
                            with gzip.open(gzip_path, "rb") as stream:
                                gzip_matches = (
                                    hashlib.sha256(stream.read()).digest()
                                    == hashlib.sha256(raw).digest()
                                )
                        except OSError:
                            gzip_matches = False
                    if not gzip_matches:
                        with gzip.open(temp_gzip, "wb", compresslevel=6) as stream:
                            stream.write(raw)
                        with gzip.open(temp_gzip, "rb") as stream:
                            restored = stream.read()
                        if hashlib.sha256(restored).digest() != hashlib.sha256(raw).digest():
                            raise OSError("gzip verification failed")
                        os.replace(temp_gzip, gzip_path)
                    stub = {
                        "schema_version": PROBLEM_LOG_SCHEMA_VERSION,
                        "compressed_attachment": True,
                        "compression": "gzip",
                        "gzip_path": str(gzip_path),
                        "relative_gzip_path": str(
                            gzip_path.relative_to(session_dir)
                        ),
                        "original_size_bytes": len(raw),
                        "sha256": hashlib.sha256(raw).hexdigest(),
                        "hint_for_codex": (
                            "Полный JSON сжат. Откройте gzip_path и распакуйте как UTF-8 JSON."
                        ),
                    }
                    self._atomic_write_json_file(path, stub)
                    compressed += 1
                except OSError as e:
                    self._remember_retention_error(path, e)
                finally:
                    try:
                        if temp_gzip.exists():
                            temp_gzip.unlink()
                    except OSError:
                        pass
        return compressed


    def _session_archive_source_files(self, session_dir: Path) -> List[Path]:
        """Возвращает только распознанные файлы, которые разрешено архивировать."""
        names = {
            "ai_problem_log.jsonl",
            "events.jsonl",
            "incidents.jsonl",
            "latest_problem_snapshot.json",
            "problem_report.txt",
            "active_run_state.json",
            "app_debug.log",
        }
        files: List[Path] = []
        for name in names:
            path = session_dir / name
            if path.is_file():
                files.append(path)
        files.extend(
            path for path in session_dir.glob("app_debug.log.*")
            if path.is_file()
        )
        attachments = session_dir / "attachments"
        if attachments.is_dir():
            files.extend(
                path for path in attachments.rglob("*") if path.is_file()
            )
        return sorted(
            {str(path.resolve()): path for path in files}.values(),
            key=lambda path: str(path.relative_to(session_dir)).casefold(),
        )


    def _remove_verified_archive_sources(self, session_dir: Path,
                                         files: List[Path]) -> None:
        session_root = session_dir.resolve()
        for path in files:
            try:
                resolved = path.resolve()
                if session_root not in resolved.parents:
                    raise OSError("archive source escaped session directory")
                path.unlink()
            except OSError as e:
                self._remember_retention_error(path, e)
        attachments = session_dir / "attachments"
        try:
            if attachments.exists():
                attachments_resolved = attachments.resolve()
                if (
                    attachments_resolved.parent == session_root
                    and not any(
                        path.is_file() for path in attachments.rglob("*")
                    )
                ):
                    shutil.rmtree(attachments)
        except OSError as e:
            self._remember_retention_error(attachments, e)


    def _archive_old_problem_sessions(self, cutoff: datetime) -> int:
        """Архивирует старую сессию только после проверки каждого SHA-256."""
        root = getattr(self, "problem_sessions_dir", None)
        if not root or not root.exists():
            return 0
        archived_count = 0
        for session_dir in root.iterdir():
            if (
                not session_dir.is_dir()
                or session_dir == getattr(self, "problem_session_dir", None)
                or not self._is_recognized_problem_session_dir(session_dir)
            ):
                continue
            try:
                session_dt = datetime.strptime(
                    session_dir.name[:19], "%Y-%m-%d_%H-%M-%S"
                )
            except ValueError:
                continue
            if session_dt >= cutoff:
                continue

            sources = self._session_archive_source_files(session_dir)
            archive_path = session_dir / "session_archive.zip"
            archive_manifest_path = session_dir / "archive_manifest.json"
            if not sources:
                continue

            # Если архив уже был успешно создан, проверяем оставшиеся после
            # прерванной очистки исходники и только затем удаляем их.
            if archive_path.is_file() and archive_manifest_path.is_file():
                try:
                    with zipfile.ZipFile(archive_path, "r") as archive:
                        if archive.testzip() is not None:
                            raise OSError("existing session archive has bad CRC")
                        for source in sources:
                            member = str(
                                source.relative_to(session_dir)
                            ).replace("\\", "/")
                            archived_digest = hashlib.sha256()
                            with archive.open(member, "r") as archived_stream:
                                for chunk in iter(
                                    lambda: archived_stream.read(1024 * 1024), b""
                                ):
                                    archived_digest.update(chunk)
                            if archived_digest.hexdigest() != self._file_sha256(
                                source
                            ):
                                raise OSError(
                                    f"existing archive mismatch: {member}"
                                )
                    self._remove_verified_archive_sources(
                        session_dir, sources
                    )
                    archived_count += 1
                except (OSError, KeyError, zipfile.BadZipFile) as e:
                    self._remember_retention_error(archive_path, e)
                continue

            temp_archive = archive_path.with_name(
                f".{archive_path.name}.{os.getpid()}.tmp"
            )
            try:
                file_manifest = []
                with zipfile.ZipFile(
                    temp_archive,
                    "w",
                    compression=zipfile.ZIP_DEFLATED,
                    compresslevel=6,
                ) as archive:
                    for source in sources:
                        member = str(
                            source.relative_to(session_dir)
                        ).replace("\\", "/")
                        archive.write(source, member)
                        file_manifest.append({
                            "path": member,
                            "size_bytes": source.stat().st_size,
                            "sha256": self._file_sha256(source),
                        })

                with zipfile.ZipFile(temp_archive, "r") as archive:
                    bad_member = archive.testzip()
                    if bad_member is not None:
                        raise OSError(
                            f"archive CRC verification failed: {bad_member}"
                        )
                    for item in file_manifest:
                        archived_digest = hashlib.sha256()
                        with archive.open(item["path"], "r") as archived_stream:
                            for chunk in iter(
                                lambda: archived_stream.read(1024 * 1024), b""
                            ):
                                archived_digest.update(chunk)
                        if archived_digest.hexdigest() != item["sha256"]:
                            raise OSError(
                                f"archive SHA-256 verification failed: {item['path']}"
                            )

                os.replace(temp_archive, archive_path)
                archive_manifest = {
                    "schema_version": PROBLEM_LOG_SCHEMA_VERSION,
                    "archive_format": "zip",
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                    "session_dir": str(session_dir),
                    "archive_path": str(archive_path),
                    "archive_sha256": self._file_sha256(archive_path),
                    "file_count": len(file_manifest),
                    "files": file_manifest,
                    "kept_outside_archive": [
                        "session_summary.json",
                        "session_summary.md",
                        "manifest.json",
                        "validation_report.json",
                    ],
                    "hint_for_codex": (
                        "Сводка оставлена рядом. Полные старые события, инциденты, "
                        "вложения и debug-лог находятся в session_archive.zip."
                    ),
                }
                self._atomic_write_json_file(
                    archive_manifest_path, archive_manifest
                )
                self._remove_verified_archive_sources(session_dir, sources)
                archived_count += 1
            except (OSError, KeyError, zipfile.BadZipFile) as e:
                self._remember_retention_error(archive_path, e)
            finally:
                try:
                    if temp_archive.exists():
                        temp_archive.unlink()
                except OSError:
                    pass
        return archived_count


    def _enforce_problem_log_size_limit(self) -> int:
        """Удаляет только самые старые распознанные сессии сверх общего лимита."""
        root = getattr(self, "problem_sessions_dir", None)
        if not root or not root.exists():
            return 0
        sessions = [
            path for path in root.iterdir()
            if path.is_dir() and self._is_recognized_problem_session_dir(path)
        ]
        sessions.sort(
            key=lambda path: path.stat().st_mtime if path.exists() else 0,
            reverse=True,
        )
        sizes = {path: self._problem_session_size(path) for path in sessions}
        total = sum(sizes.values())
        removed = 0
        for path in reversed(sessions[PROBLEM_LOG_MIN_SESSIONS_TO_KEEP:]):
            if total <= PROBLEM_LOG_MAX_TOTAL_BYTES:
                break
            if path == getattr(self, "problem_session_dir", None):
                continue
            try:
                shutil.rmtree(path)
                total -= sizes.get(path, 0)
                removed += 1
            except OSError as e:
                self._remember_retention_error(path, e)
        return removed
