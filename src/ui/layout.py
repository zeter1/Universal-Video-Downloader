"""Создание и компоновка всего Tkinter-интерфейса.

Автоматически выделено из прежнего модуля ui_components.py без изменения тел методов.
"""

from pathlib import Path
from tkinter import scrolledtext
import tkinter as tk
from tkinter import ttk

from src.core.constants import APP_VERSION, LOG_TAB_ALL, LOG_TAB_DOWNLOAD_FAILED, LOG_TAB_ERRORS, MAX_CONCURRENT_DL, MAX_DOWNLOAD_LINK_SESSIONS, MAX_SPLIT_HOURS


class UILayoutMixin:
    def setup_ui(self) -> None:
        style = ttk.Style()
        style.configure("TFrame",      background="#f0f0f0")
        style.configure("TLabel",      background="#f0f0f0", font=("Arial", 9))
        style.configure("TButton",     font=("Arial", 9))
        style.configure("TCheckbutton", background="#f0f0f0", font=("Arial", 9))

        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(5, weight=1)

        # ── Прогресс ──
        self.main_progress_frame = ttk.Frame(main_frame, relief="solid", borderwidth=1)
        self.main_progress_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        self.main_progress_frame.columnconfigure(0, weight=1)
        self.main_progress_frame.grid_remove()

        pb_frame = ttk.Frame(self.main_progress_frame)
        pb_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), padx=10, pady=5)
        pb_frame.columnconfigure(0, weight=1)
        ttk.Label(pb_frame, text="Общий прогресс:",
                  font=("Arial", 10, "bold")).grid(row=0, column=0, sticky=tk.W)
        self.main_progress = ttk.Progressbar(pb_frame, mode='determinate')
        self.main_progress.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=5)
        self.progress_percent = ttk.Label(pb_frame, text="0%", font=("Arial", 10, "bold"))
        self.progress_percent.grid(row=1, column=1, padx=10)

        # ── URL ──
        url_frame = ttk.LabelFrame(
            main_frame,
            text="Ссылки для скачивания (каждая с новой строки)",
            padding="5"
        )
        url_frame.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=5)
        url_frame.columnconfigure(0, weight=1)

        url_btn_frame = ttk.Frame(url_frame)
        url_btn_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 5))

        ttk.Button(url_btn_frame, text="📋 Вставить из буфера",
                   command=self.paste_from_clipboard).pack(side=tk.LEFT, padx=5)
        ttk.Button(url_btn_frame, text="🗑 Очистить список",
                   command=self.clear_urls).pack(side=tk.LEFT, padx=5)
        ttk.Button(url_btn_frame, text="📂 Загрузить плейлист из .html",
                   command=self.load_from_html).pack(side=tk.LEFT, padx=5)

        self.url_text = scrolledtext.ScrolledText(url_frame, height=4, wrap=tk.WORD)
        self.url_text.grid(row=1, column=0, sticky=(tk.W, tk.E))
        self.url_text.tag_config("DUPLICATE", background="#ffcccc", foreground="red")
        self.url_text.tag_config("NEW", background="#ccffcc")

        self.context_menu = tk.Menu(self.url_text, tearoff=0)
        self.context_menu.add_command(label="Копировать", command=self.copy_text)
        self.context_menu.add_command(label="Вставить",   command=self.paste_text)
        self.context_menu.add_command(label="Удалить",    command=self.delete_text)
        self.url_text.bind("<Control-KeyPress>", self._on_url_clipboard_shortcut)
        self.url_text.bind("<Button-3>", self.show_context_menu)

        # ── Список видео из HTML ──
        self.video_list_frame = ttk.LabelFrame(
            main_frame, text="Видео из HTML плейлиста", padding="5"
        )
        self.video_list_frame.grid(
            row=2, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), pady=5
        )
        self.video_list_frame.columnconfigure(0, weight=1)
        self.video_list_frame.rowconfigure(1, weight=1)
        self.video_list_frame.grid_remove()

        vl_btn = ttk.Frame(self.video_list_frame)
        vl_btn.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 5))

        ttk.Button(vl_btn, text="✅ Добавить выбранные в очередь",
                   command=self.add_selected_to_queue).pack(side=tk.LEFT, padx=5)
        ttk.Button(vl_btn, text="☑ Все",
                   command=self.select_all_videos).pack(side=tk.LEFT, padx=5)
        ttk.Button(vl_btn, text="🆕 Только новые",
                   command=self.select_only_new).pack(side=tk.LEFT, padx=5)
        ttk.Button(vl_btn, text="☐ Снять выбор",
                   command=self.deselect_all_videos).pack(side=tk.LEFT, padx=5)
        ttk.Button(vl_btn, text="🗑️ Удалить выбранные",
                   command=self.delete_selected_videos).pack(side=tk.LEFT, padx=5)
        ttk.Checkbutton(vl_btn, text="Показать скачанные",
                        variable=self.show_downloaded,
                        command=self.display_video_list).pack(side=tk.LEFT, padx=15)
        self.video_stats_label = ttk.Label(vl_btn, text="Видео: 0",
                                            font=("Arial", 9, "bold"))
        self.video_stats_label.pack(side=tk.RIGHT, padx=10)

        list_container = ttk.Frame(self.video_list_frame)
        list_container.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        list_container.columnconfigure(0, weight=1)
        list_container.rowconfigure(0, weight=1)

        self.video_canvas    = tk.Canvas(list_container, height=200)
        self.video_scrollbar = ttk.Scrollbar(
            list_container, orient="vertical", command=self.video_canvas.yview
        )
        self.video_scrollable_frame = ttk.Frame(self.video_canvas)
        self.video_scrollable_frame.bind(
            "<Configure>",
            lambda e: self.video_canvas.configure(
                scrollregion=self.video_canvas.bbox("all"))
        )
        self.video_canvas.create_window(
            (0, 0), window=self.video_scrollable_frame, anchor="nw"
        )
        self.video_canvas.configure(yscrollcommand=self.video_scrollbar.set)
        self.video_canvas.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        self.video_scrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))

        # Скролл мышью — Windows и Linux (FIX #21 продолжение)
        for widget in (self.video_canvas, self.video_scrollable_frame):
            widget.bind("<MouseWheel>", self._on_mousewheel)   # Windows
            widget.bind("<Button-4>",   self._on_mousewheel)   # Linux scroll up
            widget.bind("<Button-5>",   self._on_mousewheel)   # Linux scroll down

        # ── Настройки ──
        settings_frame = ttk.LabelFrame(main_frame, text="Настройки загрузки", padding="5")
        settings_frame.grid(row=3, column=0, sticky=(tk.W, tk.E), pady=5)

        path_frame = ttk.Frame(settings_frame)
        path_frame.grid(row=0, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=2)
        ttk.Label(path_frame, text="Путь сохранения:").pack(side=tk.LEFT, padx=5)
        self.save_path = tk.StringVar(
            value=self.settings.get("save_path", str(Path.home() / "Downloads"))
        )
        ttk.Entry(path_frame, textvariable=self.save_path, width=55).pack(
            side=tk.LEFT, padx=5, fill=tk.X, expand=True
        )
        ttk.Button(path_frame, text="Обзор", command=self.browse_path).pack(
            side=tk.LEFT, padx=5
        )

        quality_frame = ttk.Frame(settings_frame)
        quality_frame.grid(row=1, column=0, sticky=tk.W, pady=2)
        # Качество видео больше не выбирается в интерфейсе: программа всегда
        # скачивает авто до 1080p, а если у ролика нет 1080p — берёт ближайшее доступное ниже.
        self.quality = tk.StringVar(value="1080p")
        ttk.Label(
            quality_frame,
            text="Качество видео: приоритет 1080p, максимум 1080p (если недоступно — лучшее ниже)",
            foreground="gray"
        ).pack(side=tk.LEFT, padx=5)

        ttk.Label(quality_frame, text="Качество аудио:").pack(side=tk.LEFT, padx=(20, 5))
        self.audio_quality = tk.StringVar(
            value=self.settings.get("audio_quality", "320k")
        )
        aq_combo = ttk.Combobox(
            quality_frame, textvariable=self.audio_quality,
            values=["128k", "192k", "256k", "320k", "VBR-0 (лучшее)"],
            width=15, state="readonly"
        )
        aq_combo.pack(side=tk.LEFT, padx=5)
        aq_combo.bind("<<ComboboxSelected>>", lambda e: self.save_current_settings())

        adv_frame = ttk.Frame(settings_frame)
        adv_frame.grid(row=1, column=1, sticky=tk.W, pady=2, padx=20)
        self.download_subtitles = tk.BooleanVar(
            value=self.settings.get("download_subtitles", False)
        )
        ttk.Checkbutton(adv_frame, text="Субтитры",
                        variable=self.download_subtitles).pack(side=tk.LEFT, padx=5)
        self.write_problem_logs = tk.BooleanVar(
            value=self.settings.get("write_problem_logs", True)
        )
        ttk.Checkbutton(
            adv_frame, text="Писать логи проблем",
            variable=self.write_problem_logs,
            command=self.on_problem_logging_change
        ).pack(side=tk.LEFT, padx=(20, 5))
        ttk.Label(adv_frame, text="Потоков:").pack(side=tk.LEFT, padx=(20, 5))
        self.concurrent_var = tk.IntVar(
            value=self.settings.get("concurrent_downloads", 2)
        )
        ttk.Spinbox(
            adv_frame, from_=1, to=MAX_CONCURRENT_DL,
            textvariable=self.concurrent_var, width=5
        ).pack(side=tk.LEFT, padx=5)

        merge_frame = ttk.LabelFrame(settings_frame, text="Объединение аудио", padding="5")
        merge_frame.grid(row=2, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=5)
        self.merge_audio = tk.BooleanVar(value=self.settings.get("merge_audio", False))
        ttk.Checkbutton(merge_frame, text="Объединить все аудио в один файл",
                        variable=self.merge_audio,
                        command=self.on_merge_change).grid(row=0, column=0, sticky=tk.W)
        self.split_frame = ttk.Frame(merge_frame)
        self.split_frame.grid(row=1, column=0, sticky=tk.W, pady=2)
        ttk.Label(self.split_frame, text="Разбить по (часов):").pack(side=tk.LEFT, padx=5)
        self.split_hours = tk.IntVar(value=self.settings.get("split_hours", MAX_SPLIT_HOURS))
        sp = ttk.Spinbox(
            self.split_frame, from_=0, to=MAX_SPLIT_HOURS,
            textvariable=self.split_hours, width=5,
            command=self.check_split_warning
        )
        sp.pack(side=tk.LEFT, padx=5)
        sp.bind('<KeyRelease>', lambda e: self.check_split_warning())
        ttk.Label(
            self.split_frame,
            text=f"(0 = без разбивки, макс {MAX_SPLIT_HOURS})"
        ).pack(side=tk.LEFT, padx=5)

        # ── Кнопки действий ──
        btn_frame = ttk.Frame(main_frame)
        btn_frame.grid(row=4, column=0, pady=10)

        self.download_btn = ttk.Button(
            btn_frame, text="▶ Скачать все",
            command=self.start_download, width=20
        )
        self.download_btn.grid(row=0, column=0, padx=5)

        self.cancel_btn = ttk.Button(
            btn_frame, text="⏹ Отменить",
            command=self.cancel_download,
            state=tk.DISABLED, width=20
        )
        self.cancel_btn.grid(row=0, column=1, padx=5)

        ttk.Button(
            btn_frame, text="🗂 Очистить лог скачанных",
            command=self.clear_download_log
        ).grid(row=0, column=2, padx=5)

        # ── Лог ──
        log_frame = ttk.LabelFrame(main_frame, text="Лог загрузки", padding="5")
        log_frame.grid(row=5, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), pady=5)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        self.log_context_menu = tk.Menu(log_frame, tearoff=0)
        self.log_context_menu.add_command(
            label="Копировать", command=self._copy_selected_log_text
        )
        self._log_context_widget = None

        self.log_notebook = ttk.Notebook(log_frame)
        self.log_notebook.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        self.log_text_widgets = {}
        for tab_key, tab_title in (
            (LOG_TAB_ALL, "Весь лог загрузки"),
            (LOG_TAB_DOWNLOAD_FAILED, "Не скачалось"),
            (LOG_TAB_ERRORS, "Ошибки"),
        ):
            tab_frame = ttk.Frame(self.log_notebook)
            tab_frame.columnconfigure(0, weight=1)
            tab_frame.rowconfigure(0, weight=1)
            self.log_notebook.add(tab_frame, text=tab_title)
            text_widget = scrolledtext.ScrolledText(
                tab_frame, height=15, wrap=tk.WORD
            )
            text_widget.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
            self.log_text_widgets[tab_key] = text_widget
            self._configure_log_text_widget(text_widget)

        # Старое имя остаётся ссылкой на полный лог для совместимости методов UI.
        self.log_text = self.log_text_widgets[LOG_TAB_ALL]

        # Инициализация состояния
        self.on_merge_change()

        optional_missing = getattr(self, "optional_missing_deps", [])
        if self.missing_deps or optional_missing:
            self.show_dependency_warning()
        else:
            if self.js_runtime_found:
                self.log(f"✅ JavaScript Runtime: {self.js_runtime_name}", "SUCCESS")
            self.log(
                f"✅ Все зависимости установлены. Готов к работе. Версия v{APP_VERSION} активна.",
                "SUCCESS",
            )
            self.log_retention_status()
            self.log("📄 История скачанных: отдельные файлы История ссылок/downloaded_*.txt", "HISTORY_FOLDER_LINK")
            self.log(
                f"📄 Ссылки из очереди при закрытии: Ссылки на скачивания/links_*.txt "
                f"(последние {MAX_DOWNLOAD_LINK_SESSIONS} сессий)",
                "DOWNLOAD_LINKS_FOLDER_LINK"
            )
            self.log("📄 Лог уже скачанных для программы: Логи скачивания/Сессии скачанных/downloaded_log_*.txt", "DOWNLOADED_SESSIONS_FOLDER_LINK")
            self.log(
                "📄 Ручная обработка: Обработать вручную — ссылки на несостоявшиеся загрузки "
                "и видео с ошибками конвертации",
                "MANUAL_FOLDER_LINK"
            )
            if self.should_write_problem_logs():
                self.log(
                    "📄 Диагностика проблем для ИИ: Логи проблем/Сессии/<запуск>/events.jsonl "
                    "+ incidents.jsonl + attachments; начинать с корневого latest_run.json "
                    "и README_FOR_CODEX.md",
                    "PROBLEM_FOLDER_LINK"
                )
            else:
                self.log(
                    "📄 Диагностика проблем для ИИ отключена галочкой «Писать логи проблем»",
                    "INFO"
                )
