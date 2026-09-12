"""ffprobe/метаданные медиа: длительность, потоки, разрешение и битрейт.

Автоматически выделено из прежнего модуля video_download.py без изменения тел методов.
"""

from typing import List
from typing import Tuple
import json
import subprocess


class MediaProbeMixin:
    def get_media_duration(self, media_file: str) -> float:
        try:
            cmd = ["ffprobe", "-v", "error", "-show_entries",
                   "format=duration", "-of", "csv=p=0", media_file]
            r = subprocess.run(
                cmd, capture_output=True, text=True,
                encoding='utf-8', errors='replace',
                timeout=30, creationflags=self.subprocess_flags
            )
            return float(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else 0
        except Exception as e:
            self.record_problem(
                "Не удалось получить длительность медиафайла",
                "WARNING", "ffprobe_duration",
                {"media_file": media_file}, e
            )
            return 0


    def get_audio_duration(self, audio_file: str) -> float:
        return self.get_media_duration(audio_file)


    def has_audio_stream(self, media_file: str) -> bool:
        try:
            cmd = ["ffprobe", "-v", "error", "-select_streams", "a:0",
                   "-show_entries", "stream=index", "-of", "csv=p=0", media_file]
            r = subprocess.run(
                cmd, capture_output=True, text=True,
                encoding='utf-8', errors='replace',
                timeout=30, creationflags=self.subprocess_flags
            )
            return r.returncode == 0 and bool(r.stdout.strip())
        except Exception as e:
            self.record_problem(
                "Не удалось проверить наличие аудиодорожки",
                "WARNING", "ffprobe_audio_stream",
                {"media_file": media_file}, e
            )
            return False


    def has_video_stream(self, media_file: str) -> bool:
        try:
            cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0",
                   "-show_entries", "stream=index", "-of", "csv=p=0", media_file]
            r = subprocess.run(
                cmd, capture_output=True, text=True,
                encoding='utf-8', errors='replace',
                timeout=30, creationflags=self.subprocess_flags
            )
            return r.returncode == 0 and bool(r.stdout.strip())
        except Exception as e:
            self.record_problem(
                "Не удалось проверить наличие видеодорожки",
                "WARNING", "ffprobe_video_stream",
                {"media_file": media_file}, e
            )
            return False


    def get_video_dimensions(self, video_file: str) -> Tuple[int, int]:
        """Возвращает (width, height) через устойчивый JSON-вывод ffprobe."""
        cmd = [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "json", video_file,
        ]
        try:
            r = subprocess.run(
                cmd, capture_output=True, text=True,
                encoding='utf-8', errors='replace',
                timeout=30, creationflags=self.subprocess_flags
            )
            if r.returncode == 0 and r.stdout.strip():
                payload = json.loads(r.stdout)
                streams = payload.get("streams") or []
                if streams:
                    width = int(streams[0].get("width") or 0)
                    height = int(streams[0].get("height") or 0)
                    if width > 0 and height > 0:
                        return width, height
            self.record_problem(
                "ffprobe не смог надёжно определить разрешение видео",
                "WARNING", "ffprobe_video_dimensions_unknown",
                {
                    "video_file": video_file,
                    "returncode": r.returncode,
                    "stdout": (r.stdout or "")[-1000:],
                    "stderr": (r.stderr or "")[-2000:],
                },
                command=cmd, stdout=r.stdout, stderr=r.stderr,
            )
        except Exception as error:
            self.record_problem(
                "Ошибка чтения разрешения видео через ffprobe",
                "WARNING", "ffprobe_video_dimensions",
                {"video_file": video_file}, error, command=cmd,
            )
        return 0, 0


    def get_video_resolution(self, video_file: str) -> str:
        width, height = self.get_video_dimensions(video_file)
        if width > 0 and height > 0:
            return f"{width}x{height} ({height}p)"
        return "Неизвестно"


    def get_video_bitrate(self, video_file: str) -> str:
        try:
            cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0",
                   "-show_entries", "stream=bit_rate", "-of", "csv=p=0", video_file]
            r = subprocess.run(
                cmd, capture_output=True, text=True,
                encoding='utf-8', errors='replace',
                timeout=30, creationflags=self.subprocess_flags
            )
            if r.returncode == 0 and r.stdout.strip():
                bps  = int(r.stdout.strip())
                mbps = bps / 1_000_000
                return f"{mbps:.1f} Mbps" if mbps >= 1 else f"{bps / 1000:.0f} kbps"
            return "Неизвестно"
        except Exception:
            return "Неизвестно"


    def get_audio_info(self, audio_path: str) -> str:
        try:
            cmd = ["ffprobe", "-v", "quiet",
                   "-show_entries", "stream=codec_name,sample_rate,channels,bit_rate",
                   "-of", "csv=p=0", audio_path]
            r = subprocess.run(
                cmd, capture_output=True, text=True,
                encoding='utf-8', errors='replace',
                timeout=10, creationflags=self.subprocess_flags
            )
            return r.stdout.strip() if r.returncode == 0 else "Неизвестно"
        except Exception:
            return "Ошибка"


    def get_total_audio_duration(self, audio_files: List[str]) -> float:
        return sum(self.get_audio_duration(f) for f in audio_files)


    def format_duration(self, seconds: float) -> str:
        # FIX #12: защита от отрицательных значений (ffprobe может вернуть -1)
        seconds = max(0.0, seconds)
        h, remainder = divmod(int(seconds), 3600)
        m, s = divmod(remainder, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"


    def sanitize_filename(self, filename: str) -> str:
        for ch in '<>:"/\\|?*':
            filename = filename.replace(ch, '_')
        return filename[:200]
