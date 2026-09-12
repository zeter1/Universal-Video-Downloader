# Скачивальщик видео — стартовая карта Codex

Цель: менять минимальный owner-scope и не загружать проект целиком.

## Старт
1. `python scripts/codex_scope.py "<задача>"`.
2. Если route не определён — `docs/CODE_MAP.md`.
3. Читать owner + прямой caller + релевантный test; не весь `src/`.
4. Полный список методов нужен редко: `docs/CODE_INDEX.md`.

## Главные инварианты
- Запуск: `video_downloader.py` → `src/bootstrap.py` → `src/application.py`.
- 1080p: максимум и цель задаются в `src/core/constants.py`.
- `src/core/runtime.py` — compatibility facade; новый код его не импортирует.
- Wildcard imports запрещены.
- Логи/история/настройки/пользовательские файлы не удалять без явного запроса.

## После правки
1. релевантный test;
2. `python scripts/check_codex_efficiency.py`;
3. `python -m unittest discover -s tests`.

Подробный workflow: `docs/CODEX_WORKFLOW.md`.
