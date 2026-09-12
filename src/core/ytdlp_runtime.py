"""Состояние yt-dlp, progress parsing и watchdog медленных загрузок.

Автоматически выделено из прежнего модуля download_runtime.py без изменения тел методов.
"""

from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple
import re
import time

from src.core.constants import PROBLEMATIC_DOWNLOAD_LOW_FRAGMENT_RATIO, PROBLEMATIC_DOWNLOAD_LOW_PROGRESS_PERCENT, PROBLEMATIC_DOWNLOAD_MIN_ELAPSED_SEC, PROBLEMATIC_DOWNLOAD_ZERO_SPEED_SKIP_SEC, YT_DLP_PROGRESS_LOG_INTERVAL_SEC, YT_DLP_RUNTIME_TAIL_LINES


class YtDlpRuntimeMixin:
    def _set_ytdlp_runtime(self, pid: int, updates: Dict) -> None:
        try:
            with self.ytdlp_runtime_lock:
                current = self.ytdlp_runtime.get(pid, {})
                current.update(updates)
                self.ytdlp_runtime[pid] = current
        except Exception:
            pass


    def _remove_ytdlp_runtime(self, pid: int) -> None:
        try:
            with self.ytdlp_runtime_lock:
                self.ytdlp_runtime.pop(pid, None)
        except Exception:
            pass


    def _record_active_ytdlp_snapshot(self, message: str, level: str,
                                      operation: str, command: List[str],
                                      context: Optional[Dict], output_lines: List[str],
                                      exception: Optional[BaseException] = None) -> None:
        context = dict(context or {})
        is_info = str(level).upper() == "INFO"
        if is_info:
            useful_keys = {
                "url", "attempt", "strategy", "quality", "expected_video_id",
                "returncode", "elapsed_sec", "no_output_for_sec", "last_output_line",
                "process_poll", "progress_watchdog", "proxy_enabled", "format_mode",
            }
            context = {key: value for key, value in context.items() if key in useful_keys}
            context["captured_output_line_count"] = len(output_lines)
            if output_lines:
                context["captured_output_tail"] = self._recent_output_tail(output_lines, 5)
        else:
            context.update({
                "captured_output_line_count": len(output_lines),
                "captured_output_tail": self._recent_output_tail(
                    output_lines, YT_DLP_RUNTIME_TAIL_LINES
                ),
                "diagnostic_note": (
                    "Смотрите captured_output_tail, command_details.args, "
                    "target_files_snapshot и timestamps heartbeat."
                ),
            })
            context["target_files_snapshot"] = self._snapshot_download_target_files(
                context.get("target_dir"), context.get("expected_video_id"),
                limit=40
            )
        self.record_problem(
            message, level, operation, context, exception,
            command=None if is_info else command,
            stdout="" if is_info else "\n".join(output_lines[-40:]),
            stderr=""
        )


    def _handle_streamed_yt_dlp_line(self, line: str, context: Optional[Dict],
                                     last_progress_log: float) -> float:
        stripped = line.replace("\r", "").strip()
        if not stripped:
            return last_progress_log
        lower = stripped.lower()
        now = time.time()

        important_markers = (
            "[download] destination:",
            "[download_plan]",
            "[download] downloading item",
            "[download] finished downloading playlist",
            "[merger] merging formats",
            "deleting original file",
            "[extractor]",
            "[youtube]",
            "error:",
            "warning:",
            "requested format is not available",
            "sign in",
            "cookies",
            "403",
            "429",
            "timed out",
            "retrying",
        )
        if "[download]" in lower and "%" in stripped:
            if now - last_progress_log >= YT_DLP_PROGRESS_LOG_INTERVAL_SEC:
                self.log(f"📊 yt-dlp: {stripped[:220]}", "INFO", update_only=True)
                return now
            return last_progress_log
        if any(marker in lower for marker in important_markers):
            self.log(f"🔎 yt-dlp: {stripped[:280]}", "INFO")
        return last_progress_log


    def parse_ytdlp_progress_line(self, line: str) -> Optional[Dict]:
        """Достаёт реальный прогресс из строки yt-dlp.

        Нужен не для красоты логов, а для watchdog: yt-dlp иногда бесконечно
        пишет 0.00B/s ETA Unknown, и простой контроль "есть новый вывод" не
        замечает зависание фрагмента.
        """
        if not line or "[download]" not in line or "%" not in line:
            return None
        info: Dict = {
            "raw_line": line[-400:],
            "percent": None,
            "fragment": None,
            "fragment_total": None,
            "speed_text": "",
            "speed_is_zero_or_unknown": False,
        }
        try:
            percent_match = re.search(r"\[download\]\s+([0-9]+(?:\.[0-9]+)?)%", line)
            if percent_match:
                info["percent"] = float(percent_match.group(1))

            frag_match = re.search(r"\(frag\s+(\d+)\s*/\s*(\d+)\)", line, re.IGNORECASE)
            if frag_match:
                info["fragment"] = int(frag_match.group(1))
                info["fragment_total"] = int(frag_match.group(2))

            speed_match = re.search(r"\bat\s+(.+?)\s+ETA\b", line, re.IGNORECASE)
            if speed_match:
                speed_text = speed_match.group(1).strip()
                info["speed_text"] = speed_text
                speed_l = speed_text.lower().replace(" ", "")
                speed_value = None
                speed_num_match = re.match(r"([0-9]+(?:\.[0-9]+)?)", speed_l)
                if speed_num_match:
                    try:
                        speed_value = float(speed_num_match.group(1))
                    except ValueError:
                        speed_value = None
                info["speed_is_zero_or_unknown"] = (
                    "unknown" in speed_l
                    or speed_l in {"0b/s", "0.00b/s", "0.0b/s"}
                    or speed_value == 0.0
                )

            return info
        except Exception:
            return None


    def should_skip_problematic_slow_download(self, progress_state: Dict, started: float, now: float) -> Tuple[bool, str, Dict]:
        """Определяет, что ролик слишком медленный для текущей сессии.

        Это не обычный timeout. Смысл функции — не держать очередь часами на
        видео, которое через текущий VPN/CDN идёт 5-10% за десятки минут.
        Такой URL лучше записать отдельно и попробовать позже/на другом VPN.
        """
        elapsed = now - started
        if not progress_state.get("seen") or elapsed < PROBLEMATIC_DOWNLOAD_MIN_ELAPSED_SEC:
            return False, "", {}

        percent = progress_state.get("last_percent")
        fragment = progress_state.get("last_fragment")
        fragment_total = progress_state.get("last_fragment_total")
        zero_started = progress_state.get("last_zero_speed_time")
        zero_speed_for = now - float(zero_started) if zero_started else 0.0

        fragment_ratio = None
        if fragment and fragment_total:
            try:
                fragment_ratio = float(fragment) / max(1.0, float(fragment_total))
            except Exception:
                fragment_ratio = None

        low_percent = percent is not None and float(percent) < PROBLEMATIC_DOWNLOAD_LOW_PROGRESS_PERCENT
        low_fragment = (
            fragment_ratio is not None
            and fragment_ratio < PROBLEMATIC_DOWNLOAD_LOW_FRAGMENT_RATIO
        )
        long_zero_speed = zero_speed_for >= PROBLEMATIC_DOWNLOAD_ZERO_SPEED_SKIP_SEC

        if not (low_percent or low_fragment or long_zero_speed):
            return False, "", {}

        diagnostic = {
            "elapsed_sec": round(elapsed, 2),
            "elapsed_min": round(elapsed / 60, 2),
            "last_percent": percent,
            "last_fragment": fragment,
            "last_fragment_total": fragment_total,
            "fragment_ratio": round(fragment_ratio, 4) if fragment_ratio is not None else None,
            "last_speed_text": progress_state.get("last_speed_text"),
            "zero_speed_for_sec": round(zero_speed_for, 2),
            "last_real_progress_age_sec": round(
                now - float(progress_state.get("last_real_progress_time", now)), 2
            ),
            "last_progress_line": progress_state.get("last_progress_line"),
            "thresholds": {
                "min_elapsed_sec": PROBLEMATIC_DOWNLOAD_MIN_ELAPSED_SEC,
                "low_progress_percent": PROBLEMATIC_DOWNLOAD_LOW_PROGRESS_PERCENT,
                "low_fragment_ratio": PROBLEMATIC_DOWNLOAD_LOW_FRAGMENT_RATIO,
                "zero_speed_skip_sec": PROBLEMATIC_DOWNLOAD_ZERO_SPEED_SKIP_SEC,
            },
        }
        reason = (
            "слишком медленное скачивание: "
            f"{percent if percent is not None else '?'}% / "
            f"frag {fragment or '?'} из {fragment_total or '?'} "
            f"за {int(elapsed // 60)} мин, скорость {progress_state.get('last_speed_text') or 'неизвестна'}"
        )
        return True, reason, diagnostic
