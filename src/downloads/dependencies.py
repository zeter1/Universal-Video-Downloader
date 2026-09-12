"""Проверка yt-dlp, ffmpeg и дополнительных зависимостей.

Автоматически выделено из прежнего модуля video_download.py без изменения тел методов.
"""

import subprocess


class DependenciesMixin:
    def check_dependencies(self) -> None:
        self.missing_deps = []
        self.missing_pip_packages = []
        self.optional_missing_deps = []

        try:
            r = subprocess.run(
                ["yt-dlp", "--version"], capture_output=True,
                timeout=5, creationflags=self.subprocess_flags
            )
            if r.returncode != 0:
                self.missing_deps.append("yt-dlp")
                self.missing_pip_packages.append("yt-dlp")
        except (subprocess.SubprocessError, FileNotFoundError):
            self.missing_deps.append("yt-dlp")
            self.missing_pip_packages.append("yt-dlp")

        try:
            r = subprocess.run(
                ["ffmpeg", "-version"], capture_output=True,
                timeout=5, creationflags=self.subprocess_flags
            )
            if r.returncode != 0:
                self.missing_deps.append("ffmpeg")
        except (subprocess.SubprocessError, FileNotFoundError):
            self.missing_deps.append("ffmpeg")

        js_runtime_found = False
        js_runtime_name  = None

        for runtime, args in [("deno", ["deno", "--version"]),
                               ("node", ["node", "--version"])]:
            try:
                r = subprocess.run(
                    args, capture_output=True, timeout=5, text=True,
                    encoding='utf-8', errors='replace',
                    creationflags=self.subprocess_flags
                )
                if r.returncode == 0:
                    js_runtime_found = True
                    js_runtime_name = ("Deno" if runtime == "deno"
                                       else f"Node.js {r.stdout.strip()}")
                    break
            except Exception:
                pass

        self.js_runtime_found = js_runtime_found
        self.js_runtime_name  = js_runtime_name
        if not js_runtime_found:
            self.optional_missing_deps.append("JavaScript Runtime (Deno или Node.js)")
