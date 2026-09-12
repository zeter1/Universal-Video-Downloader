# core/ — локальная карта Codex

Общие механизмы без feature-specific UI/download логики.

- `constants.py` — только константы, dependency-free
- `errors.py` — общие исключения
- `concurrency.py` — маленькие thread-safe контейнеры
- `session_settings.py` — безопасное чтение session/UI settings
- `settings.py` — persistence настроек
- `subprocess_runner.py` — запуск процессов
- `process_control.py` — остановка процессов
- `ytdlp_runtime.py` — watchdog/progress runtime
- `runtime.py` — только compatibility facade; **новый код его не импортирует**

При subprocess/cancel изменениях проверить download/audio failure tests.
