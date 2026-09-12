"""Отображение и управление списком видео.

Автоматически выделено из прежнего модуля ui_components.py без изменения тел методов.
"""

from tkinter import messagebox
import tkinter as tk
from tkinter import ttk


class VideoListMixin:
    def display_video_list(self) -> None:
        for widget in self.video_scrollable_frame.winfo_children():
            widget.destroy()
        self.video_checkboxes = []

        downloaded_count = 0
        new_count = 0

        for idx, video in enumerate(self.video_list):
            is_dl = video.get('is_downloaded', False)
            if is_dl:
                downloaded_count += 1
            else:
                new_count += 1

            if not self.show_downloaded.get() and is_dl:
                continue

            frame = ttk.Frame(self.video_scrollable_frame, relief="solid",
                              borderwidth=2 if is_dl else 1)
            frame.pack(fill=tk.X, padx=5, pady=2)

            var = tk.BooleanVar(value=not is_dl)
            ttk.Checkbutton(frame, variable=var).pack(side=tk.LEFT, padx=5)
            self.video_checkboxes.append((var, idx))

            ttk.Label(frame, text=f"{idx + 1}.", font=("Arial", 9),
                      width=4).pack(side=tk.LEFT)

            bg = "#ff4444" if is_dl else "#44cc44"
            fg = "white" if is_dl else "black"
            label_text   = "✓ УЖЕ СКАЧАНО" if is_dl else "🆕 НОВОЕ"
            status_frame = tk.Frame(frame, bg=bg, padx=8, pady=3)
            status_frame.pack(side=tk.LEFT, padx=5)
            tk.Label(status_frame, text=label_text,
                     font=("Arial", 8, "bold"), bg=bg, fg=fg).pack()

            info_frame = ttk.Frame(frame)
            info_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)
            color = "#888888" if is_dl else "blue"
            ttk.Label(info_frame, text=video['title'],
                      font=("Arial", 9, "bold"), foreground=color).pack(anchor=tk.W)
            ttk.Label(info_frame, text=video['url'],
                      font=("Arial", 8), foreground="gray").pack(anchor=tk.W)

            ttk.Button(frame, text="🗑️", width=3,
                       command=lambda i=idx: self.delete_video(i)).pack(side=tk.RIGHT, padx=5)

        self.update_video_stats(len(self.video_list), new_count, downloaded_count)


    def update_video_stats(self, total: int, new: int, downloaded: int) -> None:
        self.video_stats_label.config(
            text=f"Всего: {total} | 🟢 Новых: {new} | 🔴 Скачанных: {downloaded}"
        )


    def select_only_new(self) -> None:
        count = 0
        for var, idx in self.video_checkboxes:
            if idx < len(self.video_list):
                is_dl = self.video_list[idx].get('is_downloaded', False)
                var.set(not is_dl)
                if not is_dl:
                    count += 1
        self.log(f"✅ Выбрано {count} новых видео", "SUCCESS")


    def delete_video(self, index: int) -> None:
        if 0 <= index < len(self.video_list):
            del self.video_list[index]
            self.display_video_list()


    def delete_selected_videos(self) -> None:
        indices = [idx for var, idx in self.video_checkboxes
                   if var.get() and idx < len(self.video_list)]
        if not indices:
            messagebox.showwarning("Предупреждение", "Не выбрано ни одного видео")
            return
        if messagebox.askyesno("Подтверждение", f"Удалить {len(indices)} видео из списка?"):
            for idx in sorted(indices, reverse=True):
                if idx < len(self.video_list):
                    del self.video_list[idx]
            self.display_video_list()
            self.log(f"🗑️ Удалено {len(indices)} видео", "SUCCESS")


    def select_all_videos(self) -> None:
        for var, _ in self.video_checkboxes:
            var.set(True)


    def deselect_all_videos(self) -> None:
        for var, _ in self.video_checkboxes:
            var.set(False)


    def add_selected_to_queue(self) -> None:
        selected = [self.video_list[idx]['url']
                    for var, idx in self.video_checkboxes
                    if var.get() and idx < len(self.video_list)]
        if not selected:
            messagebox.showwarning("Предупреждение", "Не выбрано ни одного видео")
            return
        added = self.add_urls_with_check(selected)
        if added > 0:
            messagebox.showinfo("Готово", f"✅ Добавлено {added} видео в очередь")
