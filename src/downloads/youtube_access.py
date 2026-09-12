"""YouTube EJS, proxy и проверка сетевого доступа.

Автоматически выделено из прежнего модуля video_download.py без изменения тел методов.
"""

from typing import List
from typing import Optional
from tkinter import messagebox
import shutil
import threading
import urllib.request
import urllib.parse

from src.core.constants import DEFAULT_PROXY_EXAMPLE, YT_DLP_EJS_GITHUB_COMPONENT, YT_DLP_EJS_NPM_COMPONENT
from src.core.session_settings import session_setting


class YouTubeAccessMixin:
    def build_youtube_ejs_args(self, url: str, ejs_mode: str = "github") -> List[str]:
        """
        Аргументы для новых требований YouTube/yt-dlp.

        Свежие логи показали предупреждение:
        "Remote components challenge solver script ... were skipped" и
        "n challenge solving failed". Это значит, что yt-dlp видит Deno/Node,
        но не получает EJS-скрипты для решения YouTube n-challenge. Без этого
        могут пропадать форматы или выдаваться CDN-ссылки, которые потом
        падают на googlevideo.com с timeout/SSL EOF.
        """
        if not self.is_youtube_url(url):
            # Ветка нужна только чтобы статические анализаторы не ругались:
            # метод ниже вызывается только для YouTube. Возвращаем пусто для
            # любых будущих не-YouTube вызовов.
            return []

        args: List[str] = []
        deno_path = shutil.which("deno")
        node_path = shutil.which("node")

        # Deno включён в yt-dlp по умолчанию, но указываем явно: так в логах
        # сразу видно, каким runtime программа пытается пользоваться.
        if deno_path:
            args.extend(["--js-runtimes", "deno"])
        if node_path:
            # Опция может повторяться. Node — запасной runtime, если Deno
            # найден, но не подходит конкретному окружению.
            args.extend(["--js-runtimes", "node"])

        if ejs_mode == "none":
            return args
        if ejs_mode == "npm" and deno_path:
            args.extend(["--remote-components", YT_DLP_EJS_NPM_COMPONENT])
        else:
            args.extend(["--remote-components", YT_DLP_EJS_GITHUB_COMPONENT])
        return args


    def youtube_ejs_status_text(self, ejs_mode: str) -> str:
        deno_found = bool(shutil.which("deno"))
        node_found = bool(shutil.which("node"))
        if ejs_mode == "npm":
            component = YT_DLP_EJS_NPM_COMPONENT
        elif ejs_mode == "none":
            component = "без remote-components"
        else:
            component = YT_DLP_EJS_GITHUB_COMPONENT
        return (
            f"EJS={component}, Deno={'есть' if deno_found else 'нет'}, "
            f"Node={'есть' if node_found else 'нет'}"
        )


    def normalize_proxy_url(self, proxy_url: Optional[str]) -> str:
        """Нормализует proxy URL для yt-dlp.

        Поддерживаются:
        - socks5://127.0.0.1:1080
        - socks5h://127.0.0.1:1080
        - http://127.0.0.1:7890
        - https://host:port
        Если пользователь ввёл только host:port, считаем это socks5://host:port.
        """
        proxy_url = str(proxy_url or "").strip()
        if not proxy_url:
            return ""
        if "://" not in proxy_url:
            proxy_url = "socks5://" + proxy_url
        parsed = urllib.parse.urlsplit(proxy_url)
        if parsed.scheme.lower() not in {"http", "https", "socks4", "socks4a", "socks5", "socks5h"}:
            return ""
        if not parsed.netloc:
            return ""
        return proxy_url


    def get_proxy_url(self) -> str:
        try:
            enabled = bool(session_setting(self, "proxy_enabled")) if hasattr(self, "proxy_enabled") else bool(
                self.settings.get("proxy_enabled", False)
            )
        except Exception:
            enabled = bool(getattr(self, "settings", {}).get("proxy_enabled", False))
        if not enabled:
            return ""
        try:
            raw_proxy = session_setting(self, "proxy_url") if hasattr(self, "proxy_url") else self.settings.get("proxy_url", "")
        except Exception:
            raw_proxy = self.settings.get("proxy_url", "")
        return self.normalize_proxy_url(raw_proxy)


    def build_proxy_args(self) -> List[str]:
        proxy_url = self.get_proxy_url()
        return ["--proxy", proxy_url] if proxy_url else []


    def proxy_status_text(self) -> str:
        proxy_url = self.get_proxy_url()
        if proxy_url:
            return f"proxy={self.mask_proxy_url(proxy_url)}"
        try:
            enabled = bool(session_setting(self, "proxy_enabled")) if hasattr(self, "proxy_enabled") else bool(
                self.settings.get("proxy_enabled", False)
            )
        except Exception:
            enabled = False
        if enabled:
            return "proxy включён, но URL пустой или некорректный"
        return "proxy выключен"


    def on_proxy_settings_change(self, event=None) -> None:
        """Сохраняет proxy-настройки без перезапуска программы."""
        if hasattr(self, "settings"):
            self.settings["proxy_enabled"] = bool(session_setting(self, "proxy_enabled")) if hasattr(self, "proxy_enabled") else False
            self.settings["proxy_url"] = (
                str(session_setting(self, "proxy_url")).strip() if hasattr(self, "proxy_url") else ""
            )
        self.save_current_settings()


    def test_proxy_with_ytdlp(self) -> None:
        """Быстрый тест: сможет ли yt-dlp через proxy получить служебную страницу YouTube."""
        proxy_url = self.get_proxy_url()
        if not proxy_url:
            messagebox.showwarning(
                "Прокси не задан",
                "Включите галочку «Прокси yt-dlp» и укажите адрес, например:\n"
                f"{DEFAULT_PROXY_EXAMPLE}"
            )
            return

        def worker():
            masked_proxy = self.mask_proxy_url(proxy_url)
            self.log(f"🌐 Проверяю proxy для yt-dlp: {masked_proxy}", "INFO")
            cmd = [
                "yt-dlp", "--skip-download", "--no-playlist",
                "--encoding", "utf-8", "--socket-timeout", "20",
                "--proxy", proxy_url, "--get-title", "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            ]
            try:
                r = self.run_command(
                    cmd, 45, "test_ytdlp_proxy",
                    {"proxy_enabled": True, "proxy_url_masked": masked_proxy},
                    allow_cancel=False
                )
                if r.returncode == 0 and (r.stdout or "").strip():
                    self.log("✅ Proxy работает: yt-dlp получил ответ от YouTube", "SUCCESS")
                    self.root.after(0, lambda: messagebox.showinfo("Прокси работает", "✅ yt-dlp получил ответ от YouTube через proxy."))
                else:
                    tail = ((r.stderr or "") + "\n" + (r.stdout or ""))[-1000:]
                    self.log(f"❌ Proxy-тест не прошёл: {tail[:300]}", "ERROR")
                    self.record_problem(
                        "Proxy-тест yt-dlp не прошёл",
                        "ERROR", "test_ytdlp_proxy_failed",
                        {"proxy_enabled": True, "proxy_url_masked": masked_proxy},
                        command=cmd, stdout=r.stdout, stderr=r.stderr
                    )
                    self.root.after(0, lambda: messagebox.showerror("Прокси не работает", f"yt-dlp не смог пройти тест через proxy.\n\n{tail[:700]}"))
            except Exception as e:
                self.log(f"❌ Ошибка proxy-теста: {e}", "ERROR")
                self.record_problem(
                    "Исключение во время proxy-теста yt-dlp",
                    "ERROR", "test_ytdlp_proxy_exception",
                    {"proxy_enabled": True, "proxy_url_masked": masked_proxy},
                    e, command=cmd
                )
                self.root.after(0, lambda: messagebox.showerror("Прокси не работает", str(e)))

        threading.Thread(target=worker, daemon=True).start()
