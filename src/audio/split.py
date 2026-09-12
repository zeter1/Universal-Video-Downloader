"""Разбиение MP3 в отдельной рабочей папке с сохранением исходника при сбое."""
from pathlib import Path
import shutil

from src.core.constants import AUDIO_DURATION_TOLERANCE_SEC, SPLIT_TIMEOUT_SEC
from src.core.errors import CommandCancelledError
import math
import tempfile


class AudioSplitMixin:
    def split_audio_file(self, temp_file: Path, split_dir: Path,
                         folder_name: str, hours: int) -> None:
        seconds = float(hours) * 3600
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("Интервал разбиения должен быть положительным")
        source_duration = self.get_audio_duration(str(temp_file))
        if not math.isfinite(source_duration) or source_duration <= 0:
            raise RuntimeError("Не удалось определить длительность исходного MP3")
        split_dir.mkdir(parents=True, exist_ok=True)
        work_dir = Path(tempfile.mkdtemp(prefix=".split_", dir=split_dir))
        # Do not create a new segment at EOF; the last MP3 packet may straddle it.
        cuts = [i * seconds for i in range(1, math.ceil(source_duration / seconds))
                if source_duration - i * seconds >= 0.1]
        timing_args = (["-segment_times", ",".join(f"{cut:.6f}" for cut in cuts)]
                       if cuts else ["-segment_time", str(source_duration + 1)])
        cmd = ["ffmpeg", "-nostdin", "-y", "-i", str(temp_file),
               "-map", "0:a:0", "-f", "segment", *timing_args,
               "-c", "copy", "-reset_timestamps", "1", str(work_dir / "part_%03d.mp3")]
        succeeded = False
        try:
            result = self.run_command(cmd, SPLIT_TIMEOUT_SEC, "split_audio_file",
                                      {"temp_file": str(temp_file), "split_dir": str(work_dir),
                                       "hours": hours})
            if result.returncode != 0:
                raise RuntimeError(f"Ошибка FFmpeg при разбиении: {(result.stderr or '')[-500:]}")
            parts = sorted(work_dir.glob("part_*.mp3"))
            durations = [self.get_audio_duration(str(p)) for p in parts]
            if (not parts or any(not math.isfinite(d) or d <= 0 for d in durations)
                    or abs(sum(durations) - source_duration) >= AUDIO_DURATION_TOLERANCE_SEC):
                raise RuntimeError(f"Части MP3 не прошли проверку: исходник={source_duration}, части={durations}")
            if self.cancel_flag.is_set():
                raise CommandCancelledError("Разбиение отменено перед сохранением")
            with self.file_lock:
                targets = [self.make_unique_media_path(split_dir / f"{folder_name}_Часть_{i:03d}.mp3")
                           for i in range(1, len(parts) + 1)]
                published = []
                try:
                    for part, target in zip(parts, targets):
                        part.rename(target)
                        published.append((part, target))
                except OSError:
                    for part, target in reversed(published):
                        try:
                            target.rename(part)
                        except OSError as rollback_error:
                            self.record_problem(
                                "Не удалось вернуть неполный набор частей в рабочую папку",
                                "ERROR", "split_publication_rollback_failed",
                                {"published_part": str(target), "recovery_dir": str(work_dir)},
                                rollback_error,
                            )
                    raise
            succeeded = True
            self.log(f"✅ Создано {len(parts)} частей", "SUCCESS")
        finally:
            if succeeded:
                shutil.rmtree(work_dir, ignore_errors=True)
            else:
                self.log(f"📁 Исходный MP3 и промежуточные части сохранены: {work_dir}", "WARNING")
