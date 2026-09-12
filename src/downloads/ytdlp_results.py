"""Разбор результата yt-dlp и поиск реально созданного видеофайла.

Автоматически выделено из прежнего модуля video_download.py без изменения тел методов.
"""

from typing import Dict
from typing import List
from typing import Optional
from pathlib import Path
from typing import Set
import os
import re
import subprocess
import time

from src.core.constants import MIN_VIDEO_FILE_SIZE_BYTES, SUPPORTED_VIDEO_EXTENSIONS


class YtDlpResultsMixin:
    def record_yt_dlp_attempt(self, url: str, attempt: int, strategy_name: str,
                              command: List[str], result: subprocess.CompletedProcess,
                              message: str, level: str = "WARNING",
                              extra_context: Optional[Dict] = None) -> None:
        diagnostic_landmarks = list(
            getattr(result, "diagnostic_landmarks", []) or []
        )
        context = {
            "url": url,
            "attempt": attempt,
            "strategy": strategy_name,
            "returncode": result.returncode,
            "stream_capture_mode": "yt_dlp_stderr_merged_into_stdout",
            "stdout_captured_line_count": len(str(result.stdout or "").splitlines()),
            "yt_dlp_diagnostic_landmarks": diagnostic_landmarks[-40:],
        }
        if extra_context:
            context.update(extra_context)
        self.record_problem(
            message, level, "yt_dlp_download_attempt", context,
            command=command, stdout=result.stdout, stderr=result.stderr
        )


    def parse_yt_dlp_output(self, output: str, url: str, target_dir: Path,
                             files_before: Set[Path],
                             expected_video_id: Optional[str] = None) -> Optional[str]:
        found_path = None

        for line in output.split('\n'):
            stripped = line.strip().strip('"\'')
            if (stripped and not stripped.startswith('[')
                    and Path(stripped).suffix.lower().lstrip(".") in SUPPORTED_VIDEO_EXTENSIONS):
                found_path = self.normalize_downloaded_path(stripped)
            elif '[Merger] Merging formats into' in line:
                path = line.split('[Merger] Merging formats into', 1)[1].strip().strip('"\'')
                path = self.normalize_downloaded_path(path)
                if Path(path).suffix.lower().lstrip(".") in SUPPORTED_VIDEO_EXTENSIONS:
                    found_path = path
            elif '[download] Destination:' in line:
                path = line.split('Destination:')[-1].strip()
                path = self.normalize_downloaded_path(path)
                if Path(path).suffix.lower().lstrip(".") in SUPPORTED_VIDEO_EXTENSIONS:
                    found_path = path
            elif 'has already been downloaded' in line:
                self.log("ℹ️ Файл уже существовал", "INFO")

        eligible = self.snapshot_video_files(target_dir) - files_before
        eligible_resolved = {p.resolve() for p in eligible}
        if (found_path and Path(found_path).resolve() in eligible_resolved
                and Path(found_path).stat().st_size >= MIN_VIDEO_FILE_SIZE_BYTES):
            found_path = self.remove_video_id_suffix_from_filename(
                found_path, expected_video_id
            )
            return found_path

        found_path = self.find_newest_file(
            target_dir, files_before, expected_video_id
        )
        if found_path:
            found_path = self.remove_video_id_suffix_from_filename(
                found_path, expected_video_id
            )
            return found_path

        self.log("❌ Новых видеофайлов не обнаружено", "ERROR")
        return None


    def parse_download_plan_dimensions(self, output: str) -> Optional[tuple[int, int]]:
        """Вернуть (width, height) из последнего [DOWNLOAD_PLAN], если он есть."""
        matches = re.findall(
            r"\[DOWNLOAD_PLAN\].*?height=(\d+).*?width=(\d+)",
            str(output or ""),
        )
        if not matches:
            return None
        height_text, width_text = matches[-1]
        try:
            width = int(width_text)
            height = int(height_text)
        except (TypeError, ValueError):
            return None
        if width <= 0 or height <= 0:
            return None
        return width, height


    def _log_video_info(self, path: str) -> None:
        # FIX #10: проверяем существование файла перед логированием
        if not path or not os.path.exists(path):
            self.log("⚠️ Файл не найден для логирования", "WARNING")
            return
        duration   = self.get_media_duration(path)
        resolution = self.get_video_resolution(path)
        bitrate    = self.get_video_bitrate(path)
        size = os.path.getsize(path) / (1024 * 1024)
        self.log(f"✅ Видео: {os.path.basename(path)}", "SUCCESS")
        self.log(
            f"   📊 {resolution} | {bitrate} | {size:.1f} MB | {self.format_duration(duration)}",
            "INFO"
        )


    def find_newest_file(self, directory: Path, files_before: Set[Path],
                          expected_video_id: Optional[str] = None) -> Optional[str]:
        if not directory.exists():
            return None
        # The job owns one output: ambiguity is a failed identification, not success.
        with self.file_lock:
            try:
                candidates = self.snapshot_video_files(directory) - files_before
                if len(candidates) == 1:
                    return str(next(iter(candidates)))
                if len(candidates) > 1:
                    self.record_problem(
                        "Несколько итоговых файлов; результат скачивания не определён",
                        "WARNING", "find_newest_file",
                        {"directory": str(directory), "candidate_count": len(candidates)},
                    )
                return None
            except OSError as error:
                self.file_logger.error(f"find_newest_file: {error}")
                return None


    def wait_for_file(self, file_path: str, timeout: int = 15) -> bool:
        if not file_path:
            return False
        path = Path(file_path)
        if path not in self.snapshot_video_files(path.parent):
            # Wait only for the exact final path, never for an alternate temp file.
            if ".temp." in path.name or re.search(r"\.f\d+\.", path.name):
                return False
        deadline = time.monotonic() + timeout
        while not self.cancel_flag.is_set():
            try:
                if path.is_file() and path.stat().st_size >= MIN_VIDEO_FILE_SIZE_BYTES:
                    return True
            except OSError:
                pass
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            if self.cancel_flag.wait(min(1.0, remaining)):
                return False
        return False
