# Скачивальщик видео

Windows-приложение на Python/Tkinter: скачивание видео до 1080p с обязательным итоговым контейнером `.mp4` и конвертация в MP3. Требуются yt-dlp, FFmpeg и ffprobe.

Итог публикации видео: `.mp4`. Программа сначала предпочитает MP4/M4A на том же качестве, затем при необходимости делает быстрый lossless remux в MP4; H.264/AAC перекодирование используется только как крайний fallback.
Запуск: `python video_downloader.py`.
[Архитектура и правила качества](docs/ARCHITECTURE.md).

## Для Codex

Перед правкой: `python scripts/codex_scope.py "описание задачи"`.
Проверка структуры: `python scripts/check_codex_efficiency.py`.
Короткая карта: [`docs/CODE_MAP.md`](docs/CODE_MAP.md).
