# Логи проблем — порядок чтения для Codex

Формат диагностики: 4.0. Кодировка всех текстовых файлов: UTF-8.

## Быстрый порядок чтения

1. Откройте `latest_run.json`.
2. Перейдите по `session_summary` и прочитайте `session_summary.json`.
3. Проверьте `emergency.count`; при значении больше нуля прочитайте
   `emergency_problem_log.jsonl` — это сбои самой диагностической подсистемы.
4. Если `unresolved_count` больше нуля, откройте `latest_unresolved_problem.json`.
5. Для хронологии читайте `events.jsonl`.
6. Для жизненного цикла ошибок читайте `incidents.jsonl`.
7. `ai_problem_log.jsonl` — совместимое зеркало `events.jsonl` для старых инструментов.
8. Полный снимок выбранного WARNING/ERROR/CRITICAL находится по
   `attachment.content_path`; `attachment.path` содержит небольшой указатель события.
9. `validation_report.json` проверяет JSONL, связи событий и доступные вложения.
10. `manifest.json` содержит версии программы, Python, yt-dlp, FFmpeg и параметры среды.
11. `health_history.jsonl` хранит по одной итоговой записи на запуск.

## Важные поля

- `event_id`, `parent_event_id`, `task_id`, `attempt_id`, `command_id` связывают действия.
- `error_signature` — стабильная сигнатура без времени, PID, путей и прогресса.
- `problem_fingerprint` — точный отпечаток конкретного события для совместимости.
- `incident_id` и `status` показывают переходы `detected -> retrying -> recovered`.
- `strategy_metrics`, `strategy_findings` и `recommended_next_checks` показывают
  неэффективные стратегии и подтверждённо успешные fallback даже при итоговом успехе.
- В `yt_dlp_download_attempt` сетевые счётчики уже включают текущую попытку.
- `attachment.status=suppressed_repetition` означает, что полный повтор не записан из-за лимита.
- Одинаковые диагностические блоки хранятся один раз по SHA-256.

## Ограничения хранения

- Полные детали: максимум 20 событий одной сигнатуры.
- Полные снимки старше 30 дней сжимаются в `.json.gz`;
  исходный `.json` остаётся маленьким указателем на архив.
- Сессии старше 60 дней собираются в проверенный
  `session_archive.zip`; сводка и `archive_manifest.json` остаются рядом.
- История проблем: 120 дней.
- Общий предел распознанных сессий: 250 МБ.
- Очистка не затрагивает посторонние файлы и каталоги пользователя.

Секреты, cookies, пароли, токены и данные авторизации перед записью маскируются.

Отдельная проверка: `python problem_log_validator.py`.
Постоянные сценарии схем 3/4: `python problem_log_validator.py --examples`.
