"""История уже скачанных URL/video_id и защита от повторов.

Автоматически выделено из прежнего модуля video_download.py без изменения тел методов.
"""

from typing import Optional
import hashlib
import urllib.request
import urllib.parse


class DownloadHistoryMixin:
    def load_download_log(self) -> None:
        """Загрузка лога скачанных видео за срок хранения."""
        cutoff = self.retention_cutoff()

        # Основной машинный лог: Логи скачивания/Сессии скачанных/downloaded_log_*.txt
        try:
            if self.downloaded_sessions_dir.exists():
                for session_file in sorted(self.downloaded_sessions_dir.glob("downloaded_log_*.txt")):
                    if not self.is_recent_log_file(session_file, cutoff):
                        continue
                    with open(session_file, 'r', encoding='utf-8') as f:
                        for line in f:
                            line = line.strip()
                            if not line or line.startswith("#"):
                                continue
                            parts = line.split('\t')
                            if parts:
                                url_hash = parts[0].strip()
                                if url_hash:
                                    self.downloaded_url_hashes.add(url_hash)
                            if len(parts) > 1:
                                video_id = parts[1].strip()
                                if video_id and video_id != "-":
                                    self.downloaded_video_ids.add(video_id)
                            if len(parts) > 2:
                                self.remember_downloaded_url_in_memory(parts[2].strip())
        except Exception as e:
            self.file_logger.error(f"load_download_log (session files): {e}")

        # Дополнительная страховка: человекочитаемые История ссылок/downloaded_*.txt.
        # Если пользователь перенёс только История ссылок или старый машинный лог был очищен,
        # защита от повторного добавления всё равно сработает.
        try:
            if self.history_dir.exists():
                history_patterns = ["downloaded_*.txt", "downloaded_history.txt"]
                for pattern in history_patterns:
                    for history_file in sorted(self.history_dir.glob(pattern)):
                        if not self.is_recent_log_file(history_file, cutoff):
                            continue
                        text = history_file.read_text(encoding='utf-8', errors='replace')
                        for found_url in self.extract_urls_from_text(text):
                            self.remember_downloaded_url_in_memory(found_url)
        except Exception as e:
            self.file_logger.error(f"load_download_log (История ссылок): {e}")


    def save_download_log(self, url: str) -> None:
        """
        Сохранение URL и video_id в сессионные логи.
        Новые записи идут по одному файлу на сессию и хранятся
        DOWNLOAD_HISTORY_RETENTION_DAYS дней.
        Старые общие download_log.txt/video_ids.txt не используются: в них нет дат по строкам.
        """
        normalized_url = self.normalize_url_for_history(url)
        url_hash = hashlib.md5(normalized_url.encode()).hexdigest()
        video_id = self.extract_video_id(url)

        with self.file_lock:
            self.downloaded_url_hashes.add(url_hash)
            # Совместимость со старыми точными hash по сырой ссылке.
            self.downloaded_url_hashes.add(hashlib.md5(url.strip().encode()).hexdigest())
            if video_id:
                self.downloaded_video_ids.add(video_id)

            try:
                history_file, session_log_file = self.ensure_download_session_files()
                self.current_session_download_count += 1

                with open(history_file, 'a', encoding='utf-8') as f:
                    f.write(f"{self.current_session_download_count}. {url}\n")

                with open(session_log_file, 'a', encoding='utf-8') as f:
                    f.write(f"{url_hash}\t{video_id or '-'}\t{normalized_url}\t{url}\n")
            except Exception as e:
                self.file_logger.error(f"save_download_log (session): {e}")
                self.record_problem(
                    "Не удалось записать скачанное видео в сессионные логи",
                    "ERROR", "save_download_log",
                    {
                        "url": url,
                        "url_hash": url_hash,
                        "normalized_url": normalized_url,
                        "video_id": video_id,
                        "history_file": str(self.current_history_session_file),
                        "session_log_file": str(self.current_download_log_session_file),
                    },
                    e
                )


    def is_url_downloaded(self, url: str) -> bool:
        if not url:
            return False
        normalized_url = self.normalize_url_for_history(url)
        url_hash = hashlib.md5(normalized_url.encode()).hexdigest()
        if url_hash in self.downloaded_url_hashes:
            return True
        # Совместимость со старыми hash, где считали прямо от введённой строки.
        raw_hash = hashlib.md5(url.strip().encode()).hexdigest()
        if raw_hash in self.downloaded_url_hashes:
            return True
        video_id = self.extract_video_id(url)
        if video_id and video_id in self.downloaded_video_ids:
            return True
        return False


    def is_youtube_url(self, url: str) -> bool:
        try:
            host = (urllib.parse.urlparse(url.strip()).hostname or "").lower().rstrip(".")
            return host == "youtube.com" or host.endswith(".youtube.com") or host == "youtu.be"
        except Exception:
            return False


    def run_yt_dlp_with_download_slot(self, url: str) -> Optional[str]:
        # Раньше здесь был глобальный Lock на YouTube: даже если в настройках стояло
        # 2-3 потока, реально качался только 1 ролик, а остальные висели на строке
        # «Жду свободный слот YouTube». Это делало большие очереди очень медленными.
        # Теперь параллельность контролирует только ThreadPoolExecutor через настройку
        # «Потоков», а устойчивость даёт список fallback-стратегий внутри method_yt_dlp.
        return self.method_yt_dlp(url)


    def clear_download_log(self) -> None:
        try:
            if self.log_file.exists():
                self.log_file.unlink()
            if self.video_ids_file.exists():
                self.video_ids_file.unlink()
            if self.downloaded_sessions_dir.exists():
                for session_file in self.downloaded_sessions_dir.glob("downloaded_log_*.txt"):
                    try:
                        session_file.unlink()
                    except OSError as e:
                        self.file_logger.error(f"clear_download_log (session file): {e}")

            # Так как защита теперь умеет читать История ссылок, кнопка очистки должна
            # очищать и человекочитаемую историю скачанных, иначе после перезапуска
            # программа снова будет считать эти видео уже скачанными.
            if self.history_dir.exists():
                for pattern in ("downloaded_*.txt", "downloaded_history.txt"):
                    for history_file in self.history_dir.glob(pattern):
                        try:
                            history_file.unlink()
                        except OSError as e:
                            self.file_logger.error(f"clear_download_log (history file): {e}")

            self.downloaded_url_hashes.clear()
            self.downloaded_video_ids.clear()
            self.current_history_session_file = None
            self.current_download_log_session_file = None
            self.current_session_download_count = 0
            self.log("✓ Лог скачанных видео очищен. Теперь видео можно скачивать повторно.", "SUCCESS")

            if self.video_list:
                for video in self.video_list:
                    video['is_downloaded'] = False
                self.display_video_list()

        except Exception as e:
            self.log(f"Ошибка очистки лога: {str(e)}", "ERROR")
