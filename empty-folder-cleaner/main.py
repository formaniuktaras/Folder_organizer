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
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


APP_NAME = "Empty Folder Cleaner"
SETTINGS_FILE = Path(__file__).resolve().parent / "settings.json"

# Заборонені системні шляхи Windows. Порівняння робимо через normcase/normpath.
FORBIDDEN_PATHS = {
    os.path.normcase(os.path.normpath(r"C:\\Windows")),
    os.path.normcase(os.path.normpath(r"C:\\Program Files")),
    os.path.normcase(os.path.normpath(r"C:\\Users")),
}


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
    finished = Signal(list)

    def __init__(self, root_dir: str, target_names: list[str], dry_run: bool):
        super().__init__()
        self.root_dir = root_dir
        self.target_names = set(target_names)
        self.dry_run = dry_run

    @Slot()
    def run(self):
        results: list[ScanResult] = []
        self.log.emit(f"Почато сканування: {self.root_dir}")
        try:
            for dirpath, dirnames, _ in os.walk(self.root_dir):
                # Створюємо копію, щоб безпечно ітеруватись.
                for dirname in list(dirnames):
                    if dirname not in self.target_names:
                        continue

                    folder_path = os.path.join(dirpath, dirname)
                    try:
                        is_empty = self._is_folder_empty(folder_path)
                        status = "порожня" if is_empty else "не порожня"
                        action = "буде видалено" if is_empty else "пропущено"
                        result = ScanResult(folder_path, dirname, status, action)
                        results.append(result)
                        self.progress.emit(result)
                    except Exception as exc:
                        result = ScanResult(
                            folder_path,
                            dirname,
                            "помилка",
                            "пропущено",
                            error=str(exc),
                        )
                        results.append(result)
                        self.progress.emit(result)
                        self.log.emit(f"Помилка доступу до '{folder_path}': {exc}")
        except Exception as exc:
            self.log.emit(f"Критична помилка сканування: {exc}")

        self.log.emit(f"Сканування завершено. Знайдено: {len(results)}")
        self.finished.emit(results)

    @staticmethod
    def _is_folder_empty(folder_path: str) -> bool:
        with os.scandir(folder_path) as entries:
            for _ in entries:
                return False
        return True


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1100, 700)

        self.results: list[ScanResult] = []
        self.scan_thread: QThread | None = None
        self.scan_worker: ScanWorker | None = None

        self._build_ui()
        self._load_settings()

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

        self.delete_btn = QPushButton("Видалити порожні")
        self.delete_btn.clicked.connect(self.delete_empty)
        controls_layout.addWidget(self.delete_btn)

        self.export_btn = QPushButton("Експорт результатів у CSV")
        self.export_btn.clicked.connect(self.export_csv)
        controls_layout.addWidget(self.export_btn)

        self.clear_btn = QPushButton("Очистити результати")
        self.clear_btn.clicked.connect(self.clear_results)
        controls_layout.addWidget(self.clear_btn)

        main_layout.addLayout(controls_layout)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Шлях", "Назва", "Статус", "Дія"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 600)
        self.table.setColumnWidth(1, 150)
        self.table.setColumnWidth(2, 120)
        self.table.setColumnWidth(3, 120)
        main_layout.addWidget(self.table)

        main_layout.addWidget(QLabel("Журнал подій:"))
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFixedHeight(140)
        main_layout.addWidget(self.log_text)

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
        return True

    def start_scan(self):
        if not self.validate_root_dir():
            return

        target_names = self.get_target_names()
        if not target_names:
            QMessageBox.warning(self, "Увага", "Додайте хоча б одну назву папки для пошуку.")
            return

        self._save_settings()
        self.clear_results()
        self.log("Підготовка до сканування...")

        self.scan_btn.setEnabled(False)
        self.delete_btn.setEnabled(False)

        self.scan_thread = QThread()
        self.scan_worker = ScanWorker(
            self.root_dir_edit.text().strip(),
            target_names,
            self.dry_run_checkbox.isChecked(),
        )
        self.scan_worker.moveToThread(self.scan_thread)

        self.scan_thread.started.connect(self.scan_worker.run)
        self.scan_worker.progress.connect(self._append_result)
        self.scan_worker.log.connect(self.log)
        self.scan_worker.finished.connect(self._scan_finished)
        self.scan_worker.finished.connect(self.scan_thread.quit)
        self.scan_worker.finished.connect(self.scan_worker.deleteLater)
        self.scan_thread.finished.connect(self.scan_thread.deleteLater)

        self.scan_thread.start()

    @Slot(list)
    def _scan_finished(self, results: list[ScanResult]):
        self.results = results
        self.scan_btn.setEnabled(True)
        self.delete_btn.setEnabled(True)
        empty_count = sum(1 for r in results if r.status == "порожня")
        self.log(f"Готово. Порожніх папок для обробки: {empty_count}")

    @Slot(object)
    def _append_result(self, result: ScanResult):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(result.path))
        self.table.setItem(row, 1, QTableWidgetItem(result.folder_name))
        self.table.setItem(row, 2, QTableWidgetItem(result.status))
        self.table.setItem(row, 3, QTableWidgetItem(result.action))

    def delete_empty(self):
        if not self.validate_root_dir():
            return

        root_dir = os.path.abspath(self.root_dir_edit.text().strip())
        norm_root = os.path.normcase(os.path.normpath(root_dir))
        dry_run = self.dry_run_checkbox.isChecked()

        candidates = [r for r in self.results if r.status == "порожня"]
        if not candidates:
            QMessageBox.information(self, "Інформація", "Немає порожніх папок для видалення.")
            return

        if dry_run:
            self.log("Тестовий режим увімкнений: видалення не виконується.")
            QMessageBox.information(
                self,
                "Тестовий режим",
                f"Тестовий режим: {len(candidates)} папок позначено як 'буде видалено'.",
            )
            return

        confirm = QMessageBox.question(
            self,
            "Підтвердження видалення",
            f"Ви дійсно хочете видалити {len(candidates)} порожніх папок?",
        )
        if confirm != QMessageBox.Yes:
            self.log("Користувач скасував видалення.")
            return

        deleted_count = 0
        for result in candidates:
            target = os.path.abspath(result.path)
            norm_target = os.path.normcase(os.path.normpath(target))

            # Критична логіка безпеки: не видаляємо root та системні каталоги.
            if norm_target == norm_root or norm_target in FORBIDDEN_PATHS:
                result.action = "пропущено"
                self.log(f"Пропущено (заборонено): {target}")
                continue

            # Додатковий захист: видалення тільки всередині root.
            try:
                common = os.path.commonpath([root_dir, target])
            except ValueError:
                common = ""
            if os.path.normcase(os.path.normpath(common)) != norm_root:
                result.action = "пропущено"
                self.log(f"Пропущено (поза головною директорією): {target}")
                continue

            try:
                if ScanWorker._is_folder_empty(target):
                    os.rmdir(target)
                    result.action = "видалено"
                    deleted_count += 1
                    self.log(f"Видалено: {target}")
                else:
                    result.action = "пропущено"
                    result.status = "не порожня"
                    self.log(f"Пропущено (вже не порожня): {target}")
            except Exception as exc:
                result.status = "помилка"
                result.action = "пропущено"
                result.error = str(exc)
                self.log(f"Помилка видалення '{target}': {exc}")

        self._refresh_table()
        QMessageBox.information(self, "Готово", f"Видалено папок: {deleted_count}")

    def _refresh_table(self):
        self.table.setRowCount(0)
        for result in self.results:
            self._append_result(result)

    def export_csv(self):
        if not self.results:
            QMessageBox.information(self, "Інформація", "Немає результатів для експорту.")
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Зберегти CSV",
            "results.csv",
            "CSV файли (*.csv)",
        )
        if not file_path:
            return

        try:
            with open(file_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(["Шлях", "Назва", "Статус", "Дія", "Помилка"])
                for r in self.results:
                    writer.writerow([r.path, r.folder_name, r.status, r.action, r.error])
            self.log(f"Експортовано в CSV: {file_path}")
        except Exception as exc:
            self.log(f"Помилка експорту CSV: {exc}")
            QMessageBox.critical(self, "Помилка", f"Не вдалося експортувати CSV: {exc}")

    def clear_results(self):
        self.results = []
        self.table.setRowCount(0)
        self.log("Результати очищено.")

    def log(self, message: str):
        self.log_text.appendPlainText(message)

    def _save_settings(self):
        data = {
            "last_directory": self.root_dir_edit.text().strip(),
            "folder_names": self.names_text.toPlainText(),
        }
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            self.log(f"Помилка збереження налаштувань: {exc}")

    def _load_settings(self):
        if not SETTINGS_FILE.exists():
            return
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.root_dir_edit.setText(data.get("last_directory", ""))
            self.names_text.setPlainText(data.get("folder_names", ""))
            self.log("Налаштування завантажено.")
        except Exception as exc:
            self.log(f"Помилка завантаження налаштувань: {exc}")

    def closeEvent(self, event):
        self._save_settings()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
