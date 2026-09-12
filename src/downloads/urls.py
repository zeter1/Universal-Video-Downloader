"""Разбор, нормализация, дедупликация и валидация URL.

Автоматически выделено из прежнего модуля video_download.py без изменения тел методов.
"""

from typing import List
from typing import Optional
from typing import Set
from typing import Tuple
import hashlib
from tkinter import messagebox
import re
import tkinter as tk
import urllib.request
import urllib.parse


class UrlQueueMixin:
    def extract_video_id(self, url: str) -> Optional[str]:
        if not url:
            return None
        patterns = [
            r'(?:youtube\.com/watch\?(?:.*&)?v=|youtu\.be/)([A-Za-z0-9_-]{11})',
            r'youtube\.com/embed/([A-Za-z0-9_-]{11})',
            r'youtube\.com/v/([A-Za-z0-9_-]{11})',
            r'youtube\.com/shorts/([A-Za-z0-9_-]{11})',
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None


    def extract_urls_from_text(self, text: str) -> List[str]:
        """Достаёт URL из человекочитаемых history-файлов и логов."""
        if not text:
            return []
        urls: List[str] = []
        for match in re.findall(r'https?://[^\s<>"\']+', text):
            urls.append(match.rstrip('.,);]'))
        return urls


    def normalize_url_for_history(self, url: str) -> str:
        """
        Канонический ключ истории.
        Для YouTube разные варианты одной ссылки (watch, youtu.be, shorts, лишние
        параметры плейлиста/таймкода) считаются одним и тем же видео.
        """
        url = (url or "").strip()
        video_id = self.extract_video_id(url)
        if video_id:
            return f"https://www.youtube.com/watch?v={video_id}"

        try:
            parsed = urllib.parse.urlparse(url)
            scheme = (parsed.scheme or "https").lower()
            host = (parsed.hostname or "").lower().rstrip(".")
            path = urllib.parse.urlunparse(("", "", parsed.path or "", "", "", ""))

            # Для не-YouTube сохраняем полезные параметры, но убираем мусорные трекеры.
            drop_prefixes = ("utm_",)
            drop_exact = {"fbclid", "gclid", "si", "feature", "pp", "ab_channel"}
            query_pairs = []
            for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True):
                key_l = key.lower()
                if key_l in drop_exact or any(key_l.startswith(p) for p in drop_prefixes):
                    continue
                query_pairs.append((key, value))
            query = urllib.parse.urlencode(query_pairs, doseq=True)
            return urllib.parse.urlunparse((scheme, host, path, "", query, ""))
        except Exception:
            return url


    def url_identity(self, url: str) -> str:
        video_id = self.extract_video_id(url)
        if video_id:
            return f"youtube:{video_id}"
        return f"url:{self.normalize_url_for_history(url)}"


    def deduplicate_urls_by_identity(self, urls: List[str]) -> Tuple[List[str], List[str]]:
        """Убирает повторы внутри очереди, включая разные YouTube-ссылки на один video_id."""
        unique: List[str] = []
        duplicates: List[str] = []
        seen: Set[str] = set()
        for url in urls:
            clean = (url or "").strip()
            if not clean:
                continue
            key = self.url_identity(clean)
            if key in seen:
                duplicates.append(clean)
                continue
            seen.add(key)
            unique.append(clean)
        return unique, duplicates


    def remember_downloaded_url_in_memory(self, url: str) -> None:
        """Добавляет URL в память истории без записи на диск. Вызывать можно при загрузке логов."""
        if not url:
            return
        normalized = self.normalize_url_for_history(url)
        if normalized:
            self.downloaded_url_hashes.add(hashlib.md5(normalized.encode()).hexdigest())
        raw = url.strip()
        if raw and raw != normalized:
            # Совместимость со старыми session-логами, где hash считался от сырой строки.
            self.downloaded_url_hashes.add(hashlib.md5(raw.encode()).hexdigest())
        video_id = self.extract_video_id(url)
        if video_id:
            self.downloaded_video_ids.add(video_id)


    def add_urls_with_check(self, urls: List[str]) -> int:
        already_downloaded: List[str] = []
        new_urls: List[str] = []
        duplicate_in_batch: List[str] = []
        # Дедупликация внутри входного списка: одинаковый YouTube video_id = один ролик,
        # даже если ссылки выглядят по-разному.
        seen_in_batch: Set[str] = set()

        for url in urls:
            url = url.strip()
            if not url:
                continue
            identity = self.url_identity(url)
            if identity in seen_in_batch:
                duplicate_in_batch.append(url)
                continue
            seen_in_batch.add(identity)

            if self.is_url_downloaded(url):
                already_downloaded.append(url)
            else:
                new_urls.append(url)

        if duplicate_in_batch:
            self.log(f"ℹ️ Убрано {len(duplicate_in_batch)} дублей внутри добавляемого списка", "INFO")

        if already_downloaded:
            msg = f"⚠️ {len(already_downloaded)} видео уже были скачаны и НЕ добавлены в очередь:\n\n"
            for u in already_downloaded[:8]:
                msg += f"• {u[:90]}\n"
            if len(already_downloaded) > 8:
                msg += f"... и ещё {len(already_downloaded) - 8} видео\n"
            msg += "\nЕсли хотите скачать их повторно — сначала нажмите «Очистить лог скачанных»."
            messagebox.showwarning("Видео уже скачаны", msg)
            self.log(f"⚠️ Заблокировано {len(already_downloaded)} уже скачанных URL", "WARNING")

        if new_urls:
            current_text = self.url_text.get("1.0", tk.END).strip()
            if current_text:
                self.url_text.insert(tk.END, "\n" + "\n".join(new_urls))
            else:
                self.url_text.insert(tk.END, "\n".join(new_urls))
            self.log(f"✅ Добавлено {len(new_urls)} новых URL", "SUCCESS")

        return len(new_urls)


    def validate_url(self, url: str) -> bool:
        if not url or not url.strip():
            return False
        # FIX #8: только управляющие символы — переносы строк и табы,
        # которые реально не могут быть частью URL и ломают subprocess.
        # & % = ? # — нормальные части YouTube-URL, НЕ блокируем.
        if any(c in url for c in ('\n', '\r', '\t')):
            self.log(f"⚠️ URL содержит управляющие символы: {url[:60]}", "WARNING")
            return False
        try:
            result = urllib.parse.urlparse(url.strip())
            if not all([result.scheme in ('http', 'https'), result.netloc]):
                return False
            allowed_domains = ['youtube.com', 'youtu.be', 'vimeo.com', 'dailymotion.com']
            host = (result.hostname or "").lower().rstrip(".")
            if not any(host == domain or host.endswith("." + domain)
                       for domain in allowed_domains):
                self.log(f"⚠️ Неподдерживаемый домен: {result.netloc}", "WARNING")
                return False
            return True
        except Exception:
            return False
