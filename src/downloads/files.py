"""Файлы скачивания, временные каталоги и финализация результата.

Автоматически выделено из прежнего модуля video_download.py без изменения тел методов.
"""

from typing import Optional
from pathlib import Path
from typing import Set
import hashlib
import re
import shutil
import time

from src.core.constants import SUPPORTED_VIDEO_EXTENSIONS
from src.downloads.mp4_output import ensure_mp4_video


class DownloadFilesMixin:
    def is_supported_video_file(self, path: Path) -> bool:
        return path.suffix.lower().lstrip(".") in SUPPORTED_VIDEO_EXTENSIONS


    def snapshot_video_files(self, directory: Path) -> Set[Path]:
        if not directory.exists():
            return set()
        files: Set[Path] = set()
        for ext in SUPPORTED_VIDEO_EXTENSIONS:
            files.update(directory.glob(f"*.{ext}"))
        return {
            f for f in files
            if f.is_file() and not f.is_symlink()
            and f.resolve().parent == directory.resolve()
            and ".temp." not in f.name
            and ".part" not in f.name
            and not re.search(r"\.f\d+\.(mp4|webm|m4a)$", f.name, re.IGNORECASE)
        }


    def normalize_downloaded_path(self, path: str) -> str:
        for ext in SUPPORTED_VIDEO_EXTENSIONS:
            temp_suffix = f".temp.{ext}"
            if path.endswith(temp_suffix):
                return path[:-len(temp_suffix)] + f".{ext}"
        return path


    def make_unique_media_path(self, desired_path: Path) -> Path:
        if not desired_path.exists():
            return desired_path
        for counter in range(2, 1000):
            candidate = desired_path.with_name(
                f"{desired_path.stem} ({counter}){desired_path.suffix}"
            )
            if not candidate.exists():
                return candidate
        raise FileExistsError(f"Нет свободного имени для {desired_path.name}")


    def move_video_to_manual_processing(self, video_path: str, reason: str) -> str:
        """Безопасно переносит готовый проблемный видеофайл для ручной обработки."""
        source = Path(video_path)
        try:
            if not source.exists() or not source.is_file():
                return str(source)

            self.manual_processing_dir.mkdir(parents=True, exist_ok=True)
            if source.parent.resolve() == self.manual_processing_dir.resolve():
                return str(source)

            with self.file_lock:
                target = self.make_unique_media_path(self.manual_processing_dir / source.name)
                shutil.move(str(source), str(target))

            self.log(
                f"📦 Видео перенесено в Обработать вручную: {target.name}",
                "MANUAL_FOLDER_LINK"
            )
            return str(target)
        except Exception as e:
            self.log(
                f"❌ Не удалось перенести видео в «Обработать вручную»: {source.name} — {e}",
                "ERROR"
            )
            self.record_problem(
                "Не удалось перенести проблемный видеофайл для ручной обработки",
                "ERROR", "move_video_to_manual_processing",
                {
                    "source_video": str(source),
                    "manual_processing_dir": str(self.manual_processing_dir),
                    "reason": reason,
                },
                e
            )
            return str(source)


    def remove_video_id_suffix_from_filename(self, path: str,
                                             expected_video_id: Optional[str]) -> str:
        if not path or not expected_video_id:
            return path

        try:
            current_path = Path(path)
            if not current_path.exists() or not self.is_supported_video_file(current_path):
                return path

            id_suffix = f" [{expected_video_id}]"
            if not current_path.stem.endswith(id_suffix):
                return path

            clean_stem = current_path.stem[:-len(id_suffix)].rstrip()
            if not clean_stem:
                return path

            with self.file_lock:
                desired_path = current_path.with_name(f"{clean_stem}{current_path.suffix}")
                target_path = self.make_unique_media_path(desired_path)
                if target_path == current_path:
                    return path
                current_path.rename(target_path)

            self.log(f"📝 Имя файла очищено: {target_path.name}", "INFO")
            return str(target_path)
        except Exception as e:
            self.record_problem(
                "Не удалось убрать YouTube ID из имени готового файла",
                "WARNING", "cleanup_downloaded_filename",
                {
                    "path": path,
                    "expected_video_id": expected_video_id,
                },
                e
            )
            return path


    def make_video_download_temp_dir(self, video_dir: Path, url: str, expected_video_id: Optional[str]) -> Path:
        """Отдельная временная папка для одного ролика.

        Так параллельные загрузки не видят .part/.f137/.f140 друг друга, а
        очистка мусора не может случайно удалить активный файл другого потока.
        """
        key = expected_video_id or hashlib.md5(url.strip().encode('utf-8')).hexdigest()[:12]
        safe_key = re.sub(r"[^A-Za-z0-9_.-]+", "_", key).strip("._") or "video"
        return video_dir / "_temp_downloads" / f"{safe_key}_{time.time_ns()}"


    def cleanup_stale_download_temp_dirs(self, video_dir: Path) -> int:
        """Удаляет старые временные папки перед новой сессией."""
        temp_root = video_dir / "_temp_downloads"
        if not temp_root.exists():
            return 0
        # A failed publication is recovery data, even across the next session.
        count = 0
        if temp_root.is_symlink() or temp_root.is_junction():
            return count
        for directory in temp_root.iterdir():
            if (not directory.is_dir() or directory.is_symlink() or directory.is_junction()
                    or (directory / ".preserve_download").exists()):
                continue
            try:
                shutil.rmtree(directory)
                count += 1
            except OSError as error:
                self.record_problem("Не удалось очистить временную папку", "WARNING",
                                    "cleanup_stale_download_temp_dirs",
                                    {"temp_root": str(directory)}, error)
        return count


    def finalize_downloaded_video_file(self, temp_video_path: str, final_video_dir: Path,
                                       expected_video_id: Optional[str]) -> Optional[str]:
        """Переносит готовое видео из временной папки в основную папку Видео."""
        if not temp_video_path:
            return None
        try:
            src = Path(temp_video_path)
            if not src.exists() or not src.is_file():
                return None
            # Preserve recovery data before any MP4 normalization. If remux or
            # publication fails, the next session must not delete this job dir.
            marker = src.parent / ".preserve_download"
            marker.touch(exist_ok=True)
            mp4_path = ensure_mp4_video(
                self, str(src), expected_video_id=expected_video_id
            )
            if not mp4_path:
                self.record_problem(
                    "Готовое видео не удалось привести к обязательному MP4",
                    "ERROR", "finalize_video_mp4_required",
                    {
                        "temp_video_path": temp_video_path,
                        "final_video_dir": str(final_video_dir),
                        "expected_video_id": expected_video_id,
                    },
                )
                return None
            src = Path(mp4_path)
            final_video_dir.mkdir(parents=True, exist_ok=True)
            clean_name = src.name
            if expected_video_id:
                id_suffix = f" [{expected_video_id}]"
                if src.stem.endswith(id_suffix):
                    clean_name = f"{src.stem[:-len(id_suffix)].rstrip()}{src.suffix}"
            with self.file_lock:
                dst = self.make_unique_media_path(final_video_dir / clean_name)
                shutil.move(str(src), str(dst))
            try:
                marker.unlink(missing_ok=True)
            except OSError:
                pass
            self.log(f"📦 Готовый файл перенесён в Видео: {dst.name}", "INFO")
            return str(dst)
        except Exception as e:
            self.record_problem(
                "Не удалось перенести готовое видео из временной папки",
                "ERROR", "finalize_downloaded_video_file",
                {
                    "temp_video_path": temp_video_path,
                    "final_video_dir": str(final_video_dir),
                    "expected_video_id": expected_video_id,
                },
                e
            )
            return None
