"""Временные файлы загрузки и их безопасная очистка.

Автоматически выделено из прежнего модуля download_runtime.py без изменения тел методов.
"""

from typing import Dict
from typing import List
from typing import Optional
from pathlib import Path
from typing import Set
from datetime import datetime
import re

from src.core.constants import SUPPORTED_VIDEO_EXTENSIONS, YT_DLP_RUNTIME_TAIL_LINES


class DownloadTempMixin:
    def _recent_output_tail(self, lines: List[str], max_lines: int = YT_DLP_RUNTIME_TAIL_LINES) -> str:
        return "\n".join(lines[-max_lines:])


    def _snapshot_download_target_files(self, target_dir: Optional[str],
                                        expected_video_id: Optional[str] = None,
                                        limit: int = 40) -> List[Dict]:
        """Снимок файлов в папке загрузки: готовые, .part, .temp и совпадающие с video_id."""
        if not target_dir:
            return []
        try:
            directory = Path(target_dir)
            if not directory.exists() or not directory.is_dir():
                return [{"path": str(directory), "exists": directory.exists(), "is_dir": False}]
            candidates = []
            patterns = ["*.part", "*.ytdl", "*.temp.*"]
            patterns.extend([f"*.{ext}" for ext in SUPPORTED_VIDEO_EXTENSIONS])
            if expected_video_id:
                patterns.append(f"*{expected_video_id}*")
            seen = set()
            for pattern in patterns:
                for file_path in directory.glob(pattern):
                    try:
                        key = str(file_path.resolve())
                    except OSError:
                        key = str(file_path)
                    if key in seen or not file_path.exists():
                        continue
                    seen.add(key)
                    try:
                        stat = file_path.stat()
                        candidates.append({
                            "name": file_path.name,
                            "path": str(file_path),
                            "size_bytes": stat.st_size,
                            "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                            "suffix": file_path.suffix,
                        })
                    except OSError as e:
                        candidates.append({"path": str(file_path), "stat_error": str(e)})
            candidates.sort(key=lambda x: x.get("mtime", ""), reverse=True)
            return candidates[:limit]
        except Exception as e:
            return [{"snapshot_error": f"{type(e).__name__}: {e}"}]


    def cleanup_download_temp_files(self, target_dir, expected_video_id: Optional[str] = None,
                                    final_video_path: Optional[str] = None,
                                    reason: str = "") -> List[str]:
        """Удаляет мусор yt-dlp после успешной/неудачной попытки.

        Чистим только временные хвосты: *.part, *.ytdl, *.temp.* и фрагменты
        вида .f137.mp4/.f140.m4a, которые остались от оборванных DASH-попыток.
        Готовый итоговый файл не трогаем.
        """
        deleted: List[str] = []
        try:
            directory = Path(target_dir)
            if not directory.exists() or not directory.is_dir():
                return deleted

            final_path = Path(final_video_path).resolve() if final_video_path else None
            final_stem = Path(final_video_path).stem if final_video_path else ""
            candidate_patterns = ["*.part", "*.ytdl", "*.temp.*", "*.f*.mp4", "*.f*.webm", "*.f*.m4a"]
            seen: Set[str] = set()
            candidates: List[Path] = []
            for pattern in candidate_patterns:
                for item in directory.glob(pattern):
                    try:
                        key = str(item.resolve())
                    except OSError:
                        key = str(item)
                    if key in seen:
                        continue
                    seen.add(key)
                    candidates.append(item)

            for item in candidates:
                try:
                    if not item.exists() or not item.is_file():
                        continue
                    if final_path is not None and item.resolve() == final_path:
                        continue
                    name = item.name
                    stem = item.stem
                    should_delete = False

                    # Явные временные файлы yt-dlp безопасно удалить после завершения ролика.
                    if name.endswith(".part") or name.endswith(".ytdl") or ".temp." in name:
                        should_delete = True

                    # Фрагменты DASH без .part: title.f137.mp4 / title.f140.m4a.
                    if re.search(r"\.f\d+\.(mp4|webm|m4a)$", name, re.IGNORECASE):
                        if reason == "start_new_download_session":
                            should_delete = True
                        elif expected_video_id and expected_video_id in name:
                            should_delete = True
                        elif final_stem and (name.startswith(final_stem + ".f") or stem.startswith(final_stem + ".f")):
                            should_delete = True

                    # Если в старой версии в имени был video_id, чистим только хвосты этого ролика.
                    if expected_video_id and expected_video_id in name and (
                        name.endswith(".part") or name.endswith(".ytdl") or ".temp." in name
                        or re.search(r"\.f\d+\.(mp4|webm|m4a)$", name, re.IGNORECASE)
                    ):
                        should_delete = True

                    if should_delete:
                        size = item.stat().st_size
                        item.unlink()
                        deleted.append(f"{name} ({size} байт)")
                except OSError as e:
                    self.record_problem(
                        "Не удалось удалить временный мусор yt-dlp",
                        "WARNING", "cleanup_download_temp_files",
                        {
                            "file": str(item),
                            "expected_video_id": expected_video_id,
                            "final_video_path": final_video_path,
                            "reason": reason,
                        },
                        e
                    )

            if deleted:
                self.log(f"🧹 Удалён временный мусор yt-dlp: {len(deleted)} файл(ов)", "INFO")
                self.record_problem(
                    "Удалён временный мусор yt-dlp после скачивания",
                    "INFO", "cleanup_download_temp_files",
                    {
                        "target_dir": str(directory),
                        "expected_video_id": expected_video_id,
                        "final_video_path": final_video_path,
                        "reason": reason,
                        "deleted_files": deleted[:20],
                        "deleted_count": len(deleted),
                    }
                )
        except Exception as e:
            self.record_problem(
                "Ошибка при очистке временного мусора yt-dlp",
                "WARNING", "cleanup_download_temp_files",
                {
                    "target_dir": str(target_dir),
                    "expected_video_id": expected_video_id,
                    "final_video_path": final_video_path,
                    "reason": reason,
                },
                e
            )
        return deleted


    def cleanup_stale_download_temp_files(self, target_dir) -> int:
        """Чистит старые хвосты .part/.ytdl/.temp перед новой сессией."""
        deleted = self.cleanup_download_temp_files(
            target_dir, expected_video_id=None, final_video_path=None,
            reason="start_new_download_session"
        )
        return len(deleted)
