"""Диалог отсутствующих зависимостей и повторная проверка.

Автоматически выделено из прежнего модуля ui_components.py без изменения тел методов.
"""

from tkinter import messagebox
import subprocess
import sys
import tkinter as tk
from tkinter import ttk
import webbrowser


class DependencyDialogMixin:
    def show_dependency_warning(self) -> None:
        optional_missing = getattr(self, "optional_missing_deps", [])
        self.log("⚠️ ПРОВЕРЬТЕ ЗАВИСИМОСТИ!", "CRITICAL" if self.missing_deps else "WARNING")
        self.log_retention_status()

        if "JavaScript Runtime (Deno или Node.js)" in optional_missing:
            self.log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "WARNING")
            self.log("📢 Deno/Node.js не найден. Для YouTube сейчас это важно: без JS runtime могут пропадать форматы.", "WARNING")
            self.log("   Базовую загрузку не блокирую, но 1080p может работать хуже.", "WARNING")
            self.log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", "WARNING")
        else:
            self.log("✅ JS runtime найден. Для YouTube включаю EJS remote-components автоматически.", "SUCCESS")
        if "yt-dlp" in self.missing_deps:
            self.log("⚠️ yt-dlp не установлен", "WARNING")
        if "ffmpeg" in self.missing_deps:
            self.log("⚠️ ffmpeg не установлен", "WARNING")
        self.log(
            "📄 Ручная обработка: Обработать вручную — ссылки на несостоявшиеся загрузки "
            "и видео с ошибками конвертации",
            "MANUAL_FOLDER_LINK"
        )
        if self.should_write_problem_logs():
            self.log(
                "📄 Диагностика проблем для ИИ: Логи проблем/Сессии/<запуск>/events.jsonl "
                "+ incidents.jsonl + attachments; начинать с корневого latest_run.json "
                "и README_FOR_CODEX.md",
                "PROBLEM_FOLDER_LINK"
            )
        else:
            self.log(
                "📄 Диагностика проблем для ИИ отключена галочкой «Писать логи проблем»",
                "INFO"
            )

        dep_frame = ttk.Frame(self.root, relief="solid", borderwidth=2, padding="10")
        dep_frame.pack(fill=tk.X, padx=10, pady=10)

        title = ("⚠️ Установите недостающие компоненты:"
                 if self.missing_deps else
                 "⚠️ Рекомендуемые компоненты:")
        ttk.Label(dep_frame, text=title,
                  font=("Arial", 10, "bold"), foreground="red").pack(pady=5)

        btn_frame = ttk.Frame(dep_frame)
        btn_frame.pack(fill=tk.X, pady=5)

        if "yt-dlp" in self.missing_deps:
            ttk.Button(btn_frame, text="Установить yt-dlp",
                       command=self.auto_install_packages).pack(side=tk.LEFT, padx=5)
        if "JavaScript Runtime (Deno или Node.js)" in optional_missing:
            ttk.Button(
                btn_frame, text="Сайт Deno",
                command=lambda: webbrowser.open("https://deno.com/")
            ).pack(side=tk.LEFT, padx=5)
            ttk.Button(
                btn_frame, text="Сайт Node.js",
                command=lambda: webbrowser.open("https://nodejs.org/")
            ).pack(side=tk.LEFT, padx=5)
        if "ffmpeg" in self.missing_deps:
            ttk.Button(
                btn_frame, text="Сайт FFmpeg",
                command=lambda: webbrowser.open("https://ffmpeg.org/download.html")
            ).pack(side=tk.LEFT, padx=5)

        ttk.Button(btn_frame, text="Перепроверить",
                   command=self.recheck_dependencies).pack(side=tk.LEFT, padx=5)


    def recheck_dependencies(self) -> None:
        self.log("🔄 Перепроверка зависимостей...", "INFO")
        try:
            r = subprocess.run(
                ["yt-dlp", "--version"], capture_output=True,
                timeout=5, creationflags=self.subprocess_flags
            )
            if r.returncode == 0:
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp[default]"],
                    capture_output=True, timeout=60,
                    creationflags=self.subprocess_flags
                )
                self.log("✅ yt-dlp обновлён", "SUCCESS")
        except Exception as e:
            self.record_problem("Ошибка при перепроверке/обновлении yt-dlp",
                                "WARNING", "recheck_dependencies", exception=e)

        self.check_dependencies()
        optional_missing = getattr(self, "optional_missing_deps", [])
        if not self.missing_deps and not optional_missing:
            self.log("✅ Все зависимости установлены!", "SUCCESS")
            messagebox.showinfo("Успех", "✅ Все зависимости установлены!")
        elif not self.missing_deps:
            self.log("✅ Обязательные зависимости установлены. Есть только рекомендации.", "SUCCESS")
            messagebox.showinfo(
                "Можно скачивать",
                "✅ Обязательные зависимости установлены.\n\n"
                "Рекомендовано, но не обязательно:\n• " + "\n• ".join(optional_missing)
            )
        else:
            messagebox.showwarning(
                "Внимание",
                "Ещё отсутствуют:\n• " + "\n• ".join(self.missing_deps)
            )


    def auto_install_packages(self) -> None:
        self.log("📦 Установка yt-dlp...", "INFO")
        for p in self.missing_pip_packages:
            try:
                r = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "--upgrade", ("yt-dlp[default]" if p == "yt-dlp" else p)],
                    capture_output=True, timeout=120,
                    creationflags=self.subprocess_flags
                )
                if r.returncode == 0:
                    self.log(f"✅ {p} установлен", "SUCCESS")
                else:
                    self.log(f"❌ Ошибка установки {p}", "ERROR")
            except subprocess.TimeoutExpired:
                self.log(f"❌ Таймаут установки {p}", "ERROR")
        self.recheck_dependencies()
