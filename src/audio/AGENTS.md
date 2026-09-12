# audio/
Owner для pipeline после скачивания: convert / merge / split.
FFmpeg probing/валидация живут в `src/downloads/media_probe.py` и `media_validation.py`.
Не удалять пользовательский исходник до подтвержденного успешного результата.
Проверять `tests/test_audio_pipeline_failures.py` и quality/audio tests.
