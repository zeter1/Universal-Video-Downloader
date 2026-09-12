# downloads/ — локальная карта Codex

Сначала `python scripts/codex_scope.py "<задача>"`, затем только owner-файл.

- start/queue: `manager.py`, `task.py`
- yt-dlp orchestration: `youtube_downloader.py`
- attempt context/first logs: `youtube_attempt.py`
- retry/access classification: `youtube_retry.py`
- FFmpeg/postprocessing failure analysis: `youtube_failure_diagnostics.py`, `youtube_failure_reporting.py`
- postprocessing recovery transitions: `youtube_recovery.py`
- successful result / quality candidate: `youtube_success.py`
- final lower-quality fallback/final failure report: `youtube_finalize.py`
- strategy catalog: `youtube_strategy_catalog.py`
- adaptive network decisions: `youtube_strategy.py`
- CLI args/format selector: `youtube_command.py`
- proxy/EJS: `youtube_access.py`
- result detection: `ytdlp_results.py`, `files.py`
- mandatory final MP4 publication/remux/transcode fallback: `mp4_output.py`, `files.py`

Не импортировать `src.core.runtime`; брать constants/errors/settings из owner-модулей.
Сохранять контракт max 1080p и обязательный финальный `.mp4`. Сначала релевантный test-файл, затем полный suite.
