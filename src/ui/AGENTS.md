# ui/
Только Tkinter presentation/input handling.
Download-бизнес-решения должны оставаться в `src/downloads/`.
`layout.py` — сборка UI; остальные файлы — узкие handlers по названию.
Не выполнять сетевую работу в UI thread.
После правок не запускать GUI в автоматическом тесте; прогнать suite.
