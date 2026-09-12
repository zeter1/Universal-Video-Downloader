"""Снимки окружения, файлов, процессов, настроек и состояния приложения.

Автоматически выделено из прежнего модуля problem_diagnostics.py без изменения тел методов.
"""

from typing import Dict
from typing import List
from threading import Lock
from typing import Optional
from pathlib import Path
from datetime import datetime
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import urllib.parse

from src.core.constants import PROBLEM_LOG_FORMAT_VERSION, PROBLEM_LOG_HEAD_CHARS, PROBLEM_LOG_RECENT_LINES, PROBLEM_LOG_SCHEMA_VERSION, PROBLEM_LOG_TAIL_CHARS


class DiagnosticsSnapshotsMixin:
    def _stream_snapshot(self, text: Optional[str]) -> Dict:
        raw = "" if text is None else self._sanitize_text_for_log(str(text))
        lines = raw.splitlines()
        return {
            "char_count": len(raw),
            "line_count": len(lines),
            "was_truncated": len(raw) > (PROBLEM_LOG_HEAD_CHARS + PROBLEM_LOG_TAIL_CHARS),
            "head": raw[:PROBLEM_LOG_HEAD_CHARS],
            "tail": self._tail_text(raw),
            "last_lines": lines[-PROBLEM_LOG_RECENT_LINES:],
        }


    def _path_snapshot(self, path) -> Dict:
        info = {"path": None, "exists": False}
        if path is None:
            return info
        try:
            p = Path(path)
            info["path"] = str(p)
            info["exists"] = p.exists()
            info["is_dir"] = p.is_dir() if info["exists"] else False
            info["is_file"] = p.is_file() if info["exists"] else False
            if info["exists"]:
                stat = p.stat()
                info["size_bytes"] = stat.st_size
                info["mtime"] = datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")
            usage_target = p if p.exists() and p.is_dir() else p.parent
            if usage_target and usage_target.exists():
                usage = shutil.disk_usage(str(usage_target))
                info["disk_total_bytes"] = usage.total
                info["disk_free_bytes"] = usage.free
        except Exception as e:
            info["snapshot_error"] = f"{type(e).__name__}: {e}"
        return info


    def _recent_files_snapshot(self, directory, patterns: Optional[List[str]] = None,
                               limit: int = 20) -> List[Dict]:
        try:
            p = Path(directory)
            if not p.exists() or not p.is_dir():
                return []
            patterns = patterns or ["*"]
            files = []
            for pattern in patterns:
                files.extend(p.glob(pattern))
            unique_files = {str(f.resolve()): f for f in files}
            ordered = sorted(
                unique_files.values(),
                key=lambda f: f.stat().st_mtime if f.exists() else 0,
                reverse=True
            )
            result = []
            for f in ordered[:limit]:
                try:
                    stat = f.stat()
                    result.append({
                        "name": f.name,
                        "path": str(f),
                        "is_dir": f.is_dir(),
                        "size_bytes": stat.st_size,
                        "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                    })
                except OSError as e:
                    result.append({"path": str(f), "snapshot_error": str(e)})
            return result
        except Exception as e:
            return [{"snapshot_error": f"{type(e).__name__}: {e}"}]


    def _version_command_snapshot(self, args: List[str]) -> Dict:
        try:
            r = subprocess.run(
                args, capture_output=True, timeout=5, text=True,
                encoding='utf-8', errors='replace',
                creationflags=getattr(self, "subprocess_flags", 0)
            )
            stdout = r.stdout or ""
            stderr = r.stderr or ""
            first_line = ""
            for line in (stdout + "\n" + stderr).splitlines():
                if line.strip():
                    first_line = line.strip()
                    break
            return {
                "args": args,
                "returncode": r.returncode,
                "first_line": first_line,
                "stdout_tail": self._tail_text(stdout, 1200),
                "stderr_tail": self._tail_text(stderr, 1200),
            }
        except Exception as e:
            return {
                "args": args,
                "error_type": type(e).__name__,
                "error": str(e),
            }


    def _environment_snapshot_for_problem_log(self) -> Dict:
        cached = getattr(self, "problem_log_environment_snapshot", None)
        if cached:
            return cached

        path_entries = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
        tool_commands = {
            "yt-dlp": ["yt-dlp", "--version"],
            "aria2c": ["aria2c", "--version"],
            "ffmpeg": ["ffmpeg", "-version"],
            "ffprobe": ["ffprobe", "-version"],
            "python": ["python", "--version"],
            "py": ["py", "--version"],
            "node": ["node", "--version"],
            "deno": ["deno", "--version"],
        }
        tools = {}
        for name, args in tool_commands.items():
            resolved_path = shutil.which(name)
            tools[name] = {
                "resolved_path": resolved_path,
                "version_check": (
                    self._version_command_snapshot(args)
                    if resolved_path else {"skipped": "not found in PATH"}
                ),
            }

        snapshot = {
            "app_name": "Skachat_video_online",
            "diagnostic_schema_version": PROBLEM_LOG_SCHEMA_VERSION,
            "diagnostic_format_version": PROBLEM_LOG_FORMAT_VERSION,
            "pid": os.getpid(),
            "cwd": os.getcwd(),
            "python_executable": sys.executable,
            "python_version": sys.version,
            "platform": platform.platform(),
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "argv": self._sanitize_command_args_for_log(sys.argv),
            "path_env_entries": self._json_safe_value(path_entries),
            "tools": tools,
            "source_file": self._entry_source_file(),
        }
        self.problem_log_environment_snapshot = snapshot
        return snapshot


    def _safe_counter_value(self, attr_name: str) -> Optional[int]:
        try:
            counter = getattr(self, attr_name, None)
            return counter.value() if counter else None
        except Exception:
            return None


    def _safe_var_value(self, attr_name: str, fallback=None):
        try:
            var = getattr(self, attr_name)
            return var.get() if hasattr(var, "get") else var
        except Exception:
            return fallback


    def _settings_snapshot_for_problem_log(self) -> Dict:
        loaded_settings = self._json_safe_value(getattr(self, "settings", {}))
        save_path = self._safe_var_value(
            "save_path", loaded_settings.get("save_path") if isinstance(loaded_settings, dict) else None
        )
        effective = {
            "save_path": save_path,
            "quality": self._safe_var_value(
                "quality", loaded_settings.get("quality") if isinstance(loaded_settings, dict) else None
            ),
            "audio_quality": self._safe_var_value(
                "audio_quality", loaded_settings.get("audio_quality") if isinstance(loaded_settings, dict) else None
            ),
            "concurrent_downloads": self._safe_var_value(
                "concurrent_var",
                loaded_settings.get("concurrent_downloads") if isinstance(loaded_settings, dict) else None
            ),
            "download_subtitles": self._safe_var_value(
                "download_subtitles",
                loaded_settings.get("download_subtitles") if isinstance(loaded_settings, dict) else None
            ),
            "proxy_enabled": self._safe_var_value(
                "proxy_enabled",
                loaded_settings.get("proxy_enabled") if isinstance(loaded_settings, dict) else False
            ),
            "proxy_url_masked": self.mask_proxy_url(
                self._safe_var_value(
                    "proxy_url",
                    loaded_settings.get("proxy_url") if isinstance(loaded_settings, dict) else ""
                )
            ),
            "merge_audio": self._safe_var_value(
                "merge_audio", loaded_settings.get("merge_audio") if isinstance(loaded_settings, dict) else None
            ),
            "split_hours": self._safe_var_value(
                "split_hours", loaded_settings.get("split_hours") if isinstance(loaded_settings, dict) else None
            ),
        }
        dirs = {}
        if save_path:
            save_dir = Path(str(save_path))
            dirs = {
                "save_dir": self._path_snapshot(save_dir),
                "video_dir": self._path_snapshot(save_dir / "Видео"),
                "audio_dir": self._path_snapshot(save_dir / "Аудио"),
            }
        return {
            "loaded_settings": loaded_settings,
            "effective_ui_settings": self._json_safe_value(effective),
            "settings_file": self._path_snapshot(getattr(self, "settings_file", None)),
            "target_directories": dirs,
        }


    def _active_processes_snapshot(self) -> List[Dict]:
        try:
            lock = getattr(self, "process_lock", None)
            if lock:
                with lock:
                    items = list(getattr(self, "active_processes", {}).items())
            else:
                items = list(getattr(self, "active_processes", {}).items())
            result = []
            runtime_by_pid = {}
            try:
                with getattr(self, "ytdlp_runtime_lock", Lock()):
                    runtime_by_pid = dict(getattr(self, "ytdlp_runtime", {}))
            except Exception:
                runtime_by_pid = {}
            now = time.time()
            for pid, proc in items:
                runtime = runtime_by_pid.get(pid, {})
                runtime_snapshot = {}
                if runtime:
                    runtime_snapshot = {
                        "operation": runtime.get("operation"),
                        "url": runtime.get("url"),
                        "attempt": runtime.get("attempt"),
                        "strategy": runtime.get("strategy"),
                        "quality": runtime.get("quality"),
                        "started_at": runtime.get("started_at"),
                        "elapsed_sec": round(now - runtime.get("started_monotonic", now), 1),
                        "last_output_age_sec": round(now - runtime.get("last_output_monotonic", now), 1),
                        "last_output_line": runtime.get("last_output_line"),
                        "recent_output_tail": runtime.get("recent_output_tail"),
                        "last_progress_percent": runtime.get("last_progress_percent"),
                        "last_progress_fragment": runtime.get("last_progress_fragment"),
                        "last_progress_fragment_total": runtime.get("last_progress_fragment_total"),
                        "last_progress_speed": runtime.get("last_progress_speed"),
                        "last_real_progress_age_sec": runtime.get("last_real_progress_age_sec"),
                        "target_dir": runtime.get("target_dir"),
                        "expected_video_id": runtime.get("expected_video_id"),
                    }
                raw_proc_args = getattr(proc, "args", "")
                if isinstance(raw_proc_args, list):
                    logged_proc_args = self._sanitize_command_args_for_log(raw_proc_args)
                    args_pretty = self._command_to_string(raw_proc_args)
                else:
                    logged_proc_args = raw_proc_args
                    args_pretty = str(raw_proc_args)
                result.append({
                    "pid": pid,
                    "poll": proc.poll(),
                    "args": self._json_safe_value(logged_proc_args),
                    "args_pretty": args_pretty,
                    "sensitive_values_masked": logged_proc_args != raw_proc_args,
                    "yt_dlp_runtime": runtime_snapshot,
                })
            return result
        except Exception as e:
            return [{"snapshot_error": f"{type(e).__name__}: {e}"}]


    def _app_state_for_problem_log(self) -> Dict:
        return {
            "is_downloading": getattr(self, "is_downloading", None),
            "cancel_requested": (
                self.cancel_flag.is_set() if hasattr(self, "cancel_flag") else None
            ),
            "active_process_count": len(getattr(self, "active_processes", {})),
            "total_files": self._safe_counter_value("total_files"),
            "completed_files": self._safe_counter_value("completed_files"),
            "max_concurrent": getattr(self, "max_concurrent", None),
            "active_processes": self._active_processes_snapshot(),
        }


    def _session_state_for_problem_log(self) -> Dict:
        try:
            threads = []
            if hasattr(self, "download_threads"):
                for thread in self.download_threads.copy():
                    threads.append({
                        "name": thread.name,
                        "ident": thread.ident,
                        "is_alive": thread.is_alive(),
                    })
            return {
                "app_session_timestamp": getattr(self, "app_session_timestamp", None),
                "problem_session_dir": str(getattr(self, "problem_session_dir", "") or ""),
                "debug_session_log_file": str(getattr(self, "debug_session_log_file", "") or ""),
                "problem_log_session_id": getattr(self, "problem_log_session_id", None),
                "current_session_start": getattr(self, "current_session_start", None),
                "download_start_time": getattr(self, "download_start_time", None),
                "current_session_download_count": getattr(self, "current_session_download_count", None),
                "current_history_session_file": str(getattr(self, "current_history_session_file", "") or ""),
                "current_download_log_session_file": str(
                    getattr(self, "current_download_log_session_file", "") or ""
                ),
                "download_threads": threads,
                "python_threads": [
                    {
                        "name": t.name,
                        "ident": t.ident,
                        "daemon": t.daemon,
                        "is_alive": t.is_alive(),
                    }
                    for t in threading.enumerate()
                ],
                "downloaded_url_hash_count": len(getattr(self, "downloaded_url_hashes", set())),
                "downloaded_video_id_count": len(getattr(self, "downloaded_video_ids", set())),
            }
        except Exception as e:
            return {"snapshot_error": f"{type(e).__name__}: {e}"}


    def _paths_snapshot_for_problem_log(self) -> Dict:
        return {
            "app_dir": self._path_snapshot(getattr(self, "app_dir", None)),
            "settings_file": self._path_snapshot(getattr(self, "settings_file", None)),
            "log_dir": self._path_snapshot(getattr(self, "log_dir", None)),
            "problem_log_dir": self._path_snapshot(getattr(self, "problem_log_dir", None)),
            "problem_log_file": self._path_snapshot(getattr(self, "problem_log_file", None)),
            "problem_events_file": self._path_snapshot(
                getattr(self, "problem_events_file", None)
            ),
            "problem_report_file": self._path_snapshot(getattr(self, "problem_report_file", None)),
            "problem_latest_json_file": self._path_snapshot(
                getattr(self, "problem_latest_json_file", None)
            ),
            "problem_session_dir": self._path_snapshot(getattr(self, "problem_session_dir", None)),
            "problem_session_log_file": self._path_snapshot(
                getattr(self, "problem_session_log_file", None)
            ),
            "problem_session_report_file": self._path_snapshot(
                getattr(self, "problem_session_report_file", None)
            ),
            "problem_session_latest_json_file": self._path_snapshot(
                getattr(self, "problem_session_latest_json_file", None)
            ),
            "debug_session_log_file": self._path_snapshot(
                getattr(self, "debug_session_log_file", None)
            ),
            "download_links_dir": self._path_snapshot(getattr(self, "download_links_dir", None)),
            "manual_processing_dir": self._path_snapshot(
                getattr(self, "manual_processing_dir", None)
            ),
            "recent_manual_processing_files": self._recent_files_snapshot(
                getattr(self, "manual_processing_dir", None),
                limit=15
            ),
            "recent_problem_files": self._recent_files_snapshot(
                getattr(self, "problem_log_dir", None), limit=15
            ),
            "recent_download_link_files": self._recent_files_snapshot(
                getattr(self, "download_links_dir", None), ["links_*.txt"], limit=10
            ),
        }


    def _command_snapshot(self, command: Optional[List[str]]) -> Dict:
        if not command:
            return {}
        raw_args = [str(part) for part in command]
        args = self._sanitize_command_args_for_log(raw_args)
        executable = raw_args[0] if raw_args else ""
        return {
            "args": args,
            "pretty": self._command_to_string(raw_args),
            "executable": self._sanitize_text_for_log(executable),
            "resolved_executable": shutil.which(executable) if executable else None,
            "working_directory": os.getcwd(),
            "sensitive_values_masked": args != raw_args,
        }


    def _url_diagnostics_for_problem_log(self, context: Dict) -> Optional[Dict]:
        url = context.get("url") or context.get("source_url")
        if not url:
            return None
        try:
            raw_url = str(url)
            safe_url = self._sanitize_url_for_log(raw_url)
            parsed = urllib.parse.urlparse(safe_url)
            normalized = self._sanitize_url_for_log(
                self.normalize_url_for_history(raw_url)
            )
            return {
                "url": safe_url,
                "scheme": parsed.scheme,
                "netloc": parsed.netloc,
                "hostname": parsed.hostname,
                "path": parsed.path,
                "query_keys": sorted(urllib.parse.parse_qs(parsed.query).keys()),
                "video_id": self.extract_video_id(str(url)),
                "normalized_url": normalized,
                "normalized_url_md5": hashlib.md5(normalized.encode()).hexdigest(),
                "raw_url_md5": hashlib.md5(raw_url.strip().encode()).hexdigest(),
            }
        except Exception as e:
            return {
                "url": self._sanitize_url_for_log(str(url)),
                "snapshot_error": f"{type(e).__name__}: {e}",
            }


    def _source_location_for_problem_log(self) -> Dict:
        try:
            frames = traceback.extract_stack()
            useful = []
            for frame in frames:
                if frame.name in ("_source_location_for_problem_log", "record_problem"):
                    continue
                useful.append({
                    "file": frame.filename,
                    "line": frame.lineno,
                    "function": frame.name,
                    "code": frame.line,
                })
            return {
                "caller": useful[-1] if useful else None,
                "stack_tail": useful[-15:],
            }
        except Exception as e:
            return {"snapshot_error": f"{type(e).__name__}: {e}"}


    def _terminal_error_text_for_problem_log(
        self,
        stdout: Optional[str],
        stderr: Optional[str],
        exception: Optional[BaseException],
        context: Optional[Dict],
    ) -> str:
        """Выбирает последнюю причину сбоя, не смешивая её с ранними retry."""
        context = context if isinstance(context, dict) else {}
        marked_candidates: List[str] = []
        fallback_candidates: List[str] = []
        for value in (
            str(exception) if exception else "",
            stdout,
            stderr,
            context.get("last_error"),
        ):
            if not value:
                continue
            lines = [line.strip() for line in str(value).splitlines() if line.strip()]
            marked = [
                line for line in lines
                if any(marker in line.casefold() for marker in (
                    "error:", "got error:", "timed out", "timeout",
                    "connection reset", "ssl:", "forbidden", "sign in",
                    "not a bot", "requested format is not available",
                ))
            ]
            if marked:
                marked_candidates.append(marked[-1])
            elif lines:
                fallback_candidates.append(lines[-1])
        selected = (
            marked_candidates[-1] if marked_candidates
            else (fallback_candidates[-1] if fallback_candidates else "")
        )
        return self._sanitize_text_for_log(selected)[-2000:]
