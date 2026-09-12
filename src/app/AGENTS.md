# app/
Owner только для инициализации приложения.
- `paths.py`: пути, папки, legacy migration.
- `state.py`: mutable runtime/diagnostics state.
Не добавлять сюда download/UI бизнес-логику.
При изменении проверить импорт `src.application` и полный unittest suite.
