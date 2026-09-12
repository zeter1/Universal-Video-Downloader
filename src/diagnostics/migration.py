"""Миграция старых служебных папок без потери файлов.

Автоматически выделено из прежнего модуля problem_diagnostics.py без изменения тел методов.
"""

from pathlib import Path
import shutil


class DiagnosticsMigrationMixin:
    def _unique_path_for_migration(self, path: Path) -> Path:
        """Возвращает свободный путь, чтобы при переносе старых файлов не затереть новые."""
        if not path.exists():
            return path
        stem = path.stem
        suffix = path.suffix
        parent = path.parent
        counter = 2
        while True:
            candidate = parent / f"{stem}_из_старой_папки_{counter}{suffix}"
            if not candidate.exists():
                return candidate
            counter += 1


    def _merge_directories_for_migration(self, src: Path, dst: Path) -> None:
        """Аккуратно переносит содержимое старой папки в новую русскую папку."""
        dst.mkdir(parents=True, exist_ok=True)
        for item in src.iterdir():
            target = dst / item.name
            if item.is_dir() and target.exists() and target.is_dir():
                self._merge_directories_for_migration(item, target)
                try:
                    item.rmdir()
                except OSError:
                    pass
                continue

            if target.exists():
                target = self._unique_path_for_migration(target)
            shutil.move(str(item), str(target))

        try:
            src.rmdir()
        except OSError:
            pass


    def migrate_legacy_folders(self) -> None:
        """Переносит старые английские служебные папки в новые русские названия."""
        for old_path, new_path in getattr(self, "legacy_folder_migrations", []):
            try:
                if not old_path.exists():
                    continue
                if old_path.resolve() == new_path.resolve():
                    continue

                if old_path.is_dir():
                    if new_path.exists():
                        self._merge_directories_for_migration(old_path, new_path)
                        self.migration_notes.append(f"{old_path.name} → {new_path.name} (объединено)")
                    else:
                        new_path.parent.mkdir(parents=True, exist_ok=True)
                        old_path.rename(new_path)
                        self.migration_notes.append(f"{old_path.name} → {new_path.name}")
                else:
                    new_path.parent.mkdir(parents=True, exist_ok=True)
                    target = new_path if not new_path.exists() else self._unique_path_for_migration(new_path)
                    shutil.move(str(old_path), str(target))
                    self.migration_notes.append(f"{old_path.name} → {target.name}")
            except Exception as e:
                self.migration_notes.append(f"Не удалось перенести {old_path}: {e}")
