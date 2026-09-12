"""Запуск Tkinter-приложения и аварийный лог старта."""

from pathlib import Path
from datetime import datetime
import json
import re
import tkinter as tk
import traceback

from src.core.constants import PROBLEM_LOG_SCHEMA_VERSION, PROBLEM_LOG_TAIL_CHARS
from src.application import VideoDownloader

def _write_startup_emergency_log(exception: BaseException) -> None:
    """Пишет минимальный лог, даже если VideoDownloader не успел создаться."""
    try:
        log_dir = Path(__file__).resolve().parents[1] / "Логи проблем"
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / "emergency_problem_log.jsonl"
        message = re.sub(
            r"(?i)\b(password|passwd|secret|token|api[_-]?key|cookie)"
            r"(\s*[:=]\s*)[^\s,;]+",
            r"\1\2<redacted>",
            str(exception),
        )
        traceback_text = "".join(traceback.format_exception(
            type(exception), exception, exception.__traceback__
        ))[-PROBLEM_LOG_TAIL_CHARS:]
        traceback_text = re.sub(
            r"(?i)\b(password|passwd|secret|token|api[_-]?key|cookie)"
            r"(\s*[:=]\s*)[^\s,;]+",
            r"\1\2<redacted>",
            traceback_text,
        )
        item = {
            "schema_version": PROBLEM_LOG_SCHEMA_VERSION,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "level": "CRITICAL",
            "operation": "application_startup_failed",
            "exception_type": type(exception).__name__,
            "message": message,
            "traceback": traceback_text,
        }
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    except Exception:
        pass


def main() -> None:
    app = None
    try:
        root = tk.Tk()
        app  = VideoDownloader(root)
        root.mainloop()
    except Exception as e:
        if app is not None:
            app._record_uncaught_exception(
                "main", type(e), e, e.__traceback__
            )
        else:
            _write_startup_emergency_log(e)
        print(f"Критическая ошибка: {e}")
        traceback.print_exc()
    finally:
        if app is not None:
            app.finalize_problem_diagnostics(
                "finished", "mainloop_finished"
            )
