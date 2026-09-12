"""Сохранение и ограничение истории очередей ссылок.

Автоматически выделено из прежнего модуля download_workflow.py без изменения тел методов.
"""

from typing import List
from typing import Optional
from pathlib import Path
from datetime import datetime
import tkinter as tk

from src.core.constants import MAX_DOWNLOAD_LINK_SESSIONS


class DownloadLinkSessionsMixin:
    def get_urls_from_textbox(self) -> List[str]:
        """Возвращает все непустые строки из поля ссылок без изменения порядка."""
        try:
            raw_text = self.url_text.get("1.0", tk.END)
        except Exception as e:
            self.record_problem(
                "Не удалось прочитать поле ссылок для сохранения сессии",
                "WARNING", "get_urls_from_textbox", exception=e
            )
            return []

        urls: List[str] = []
        for line in raw_text.splitlines():
            clean = line.strip()
            if clean:
                urls.append(clean)
        return urls


    def trim_download_link_sessions(self) -> int:
        """
        Оставляет только MAX_DOWNLOAD_LINK_SESSIONS последних файлов очереди ссылок.
        Сортировка идёт по дате из имени файла, а если её нет — по времени изменения.
        """
        if not self.download_links_dir.exists():
            return 0

        files = [
            p for p in self.download_links_dir.glob("links_*.txt")
            if p.is_file()
        ]

        def sort_key(path: Path) -> datetime:
            parsed = self._parse_session_datetime_from_name(path)
            if parsed:
                return parsed
            try:
                return datetime.fromtimestamp(path.stat().st_mtime)
            except OSError:
                return datetime.min

        files.sort(key=sort_key, reverse=True)
        old_files = files[MAX_DOWNLOAD_LINK_SESSIONS:]
        removed = 0

        for old_file in old_files:
            try:
                old_file.unlink()
                removed += 1
            except OSError as e:
                self.record_problem(
                    "Не удалось удалить старый файл сессии ссылок",
                    "WARNING", "trim_download_link_sessions",
                    {"file": str(old_file), "max_sessions": MAX_DOWNLOAD_LINK_SESSIONS},
                    e
                )

        return removed


    def save_download_links_session(self, reason: str = "закрытие программы") -> Optional[Path]:
        """
        Сохраняет текущую очередь ссылок в отдельный .txt-файл.
        Это страховка на случай случайного закрытия программы: введённые ссылки
        можно восстановить из папки «Ссылки на скачивания».
        """
        urls = self.get_urls_from_textbox()
        if not urls:
            return None

        try:
            session_ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            links_file = self.make_unique_session_file(
                self.download_links_dir, "links", session_ts
            )

            with open(links_file, 'w', encoding='utf-8') as f:
                f.write("Ссылки на скачивания\n")
                f.write(f"Сессия: {session_ts}\n")
                f.write(f"Причина сохранения: {reason}\n")
                f.write(f"Количество строк: {len(urls)}\n")
                f.write(
                    f"Хранение: автоматически остаются только последние "
                    f"{MAX_DOWNLOAD_LINK_SESSIONS} сессий\n"
                )
                f.write("=" * 80 + "\n\n")
                for url in urls:
                    f.write(url + "\n")

            removed = self.trim_download_link_sessions()
            self.log(
                f"💾 Ссылки из очереди сохранены: {links_file.name} "
                f"(строк: {len(urls)})",
                "DOWNLOAD_LINKS_FOLDER_LINK"
            )
            if removed:
                self.log(
                    f"🧹 Старые сессии ссылок удалены: {removed}. "
                    f"Оставлено последних {MAX_DOWNLOAD_LINK_SESSIONS}.",
                    "INFO"
                )
            return links_file

        except Exception as e:
            self.log(f"❌ Не удалось сохранить ссылки из очереди: {e}", "ERROR")
            self.record_problem(
                "Не удалось сохранить текущие ссылки на скачивания в отдельный файл",
                "ERROR", "save_download_links_session",
                {
                    "reason": reason,
                    "url_count": len(urls),
                    "download_links_dir": str(self.download_links_dir),
                },
                e
            )
            return None
