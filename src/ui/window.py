"""Закрытие окна и ожидание фоновых потоков.

Автоматически выделено из прежнего модуля ui_components.py без изменения тел методов.
"""

from tkinter import messagebox
import time


class WindowLifecycleMixin:
    def on_closing(self) -> None:
        if self.is_downloading:
            if messagebox.askyesno("Подтверждение", "Идёт загрузка. Остановить и выйти?"):
                self.save_download_links_session("закрытие программы во время загрузки")
                self.cancel_flag.set()
                self.terminate_active_processes()
                self.log("🛑 Остановка...", "WARNING")
                self.save_current_settings()
                self.wait_for_threads(timeout=30)
                self.finalize_problem_diagnostics(
                    "finished",
                    "application_closed_after_cancel",
                    {"download_was_active": True},
                )
                self.root.destroy()
        else:
            self.save_download_links_session("закрытие программы")
            self.save_current_settings()
            self.finalize_problem_diagnostics(
                "finished",
                "application_closed",
                {"download_was_active": False},
            )
            self.root.destroy()


    def wait_for_threads(self, timeout: int = 30) -> None:
        # ThreadSafeList.__iter__ возвращает копию — безопасно при модификации списка потоками
        start = time.time()
        for thread in self.download_threads:
            if thread.is_alive():
                remaining = timeout - (time.time() - start)
                if remaining > 0:
                    thread.join(timeout=remaining)
