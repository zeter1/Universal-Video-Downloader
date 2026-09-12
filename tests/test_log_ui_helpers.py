import logging
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

from src.application import VideoDownloader
from src.core.constants import (
    LOG_CATEGORY_DOWNLOAD_FAILED, LOG_TAB_ALL, LOG_TAB_DOWNLOAD_FAILED, LOG_TAB_ERRORS,
)
from src.ui import log_view as log_view_module
from src.ui.helpers import (
    find_clickable_urls, is_copy_shortcut, is_paste_shortcut, log_tabs_for_entry,
)


class FakeLogWidget:
    def __init__(self):
        self.content = ""
        self.insert_calls = []
        self.seen = False

    def index(self, index):
        if index == "end-1c":
            return f"{self.content.count(chr(10)) + 1}.0"
        return index

    def insert(self, _index, text, tags=None):
        self.content += text
        self.insert_calls.append((text, tags))

    def yview(self):
        return (0.0, 1.0)

    def delete(self, _start, _end):
        raise AssertionError("Короткий тестовый лог не должен обрезаться")

    def see(self, _index):
        self.seen = True


class FakeClipboardRoot:
    def __init__(self):
        self.clipboard = "старое значение"

    def clipboard_clear(self):
        self.clipboard = ""

    def clipboard_append(self, text):
        self.clipboard += text


class LogUiHelperTests(unittest.TestCase):
    def test_finds_full_youtube_urls_and_trims_sentence_punctuation(self):
        text = (
            "Ошибка: https://www.youtube.com/watch?v=abc123&list=PL42, "
            "зеркало https://youtu.be/xyz987."
        )

        found = find_clickable_urls(text)

        self.assertEqual(
            [url for _start, _end, url in found],
            [
                "https://www.youtube.com/watch?v=abc123&list=PL42",
                "https://youtu.be/xyz987",
            ],
        )
        for start, end, url in found:
            self.assertEqual(text[start:end], url)

    def test_keeps_balanced_parenthesis_inside_url(self):
        text = "Ссылка (https://example.com/wiki/Test_(video))."
        self.assertEqual(
            find_clickable_urls(text)[0][2],
            "https://example.com/wiki/Test_(video)",
        )

    def test_download_failure_and_error_tabs_are_independent(self):
        self.assertEqual(
            log_tabs_for_entry(
                "WARNING", LOG_CATEGORY_DOWNLOAD_FAILED
            ),
            (LOG_TAB_ALL, LOG_TAB_DOWNLOAD_FAILED),
        )
        self.assertEqual(
            log_tabs_for_entry(
                "ERROR", LOG_CATEGORY_DOWNLOAD_FAILED
            ),
            (
                LOG_TAB_ALL,
                LOG_TAB_DOWNLOAD_FAILED,
                LOG_TAB_ERRORS,
            ),
        )
        self.assertEqual(
            log_tabs_for_entry("ERROR", None),
            (LOG_TAB_ALL, LOG_TAB_ERRORS),
        )

    def test_copy_shortcut_supports_english_and_russian_layouts(self):
        self.assertTrue(is_copy_shortcut("c", 67))
        self.assertTrue(is_copy_shortcut("Cyrillic_es", 0))
        self.assertTrue(is_copy_shortcut("с", 0))
        self.assertTrue(is_copy_shortcut("unknown", 67))
        self.assertFalse(is_copy_shortcut("v", 86))

    def test_paste_shortcut_supports_english_and_russian_layouts(self):
        self.assertTrue(is_paste_shortcut("v", 86))
        self.assertTrue(is_paste_shortcut("Cyrillic_em", 0))
        self.assertTrue(is_paste_shortcut("м", 0))
        self.assertTrue(is_paste_shortcut("unknown", 86))
        self.assertFalse(is_paste_shortcut("c", 67))

    def test_russian_paste_shortcut_generates_one_standard_paste_event(self):
        app = VideoDownloader.__new__(VideoDownloader)
        widget = mock.Mock()
        event = SimpleNamespace(widget=widget, keysym="Cyrillic_em", keycode=0)

        result = app._on_url_paste_shortcut(event)

        self.assertEqual(result, "break")
        widget.event_generate.assert_called_once_with("<<Paste>>")

    def test_unrelated_control_shortcut_is_not_intercepted(self):
        app = VideoDownloader.__new__(VideoDownloader)
        widget = mock.Mock()
        event = SimpleNamespace(widget=widget, keysym="a", keycode=65)

        result = app._on_url_paste_shortcut(event)

        self.assertIsNone(result)
        widget.event_generate.assert_not_called()

    def test_russian_copy_shortcut_copies_selected_log_text(self):
        app = VideoDownloader.__new__(VideoDownloader)
        app.root = FakeClipboardRoot()
        widget = SimpleNamespace(get=lambda _start, _end: "выделенный текст")
        event = SimpleNamespace(widget=widget, keysym="Cyrillic_es", keycode=0)

        result = app._on_log_copy_shortcut(event)

        self.assertEqual(result, "break")
        self.assertEqual(app.root.clipboard, "выделенный текст")

    def test_failed_error_is_routed_to_all_three_tabs_with_full_url_tag(self):
        app = VideoDownloader.__new__(VideoDownloader)
        widgets = {
            LOG_TAB_ALL: FakeLogWidget(),
            LOG_TAB_DOWNLOAD_FAILED: FakeLogWidget(),
            LOG_TAB_ERRORS: FakeLogWidget(),
        }
        url = "https://www.youtube.com/watch?v=full-id&list=full-list"
        message = f"[12:00:00] ❌ Не удалось скачать: {url}"
        app._log_lock = threading.Lock()
        app._log_queue = [
            (
                message,
                "ERROR",
                False,
                LOG_CATEGORY_DOWNLOAD_FAILED,
            )
        ]
        app._log_flush_pending = True
        app.log_text_widgets = widgets
        app.log_text = widgets[LOG_TAB_ALL]
        app.file_logger = logging.getLogger("log-ui-routing-test")

        app._flush_log_queue()

        for widget in widgets.values():
            self.assertEqual(widget.content, message + "\n")
            self.assertTrue(widget.seen)
            self.assertIn((url, ("ERROR", "URL_LINK")), widget.insert_calls)

    def test_clicking_url_tag_opens_exact_full_url(self):
        app = VideoDownloader.__new__(VideoDownloader)
        url = "https://youtu.be/full-video-id?list=full-list-id"
        positions = {"1.10": 10, "1.15": 15, "1.60": 60}
        widget = SimpleNamespace(
            index=lambda _position: "1.15",
            tag_ranges=lambda _tag: ("1.10", "1.60"),
            compare=lambda left, operator, right: (
                positions[left] >= positions[right]
                if operator == ">="
                else positions[left] < positions[right]
            ),
            get=lambda _start, _end: url,
        )
        event = SimpleNamespace(widget=widget, x=10, y=10)

        with mock.patch.object(log_view_module.webbrowser, "open_new_tab") as open_url:
            result = app._open_url_at_event(event)

        self.assertEqual(result, "break")
        open_url.assert_called_once_with(url)


if __name__ == "__main__":
    unittest.main()
