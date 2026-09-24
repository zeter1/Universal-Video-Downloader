"""Запуск Tkinter-приложения и аварийный лог старта."""

from pathlib import Path
from datetime import datetime
import json
import os
import re
import subprocess
import sys
import tkinter as tk
import traceback

from src.core.constants import PROBLEM_LOG_SCHEMA_VERSION, PROBLEM_LOG_TAIL_CHARS
from src.app.paths import get_program_dir, get_runtime_bin_dir

PROGRAM_DIR = get_program_dir()
RUNTIME_BIN_DIR = get_runtime_bin_dir()
if getattr(sys, "frozen", False):
    os.environ["PATH"] = os.pathsep.join(
        [str(RUNTIME_BIN_DIR), str(PROGRAM_DIR), os.environ.get("PATH", "")]
    )

from src.application import VideoDownloader

def _write_startup_emergency_log(exception: BaseException) -> None:
    """Пишет минимальный лог, даже если VideoDownloader не успел создаться."""
    try:
        log_dir = PROGRAM_DIR / "Логи проблем"
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


def _run_packaged_self_test() -> int:
    """Verify the portable toolchain without opening the GUI or using the network."""
    tools = {
        "yt-dlp.exe": ["--version"],
        "ffmpeg.exe": ["-version"],
        "ffprobe.exe": ["-version"],
        "deno.exe": ["--version"],
    }
    results = {}
    for name, args in tools.items():
        path = RUNTIME_BIN_DIR / name
        item = {"path": str(path), "exists": path.exists(), "returncode": None}
        if path.exists():
            try:
                completed = subprocess.run(
                    [str(path), *args],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=20,
                )
                item["returncode"] = completed.returncode
                item["output"] = (completed.stdout or "")[:500]
            except Exception as exc:
                item["error"] = f"{type(exc).__name__}: {exc}"
        results[name] = item

    payload = {
        "program_dir": str(PROGRAM_DIR),
        "runtime_bin_dir": str(RUNTIME_BIN_DIR),
        "frozen": bool(getattr(sys, "frozen", False)),
        "tools": results,
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    ok = all(item.get("exists") and item.get("returncode") == 0 for item in results.values())
    return 0 if ok else 1


def main() -> None:
    if "--self-test" in sys.argv[1:]:
        raise SystemExit(_run_packaged_self_test())

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
