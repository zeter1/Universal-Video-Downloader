"""Запуск subprocess с потоковым выводом, таймаутами и диагностикой.

Автоматически выделено из прежнего модуля download_runtime.py без изменения тел методов.
"""

from typing import Dict
from typing import List
from typing import Optional
from datetime import datetime
import queue
import subprocess
import threading
import time

from src.core.constants import YT_DLP_HEARTBEAT_INTERVAL_SEC, YT_DLP_INACTIVITY_TIMEOUT_SEC, YT_DLP_PROGRESS_MIN_PERCENT_DELTA, YT_DLP_PROGRESS_STALL_TIMEOUT_SEC, YT_DLP_RUNTIME_MAX_CAPTURED_LINES, YT_DLP_ZERO_SPEED_STALL_TIMEOUT_SEC
from src.core.errors import CommandCancelledError, ProblematicDownloadSkipped


_YTDLP_DIAGNOSTIC_LANDMARKS = (
    "[download_plan]",
    "[debug] exe versions:",
    "[debug] invoking ",
    "downloading 1 format",
    "downloading 2 format",
    "[merger] merging formats",
    "ffmpeg command line:",
    "timestamps are unset in a packet",
    "can't write packet with unknown timestamp",
    "cannot write packet with unknown timestamp",
    "error submitting a packet to the muxer",
    "error muxing a packet",
    "conversion failed!",
    "error: postprocessing:",
)


def _is_ytdlp_diagnostic_landmark(line: str) -> bool:
    lower = str(line or "").casefold()
    return any(marker in lower for marker in _YTDLP_DIAGNOSTIC_LANDMARKS)


class SubprocessRunnerMixin:
    def run_command_streamed(self, command: List[str], timeout: int,
                             operation: str, context: Optional[Dict] = None,
                             allow_cancel: bool = True) -> subprocess.CompletedProcess:
        """
        Потоковый запуск yt-dlp.
        Важное отличие от communicate(): stdout/stderr читаются в реальном времени,
        поэтому yt-dlp не может зависнуть из-за заполненной pipe, а в «Логи проблем»
        попадают heartbeat-снимки до того, как пользователь нажмёт отмену.
        """
        started = time.time()
        last_heartbeat = started
        last_output_time = started
        last_progress_log = 0.0
        proc: Optional[subprocess.Popen] = None
        output_lines: List[str] = []
        diagnostic_lines: List[str] = []
        output_queue: queue.Queue = queue.Queue()
        context = dict(context or {})
        progress_state = {
            "seen": False,
            "last_real_progress_time": started,
            "last_percent": None,
            "last_fragment": None,
            "last_fragment_total": None,
            "last_progress_line": "",
            "last_speed_text": "",
            "last_zero_speed_time": None,
        }

        def append_output(raw_line: str) -> None:
            nonlocal last_output_time, last_progress_log
            clean = raw_line.rstrip("\r\n")
            if clean:
                output_lines.append(clean)
                if _is_ytdlp_diagnostic_landmark(clean):
                    diagnostic_lines.append(clean[-1200:])
                    if len(diagnostic_lines) > 80:
                        del diagnostic_lines[:len(diagnostic_lines) - 80]
                if len(output_lines) > YT_DLP_RUNTIME_MAX_CAPTURED_LINES:
                    del output_lines[:len(output_lines) - YT_DLP_RUNTIME_MAX_CAPTURED_LINES]
                last_output_time = time.time()
                progress_info = self.parse_ytdlp_progress_line(clean)
                if progress_info:
                    progress_state["seen"] = True
                    progress_state["last_progress_line"] = clean
                    progress_state["last_speed_text"] = progress_info.get("speed_text", "")
                    pct = progress_info.get("percent")
                    frag = progress_info.get("fragment")
                    frag_total = progress_info.get("fragment_total")
                    zero_or_unknown = bool(progress_info.get("speed_is_zero_or_unknown"))
                    real_progress = False

                    if pct is not None:
                        prev_pct = progress_state.get("last_percent")
                        if prev_pct is None or pct >= float(prev_pct) + YT_DLP_PROGRESS_MIN_PERCENT_DELTA:
                            real_progress = True
                        if prev_pct is None or pct > float(prev_pct):
                            progress_state["last_percent"] = pct

                    if frag is not None:
                        prev_frag = progress_state.get("last_fragment")
                        if prev_frag is None or frag > int(prev_frag):
                            real_progress = True
                        if prev_frag is None or frag > int(prev_frag):
                            progress_state["last_fragment"] = frag
                        progress_state["last_fragment_total"] = frag_total

                    if real_progress or not zero_or_unknown:
                        progress_state["last_real_progress_time"] = time.time()
                        progress_state["last_zero_speed_time"] = None
                    elif zero_or_unknown and progress_state.get("last_zero_speed_time") is None:
                        progress_state["last_zero_speed_time"] = time.time()

                if proc is not None:
                    runtime_update = {
                        "last_output_monotonic": last_output_time,
                        "last_output_line": clean,
                        "recent_output_tail": self._recent_output_tail(output_lines),
                    }
                    if progress_state.get("seen"):
                        runtime_update.update({
                            "last_progress_percent": progress_state.get("last_percent"),
                            "last_progress_fragment": progress_state.get("last_fragment"),
                            "last_progress_fragment_total": progress_state.get("last_fragment_total"),
                            "last_progress_speed": progress_state.get("last_speed_text"),
                            "last_real_progress_age_sec": round(
                                time.time() - float(progress_state.get("last_real_progress_time", time.time())), 1
                            ),
                        })
                    self._set_ytdlp_runtime(proc.pid, runtime_update)
                last_progress_log = self._handle_streamed_yt_dlp_line(
                    clean, context, last_progress_log
                )

        try:
            proc = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding='utf-8',
                errors='replace',
                bufsize=1,
                env=self._utf8_subprocess_env(),
                creationflags=self.subprocess_flags
            )
            self._register_process(proc)
            self._set_ytdlp_runtime(proc.pid, {
                "operation": operation,
                "url": context.get("url"),
                "attempt": context.get("attempt"),
                "strategy": context.get("strategy"),
                "quality": context.get("quality"),
                "target_dir": context.get("target_dir"),
                "expected_video_id": context.get("expected_video_id"),
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "started_monotonic": started,
                "last_output_monotonic": started,
                "last_output_line": None,
                "recent_output_tail": "",
                "command_pretty": self._command_to_string(command),
            })
            self._record_active_ytdlp_snapshot(
                "Запущена попытка yt-dlp: стартовый диагностический снимок",
                "INFO", "yt_dlp_attempt_started", command, context, output_lines
            )

            def reader() -> None:
                try:
                    if proc and proc.stdout:
                        for line in proc.stdout:
                            output_queue.put(line)
                except Exception as e:
                    output_queue.put(f"[reader-error] {type(e).__name__}: {e}\n")

            reader_thread = threading.Thread(target=reader, daemon=True)
            reader_thread.start()

            while True:
                drained = False
                while True:
                    try:
                        line = output_queue.get_nowait()
                    except queue.Empty:
                        break
                    append_output(line)
                    drained = True

                returncode = proc.poll()
                now = time.time()
                if returncode is not None:
                    reader_thread.join(timeout=2)
                    while True:
                        try:
                            line = output_queue.get_nowait()
                        except queue.Empty:
                            break
                        append_output(line)
                    stdout = "\n".join(output_lines)
                    if returncode == 0:
                        self._record_active_ytdlp_snapshot(
                            "yt-dlp успешно завершил попытку",
                            "INFO", "yt_dlp_attempt_succeeded", command,
                            {
                                **context,
                                "returncode": returncode,
                                "elapsed_sec": round(now - started, 2),
                            },
                            output_lines
                        )
                    result = subprocess.CompletedProcess(command, returncode, stdout, "")
                    result.diagnostic_landmarks = list(diagnostic_lines)
                    return result

                if allow_cancel and self.cancel_flag.is_set():
                    self._record_active_ytdlp_snapshot(
                        "yt-dlp остановлен пользователем: снимок перед завершением процесса",
                        "WARNING", "yt_dlp_cancel_before_terminate", command,
                        {**context, "elapsed_sec": round(now - started, 2)}, output_lines
                    )
                    self.terminate_process(proc)
                    reader_thread.join(timeout=3)
                    while True:
                        try:
                            line = output_queue.get_nowait()
                        except queue.Empty:
                            break
                        append_output(line)
                    raise CommandCancelledError("Операция отменена пользователем")

                if now - last_heartbeat >= YT_DLP_HEARTBEAT_INTERVAL_SEC:
                    no_output_for = now - last_output_time
                    last_line = output_lines[-1] if output_lines else "вывода от yt-dlp ещё не было"
                    self.log(
                        f"⏳ yt-dlp работает {int(now - started)} сек | "
                        f"нет нового вывода {int(no_output_for)} сек | {last_line[:120]}",
                        "INFO"
                    )
                    heartbeat_context = {
                        **context,
                        "elapsed_sec": round(now - started, 2),
                        "no_output_for_sec": round(no_output_for, 2),
                        "last_output_line": last_line,
                        "process_poll": returncode,
                    }
                    if progress_state.get("seen"):
                        heartbeat_context["progress_watchdog"] = {
                            "last_percent": progress_state.get("last_percent"),
                            "last_fragment": progress_state.get("last_fragment"),
                            "last_fragment_total": progress_state.get("last_fragment_total"),
                            "last_speed_text": progress_state.get("last_speed_text"),
                            "last_real_progress_age_sec": round(now - float(progress_state.get("last_real_progress_time", now)), 2),
                            "zero_speed_age_sec": (
                                round(now - float(progress_state["last_zero_speed_time"]), 2)
                                if progress_state.get("last_zero_speed_time") else 0
                            ),
                            "last_progress_line": progress_state.get("last_progress_line"),
                        }
                    self._record_active_ytdlp_snapshot(
                        "Heartbeat yt-dlp: процесс ещё работает",
                        "INFO", "yt_dlp_heartbeat", command,
                        heartbeat_context,
                        output_lines
                    )
                    last_heartbeat = now

                if progress_state.get("seen"):
                    should_skip, skip_reason, skip_diag = self.should_skip_problematic_slow_download(
                        progress_state, started, now
                    )
                    if should_skip:
                        skip_context = {
                            **context,
                            "skip_reason": skip_reason,
                            "slow_download_diagnostic": skip_diag,
                        }
                        self._record_active_ytdlp_snapshot(
                            "Видео скачивается слишком медленно — пропускаю его и записываю в отдельный список",
                            "WARNING", "yt_dlp_problematic_slow_download_skip", command,
                            skip_context, output_lines
                        )
                        self.terminate_process(proc)
                        raise ProblematicDownloadSkipped(
                            str(context.get("url") or ""), skip_reason, skip_context
                        )

                    progress_stalled_for = now - float(progress_state.get("last_real_progress_time", now))
                    zero_started = progress_state.get("last_zero_speed_time")
                    zero_speed_for = now - float(zero_started) if zero_started else 0
                    if (
                        progress_stalled_for >= YT_DLP_PROGRESS_STALL_TIMEOUT_SEC
                        or zero_speed_for >= YT_DLP_ZERO_SPEED_STALL_TIMEOUT_SEC
                    ):
                        err = subprocess.TimeoutExpired(command, int(progress_stalled_for),
                                                        output="\n".join(output_lines), stderr="")
                        self._record_active_ytdlp_snapshot(
                            "yt-dlp пишет вывод, но реальный прогресс скачивания застыл — перезапускаю попытку",
                            "ERROR", "yt_dlp_progress_stalled", command,
                            {
                                **context,
                                "elapsed_sec": round(now - started, 2),
                                "progress_stalled_for_sec": round(progress_stalled_for, 2),
                                "zero_speed_for_sec": round(zero_speed_for, 2),
                                "progress_stall_timeout_sec": YT_DLP_PROGRESS_STALL_TIMEOUT_SEC,
                                "zero_speed_stall_timeout_sec": YT_DLP_ZERO_SPEED_STALL_TIMEOUT_SEC,
                                "last_percent": progress_state.get("last_percent"),
                                "last_fragment": progress_state.get("last_fragment"),
                                "last_fragment_total": progress_state.get("last_fragment_total"),
                                "last_speed_text": progress_state.get("last_speed_text"),
                                "last_progress_line": progress_state.get("last_progress_line"),
                            },
                            output_lines, err
                        )
                        self.terminate_process(proc)
                        raise err

                if now - last_output_time >= YT_DLP_INACTIVITY_TIMEOUT_SEC:
                    err = subprocess.TimeoutExpired(command, YT_DLP_INACTIVITY_TIMEOUT_SEC,
                                                    output="\n".join(output_lines), stderr="")
                    self._record_active_ytdlp_snapshot(
                        f"yt-dlp не отдавал новый вывод {YT_DLP_INACTIVITY_TIMEOUT_SEC} сек — считаю попытку зависшей",
                        "ERROR", "yt_dlp_inactivity_timeout", command,
                        {
                            **context,
                            "elapsed_sec": round(now - started, 2),
                            "inactivity_timeout_sec": YT_DLP_INACTIVITY_TIMEOUT_SEC,
                            "last_output_line": output_lines[-1] if output_lines else None,
                        },
                        output_lines, err
                    )
                    self.terminate_process(proc)
                    raise err

                if now - started >= timeout:
                    err = subprocess.TimeoutExpired(command, timeout,
                                                    output="\n".join(output_lines), stderr="")
                    self._record_active_ytdlp_snapshot(
                        f"Тайм-аут команды после {timeout} сек.",
                        "ERROR", operation, command,
                        {**context, "elapsed_sec": round(now - started, 2)},
                        output_lines, err
                    )
                    self.terminate_process(proc)
                    raise err

                time.sleep(0.2 if drained else 0.5)

        except ProblematicDownloadSkipped:
            raise
        except CommandCancelledError as e:
            self.record_problem(
                "Команда остановлена по запросу пользователя",
                "WARNING", operation, context, e,
                command, "\n".join(output_lines), ""
            )
            raise
        except Exception as e:
            if not isinstance(e, subprocess.TimeoutExpired):
                self.record_problem(
                    "Ошибка запуска или выполнения потоковой команды",
                    "ERROR", operation, context, e,
                    command, "\n".join(output_lines), ""
                )
            raise
        finally:
            if proc is not None:
                self._remove_ytdlp_runtime(proc.pid)
                self._unregister_process(proc)


    def run_command(self, command: List[str], timeout: int,
                    operation: str, context: Optional[Dict] = None,
                    allow_cancel: bool = True) -> subprocess.CompletedProcess:
        if operation == "yt_dlp_download":
            return self.run_command_streamed(command, timeout, operation, context, allow_cancel)

        started = time.time()
        last_heartbeat = started
        proc: Optional[subprocess.Popen] = None
        stdout = ""
        stderr = ""
        try:
            proc = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8', errors='replace',
                env=self._utf8_subprocess_env(),
                creationflags=self.subprocess_flags
            )
            self._register_process(proc)

            while True:
                if allow_cancel and self.cancel_flag.is_set():
                    self.terminate_process(proc)
                    try:
                        stdout, stderr = proc.communicate(timeout=5)
                    except Exception:
                        stdout, stderr = "", ""
                    raise CommandCancelledError("Операция отменена пользователем")

                try:
                    stdout, stderr = proc.communicate(timeout=0.5)
                    return subprocess.CompletedProcess(command, proc.returncode, stdout, stderr)
                except subprocess.TimeoutExpired:
                    now = time.time()
                    if operation == "yt_dlp_download" and now - last_heartbeat >= 60:
                        elapsed = int(now - started)
                        strategy = (context or {}).get("strategy", "")
                        self.log(
                            f"⏳ yt-dlp ещё работает: {elapsed} сек"
                            + (f" | {strategy}" if strategy else ""),
                            "INFO"
                        )
                        last_heartbeat = now
                    if now - started >= timeout:
                        self.terminate_process(proc)
                        try:
                            stdout, stderr = proc.communicate(timeout=5)
                        except Exception:
                            stdout, stderr = "", ""
                        err = subprocess.TimeoutExpired(command, timeout, output=stdout, stderr=stderr)
                        self.record_problem(
                            f"Тайм-аут команды после {timeout} сек.",
                            "ERROR", operation, context, err,
                            command, stdout, stderr
                        )
                        raise err
        except CommandCancelledError as e:
            self.record_problem(
                "Команда остановлена по запросу пользователя",
                "WARNING", operation, context, e,
                command, stdout, stderr
            )
            raise
        except Exception as e:
            if not isinstance(e, subprocess.TimeoutExpired):
                self.record_problem(
                    "Ошибка запуска или выполнения команды",
                    "ERROR", operation, context, e,
                    command, stdout, stderr
                )
            raise
        finally:
            if proc is not None:
                self._unregister_process(proc)
