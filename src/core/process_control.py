"""Регистрация, остановка и завершение внешних процессов.

Автоматически выделено из прежнего модуля download_runtime.py без изменения тел методов.
"""

import platform
import subprocess


class ProcessControlMixin:
    def _register_process(self, proc: subprocess.Popen) -> None:
        with self.process_lock:
            self.active_processes[proc.pid] = proc


    def _unregister_process(self, proc: subprocess.Popen) -> None:
        with self.process_lock:
            self.active_processes.pop(proc.pid, None)


    def terminate_process(self, proc: subprocess.Popen) -> None:
        if proc.poll() is not None:
            return
        try:
            if platform.system() == "Windows":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True, timeout=10,
                    creationflags=self.subprocess_flags
                )
            else:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        except Exception as e:
            self.record_problem(
                "Не удалось штатно остановить процесс, пробую proc.kill()",
                "WARNING", "terminate_process",
                {"pid": proc.pid}, e
            )
            try:
                proc.kill()
            except Exception:
                pass


    def terminate_active_processes(self) -> int:
        with self.process_lock:
            processes = list(self.active_processes.values())
        if processes:
            self.record_problem(
                "Запрошена остановка активных процессов: снимок перед taskkill/terminate",
                "WARNING", "terminate_active_processes_snapshot",
                {
                    "process_count": len(processes),
                    "active_processes_before_terminate": self._active_processes_snapshot(),
                    "reason": "cancel/close",
                }
            )
        for proc in processes:
            self.terminate_process(proc)
        return len(processes)


    def signal_handler(self, signum, frame) -> None:
        self.on_closing()
