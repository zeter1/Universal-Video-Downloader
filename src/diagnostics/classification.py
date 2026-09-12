"""Классификация ошибок, fingerprints, AI hints и correlation IDs.

Автоматически выделено из прежнего модуля problem_diagnostics.py без изменения тел методов.
"""

from typing import Dict
from typing import List
from typing import Optional
import hashlib
import json
import re
import urllib.request
import urllib.parse


class DiagnosticsClassificationMixin:
    def _canonical_problem_error_code(self, text: str) -> str:
        normalized = str(text or "").casefold()
        if any(marker in normalized for marker in (
            "timestamps are unset in a packet",
            "can't write packet with unknown timestamp",
            "cannot write packet with unknown timestamp",
        )):
            return "ffmpeg_timestamp_mux_failure"
        if any(marker in normalized for marker in (
            "error muxing a packet",
            "error submitting a packet to the muxer",
        )):
            return "ffmpeg_mux_failure"
        if any(marker in normalized for marker in (
            "postprocessing: conversion failed",
            "ffmpegpostprocessorerror: conversion failed",
            "conversion failed!",
        )):
            return "yt_dlp_postprocessing_conversion_failed"
        if any(marker in normalized for marker in (
            "sign in", "not a bot", "private video", "members-only",
            "age-restricted", "login required", "cookies",
        )):
            return "youtube_auth_required"
        if "http error 403" in normalized or "forbidden" in normalized:
            return "youtube_http_403"
        if "decryption_failed_or_bad_record_mac" in normalized or "bad record mac" in normalized:
            return "tls_bad_record_mac"
        if any(marker in normalized for marker in (
            "timed out", "timeout", "read timed out", "connect timeout",
        )):
            return "network_timeout"
        if any(marker in normalized for marker in (
            "connection reset", "connectionreseterror", "winerror 10054",
            "remote end closed", "incomplete read",
        )):
            return "network_connection_reset"
        if any(marker in normalized for marker in (
            "ssl:", "unexpected_eof", "eof occurred", "handshake failure",
        )):
            return "network_tls_error"
        if any(marker in normalized for marker in (
            "requested format is not available", "format is not available",
            "only images are available",
        )):
            return "yt_dlp_format_unavailable"
        if any(marker in normalized for marker in (
            "permission", "access is denied", "отказано в доступе",
        )):
            return "filesystem_permission_denied"
        if any(marker in normalized for marker in ("cancel", "отмен")):
            return "user_cancelled"
        return "unknown"


    def _ai_debug_hint_for_problem_log(self, operation: str, message: str,
                                       stderr: Optional[str],
                                       exception: Optional[BaseException],
                                       stdout: Optional[str] = None,
                                       context: Optional[Dict] = None) -> Dict:
        try:
            context_text = json.dumps(context or {}, ensure_ascii=False, default=str)
        except Exception:
            context_text = str(context or {})
        full_text = "\n".join([
            operation or "",
            message or "",
            stdout or "",
            stderr or "",
            str(exception) if exception else "",
            context_text,
        ]).lower()
        terminal_error = self._terminal_error_text_for_problem_log(
            stdout, stderr, exception, context
        )
        error_code = self._canonical_problem_error_code(terminal_error)
        failure_analysis = (context or {}).get("failure_analysis", {}) if isinstance(context, dict) else {}
        analysis_kind = str(failure_analysis.get("kind") or "")
        if analysis_kind in {
            "ffmpeg_timestamp_mux_failure",
            "yt_dlp_postprocessing_conversion_failed",
            "yt_dlp_format_unavailable",
        }:
            # Structured analysis can be more specific than yt-dlp's final one-line
            # terminal message (e.g. generic Conversion failed after timestamp errors).
            error_code = analysis_kind
        # Для категории сначала используем последнюю причину. Полный вывод нужен
        # только как fallback, иначе ранний SSL retry маскирует итоговый HTTP 403.
        text = "\n".join((operation or "", message or "", terminal_error)).lower()
        if error_code == "unknown":
            text = full_text
        category = "unknown"
        next_checks = [
            f"В коде сначала искать operation: {operation}",
            "Сверить context, command_details.args, stderr.tail и app_state.",
            "Если ошибка воспроизводится, сравнить эту запись с предыдущими по problem_fingerprint.",
        ]

        if error_code in {
            "ffmpeg_timestamp_mux_failure",
            "ffmpeg_mux_failure",
            "yt_dlp_postprocessing_conversion_failed",
        }:
            category = "media_validation_or_conversion"
            next_checks.extend([
                "Сначала открыть context.failure_analysis и context.yt_dlp_diagnostic_landmarks.",
                "Проверить DOWNLOAD_PLAN: format_id, protocol, container, height и выбранный format_profile.",
                "Если есть hlsnative/m3u8, unknown timestamp или Error muxing, сравнить с direct HTTP/DASH fallback.",
                "Проверить ffmpeg command line/ошибки merger; не считать полную загрузку 100% успешным видео до merge+ffprobe.",
                "Проверить target_files_after_attempt: остались ли отдельные video/audio части после неудачного merge.",
            ])
        elif any(x in text for x in (
            "ssl:", "unexpected_eof", "eof occurred", "connection reset",
            "connectionreseterror", "connection aborted", "timed out", "timeout",
            "handshake failure", "aria2c exited",
            "удаленный хост принудительно разорвал",
            "удалённый хост принудительно разорвал"
        )):
            category = "network_or_tls"
            next_checks.extend([
                "Проверить сетевые аргументы yt-dlp: chunk size, force-ipv4, retries, socket-timeout.",
                "Проверить, есть ли в command_details.args флаг --proxy и корректный proxy URL.",
                "Если youtube.com отвечает, а googlevideo.com повторно даёт Read timed out/SSL/10054 на разных стратегиях, проверить VPN/proxy или другого провайдера.",
                "Проверить, на какой стратегии yt-dlp произошел сбой и была ли следующая fallback-стратегия.",
            ])
        elif any(x in text for x in ("http error 403", "forbidden")):
            category = "youtube_forbidden_or_client_blocked"
            next_checks.extend([
                "Проверить порядок fallback-стратегий: другой player_client, cookies, внешний downloader.",
                "Если 403 повторяется на всех стратегиях, проверить cookies/авторизацию и обновление yt-dlp.",
            ])
        elif any(x in text for x in (
            "requested format is not available",
            "format is not available",
            "only images are available",
        )):
            category = "yt_dlp_format_selection"
            next_checks.extend([
                "Проверить строку -f и сортировку -S в command_details.args.",
                "Сравнить requested quality с доступными форматами YouTube для этого video_id.",
            ])
        elif any(x in text for x in ("sign in", "private video", "members-only", "age-restricted", "cookies", "not a bot")):
            category = "auth_or_cookies"
            next_checks.extend([
                "Проверить, нужна ли авторизация YouTube или cookies браузера.",
                "Смотреть ошибки --cookies-from-browser и доступность браузера.",
            ])
        elif any(x in text for x in ("ffmpeg", "ffprobe", "moov atom", "invalid data found")):
            category = "media_validation_or_conversion"
            next_checks.extend([
                "Проверить ffmpeg/ffprobe версию и stderr команды конвертации.",
                "Проверить размер и длительность скачанного файла.",
                "Если это yt-dlp, смотреть context.failure_analysis и DOWNLOAD_PLAN вместо общего сообщения Conversion failed.",
            ])
        elif any(x in text for x in ("permission", "access is denied", "не удалось записать", "no such file", "cannot find the path")):
            category = "filesystem_or_permissions"
            next_checks.extend([
                "Проверить paths, свободное место на диске и права записи в целевые папки.",
                "Проверить русские пути и длину итогового имени файла.",
            ])
        elif any(x in text for x in ("отмен", "cancel", "commandcancellederror")):
            category = "user_cancelled"
            next_checks.append("Это может быть штатная отмена; искать проблему только если состояние UI осталось некорректным.")

        return {
            "category": category,
            "error_code": error_code,
            "terminal_error": terminal_error,
            "operation_to_search": operation,
            "source_file": self._entry_source_file(),
            "important_log_files": {
                "jsonl": str(getattr(self, "problem_events_file", "")),
                "legacy_jsonl": str(getattr(self, "problem_log_file", "")),
                "latest_json": str(getattr(self, "problem_latest_json_file", "")),
                "text_report": str(getattr(self, "problem_report_file", "")),
                "session_jsonl": str(getattr(self, "problem_session_log_file", "")),
                "session_latest_json": str(getattr(self, "problem_session_latest_json_file", "")),
                "session_text_report": str(getattr(self, "problem_session_report_file", "")),
                "session_debug_log": str(getattr(self, "problem_debug_session_log_file", "")),
                "debug_log": str(getattr(self, "problem_debug_session_log_file", "")),
            },
            "next_checks": next_checks,
        }


    def _problem_fingerprint(self, operation: str, message: str,
                             context: Dict, exception_type: Optional[str],
                             stderr_tail: str) -> str:
        raw = json.dumps(
            {
                "operation": operation,
                "message": message,
                "context": context,
                "exception_type": exception_type,
                "stderr_tail": stderr_tail[-2000:],
            },
            ensure_ascii=False, sort_keys=True, default=str
        )
        return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]


    def _normalize_error_signature_text(self, value: str) -> str:
        """Убирает переменные значения, чтобы одинаковые сбои имели одну сигнатуру."""
        text = self._sanitize_text_for_log(str(value or "")).casefold()
        text = re.sub(r"\b(?:https?|ftp|socks[45])://\S+", "<url>", text)
        text = re.sub(
            r"(\[youtube\]\s+)[a-z0-9_-]{6,20}",
            r"\1<video_id>",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(
            r"[a-z0-9.-]*googlevideo\.com",
            "<googlevideo_host>",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(r"\b0x[0-9a-f]+\b", "<address>", text)
        text = re.sub(
            r"(?i)(?:[a-z]:\\|\\\\)[^\r\n\"']+",
            "<path>",
            text,
        )
        text = re.sub(r"(?i)\b[0-9a-f]{32,64}\b", "<hash>", text)
        text = re.sub(
            r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
            r"[0-9a-f]{4}-[0-9a-f]{12}\b",
            "<uuid>",
            text,
        )
        text = re.sub(r"\b\d{4}-\d{2}-\d{2}[t _]\d{2}[:-]\d{2}[:-]\d{2}\b", "<time>", text)
        text = re.sub(r"\b\d+(?:[.,]\d+)?\s*(?:ms|sec|secs|seconds|s|min|mins|minutes|%)\b", "<value>", text)
        text = re.sub(
            r"\b(pid|thread|attempt|попытка)\s*[:=#-]?\s*\d+\b",
            r"\1=<n>",
            text,
        )
        text = re.sub(r"\b\d{3,}\b", "<n>", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:4000]


    def _signature_context(self, context: Dict) -> Dict:
        volatile_parts = {
            "timestamp", "updated_at", "started_at", "finished_at",
            "timestamp_unix", "pid", "thread", "thread_id", "sequence",
            "elapsed", "elapsed_sec", "elapsed_min", "duration", "duration_sec",
            "attempt", "attempt_index", "progress", "percent", "speed",
            "eta", "last_output_age_sec", "last_real_progress_age_sec",
            "url", "source_url", "save_path", "target_dir", "path",
            "video_file", "audio_file", "original_video_path",
            "captured_output_tail", "stdout_tail", "stderr_tail",
            "network_failure_count", "slow_network_failure_count",
            "strategy", "quality", "format_mode", "returncode",
        }

        def clean(value, key: str = "", depth: int = 0):
            if depth > 4:
                return None
            normalized_key = str(key).casefold()
            if self._is_sensitive_log_key(normalized_key):
                return None
            if normalized_key in volatile_parts or any(
                part in normalized_key
                for part in ("timestamp", "elapsed", "progress", "duration")
            ):
                return None
            if isinstance(value, dict):
                result = {}
                for child_key, child_value in value.items():
                    cleaned = clean(child_value, str(child_key), depth + 1)
                    if cleaned not in (None, "", [], {}):
                        result[str(child_key)] = cleaned
                return result
            if isinstance(value, (list, tuple, set)):
                result = [clean(item, key, depth + 1) for item in list(value)[:10]]
                return [item for item in result if item not in (None, "", [], {})]
            if isinstance(value, str):
                return self._normalize_error_signature_text(value)
            if value is None or isinstance(value, (bool, int, float)):
                return value
            return self._normalize_error_signature_text(repr(value))

        return clean(context or {}) or {}


    def _stable_error_signature(self, operation: str, message: str,
                                context: Dict, exception_type: Optional[str],
                                stderr_tail: str,
                                category: Optional[str] = None,
                                error_code: Optional[str] = None) -> str:
        normalized = {
            "operation": str(operation or "unknown").casefold(),
            "message": self._normalize_error_signature_text(message),
            "exception_type": str(exception_type or "").casefold(),
            "category": str(category or "").casefold(),
            "error_code": str(error_code or "").casefold(),
            "stderr": self._normalize_error_signature_text(stderr_tail)[-2000:],
            "context": self._signature_context(context),
        }
        digest = hashlib.sha256(
            json.dumps(
                normalized, ensure_ascii=False, sort_keys=True, default=str
            ).encode("utf-8")
        ).hexdigest()[:16]
        operation_slug = re.sub(
            r"[^a-z0-9_]+", "_", str(operation or "unknown").casefold()
        ).strip("_")[:48] or "unknown"
        return f"{operation_slug}.{digest}"


    def _event_id(self, sequence: int) -> str:
        return f"{self.problem_log_session_id}:evt-{sequence:06d}"


    def _incident_id(self, incident_key: str) -> str:
        digest = hashlib.sha256(
            str(incident_key).encode("utf-8", errors="replace")
        ).hexdigest()[:16]
        return f"{self.problem_log_session_id}:inc-{digest}"


    def _correlation_fields(self, context: Dict,
                            command: Optional[List[str]] = None) -> Dict:
        context = context if isinstance(context, dict) else {}
        raw_identity = (
            context.get("task_id")
            or context.get("expected_video_id")
            or context.get("video_id")
            or context.get("url")
            or context.get("source_url")
            or context.get("video_file")
            or context.get("audio_file")
        )
        task_id = context.get("task_id")
        if not task_id and raw_identity:
            identity_for_hash = str(raw_identity)
            if re.match(r"(?i)^https?://", identity_for_hash):
                try:
                    parsed = urllib.parse.urlsplit(identity_for_hash)
                    query = [
                        (key, value)
                        for key, value in urllib.parse.parse_qsl(
                            parsed.query, keep_blank_values=True
                        )
                        if not self._is_sensitive_log_key(key)
                        and key.casefold() not in {
                            "sig", "signature", "key", "credential",
                            "access_token",
                        }
                    ]
                    identity_for_hash = urllib.parse.urlunsplit(
                        (
                            parsed.scheme.casefold(),
                            parsed.netloc.casefold(),
                            parsed.path,
                            urllib.parse.urlencode(query, doseq=True),
                            "",
                        )
                    )
                except Exception:
                    identity_for_hash = self._sanitize_url_for_log(
                        identity_for_hash
                    )
            digest = hashlib.sha256(
                identity_for_hash.encode("utf-8", errors="replace")
            ).hexdigest()[:16]
            task_id = f"task-{digest}"
        attempt = context.get("attempt") or context.get("attempt_index")
        attempt_id = context.get("attempt_id")
        if not attempt_id and attempt is not None:
            attempt_id = f"{task_id or 'session'}:attempt-{attempt}"
        command_id = context.get("command_id")
        if not command_id and command:
            digest = hashlib.sha256(
                json.dumps(
                    self._sanitize_command_args_for_log(command),
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest()[:16]
            command_id = f"cmd-{digest}"
        return {
            "parent_event_id": context.get("parent_event_id"),
            "task_id": task_id,
            "attempt_id": attempt_id,
            "command_id": command_id,
        }
