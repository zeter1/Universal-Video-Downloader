"""Редактирование секретов и безопасная сериализация диагностических данных.

Автоматически выделено из прежнего модуля problem_diagnostics.py без изменения тел методов.
"""

from typing import Dict
from typing import List
from typing import Optional
from pathlib import Path
from datetime import datetime
import hashlib
import os
import re
import subprocess
import urllib.request
import urllib.parse

from src.core.constants import PROBLEM_LOG_HEAD_CHARS, PROBLEM_LOG_MAX_COMMAND_ARGS, PROBLEM_LOG_TAIL_CHARS


class DiagnosticsRedactionMixin:
    def _tail_text(self, text: Optional[str], limit: int = PROBLEM_LOG_TAIL_CHARS) -> str:
        if not text:
            return ""
        text = str(text)
        return text[-limit:]


    def _utf8_subprocess_env(self) -> Dict[str, str]:
        """Согласует кодировку Python-утилит с UTF-8-декодированием pipe."""
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        return env


    def mask_proxy_url(self, proxy_url: Optional[str]) -> str:
        """Маскирует логин/пароль в proxy URL перед записью в логи."""
        if not proxy_url:
            return ""
        proxy_url = str(proxy_url).strip()
        try:
            parsed = urllib.parse.urlsplit(proxy_url)
            if not parsed.netloc or "@" not in parsed.netloc:
                return proxy_url
            userinfo, hostinfo = parsed.netloc.rsplit("@", 1)
            if ":" in userinfo:
                username = userinfo.split(":", 1)[0]
                masked_userinfo = f"{username}:***"
            else:
                masked_userinfo = "***"
            return urllib.parse.urlunsplit(
                (parsed.scheme, f"{masked_userinfo}@{hostinfo}", parsed.path, parsed.query, parsed.fragment)
            )
        except Exception:
            return re.sub(r"://([^/@:]+):[^/@]+@", r"://\1:***@", proxy_url)


    def _redacted_value_marker(self, value) -> str:
        digest = hashlib.sha256(
            str(value).encode("utf-8", errors="replace")
        ).hexdigest()[:12]
        return f"<redacted sha256:{digest}>"


    def _is_sensitive_log_key(self, key: str) -> bool:
        normalized = re.sub(r"[^a-z0-9]+", "_", str(key).casefold()).strip("_")
        if normalized.endswith(("_masked", "_hash", "_sha256", "_md5")):
            return False
        return bool(re.search(
            r"(?:^|_)(?:password|passwd|passphrase|secret|token|"
            r"authorization|auth_header|api_key|apikey|cookie|cookies|"
            r"session_cookie|client_secret)(?:_|$)",
            normalized,
        ))


    def _sanitize_url_for_log(self, url: str) -> str:
        """Сохраняет диагностическую часть URL, но удаляет учётные данные и секреты."""
        raw = str(url)
        try:
            parsed = urllib.parse.urlsplit(raw)
            if parsed.scheme not in {"http", "https", "ftp", "socks4", "socks5"}:
                return raw
            host = parsed.hostname or ""
            if parsed.port:
                host = f"{host}:{parsed.port}"
            if parsed.username or parsed.password:
                host = f"***@{host}"
            query_items = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
            safe_query = []
            for key, value in query_items:
                if self._is_sensitive_log_key(key) or key.casefold() in {
                    "sig", "signature", "key", "credential", "access_token",
                }:
                    safe_query.append((
                        key,
                        value if str(value).startswith("<redacted ")
                        else self._redacted_value_marker(value),
                    ))
                else:
                    safe_query.append((key, value))
            fragment = "<redacted>" if parsed.fragment else ""
            return urllib.parse.urlunsplit(
                (
                    parsed.scheme,
                    host,
                    parsed.path,
                    urllib.parse.urlencode(safe_query, doseq=True),
                    fragment,
                )
            )
        except Exception:
            return self.mask_proxy_url(raw)


    def _sanitize_text_for_log(self, text: str) -> str:
        protected_urls: List[str] = []

        def protect_url(match) -> str:
            protected_urls.append(self._sanitize_url_for_log(match.group(0)))
            return f"__CODEX_SAFE_URL_{len(protected_urls) - 1}__"

        safe = re.sub(
            r"(?i)\b(?:https?|ftp|socks[45])://[^\s\"'<>]+",
            protect_url,
            str(text),
        )
        safe = self.mask_proxy_url(safe)
        safe = re.sub(
            r"(?i)\b(authorization\s*[:=]\s*(?:bearer\s+|basic\s+)?)[^\s,;]+",
            r"\1<redacted>",
            safe,
        )
        safe = re.sub(
            r"(?i)\b(password|passwd|passphrase|secret|token|api[_-]?key|cookie)"
            r"(\s*[:=]\s*)[^\s,;]+",
            r"\1\2<redacted>",
            safe,
        )
        for index, safe_url in enumerate(protected_urls):
            safe = safe.replace(f"__CODEX_SAFE_URL_{index}__", safe_url)
        return safe


    def _redact_for_log(self, value, key_hint: str = "", depth: int = 0):
        """Единая рекурсивная очистка данных перед записью в любой диагностический файл."""
        if self._is_sensitive_log_key(key_hint):
            return self._redacted_value_marker(value)
        if depth > 10:
            return self._sanitize_text_for_log(repr(value)[:1000])
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            return self._sanitize_text_for_log(value)
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, dict):
            return {
                str(key): self._redact_for_log(item, str(key), depth + 1)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple, set)):
            return [
                self._redact_for_log(item, key_hint, depth + 1)
                for item in value
            ]
        return value


    def _sanitize_command_args_for_log(self, command: Optional[List[str]]) -> List[str]:
        """Убирает секреты из команды перед попаданием в JSON/текстовые логи."""
        if not command:
            return []
        args = [str(part) for part in command[:PROBLEM_LOG_MAX_COMMAND_ARGS]]
        sanitized: List[str] = []
        value_mode = ""
        secret_flags = {
            "--password", "--video-password", "--username",
            "--cookies", "--cookies-from-browser",
            "--netrc-location", "--client-certificate-key",
        }
        for part in args:
            if value_mode == "proxy":
                sanitized.append(self.mask_proxy_url(part))
                value_mode = ""
                continue
            if value_mode == "secret":
                sanitized.append(self._redacted_value_marker(part))
                value_mode = ""
                continue
            sanitized.append(self._sanitize_text_for_log(part))
            if part == "--proxy":
                value_mode = "proxy"
            elif part in secret_flags:
                value_mode = "secret"
        if len(command) > PROBLEM_LOG_MAX_COMMAND_ARGS:
            sanitized.append(
                f"<omitted {len(command) - PROBLEM_LOG_MAX_COMMAND_ARGS} args>"
            )
        return sanitized


    def _command_to_string(self, command: Optional[List[str]]) -> str:
        if not command:
            return ""
        try:
            return subprocess.list2cmdline(self._sanitize_command_args_for_log(command))
        except Exception:
            return " ".join(self._sanitize_command_args_for_log(command))


    def _json_safe_value(self, value, depth: int = 0, key_hint: str = ""):
        value = self._redact_for_log(value, key_hint)
        if depth > 7:
            return self._sanitize_text_for_log(repr(value))
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            if len(value) <= PROBLEM_LOG_TAIL_CHARS * 2:
                return value
            return {
                "truncated": True,
                "char_count": len(value),
                "head": value[:PROBLEM_LOG_HEAD_CHARS],
                "tail": value[-PROBLEM_LOG_TAIL_CHARS:],
            }
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, datetime):
            return value.isoformat(timespec="seconds")
        if isinstance(value, BaseException):
            return {
                "type": type(value).__name__,
                "message": self._sanitize_text_for_log(str(value)),
            }
        if isinstance(value, dict):
            return {
                str(k): self._json_safe_value(v, depth + 1, str(k))
                for k, v in value.items()
            }
        if isinstance(value, (list, tuple, set)):
            return [self._json_safe_value(v, depth + 1, key_hint) for v in value]
        return self._sanitize_text_for_log(repr(value))
