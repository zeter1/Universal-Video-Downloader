"""Открытие служебных папок и обработчики колеса мыши.

Автоматически выделено из прежнего модуля ui_components.py без изменения тел методов.
"""

from pathlib import Path
import os
import platform
import webbrowser


class FoldersMixin:
    def open_folder_path(self, folder: Path, error_label: str) -> None:
        try:
            folder.mkdir(parents=True, exist_ok=True)
            if platform.system() == "Windows":
                os.startfile(str(folder))
            else:
                webbrowser.open(folder.resolve().as_uri())
        except Exception as e:
            self.log(f"❌ Не удалось открыть папку {error_label}: {e}", "ERROR")


    def open_history_folder(self, event=None) -> None:
        self.open_folder_path(self.history_dir, "История ссылок")


    def open_download_links_folder(self, event=None) -> None:
        self.open_folder_path(self.download_links_dir, "Ссылки на скачивания")


    def open_downloaded_sessions_folder(self, event=None) -> None:
        self.open_folder_path(self.downloaded_sessions_dir, "Логи скачивания/Сессии скачанных")


    def open_manual_processing_folder(self, event=None) -> None:
        self.open_folder_path(self.manual_processing_dir, "Обработать вручную")


    def open_problem_logs_folder(self, event=None) -> None:
        self.open_folder_path(self.problem_log_dir, "Логи проблем")


    def _on_mousewheel(self, event) -> None:
        # FIX #21: поддержка Linux (Button-4/Button-5) и Windows (event.delta)
        try:
            if not self.video_list_frame.winfo_ismapped():
                return
            if event.delta:
                self.video_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            elif event.num == 4:
                self.video_canvas.yview_scroll(-1, "units")
            elif event.num == 5:
                self.video_canvas.yview_scroll(1, "units")
        except Exception:
            pass


    def _on_log_mousewheel(self, event):
        try:
            if getattr(event, "num", None) == 4:
                units = -3
            elif getattr(event, "num", None) == 5:
                units = 3
            else:
                delta = getattr(event, "delta", 0)
                if not delta:
                    return "break"
                units = int(-delta / 120)
                if units == 0:
                    units = -1 if delta > 0 else 1
                units *= 3
            event.widget.yview_scroll(units, "units")
            return "break"
        except Exception:
            return "break"
