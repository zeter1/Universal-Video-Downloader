# Codex workflow — минимум лишнего контекста

## Быстрый вход

1. `python scripts/codex_scope.py "описание задачи"`.
2. Читать `PRIMARY`, затем только нужный `RELATED` и один релевантный test.
3. Если route не найден — `docs/CODE_MAP.md` + точечный `rg`.
4. `docs/CODE_INDEX.md` открывать только когда нужен точный список методов.

## Для багфикса

1. Найти текст ошибки/метод через `rg -n "..." src tests`.
2. Определить owner, не читать соседнюю подсистему «на всякий случай».
3. Сначала точечный тест.
4. Затем `python scripts/check_codex_efficiency.py`.
5. Затем полный `python -m unittest discover -s tests`.

## Не делать

- не использовать `from ... import *`;
- не импортировать `src.core.runtime` в новом/изменяемом коде;
- не читать весь `src/` или `src/diagnostics/` для локального download/UI бага;
- не возвращать бизнес-логику в `application.py`;
- не копировать длинные списки yt-dlp ошибок/стратегий в orchestration-файл;
- не редактировать `docs/CODE_INDEX.md` вручную — он генерируется скриптом.

## Owner-границы yt-dlp

- `youtube_downloader.py` — только control flow одной загрузки.
- `youtube_attempt.py` — context + первые сообщения попытки.
- `youtube_retry.py` — чистая классификация ошибок.
- `youtube_success.py` — проверка успешного результата и quality candidate.
- `youtube_finalize.py` — lower-quality fallback и итоговая failure-диагностика.
- `youtube_command.py` — CLI/format selector.
- `youtube_strategy_catalog.py` — данные стратегий.

## Definition of done

- owner-границы сохранены;
- нет wildcard/runtime imports;
- релевантные тесты проходят;
- full suite проходит либо зафиксирована уже существовавшая fixture/environment-проблема;
- для сетевых изменений живой YouTube smoke выполняется отдельно при необходимости.
