"""Буфер обмена, контекстное меню и работа с текстом ссылок.

Автоматически выделено из прежнего модуля ui_components.py без изменения тел методов.
"""

from tkinter import messagebox
import tkinter as tk

from src.ui.helpers import is_copy_shortcut, is_paste_shortcut


class ClipboardMixin:
    def paste_from_clipboard(self) -> None:
        try:
            clipboard_text = self.root.clipboard_get().strip()
            if not clipboard_text:
                self.log("⚠️ Буфер обмена пуст", "WARNING")
                return
            urls = [u.strip() for u in clipboard_text.split("\n") if u.strip()]
            if not urls:
                return
            self.add_urls_with_check(urls)
        except tk.TclError:
            self.log("⚠️ Буфер обмена недоступен", "WARNING")
        except Exception as e:
            self.log(f"❌ Ошибка вставки: {str(e)}", "ERROR")


    def clear_urls(self) -> None:
        self.url_text.delete("1.0", tk.END)


    def show_context_menu(self, event) -> None:
        try:
            self.context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.context_menu.grab_release()


    def copy_text(self) -> None:
        try:
            text = self.url_text.get(tk.SEL_FIRST, tk.SEL_LAST)
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
        except tk.TclError:
            pass


    def paste_text(self) -> None:
        try:
            text = self.root.clipboard_get().strip()
            if text:
                self.url_text.insert(tk.INSERT, text)
        except tk.TclError:
            pass


    def _on_url_clipboard_shortcut(self, event):
        keysym = getattr(event, "keysym", "")
        keycode = getattr(event, "keycode", None)

        # Физические C/V на Windows имеют одинаковые keycode независимо от
        # активной EN/RU раскладки; keysym дополнительно покрывает Tk/Linux.
        if is_copy_shortcut(keysym, keycode):
            event.widget.event_generate("<<Copy>>")
            return "break"
        if is_paste_shortcut(keysym, keycode):
            # Штатная виртуальная вставка Tk корректно заменяет выделение.
            event.widget.event_generate("<<Paste>>")
            return "break"
        return None


    def delete_text(self) -> None:
        try:
            self.url_text.delete(tk.SEL_FIRST, tk.SEL_LAST)
        except tk.TclError:
            pass


    def check_duplicates_in_textbox(self) -> None:
        content = self.url_text.get("1.0", tk.END)
        if not content.strip():
            messagebox.showinfo("Информация", "Список пуст")
            return

        self.url_text.tag_remove("DUPLICATE", "1.0", tk.END)
        self.url_text.tag_remove("NEW", "1.0", tk.END)

        # FIX #16: отслеживаем реальный номер строки в виджете,
        #          а не порядковый индекс непустых URL (фикс смещения при пустых строках)
        dup_count = 0
        new_count = 0
        lines_raw = content.split("\n")
        for line_num, raw_line in enumerate(lines_raw, start=1):
            url = raw_line.strip()
            if not url:
                continue
            start = f"{line_num}.0"
            end   = f"{line_num}.end"
            if self.is_url_downloaded(url):
                self.url_text.tag_add("DUPLICATE", start, end)
                dup_count += 1
            else:
                self.url_text.tag_add("NEW", start, end)
                new_count += 1

        if dup_count > 0:
            messagebox.showwarning(
                "Найдены дубликаты!",
                f"🔴 Уже скачано: {dup_count}\n"
                f"🟢 Новых: {new_count}\n\n"
                f"Дубликаты подсвечены красным."
            )
            self.log(f"⚠️ Найдено {dup_count} дубликатов", "WARNING")
        else:
            messagebox.showinfo("Отлично!", f"✅ Все {new_count} видео новые!")
            self.log(f"✅ Дубликатов нет: {new_count} новых видео", "SUCCESS")
