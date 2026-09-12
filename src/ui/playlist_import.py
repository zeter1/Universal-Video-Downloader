"""Импорт списка видео из HTML/JSON YouTube и восстановление названий.

Автоматически выделено из прежнего модуля ui_components.py без изменения тел методов.
"""

from typing import Dict
from typing import List
from typing import Optional
from typing import Set
from concurrent.futures import ThreadPoolExecutor
from typing import Tuple
from tkinter import filedialog
import html as html_module
import json
from tkinter import messagebox
import re
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk

from src.core.constants import FETCH_TITLE_TIMEOUT, MAX_VIDEOS_PER_LIST


class PlaylistImportMixin:
    def load_from_html(self) -> None:
        file_path = filedialog.askopenfilename(
            title="Выберите HTML файл плейлиста YouTube",
            filetypes=[("HTML файлы", "*.html *.htm"), ("Все файлы", "*.*")]
        )
        if not file_path:
            return

        win = tk.Toplevel(self.root)
        win.title("Загрузка плейлиста")
        win.geometry("450x180")
        win.transient(self.root)
        win.grab_set()

        ttk.Label(win, text="⚡ Парсинг HTML файла...",
                  font=("Arial", 11, "bold")).pack(pady=20)
        progress = ttk.Progressbar(win, mode='indeterminate', length=350)
        progress.pack(pady=10)
        progress.start(8)
        status_label = ttk.Label(win, text="Чтение файла...")
        status_label.pack(pady=10)

        start_time = time.time()

        def parse_thread():
            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    html_content = f.read()
                self.root.after(0, lambda: status_label.config(text="⚡ Извлечение видео..."))
                videos     = self.parse_youtube_playlist_html_fast(html_content)
                parse_time = time.time() - start_time
                for video in videos:
                    video['is_downloaded'] = self.is_url_downloaded(video['url'])
                self.root.after(
                    0, lambda: self.finish_html_loading(videos, win, parse_time)
                )
            except Exception as e:
                self.root.after(0, lambda: self.handle_html_error(str(e), win))

        threading.Thread(target=parse_thread, daemon=True).start()


    def parse_youtube_playlist_html_fast(self, html_content: str) -> List[Dict]:
        videos: List[Dict] = []
        seen_ids: Set[str] = set()

        json_data = None
        for pattern in [r'var ytInitialData\s*=\s*(\{.*?\});',
                         r'window\["ytInitialData"\]\s*=\s*(\{.*?\});',
                         r'ytInitialData\s*=\s*(\{.*?\});']:
            try:
                m = re.search(pattern, html_content, re.DOTALL)
                if m:
                    json_data = json.loads(m.group(1))
                    break
            except Exception:
                continue

        if json_data:
            videos = self.extract_all_videos_from_json(json_data)
            if videos:
                unique: List[Dict] = []
                for v in videos:
                    if v['video_id'] not in seen_ids:
                        seen_ids.add(v['video_id'])
                        unique.append(v)
                self.log(f"⚡ JSON: {len(unique)} видео", "SUCCESS")
                self.fetch_missing_titles(unique)
                return unique

        self.log("⚠️ JSON не найден, резервный метод", "WARNING")
        vid_ids = re.findall(r'"videoId":"([A-Za-z0-9_-]{11})"', html_content)
        titles  = re.findall(r'"title":{"runs":\[{"text":"([^"]+)"', html_content)

        for i, vid in enumerate(vid_ids):
            if vid not in seen_ids and len(vid) == 11:
                seen_ids.add(vid)
                title = titles[i] if i < len(titles) else 'Без названия'
                videos.append({
                    'url': f'https://www.youtube.com/watch?v={vid}',
                    'title': self.clean_title(title)[:150],
                    'video_id': vid
                })

        if videos:
            self.log(f"⚡ Резервный: {len(videos)} видео", "SUCCESS")
            self.fetch_missing_titles(videos)
        else:
            self.log("❌ Видео не найдены", "ERROR")
        return videos


    def extract_title_from_renderer(self, renderer: dict) -> str:
        title_text = "Без названия"
        try:
            if 'title' in renderer:
                t = renderer['title']
                if 'runs' in t and t['runs']:
                    title_text = t['runs'][0].get('text', '')
                elif 'simpleText' in t:
                    title_text = t['simpleText']
        except Exception:
            pass
        return self.clean_title(title_text)


    def clean_title(self, title: str) -> str:
        if not title:
            return "Без названия"
        try:
            title = html_module.unescape(title)
        except Exception:
            pass

        title = re.sub(r'\\u([0-9a-fA-F]{4})',
                       lambda m: chr(int(m.group(1), 16)), title)
        for old, new in [('\\n', ' '), ('\\r', ' '), ('\\t', ' '),
                         ('\\"', '"'), ("\'", "'"), ('&quot;', '"'),
                         ('&amp;', '&'), ('&lt;', '<'), ('&gt;', '>'),
                         ('&#39;', "'"), ('&nbsp;', ' ')]:
            title = title.replace(old, new)
        title = ' '.join(title.split())
        return title.strip()[:150] if len(title.strip()) >= 2 else "Без названия"


    def fetch_missing_titles(self, videos: List[Dict]) -> None:
        SUSPICIOUS = {'описание', 'без названия', 'комментарии', 'playlist',
                      'description', 'overview', 'about', 'comments'}
        to_fetch = [v for v in videos
                    if (v['title'] == 'Без названия' or not v['title'].strip()
                        or any(w in v['title'].lower() for w in SUSPICIOUS)
                        or len(v['title']) < 10 or '\\u' in v['title'])]
        if not to_fetch:
            return

        self.log(f"📝 Подгрузка названий для {len(to_fetch)} видео...", "INFO")

        def fetch_one(video: Dict) -> Tuple[Dict, Optional[str]]:
            try:
                r = subprocess.run(
                    ["yt-dlp", "--skip-download", "--encoding", "utf-8", "--get-title",
                     "--no-warnings", "--quiet", video['url']],
                    capture_output=True, text=True,
                    encoding='utf-8', errors='replace',
                    env=self._utf8_subprocess_env(),
                    timeout=FETCH_TITLE_TIMEOUT,
                    creationflags=self.subprocess_flags
                )
                return video, r.stdout.strip() if r.returncode == 0 else None
            except Exception:
                return video, None

        # FIX #18: executor.map без try/except ронял весь поток при одной ошибке
        try:
            with ThreadPoolExecutor(max_workers=3) as executor:
                for video, title in executor.map(fetch_one, to_fetch):
                    if title:
                        video['title'] = self.clean_title(title)[:150]
        except Exception as e:
            self.file_logger.error(f"fetch_missing_titles: {e}")

        self.log("✅ Подгрузка завершена", "SUCCESS")


    def extract_all_videos_from_json(self, json_data: dict) -> List[Dict]:
        videos: List[Dict] = []
        # FIX #19: защита от циклических/рекурсивных структур через id() объекта
        seen_objects: Set[int] = set()

        def find(obj, depth: int = 0) -> None:
            obj_id = id(obj)
            if obj_id in seen_objects:
                return
            seen_objects.add(obj_id)

            if depth > 15 or len(videos) >= MAX_VIDEOS_PER_LIST:
                return
            if isinstance(obj, dict):
                for rtype in ['playlistVideoRenderer', 'videoRenderer',
                               'gridVideoRenderer', 'reelItemRenderer']:
                    if rtype in obj:
                        r   = obj[rtype]
                        vid = r.get('videoId')
                        if vid and len(vid) == 11:
                            videos.append({
                                'url': f'https://www.youtube.com/watch?v={vid}',
                                'title': self.extract_title_from_renderer(r),
                                'video_id': vid
                            })
                            return
                for key in obj:
                    find(obj[key], depth + 1)
            elif isinstance(obj, list):
                for item in obj[:100]:
                    find(item, depth + 1)

        try:
            find(json_data)
        except Exception as e:
            self.log(f"⚠️ Ошибка извлечения: {str(e)[:100]}", "WARNING")
        return videos


    def finish_html_loading(self, videos: List[Dict], win: tk.Toplevel,
                             parse_time: float) -> None:
        win.destroy()
        if not videos:
            messagebox.showwarning(
                "Предупреждение", "В HTML файле не найдено видео из плейлиста."
            )
            return
        self.video_list = videos
        self.display_video_list()
        self.video_list_frame.grid()

        downloaded_count = sum(1 for v in videos if v['is_downloaded'])
        new_count = len(videos) - downloaded_count

        msg = (f"⚡ Парсинг за {parse_time:.2f}с\n\n"
               f"📊 {len(videos)} видео:\n"
               f"🔴 Уже скачано: {downloaded_count}\n"
               f"🟢 Новых: {new_count}")
        self.log(
            f"⚡ Парсинг {parse_time:.2f}с: {new_count} новых, {downloaded_count} скачанных",
            "SUCCESS" if downloaded_count == 0 else "WARNING"
        )
        messagebox.showinfo("Готово!", msg)


    def handle_html_error(self, error: str, win: tk.Toplevel) -> None:
        win.destroy()
        messagebox.showerror("Ошибка", f"Не удалось загрузить HTML:\n{error[:200]}")
        self.log(f"❌ Ошибка HTML: {error}", "ERROR")
