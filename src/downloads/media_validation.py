"""Проверка целостности скачанного видео и конвертированного аудио.

Автоматически выделено из прежнего модуля video_download.py без изменения тел методов.
"""

from typing import Optional
from pathlib import Path
from typing import Tuple
import os
import platform
import shutil
import threading

from src.core.constants import AUDIO_DURATION_TOLERANCE_SEC, MIN_AUDIO_FILE_SIZE_BYTES, MIN_VIDEO_FILE_SIZE_BYTES
import math


class MediaValidationMixin:
    def validate_downloaded_video(self, video_path: str, url: str) -> Tuple[bool, str]:
        path = Path(video_path)
        if not path.exists():
            return False, "файл не найден после скачивания"
        if not self.is_supported_video_file(path):
            return False, f"неподдерживаемое расширение: {path.suffix}"

        try:
            size = path.stat().st_size
        except OSError as e:
            self.record_problem(
                "Не удалось проверить размер скачанного видео",
                "ERROR", "validate_downloaded_video",
                {"url": url, "video_path": str(path)}, e
            )
            return False, "не удалось проверить размер файла"

        if size < MIN_VIDEO_FILE_SIZE_BYTES:
            return False, f"файл слишком маленький: {size} байт"

        duration = self.get_media_duration(str(path))
        if not math.isfinite(duration) or duration <= 0:
            return False, "ffprobe не смог прочитать длительность видео"

        if not self.has_video_stream(str(path)):
            return False, "в файле нет видеодорожки или она не читается"

        if not self.has_audio_stream(str(path)):
            self.log(
                f"⚠️ Видео скачано, но аудиодорожка не обнаружена: {path.name}",
                "WARNING"
            )
            self.record_problem(
                "Скачанное видео не содержит аудиодорожку; конвертация в mp3 может быть невозможна",
                "WARNING", "validate_downloaded_video_no_audio",
                {
                    "url": url,
                    "video_path": str(path),
                    "duration": duration,
                    "size_bytes": size,
                }
            )

        return True, f"проверено ffprobe: {self.format_duration(duration)}, {size / (1024 * 1024):.1f} MB"


    def validate_converted_audio(self, video_file: Path,
                                 audio_file: Path) -> Tuple[bool, str, float, float]:
        source_duration = self.get_media_duration(str(video_file))

        if not audio_file.exists():
            return False, "mp3-файл не создан", source_duration, 0

        try:
            if audio_file.stat().st_size < MIN_AUDIO_FILE_SIZE_BYTES:
                return False, "mp3-файл слишком маленький или пустой", source_duration, 0
        except OSError as e:
            self.record_problem(
                "Не удалось проверить размер mp3 после конвертации",
                "ERROR", "validate_converted_audio",
                {"video_file": str(video_file), "audio_file": str(audio_file)}, e
            )
            return False, "не удалось проверить размер mp3", source_duration, 0

        audio_duration = self.get_audio_duration(str(audio_file))

        if not math.isfinite(audio_duration) or audio_duration <= 0:
            return False, "ffprobe не смог прочитать длительность mp3", source_duration, audio_duration

        if not math.isfinite(source_duration) or source_duration <= 0:
            return False, "исходная длительность неизвестна; полнота mp3 не подтверждена", source_duration, audio_duration

        lost_seconds = source_duration - audio_duration
        if abs(lost_seconds) >= AUDIO_DURATION_TOLERANCE_SEC:
            return (
                False,
                f"длительность mp3 отличается от исходного видео на {abs(lost_seconds):.2f} сек",
                source_duration,
                audio_duration,
            )

        return (
            True,
            f"длительность проверена, расхождение {abs(lost_seconds):.2f} сек",
            source_duration,
            audio_duration,
        )


    def _normalized_process_returncode(self, returncode: Optional[int]) -> Optional[int]:
        if returncode is None:
            return None
        value = int(returncode)
        if platform.system() == "Windows" and value >= 2 ** 31:
            return value - 2 ** 32
        return value


    def _diagnostic_stderr_excerpt(self, stderr: Optional[str],
                                   head_chars: int = 240,
                                   tail_chars: int = 2000) -> str:
        text = str(stderr or "")
        if len(text) <= head_chars + tail_chars:
            return text
        return (
            text[:head_chars]
            + f"\n... пропущено {len(text) - head_chars - tail_chars} символов ...\n"
            + text[-tail_chars:]
        )


    def _conversion_temp_path(self, audio_file: Path) -> Path:
        return audio_file.with_name(
            f".{audio_file.stem}.{os.getpid()}.{threading.get_ident()}.converting.mp3"
        )


    def move_audio_to_manual_processing(self, audio_path: Path,
                                        reason: str) -> Optional[Path]:
        """Сохраняет плохой или неполный MP3 вместо его удаления."""
        try:
            if not audio_path.exists() or not audio_path.is_file():
                return None
            target_dir = self.manual_processing_dir / "Неполные MP3"
            target_dir.mkdir(parents=True, exist_ok=True)
            target = self.make_unique_media_path(target_dir / audio_path.name.lstrip("."))
            with self.file_lock:
                shutil.move(str(audio_path), str(target))
            self.log(
                f"📦 Неполный MP3 сохранён для проверки: {target.name}",
                "INFO"
            )
            return target
        except Exception as e:
            self.record_problem(
                "Не удалось сохранить проблемный MP3 для ручной проверки",
                "ERROR", "move_audio_to_manual_processing",
                {
                    "audio_path": str(audio_path),
                    "reason": reason,
                },
                e
            )
            return None
