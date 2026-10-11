"""Постоянные уведомления и доставка завершения worker → главный поток Tk."""

import ctypes
from ctypes import wintypes
from pathlib import Path
import platform
import queue
import tkinter as tk
from tkinter import ttk


def desktop_work_area(root):
    """Рабочая область основного экрана без панели задач Windows."""
    if platform.system() == "Windows":
        rect = wintypes.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
            return rect.left, rect.top, rect.right, rect.bottom
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


class CompletionNotificationMixin:
    def initialize_completion_notifications(self) -> None:
        self._completion_queue = queue.Queue()
        self._completion_closed = False
        self._completion_windows = []
        self._completion_path_trace = self.save_path.trace_add(
            "write", self._refresh_completion_notification_paths
        )
        self._completion_after_id = self.root.after(100, self._poll_download_completion)
        self.root.bind("<Destroy>", self._stop_completion_notifications, add="+")

    def _poll_download_completion(self) -> None:
        self._completion_after_id = None
        if self._completion_closed:
            return
        try:
            while True:
                try:
                    result = self._completion_queue.get_nowait()
                except queue.Empty:
                    break
                self._finish_download_ui(*result)
        finally:
            if not self._completion_closed:
                self._completion_after_id = self.root.after(100, self._poll_download_completion)

    def _stop_completion_notifications(self, event) -> None:
        if event.widget is not self.root or self._completion_closed:
            return
        self._completion_closed = True
        self.save_path.trace_remove("write", self._completion_path_trace)
        if self._completion_after_id is not None:
            self.root.after_cancel(self._completion_after_id)
            self._completion_after_id = None
        self._completion_windows.clear()

    def show_completion_notification(self, outcome: str, elapsed: float) -> None:
        if getattr(self, "_completion_closed", True):
            return
        title, message, color = {
            "completed": ("Готово", "Скачивание и конвертация завершены.", "#7de2a3"),
            "failed": ("Завершено с ошибками", "Подробности — в логе программы.", "#ffb197"),
            "cancelled": ("Задача отменена", "Готовые файлы остаются в выбранной папке.", "#ffd479"),
        }.get(outcome, ("Задача завершена", "Проверьте итог в логе программы.", "#ffd479"))
        window = tk.Toplevel(self.root)
        window.withdraw()
        window.overrideredirect(True)
        window.attributes("-topmost", True)
        window.protocol("WM_DELETE_WINDOW", lambda: None)
        window.bind("<Escape>", lambda event: "break")
        background = "#182635"
        window.configure(bg=background, highlightbackground="#607387", highlightthickness=1)
        left, top, right, bottom = desktop_work_area(self.root)
        width = min(420, max(240, right - left - 32))
        header = tk.Frame(window, bg=background)
        header.pack(fill=tk.X, padx=16, pady=(12, 0))
        tk.Label(header, text=title, font=("Segoe UI", 12, "bold"),
                 fg=color, bg=background).pack(side=tk.LEFT)
        tk.Button(header, text="×", font=("Segoe UI", 16), width=2,
                  fg="#ffffff", bg=background, activebackground="#34485d",
                  activeforeground="#ffffff", bd=0, cursor="hand2",
                  command=lambda: self._close_completion_notification(window)).pack(side=tk.RIGHT)
        tk.Label(window, text=f"{message}\nВремя: {self.format_duration(elapsed)}",
                 font=("Segoe UI", 10), fg="#ffffff", bg=background,
                 wraplength=width - 36, justify=tk.LEFT, anchor="w").pack(
                     fill=tk.X, padx=16, pady=(8, 0))
        window.completion_path_label = tk.Label(window, text=self.save_path.get(), font=("Segoe UI", 9),
                 fg="#c2cfdd", bg=background, wraplength=width - 36,
                 height=2, justify=tk.LEFT, anchor="w")
        window.completion_path_label.pack(fill=tk.X, padx=16, pady=8)
        # Надпись и кнопка следуют текущему полю «Место сохранения».
        ttk.Button(window, text="Открыть папку", command=self._open_completion_folder).pack(
            fill=tk.X, padx=16, pady=(0, 16))
        window.update_idletasks()
        window.geometry(f"{width}x{window.winfo_reqheight()}")
        self._completion_windows.append(window)
        self._position_completion_notifications((left, top, right, bottom))
        window.deiconify()
        window.lift()

    def _refresh_completion_notification_paths(self, *args) -> None:
        if self._completion_closed:
            return
        path = self.save_path.get()
        for window in self._completion_windows:
            window.completion_path_label.config(text=path)

    def _open_completion_folder(self) -> None:
        path = self.save_path.get()
        if not path.strip():
            self.log("⚠️ Сначала выберите место сохранения", "WARNING")
            return
        self.open_folder_path(Path(path), "результаты скачивания и конвертации")

    def _position_completion_notifications(self, area=None) -> None:
        left, top, right, bottom = area or desktop_work_area(self.root)
        y = bottom - 16
        for window in reversed(self._completion_windows):
            height = window.winfo_reqheight()
            width = min(420, max(240, right - left - 32))
            y -= height
            window.geometry(f"{width}x{height}{max(left, right - width - 16):+d}{max(top, y):+d}")
            y -= 12

    def _close_completion_notification(self, window) -> None:
        if window not in self._completion_windows:
            return
        self._completion_windows.remove(window)
        window.destroy()
        self._position_completion_notifications()
