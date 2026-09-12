# diagnostics/
Изолированная подсистема problem logs. Для обычного download/UI бага её не читать целиком.
- запись: `reporting.py`; incidents: `events.py`; context: `snapshots.py`.
- retention entry: `retention.py`; даты/delete: `retention_base.py`; archive/size: `retention_archive.py`; atomic/trim: `retention_storage.py`.
- validator public/CLI: `validator.py`; schema/JSONL: `validator_core.py`; integrity: `validator_integrity.py`; session/examples: `validator_session.py`.
Сохранять redaction секретов, atomic writes и обратную совместимость schema.
Проверять оба diagnostics test-файла, затем полный suite.
