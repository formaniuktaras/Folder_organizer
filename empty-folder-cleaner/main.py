import csv
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


APP_NAME = "Empty Folder Cleaner"
SETTINGS_FILE = Path(__file__).resolve().parent / "settings.json"
DEFAULT_IGNORED_FILES = ["desktop.ini"]


@dataclass
class ScanResult:
    path: str
    folder_name: str
    status: str
    action: str
    error: str = ""


class ScanWorker(QObject):
    progress = Signal(object)
    log = Signal(str)
    finished = Signal(list, bool)

    def __init__(self, root_dir: str, target_names: list[str], ignored_files_enabled: bool, ignored_files: list[str]):
        super().__init__()
        self.root_dir = root_dir
        self.target_names = set(target_names)
        self.ignored_files_enabled = ignored_files_enabled
        self.ignored_files = {name.casefold() for name in ignored_files if name.strip()}
        self.cancel_requested = False

    def cancel(self):
        self.cancel_requested = True

    @Slot()
    def run(self):
        results: list[ScanResult] = []
        cancelled = False
        try:
            for dirpath, dirnames, _ in os.walk(self.root_dir):
                if self.cancel_requested:
                    cancelled = True
                    break
                for dirname in list(dirnames):
                    if self.cancel_requested:
                        cancelled = True
                        break
                    if dirname not in self.target_names:
                        continue
                    folder_path = os.path.join(dirpath, dirname)
                    try:
                        status, action = self._evaluate_folder(folder_path, self.ignored_files_enabled, self.ignored_files)
                        result = ScanResult(folder_path, dirname, status, action)
                        results.append(result)
                        self.progress.emit(result)
                    except (PermissionError, FileNotFoundError, OSError) as exc:
                        result = ScanResult(folder_path, dirname, "помилка", "пропущено", error=str(exc))
                        results.append(result)
                        self.progress.emit(result)
                        self.log.emit(f"Помилка доступу до '{folder_path}': {exc}")
                    except Exception as exc:
                        result = ScanResult(folder_path, dirname, "помилка", "пропущено", error=str(exc))
                        results.append(result)
                        self.progress.emit(result)
                        self.log.emit(f"Неочікувана помилка для '{folder_path}': {exc}")
                if cancelled:
                    break
        except (PermissionError, FileNotFoundError, OSError) as exc:
            self.log.emit(f"Критична помилка сканування: {exc}")
        except Exception as exc:
            self.log.emit(f"Неочікувана критична помилка сканування: {exc}")

        self.finished.emit(results, cancelled)

    @staticmethod
    def _evaluate_folder(folder_path: str, ignored_files_enabled: bool, ignored_files: set[str]) -> tuple[str, str]:
        has_subdirs = False
        has_non_ignored_files = False
        has_ignored_files = False

        with os.scandir(folder_path) as entries:
            for entry in entries:
                if entry.is_dir(follow_symlinks=False):
                    has_subdirs = True
                    break
                if entry.is_file(follow_symlinks=False):
                    if ignored_files_enabled and entry.name.casefold() in ignored_files:
                        has_ignored_files = True
                    else:
                        has_non_ignored_files = True
                        break
                else:
                    has_non_ignored_files = True
                    break

        if has_subdirs or has_non_ignored_files:
            return "не порожня", "пропущено"
        if has_ignored_files:
            return "умовно порожня", "кандидат на видалення"
        return "порожня", "кандидат на видалення"


class SettingsDialog(QDialog):
    def __init__(
        self,
        parent,
        protect_enabled: bool,
        protect_nested: bool,
        protected_dirs: list[str],
        ignored_files_enabled: bool,
        ignored_files: list[str],
    ):
        super().__init__(parent)
        self.setWindowTitle("Налаштування")
        self.resize(760, 560)

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Захищені директорії"))
        self.protect_enabled_checkbox = QCheckBox("Увімкнути захист директорій")
        self.protect_enabled_checkbox.setChecked(protect_enabled)
        layout.addWidget(self.protect_enabled_checkbox)

        self.protect_nested_checkbox = QCheckBox("Блокувати також вкладені папки захищених директорій")
        self.protect_nested_checkbox.setChecked(protect_nested)
        layout.addWidget(self.protect_nested_checkbox)

        self.list_widget = QListWidget()
        for path in protected_dirs:
            self.list_widget.addItem(QListWidgetItem(path))
        layout.addWidget(self.list_widget)

        buttons = QHBoxLayout()
        add_btn = QPushButton("Додати директорію")
        add_btn.clicked.connect(self.add_directory)
        buttons.addWidget(add_btn)
        remove_btn = QPushButton("Видалити зі списку")
        remove_btn.clicked.connect(self.remove_directory)
        buttons.addWidget(remove_btn)
        clear_btn = QPushButton("Очистити список")
        clear_btn.clicked.connect(self.list_widget.clear)
        buttons.addWidget(clear_btn)
        layout.addLayout(buttons)

        layout.addWidget(QLabel("Ігноровані файли при перевірці порожності"))
        self.ignored_enabled_checkbox = QCheckBox("Увімкнути ігнорування службових файлів")
        self.ignored_enabled_checkbox.setChecked(ignored_files_enabled)
        layout.addWidget(self.ignored_enabled_checkbox)

        self.ignored_list_widget = QListWidget()
        for name in ignored_files:
            self.ignored_list_widget.addItem(QListWidgetItem(name))
        layout.addWidget(self.ignored_list_widget)

        ignored_controls = QHBoxLayout()
        self.ignored_file_edit = QLineEdit()
        self.ignored_file_edit.setPlaceholderText("Введіть ім'я файлу, наприклад desktop.ini")
        ignored_controls.addWidget(self.ignored_file_edit)
        ignored_add_btn = QPushButton("Додати файл")
        ignored_add_btn.clicked.connect(self.add_ignored_file)
        ignored_controls.addWidget(ignored_add_btn)
        ignored_remove_btn = QPushButton("Видалити зі списку")
        ignored_remove_btn.clicked.connect(self.remove_ignored_file)
        ignored_controls.addWidget(ignored_remove_btn)
        ignored_clear_btn = QPushButton("Очистити список")
        ignored_clear_btn.clicked.connect(self.ignored_list_widget.clear)
        ignored_controls.addWidget(ignored_clear_btn)
        layout.addLayout(ignored_controls)

        action_buttons = QHBoxLayout()
        save_btn = QPushButton("Зберегти")
        save_btn.clicked.connect(self.accept)
        action_buttons.addWidget(save_btn)
        cancel_btn = QPushButton("Скасувати")
        cancel_btn.clicked.connect(self.reject)
        action_buttons.addWidget(cancel_btn)
        layout.addLayout(action_buttons)

    def add_directory(self):
        folder = QFileDialog.getExistingDirectory(self, "Оберіть захищену директорію")
        if folder:
            existing = {self.list_widget.item(i).text() for i in range(self.list_widget.count())}
            if folder not in existing:
                self.list_widget.addItem(QListWidgetItem(folder))

    def remove_directory(self):
        for item in self.list_widget.selectedItems():
            self.list_widget.takeItem(self.list_widget.row(item))

    def add_ignored_file(self):
        name = self.ignored_file_edit.text().strip()
        if not name:
            return
        existing = {self.ignored_list_widget.item(i).text().casefold() for i in range(self.ignored_list_widget.count())}
        if name.casefold() not in existing:
            self.ignored_list_widget.addItem(QListWidgetItem(name))
        self.ignored_file_edit.clear()

    def remove_ignored_file(self):
        for item in self.ignored_list_widget.selectedItems():
            self.ignored_list_widget.takeItem(self.ignored_list_widget.row(item))

    def get_directories(self) -> list[str]:
        return [self.list_widget.item(i).text().strip() for i in range(self.list_widget.count()) if self.list_widget.item(i).text().strip()]

    def get_ignored_files(self) -> list[str]:
        files: list[str] = []
        seen: set[str] = set()
        for i in range(self.ignored_list_widget.count()):
            name = self.ignored_list_widget.item(i).text().strip()
            key = name.casefold()
            if name and key not in seen:
                seen.add(key)
                files.append(name)
        return files


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1100, 700)

        self.results: list[ScanResult] = []
        self.scan_thread: QThread | None = None
        self.scan_worker: ScanWorker | None = None
        self.current_root_dir: str = ""

        self.protect_directories_enabled = False
        self.protect_nested_directories = True
        self.protected_directories: list[str] = []
        self.ignored_files_enabled = True
        self.ignored_files: list[str] = DEFAULT_IGNORED_FILES.copy()

        self._build_ui()
        self._load_settings()
        self._update_action_buttons_after_scan()

    def _build_ui(self):
        central = QWidget(self)
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        folder_layout = QHBoxLayout()
        folder_layout.addWidget(QLabel("Головна директорія:"))
        self.root_dir_edit = QLineEdit()
        folder_layout.addWidget(self.root_dir_edit)
        self.select_folder_btn = QPushButton("Обрати папку")
        self.select_folder_btn.clicked.connect(self.choose_folder)
        folder_layout.addWidget(self.select_folder_btn)
        main_layout.addLayout(folder_layout)

        main_layout.addWidget(QLabel("Назви папок для пошуку (по одній в рядку):"))
        self.names_text = QPlainTextEdit()
        self.names_text.setPlaceholderText("Наприклад:\ncache\ntemp\nempty")
        self.names_text.setFixedHeight(120)
        main_layout.addWidget(self.names_text)

        controls_layout = QHBoxLayout()
        self.dry_run_checkbox = QCheckBox("Тестовий режим без видалення")
        self.dry_run_checkbox.setChecked(True)
        controls_layout.addWidget(self.dry_run_checkbox)

        self.scan_btn = QPushButton("Сканувати")
        self.scan_btn.clicked.connect(self.start_scan)
        controls_layout.addWidget(self.scan_btn)

        self.stop_scan_btn = QPushButton("Зупинити сканування")
        self.stop_scan_btn.clicked.connect(self.stop_scan)
        self.stop_scan_btn.setEnabled(False)
        controls_layout.addWidget(self.stop_scan_btn)

        self.delete_btn = QPushButton("Видалити порожні")
        self.delete_btn.clicked.connect(self.delete_empty)
        self.delete_btn.setEnabled(False)
        controls_layout.addWidget(self.delete_btn)

        self.export_btn = QPushButton("Експорт результатів у CSV")
        self.export_btn.clicked.connect(self.export_csv)
        self.export_btn.setEnabled(False)
        controls_layout.addWidget(self.export_btn)

        self.clear_btn = QPushButton("Очистити результати")
        self.clear_btn.clicked.connect(self.clear_results)
        controls_layout.addWidget(self.clear_btn)

        self.settings_btn = QPushButton("Налаштування")
        self.settings_btn.clicked.connect(self.open_settings)
        controls_layout.addWidget(self.settings_btn)

        main_layout.addLayout(controls_layout)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Шлях", "Назва", "Статус", "Дія"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 600)
        self.table.setColumnWidth(1, 150)
        self.table.setColumnWidth(2, 120)
        self.table.setColumnWidth(3, 180)
        main_layout.addWidget(self.table)

        main_layout.addWidget(QLabel("Журнал подій:"))
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFixedHeight(140)
        main_layout.addWidget(self.log_text)

    def _normalize(self, path: str) -> str:
        return os.path.normcase(os.path.normpath(os.path.abspath(path)))

    def _is_subpath_or_same(self, parent: str, child: str) -> bool:
        try:
            return self._normalize(os.path.commonpath([parent, child])) == self._normalize(parent)
        except ValueError:
            return False

    def _is_protected_directory(self, candidate: str) -> bool:
        if not self.protect_directories_enabled:
            return False
        norm_candidate = self._normalize(candidate)
        for protected in self.protected_directories:
            norm_protected = self._normalize(protected)
            if norm_candidate == norm_protected:
                return True
            if self.protect_nested_directories and self._is_subpath_or_same(norm_protected, norm_candidate):
                return True
        return False

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Оберіть головну директорію")
        if folder:
            self.root_dir_edit.setText(folder)
            self.log(f"Обрано директорію: {folder}")
            self._save_settings()

    def get_target_names(self) -> list[str]:
        names = [line.strip() for line in self.names_text.toPlainText().splitlines()]
        return [name for name in names if name]

    def validate_root_dir(self) -> bool:
        root_dir = self.root_dir_edit.text().strip()
        if not root_dir:
            QMessageBox.warning(self, "Увага", "Оберіть головну директорію.")
            return False
        if not os.path.isdir(root_dir):
            QMessageBox.warning(self, "Увага", "Вказана директорія не існує.")
            return False
        if self._is_protected_directory(root_dir):
            QMessageBox.warning(self, "Захищена директорія", "Сканування скасовано: обрана директорія належить до захищених.")
            self.log(f"Сканування заблоковано: '{root_dir}' належить до захищених директорій.")
            return False
        return True

    def start_scan(self):
        if not self.validate_root_dir():
            return

        target_names = self.get_target_names()
        if not target_names:
            QMessageBox.warning(self, "Увага", "Додайте хоча б одну назву папки для пошуку.")
            return

        self._save_settings()
        self.clear_results(log_message=False)
        self.current_root_dir = self.root_dir_edit.text().strip()

        self.log("Початок сканування")
        self.log(f"Обрана головна директорія: {self.current_root_dir}")
        self.log(f"Кількість назв папок у списку: {len(target_names)}")
        self.log(f"Захист директорій: {'увімкнено' if self.protect_directories_enabled else 'вимкнено'}")
        self.log(f"Кількість директорій у списку захисту: {len(self.protected_directories)}")
        self.log(f"Ігнорування службових файлів: {'увімкнено' if self.ignored_files_enabled else 'вимкнено'}")
        self.log(f"Список ігнорованих файлів: {', '.join(self.ignored_files) if self.ignored_files else '(порожній)'}")

        self.scan_btn.setEnabled(False)
        self.stop_scan_btn.setEnabled(True)
        self.delete_btn.setEnabled(False)
        self.export_btn.setEnabled(False)

        self.scan_thread = QThread()
        self.scan_worker = ScanWorker(self.current_root_dir, target_names, self.ignored_files_enabled, self.ignored_files)
        self.scan_worker.moveToThread(self.scan_thread)

        self.scan_thread.started.connect(self.scan_worker.run)
        self.scan_worker.progress.connect(self._append_result)
        self.scan_worker.log.connect(self.log)
        self.scan_worker.finished.connect(self._scan_finished)
        self.scan_worker.finished.connect(self.scan_thread.quit)
        self.scan_worker.finished.connect(self.scan_worker.deleteLater)
        self.scan_thread.finished.connect(self.scan_thread.deleteLater)

        self.scan_thread.start()

    def stop_scan(self):
        if self.scan_worker:
            self.scan_worker.cancel()
            self.stop_scan_btn.setEnabled(False)
            self.log("Запит на зупинку сканування надіслано...")

    @Slot(list, bool)
    def _scan_finished(self, results: list[ScanResult], cancelled: bool):
        self.results = results
        self.scan_btn.setEnabled(True)
        self.stop_scan_btn.setEnabled(False)

        matches = len(results)
        empty_count = sum(1 for r in results if r.status == "порожня")
        conditional_empty_count = sum(1 for r in results if r.status == "умовно порожня")
        non_empty_count = sum(1 for r in results if r.status == "не порожня")
        errors_count = sum(1 for r in results if r.status == "помилка")

        self.log(f"Кількість знайдених збігів: {matches}")
        self.log(f"Кількість повністю порожніх папок: {empty_count}")
        self.log(f"Кількість умовно порожніх папок: {conditional_empty_count}")
        self.log(f"Кількість пропущених непорожніх папок: {non_empty_count}")
        self.log(f"Кількість помилок: {errors_count}")

        if cancelled:
            self.log("Сканування зупинено користувачем")
        self.log("Завершення сканування")

        self._update_action_buttons_after_scan()

    def _update_action_buttons_after_scan(self):
        has_results = len(self.results) > 0
        can_delete = any(r.status in {"порожня", "умовно порожня"} and r.action == "кандидат на видалення" for r in self.results)
        self.export_btn.setEnabled(has_results)
        self.delete_btn.setEnabled(can_delete)

    @Slot(object)
    def _append_result(self, result: ScanResult):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(result.path))
        self.table.setItem(row, 1, QTableWidgetItem(result.folder_name))
        self.table.setItem(row, 2, QTableWidgetItem(result.status))
        self.table.setItem(row, 3, QTableWidgetItem(result.action))

    def _recheck_for_deletion(self, folder_path: str) -> tuple[bool, list[str], str]:
        ignored_to_remove: list[str] = []
        with os.scandir(folder_path) as entries:
            for entry in entries:
                if entry.is_dir(follow_symlinks=False):
                    return False, [], "містить підпапки"
                if entry.is_file(follow_symlinks=False):
                    if self.ignored_files_enabled and entry.name.casefold() in {n.casefold() for n in self.ignored_files if n.strip()}:
                        ignored_to_remove.append(entry.path)
                    else:
                        return False, [], f"знайдено неігнорований файл: {entry.name}"
                else:
                    return False, [], f"містить неочікуваний тип елемента: {entry.name}"
        return True, ignored_to_remove, ""

    def delete_empty(self):
        if not self.validate_root_dir():
            return

        root_dir = os.path.abspath(self.root_dir_edit.text().strip())
        norm_root = self._normalize(root_dir)
        dry_run = self.dry_run_checkbox.isChecked()

        candidates = [r for r in self.results if r.status in {"порожня", "умовно порожня"} and r.action == "кандидат на видалення"]
        if not candidates:
            QMessageBox.information(self, "Інформація", "Немає порожніх папок для видалення.")
            return

        confirm = QMessageBox.question(self, "Підтвердження видалення", f"Ви дійсно хочете обробити {len(candidates)} папок-кандидатів?")
        if confirm != QMessageBox.Yes:
            self.log("Користувач скасував видалення.")
            return

        processed = deleted_count = skipped_count = error_count = removed_ignored_files_count = 0
        for result in candidates:
            processed += 1
            target = os.path.abspath(result.path)
            norm_target = self._normalize(target)

            if norm_target == norm_root:
                result.action = "пропущено"
                skipped_count += 1
                self.log(f"Пропущено головну директорію: {target}")
                continue

            if self.protect_directories_enabled and self._is_protected_directory(target):
                result.action = "пропущено"
                skipped_count += 1
                self.log(f"Пропущено (захищена директорія): {target}")
                continue

            if not self._is_subpath_or_same(root_dir, target):
                result.action = "пропущено"
                skipped_count += 1
                self.log(f"Пропущено (поза головною директорією): {target}")
                continue

            if not os.path.exists(target):
                result.status = "помилка"
                result.action = "пропущено"
                result.error = "Папка вже не існує"
                error_count += 1
                self.log(f"Помилка: папка не існує: {target}")
                continue

            try:
                can_delete, ignored_file_paths, reason = self._recheck_for_deletion(target)
                if not can_delete:
                    result.status = "не порожня"
                    result.action = "пропущено"
                    skipped_count += 1
                    self.log(f"Пропущено '{target}': {reason}")
                    continue

                if dry_run:
                    result.action = "буде видалено"
                    self.log(f"Тестовий режим: буде видалено {target}")
                    continue

                for ignored_file in ignored_file_paths:
                    try:
                        os.remove(ignored_file)
                        removed_ignored_files_count += 1
                    except (PermissionError, FileNotFoundError, OSError) as exc:
                        raise OSError(f"Не вдалося видалити службовий файл '{ignored_file}': {exc}") from exc

                os.rmdir(target)
                result.action = "видалено"
                deleted_count += 1
                self.log(f"Видалено: {target}")
            except (PermissionError, FileNotFoundError, OSError) as exc:
                result.status = "помилка"
                result.action = "пропущено"
                result.error = str(exc)
                error_count += 1
                self.log(f"Помилка видалення '{target}': {exc}")
            except Exception as exc:
                result.status = "помилка"
                result.action = "пропущено"
                result.error = str(exc)
                error_count += 1
                self.log(f"Неочікувана помилка видалення '{target}': {exc}")

        self._refresh_table()
        self._update_action_buttons_after_scan()
        self.log(
            "Результат видалення: "
            f"оброблено={processed}, видалено={deleted_count}, пропущено={skipped_count}, помилок={error_count}, "
            f"видалено службових файлів={removed_ignored_files_count}"
        )

        if dry_run:
            QMessageBox.information(self, "Тестовий режим", f"Оброблено папок: {processed}. Позначено 'буде видалено': {sum(1 for r in self.results if r.action == 'буде видалено')}")
        else:
            QMessageBox.information(self, "Готово", f"Видалено папок: {deleted_count}. Видалено службових файлів: {removed_ignored_files_count}")

    def _refresh_table(self):
        self.table.setRowCount(0)
        for result in self.results:
            self._append_result(result)

    def export_csv(self):
        if not self.results:
            QMessageBox.information(self, "Інформація", "Немає результатів для експорту.")
            return

        file_path, _ = QFileDialog.getSaveFileName(self, "Зберегти CSV", "results.csv", "CSV файли (*.csv)")
        if not file_path:
            return

        try:
            with open(file_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(["Шлях", "Назва", "Статус", "Дія", "Помилка"])
                for r in self.results:
                    writer.writerow([r.path, r.folder_name, r.status, r.action, r.error])
            self.log(f"Експортовано в CSV: {file_path}")
        except (PermissionError, FileNotFoundError, OSError) as exc:
            self.log(f"Помилка експорту CSV: {exc}")
            QMessageBox.critical(self, "Помилка", f"Не вдалося експортувати CSV: {exc}")

    def clear_results(self, log_message: bool = True):
        self.results = []
        self.table.setRowCount(0)
        self._update_action_buttons_after_scan()
        if log_message:
            self.log("Результати очищено.")

    def log(self, message: str):
        self.log_text.appendPlainText(message)

    def open_settings(self):
        dialog = SettingsDialog(
            self,
            self.protect_directories_enabled,
            self.protect_nested_directories,
            self.protected_directories,
            self.ignored_files_enabled,
            self.ignored_files,
        )
        if dialog.exec() == QDialog.Accepted:
            self.protect_directories_enabled = dialog.protect_enabled_checkbox.isChecked()
            self.protect_nested_directories = dialog.protect_nested_checkbox.isChecked()
            self.protected_directories = dialog.get_directories()
            self.ignored_files_enabled = dialog.ignored_enabled_checkbox.isChecked()
            self.ignored_files = dialog.get_ignored_files()
            self._save_settings()
            self.log("Налаштування оновлено.")

    def _save_settings(self):
        data = {
            "last_directory": self.root_dir_edit.text().strip(),
            "folder_names": self.names_text.toPlainText(),
            "dry_run": self.dry_run_checkbox.isChecked(),
            "protect_directories_enabled": self.protect_directories_enabled,
            "protect_nested_directories": self.protect_nested_directories,
            "protected_directories": self.protected_directories,
            "ignored_files_enabled": self.ignored_files_enabled,
            "ignored_files": self.ignored_files,
        }
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except (PermissionError, FileNotFoundError, OSError) as exc:
            self.log(f"Помилка збереження налаштувань: {exc}")

    def _load_settings(self):
        if not SETTINGS_FILE.exists():
            return
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.root_dir_edit.setText(data.get("last_directory", ""))
            self.names_text.setPlainText(data.get("folder_names", ""))
            self.dry_run_checkbox.setChecked(data.get("dry_run", True))
            self.protect_directories_enabled = data.get("protect_directories_enabled", False)
            self.protect_nested_directories = data.get("protect_nested_directories", True)
            self.protected_directories = data.get("protected_directories", [])
            self.ignored_files_enabled = data.get("ignored_files_enabled", True)
            self.ignored_files = data.get("ignored_files", DEFAULT_IGNORED_FILES.copy())
            if not isinstance(self.ignored_files, list):
                self.ignored_files = DEFAULT_IGNORED_FILES.copy()
            self.log("Налаштування завантажено.")
        except (PermissionError, FileNotFoundError, OSError, json.JSONDecodeError) as exc:
            self.log(f"Помилка завантаження налаштувань: {exc}")

    def closeEvent(self, event):
        if self.scan_worker:
            self.scan_worker.cancel()
        if self.scan_thread and self.scan_thread.isRunning():
            self.scan_thread.quit()
            self.scan_thread.wait(2000)
        self._save_settings()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
