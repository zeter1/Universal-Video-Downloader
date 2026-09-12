"""Логи в интерфейсе, вкладки, ссылки и копирование.

Автоматически выделено из прежнего модуля ui_components.py без изменения тел методов.
"""

from typing import Optional
from datetime import datetime
import tkinter as tk
import webbrowser

from src.core.constants import LOG_ENTRY_SPACING_PX, LOG_FLUSH_INTERVAL, LOG_TAB_ALL, MAX_LOG_LINES
from src.ui.helpers import find_clickable_urls, is_copy_shortcut, log_tabs_for_entry


class LogViewMixin:
    def log(self, message: str, level: str = "INFO", update_only: bool = False,
            record_as_problem: bool = False,
            category: Optional[str] = None) -> None:
        timestamp   = datetime.now().strftime("%H:%M:%S")
        log_message = f"[{timestamp}] {message}"

        # Дублируем ошибки в файловый лог
        if level in ("ERROR", "CRITICAL"):
            self.file_logger.error(message)
        if level in ("ERROR", "CRITICAL") or (
            level == "WARNING" and record_as_problem
        ):
            self.record_problem(
                message, level, "app_log",
                {"update_only": update_only}
            )

        with self._log_lock:
            self._log_queue.append((log_message, level, update_only, category))
            if not self._log_flush_pending:
                self._log_flush_pending = True
                self.root.after(LOG_FLUSH_INTERVAL, self._flush_log_queue)


    def _insert_log_message(self, log_message: str, level: str,
                            text_widget=None) -> None:
        text_widget = text_widget or self.log_text
        folder_link_text = {
            "HISTORY_FOLDER_LINK": "История ссылок",
            "DOWNLOAD_LINKS_FOLDER_LINK": "Ссылки на скачивания",
            "DOWNLOADED_SESSIONS_FOLDER_LINK": "Логи скачивания/Сессии скачанных",
            "MANUAL_FOLDER_LINK": "Обработать вручную",
            "PROBLEM_FOLDER_LINK": "Логи проблем",
        }.get(level)

        base_level = "INFO" if folder_link_text else level
        link_spans = []

        if folder_link_text and folder_link_text in log_message:
            start = log_message.find(folder_link_text)
            end = start + len(folder_link_text)
            link_spans.append((start, end, level))

        for start, end, _url in find_clickable_urls(log_message):
            link_spans.append((start, end, "URL_LINK"))

        cursor = 0
        for start, end, link_tag in sorted(link_spans):
            if start < cursor:
                continue
            if start > cursor:
                text_widget.insert(tk.END, log_message[cursor:start], base_level)
            text_widget.insert(
                tk.END, log_message[start:end], (base_level, link_tag)
            )
            cursor = end
        text_widget.insert(tk.END, log_message[cursor:] + "\n", base_level)


    def _flush_log_queue(self) -> None:
        """Сброс накопленных лог-сообщений в UI (вызывается только из main thread)."""
        with self._log_lock:
            items = self._log_queue[:]
            self._log_queue.clear()
            self._log_flush_pending = False

        try:
            log_widgets = getattr(
                self, "log_text_widgets", {LOG_TAB_ALL: self.log_text}
            )
            affected_tabs = {
                tab
                for _message, level, _update_only, category in items
                for tab in log_tabs_for_entry(level, category)
                if tab in log_widgets
            }
            keep_at_bottom = {
                tab: self._is_log_scrolled_to_bottom(log_widgets[tab])
                for tab in affected_tabs
            }

            for log_message, level, update_only, category in items:
                for tab in log_tabs_for_entry(level, category):
                    text_widget = log_widgets.get(tab)
                    if text_widget is None:
                        continue
                    if update_only:
                        idx = text_widget.index('end-1c').split('.')[0]
                        if int(idx) > 1:
                            text_widget.delete(f"{idx}.0", "end")
                    self._insert_log_message(log_message, level, text_widget)

            for tab in affected_tabs:
                text_widget = log_widgets[tab]
                lines = int(text_widget.index('end-1c').split('.')[0])
                if lines > MAX_LOG_LINES:
                    text_widget.delete("1.0", f"{lines - MAX_LOG_LINES}.0")
                if keep_at_bottom[tab]:
                    text_widget.see(tk.END)
        except Exception as e:
            try:
                self.file_logger.error(f"_flush_log_queue: {e}")
            except Exception:
                pass


    def _is_log_scrolled_to_bottom(self, text_widget=None) -> bool:
        text_widget = text_widget or self.log_text
        try:
            return text_widget.yview()[1] >= 0.995
        except Exception:
            return True


    def _open_url_at_event(self, event):
        text_widget = event.widget
        try:
            click_index = text_widget.index(f"@{event.x},{event.y}")
            ranges = text_widget.tag_ranges("URL_LINK")
            for start, end in zip(ranges[0::2], ranges[1::2]):
                if (text_widget.compare(click_index, ">=", start)
                        and text_widget.compare(click_index, "<", end)):
                    url = text_widget.get(start, end)
                    webbrowser.open_new_tab(url)
                    break
        except Exception as e:
            self.log(f"❌ Не удалось открыть ссылку: {e}", "ERROR")
        return "break"


    def _copy_log_selection(self, text_widget) -> bool:
        try:
            text = text_widget.get(tk.SEL_FIRST, tk.SEL_LAST)
            if not text:
                return False
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            return True
        except tk.TclError:
            return False


    def _on_log_copy_shortcut(self, event):
        if not is_copy_shortcut(
                getattr(event, "keysym", ""), getattr(event, "keycode", None)):
            return None
        self._copy_log_selection(event.widget)
        return "break"


    def _copy_selected_log_text(self) -> None:
        text_widget = getattr(self, "_log_context_widget", None)
        if text_widget is not None:
            self._copy_log_selection(text_widget)


    def _show_log_context_menu(self, event):
        self._log_context_widget = event.widget
        try:
            event.widget.get(tk.SEL_FIRST, tk.SEL_LAST)
            state = tk.NORMAL
        except tk.TclError:
            state = tk.DISABLED
        self.log_context_menu.entryconfigure(0, state=state)
        try:
            self.log_context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.log_context_menu.grab_release()
        return "break"


    def _configure_log_text_widget(self, text_widget) -> None:
        text_widget.configure(spacing3=LOG_ENTRY_SPACING_PX)
        text_widget.bind("<MouseWheel>", self._on_log_mousewheel)
        text_widget.bind("<Button-4>", self._on_log_mousewheel)
        text_widget.bind("<Button-5>", self._on_log_mousewheel)
        text_widget.bind("<Control-KeyPress>", self._on_log_copy_shortcut)
        text_widget.bind("<Button-3>", self._show_log_context_menu)

        text_widget.tag_config("ERROR", foreground="red", font=("Arial", 9, "bold"))
        text_widget.tag_config("WARNING", foreground="orange", font=("Arial", 9, "bold"))
        text_widget.tag_config("SUCCESS", foreground="green")
        text_widget.tag_config("INFO", foreground="black")
        text_widget.tag_config("CRITICAL", foreground="red", font=("Arial", 10, "bold"))

        folder_links = {
            "HISTORY_FOLDER_LINK": self.open_history_folder,
            "DOWNLOAD_LINKS_FOLDER_LINK": self.open_download_links_folder,
            "DOWNLOADED_SESSIONS_FOLDER_LINK": self.open_downloaded_sessions_folder,
            "MANUAL_FOLDER_LINK": self.open_manual_processing_folder,
            "PROBLEM_FOLDER_LINK": self.open_problem_logs_folder,
        }
        for tag_name, callback in folder_links.items():
            text_widget.tag_config(
                tag_name, foreground="#0066cc", underline=True,
                font=("Arial", 9, "bold")
            )
            text_widget.tag_bind(tag_name, "<Button-1>", callback)
            text_widget.tag_bind(
                tag_name, "<Enter>",
                lambda _event, widget=text_widget: widget.configure(cursor="hand2")
            )
            text_widget.tag_bind(
                tag_name, "<Leave>",
                lambda _event, widget=text_widget: widget.configure(cursor="")
            )

        text_widget.tag_config(
            "URL_LINK", foreground="#0066cc", underline=True
        )
        text_widget.tag_bind("URL_LINK", "<Button-1>", self._open_url_at_event)
        text_widget.tag_bind(
            "URL_LINK", "<Enter>",
            lambda _event, widget=text_widget: widget.configure(cursor="hand2")
        )
        text_widget.tag_bind(
            "URL_LINK", "<Leave>",
            lambda _event, widget=text_widget: widget.configure(cursor="")
        )

        for tag_name in (
            "ERROR", "WARNING", "SUCCESS", "INFO", "CRITICAL",
            "HISTORY_FOLDER_LINK", "DOWNLOAD_LINKS_FOLDER_LINK",
            "DOWNLOADED_SESSIONS_FOLDER_LINK", "MANUAL_FOLDER_LINK",
            "PROBLEM_FOLDER_LINK", "URL_LINK",
        ):
            text_widget.tag_config(tag_name, spacing3=LOG_ENTRY_SPACING_PX)
