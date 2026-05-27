import csv
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtWidgets import QApplication, QCheckBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget

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


@dataclass
class CatalogCheckResult:
    target_catalog: str
    status: str
    path: str
    found_required: int
    total_required: int
    missing_folders: str
    extra_folders: str
    comment: str


class ScanWorker(QObject):
    progress = Signal(object)
    log = Signal(str)
    finished = Signal(list, bool)
    def __init__(self, root_dir: str, target_names: list[str], ignored_files_enabled: bool, ignored_files: list[str]):
        super().__init__(); self.root_dir = root_dir; self.target_names = set(target_names); self.ignored_files_enabled = ignored_files_enabled; self.ignored_files = {n.casefold() for n in ignored_files if n.strip()}; self.cancel_requested = False
    def cancel(self): self.cancel_requested = True
    @Slot()
    def run(self):
        results=[]; cancelled=False
        try:
            for dirpath, dirnames, _ in os.walk(self.root_dir):
                if self.cancel_requested: cancelled=True; break
                for dirname in list(dirnames):
                    if self.cancel_requested: cancelled=True; break
                    if dirname not in self.target_names: continue
                    folder_path = os.path.join(dirpath, dirname)
                    try:
                        status, action = self._evaluate_folder(folder_path, self.ignored_files_enabled, self.ignored_files)
                        r=ScanResult(folder_path, dirname, status, action); results.append(r); self.progress.emit(r)
                    except (PermissionError, FileNotFoundError, OSError) as exc:
                        r=ScanResult(folder_path, dirname, "помилка", "пропущено", str(exc)); results.append(r); self.progress.emit(r); self.log.emit(f"Помилка доступу до '{folder_path}': {exc}")
                if cancelled: break
        except (PermissionError, FileNotFoundError, OSError) as exc:
            self.log.emit(f"Критична помилка сканування: {exc}")
        self.finished.emit(results, cancelled)
    @staticmethod
    def _evaluate_folder(folder_path: str, ignored_files_enabled: bool, ignored_files: set[str]) -> tuple[str, str]:
        has_subdirs=False; has_non_ignored=False; has_ignored=False
        with os.scandir(folder_path) as entries:
            for e in entries:
                if e.is_dir(follow_symlinks=False): has_subdirs=True; break
                if e.is_file(follow_symlinks=False):
                    if ignored_files_enabled and e.name.casefold() in ignored_files: has_ignored=True
                    else: has_non_ignored=True; break
                else: has_non_ignored=True; break
        if has_subdirs or has_non_ignored: return "не порожня", "пропущено"
        if has_ignored: return "умовно порожня", "кандидат на видалення"
        return "порожня", "кандидат на видалення"


class CatalogCheckWorker(QObject):
    progress = Signal(object)
    log = Signal(str)
    finished = Signal(list, dict, bool)
    def __init__(self, source_dir: str, targets: list[str], required: list[str], ignore_case: bool, trim_spaces: bool, show_full_paths: bool, direct_children_only: bool):
        super().__init__(); self.source_dir=source_dir; self.targets=targets; self.required=required; self.ignore_case=ignore_case; self.trim_spaces=trim_spaces; self.show_full_paths=show_full_paths; self.direct_children_only=direct_children_only; self.cancel_requested=False
    def cancel(self): self.cancel_requested=True
    def _norm(self, name: str) -> str:
        v = name.strip() if self.trim_spaces else name
        return v.casefold() if self.ignore_case else v
    @Slot()
    def run(self):
        results=[]; cancelled=False
        stats={"found":0,"not_found":0,"full":0,"incomplete":0,"with_extra":0,"duplicates":0,"errors":0}
        norm_targets={self._norm(t): t for t in self.targets}; matches={t:[] for t in self.targets}
        try:
            for dirpath, dirnames, _ in os.walk(self.source_dir):
                if self.cancel_requested: cancelled=True; break
                for dirname in list(dirnames):
                    n=self._norm(dirname)
                    if n in norm_targets: matches[norm_targets[n]].append(os.path.join(dirpath, dirname))
        except (PermissionError, FileNotFoundError, OSError) as exc:
            self.log.emit(f"Критична помилка перевірки каталогу: {exc}")

        req={}
        for r in self.required:
            k=self._norm(r)
            if k not in req: req[k]=r
        total=len(req)

        for target in self.targets:
            if self.cancel_requested: cancelled=True; break
            found = matches.get(target, [])
            if not found:
                stats["not_found"] += 1
                r=CatalogCheckResult(target,"не знайдено","",0,total,", ".join(req.values()),"","Каталог не знайдено")
                results.append(r); self.progress.emit(r); continue
            stats["found"] += len(found)
            dup = len(found) > 1
            if dup: stats["duplicates"] += 1
            for path in found:
                try:
                    actual={}
                    with os.scandir(path) as entries:
                        for e in entries:
                            if e.is_dir(follow_symlinks=False):
                                k=self._norm(e.name)
                                if k not in actual: actual[k]=e.name
                    missing=[name for k,name in req.items() if k not in actual]
                    extra=[name for k,name in actual.items() if k not in req]
                    found_required=total-len(missing)
                    status="повний"
                    if missing: status="неповний"; stats["incomplete"] += 1
                    elif extra: status="повний із зайвими"; stats["with_extra"] += 1
                    else: stats["full"] += 1
                    if dup: status="дублікат"
                    shown = path if self.show_full_paths else os.path.relpath(path, self.source_dir)
                    comment = "Знайдено декілька каталогів з такою назвою" if dup else ""
                    r=CatalogCheckResult(target,status,shown,found_required,total,", ".join(missing),", ".join(extra),comment)
                    results.append(r); self.progress.emit(r)
                except (PermissionError, FileNotFoundError, OSError) as exc:
                    stats["errors"] += 1
                    r=CatalogCheckResult(target,"помилка",path,0,total,"","",str(exc)); results.append(r); self.progress.emit(r); self.log.emit(f"Помилка читання '{path}': {exc}")
        self.finished.emit(results, stats, cancelled)


class SettingsDialog(QDialog):
    def __init__(self, parent, protect_enabled, protect_nested, protected_dirs, ignored_files_enabled, ignored_files):
        super().__init__(parent); self.setWindowTitle("Налаштування"); self.resize(760,560)
        layout=QVBoxLayout(self); layout.addWidget(QLabel("Захищені директорії"))
        self.protect_enabled_checkbox=QCheckBox("Увімкнути захист директорій"); self.protect_enabled_checkbox.setChecked(protect_enabled); layout.addWidget(self.protect_enabled_checkbox)
        self.protect_nested_checkbox=QCheckBox("Блокувати також вкладені папки захищених директорій"); self.protect_nested_checkbox.setChecked(protect_nested); layout.addWidget(self.protect_nested_checkbox)
        self.list_widget=QListWidget(); [self.list_widget.addItem(QListWidgetItem(p)) for p in protected_dirs]; layout.addWidget(self.list_widget)
        b=QHBoxLayout();
        for text,cb in [("Додати директорію",self.add_directory),("Видалити зі списку",self.remove_directory),("Очистити список",self.list_widget.clear)]:
            btn=QPushButton(text); btn.clicked.connect(cb); b.addWidget(btn)
        layout.addLayout(b)
        layout.addWidget(QLabel("Ігноровані файли при перевірці порожності"))
        self.ignored_enabled_checkbox=QCheckBox("Увімкнути ігнорування службових файлів"); self.ignored_enabled_checkbox.setChecked(ignored_files_enabled); layout.addWidget(self.ignored_enabled_checkbox)
        self.ignored_list_widget=QListWidget(); [self.ignored_list_widget.addItem(QListWidgetItem(n)) for n in ignored_files]; layout.addWidget(self.ignored_list_widget)
        ib=QHBoxLayout(); self.ignored_file_edit=QLineEdit(); self.ignored_file_edit.setPlaceholderText("Введіть ім'я файлу, наприклад desktop.ini"); ib.addWidget(self.ignored_file_edit)
        for text,cb in [("Додати файл",self.add_ignored_file),("Видалити зі списку",self.remove_ignored_file),("Очистити список",self.ignored_list_widget.clear)]:
            btn=QPushButton(text); btn.clicked.connect(cb); ib.addWidget(btn)
        layout.addLayout(ib)
        ab=QHBoxLayout(); s=QPushButton("Зберегти"); s.clicked.connect(self.accept); ab.addWidget(s); c=QPushButton("Скасувати"); c.clicked.connect(self.reject); ab.addWidget(c); layout.addLayout(ab)
    def add_directory(self):
        folder=QFileDialog.getExistingDirectory(self,"Оберіть захищену директорію")
        if folder and folder not in {self.list_widget.item(i).text() for i in range(self.list_widget.count())}: self.list_widget.addItem(QListWidgetItem(folder))
    def remove_directory(self):
        for item in self.list_widget.selectedItems(): self.list_widget.takeItem(self.list_widget.row(item))
    def add_ignored_file(self):
        name=self.ignored_file_edit.text().strip();
        if name and name.casefold() not in {self.ignored_list_widget.item(i).text().casefold() for i in range(self.ignored_list_widget.count())}: self.ignored_list_widget.addItem(QListWidgetItem(name))
        self.ignored_file_edit.clear()
    def remove_ignored_file(self):
        for item in self.ignored_list_widget.selectedItems(): self.ignored_list_widget.takeItem(self.ignored_list_widget.row(item))
    def get_directories(self): return [self.list_widget.item(i).text().strip() for i in range(self.list_widget.count()) if self.list_widget.item(i).text().strip()]
    def get_ignored_files(self):
        files=[]; seen=set()
        for i in range(self.ignored_list_widget.count()):
            n=self.ignored_list_widget.item(i).text().strip(); k=n.casefold()
            if n and k not in seen: seen.add(k); files.append(n)
        return files


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowTitle(APP_NAME); self.resize(1200,760)
        self.results=[]; self.catalog_results=[]; self.scan_thread=None; self.scan_worker=None; self.catalog_thread=None; self.catalog_worker=None; self.current_root_dir=""; self.current_catalog_source=""
        self.protect_directories_enabled=False; self.protect_nested_directories=True; self.protected_directories=[]; self.ignored_files_enabled=True; self.ignored_files=DEFAULT_IGNORED_FILES.copy()
        self._build_ui(); self._load_settings(); self._update_action_buttons_after_scan(); self._update_catalog_buttons()

    def _build_ui(self):
        c=QWidget(self); self.setCentralWidget(c); m=QVBoxLayout(c); self.tabs=QTabWidget(); m.addWidget(self.tabs)
        self.cleanup_tab=QWidget(); self.catalog_tab=QWidget(); self.tabs.addTab(self.cleanup_tab,"Очищення порожніх папок"); self.tabs.addTab(self.catalog_tab,"Перевірка каталогу")
        self._build_cleanup_ui(); self._build_catalog_ui()
        m.addWidget(QLabel("Журнал подій:")); self.log_text=QPlainTextEdit(); self.log_text.setReadOnly(True); self.log_text.setFixedHeight(160); m.addWidget(self.log_text)

    def _build_cleanup_ui(self):
        ml=QVBoxLayout(self.cleanup_tab); fl=QHBoxLayout(); fl.addWidget(QLabel("Головна директорія:")); self.root_dir_edit=QLineEdit(); fl.addWidget(self.root_dir_edit); b=QPushButton("Обрати папку"); b.clicked.connect(self.choose_folder); fl.addWidget(b); ml.addLayout(fl)
        ml.addWidget(QLabel("Назви папок для пошуку (по одній в рядку):")); self.names_text=QPlainTextEdit(); self.names_text.setFixedHeight(110); ml.addWidget(self.names_text)
        cl=QHBoxLayout(); self.dry_run_checkbox=QCheckBox("Тестовий режим без видалення"); self.dry_run_checkbox.setChecked(True); cl.addWidget(self.dry_run_checkbox)
        self.scan_btn=QPushButton("Сканувати"); self.scan_btn.clicked.connect(self.start_scan); cl.addWidget(self.scan_btn)
        self.stop_scan_btn=QPushButton("Зупинити сканування"); self.stop_scan_btn.clicked.connect(self.stop_scan); self.stop_scan_btn.setEnabled(False); cl.addWidget(self.stop_scan_btn)
        self.delete_btn=QPushButton("Видалити порожні"); self.delete_btn.clicked.connect(self.delete_empty); self.delete_btn.setEnabled(False); cl.addWidget(self.delete_btn)
        self.export_btn=QPushButton("Експорт результатів у CSV"); self.export_btn.clicked.connect(self.export_csv); self.export_btn.setEnabled(False); cl.addWidget(self.export_btn)
        clr=QPushButton("Очистити результати"); clr.clicked.connect(self.clear_results); cl.addWidget(clr)
        s=QPushButton("Налаштування"); s.clicked.connect(self.open_settings); cl.addWidget(s); ml.addLayout(cl)
        self.table=QTableWidget(0,4); self.table.setHorizontalHeaderLabels(["Шлях","Назва","Статус","Дія"]); self.table.horizontalHeader().setStretchLastSection(True); self.table.setColumnWidth(0,580); ml.addWidget(self.table)

    def _build_catalog_ui(self):
        ml=QVBoxLayout(self.catalog_tab)
        fl=QHBoxLayout(); fl.addWidget(QLabel("Джерело сканування:")); self.catalog_source_edit=QLineEdit(); fl.addWidget(self.catalog_source_edit); b=QPushButton("Обрати папку"); b.clicked.connect(self.choose_catalog_source); fl.addWidget(b); ml.addLayout(fl)
        ml.addWidget(QLabel("Каталоги, які потрібно знайти (по одному в рядку):")); self.target_catalogs_text=QPlainTextEdit(); self.target_catalogs_text.setFixedHeight(100); ml.addWidget(self.target_catalogs_text)
        ml.addWidget(QLabel("Папки, які мають бути всередині кожного каталогу (по одній в рядку):")); self.required_folders_text=QPlainTextEdit(); self.required_folders_text.setFixedHeight(100); ml.addWidget(self.required_folders_text)
        ol=QHBoxLayout(); self.ignore_case_checkbox=QCheckBox("Ігнорувати регістр літер"); self.ignore_case_checkbox.setChecked(True); ol.addWidget(self.ignore_case_checkbox); self.trim_spaces_checkbox=QCheckBox("Ігнорувати зайві пробіли на початку і в кінці назв"); self.trim_spaces_checkbox.setChecked(True); ol.addWidget(self.trim_spaces_checkbox); self.full_paths_checkbox=QCheckBox("Показувати повні шляхи"); self.full_paths_checkbox.setChecked(True); ol.addWidget(self.full_paths_checkbox); self.direct_children_checkbox=QCheckBox("Перевіряти тільки безпосередні підпапки всередині знайденого каталогу"); self.direct_children_checkbox.setChecked(True); ol.addWidget(self.direct_children_checkbox); ml.addLayout(ol)
        bl=QHBoxLayout(); self.catalog_scan_btn=QPushButton("Сканувати каталог"); self.catalog_scan_btn.clicked.connect(self.start_catalog_scan); bl.addWidget(self.catalog_scan_btn); self.catalog_stop_btn=QPushButton("Зупинити сканування"); self.catalog_stop_btn.clicked.connect(self.stop_catalog_scan); self.catalog_stop_btn.setEnabled(False); bl.addWidget(self.catalog_stop_btn); self.catalog_clear_btn=QPushButton("Очистити результати"); self.catalog_clear_btn.clicked.connect(self.clear_catalog_results); bl.addWidget(self.catalog_clear_btn); self.catalog_export_btn=QPushButton("Експорт у CSV"); self.catalog_export_btn.clicked.connect(self.export_catalog_csv); self.catalog_export_btn.setEnabled(False); bl.addWidget(self.catalog_export_btn); ml.addLayout(bl)
        self.catalog_table=QTableWidget(0,8); self.catalog_table.setHorizontalHeaderLabels(["Каталог для перевірки","Статус каталогу","Шлях","Знайдено обов'язкових папок","Всього обов'язкових папок","Відсутні папки","Зайві папки","Коментар"]); self.catalog_table.horizontalHeader().setStretchLastSection(True); self.catalog_table.setColumnWidth(2,340); ml.addWidget(self.catalog_table)

    def _normalize(self, p): return os.path.normcase(os.path.normpath(os.path.abspath(p)))
    def _is_subpath_or_same(self, parent, child):
        try: return self._normalize(os.path.commonpath([parent, child])) == self._normalize(parent)
        except ValueError: return False
    def _is_protected_directory(self, c):
        if not self.protect_directories_enabled: return False
        nc=self._normalize(c)
        for p in self.protected_directories:
            np=self._normalize(p)
            if nc==np or (self.protect_nested_directories and self._is_subpath_or_same(np,nc)): return True
        return False

    def choose_folder(self):
        folder=QFileDialog.getExistingDirectory(self,"Оберіть головну директорію")
        if folder: self.root_dir_edit.setText(folder); self.log(f"Обрано директорію: {folder}"); self._save_settings()
    def choose_catalog_source(self):
        folder=QFileDialog.getExistingDirectory(self,"Оберіть джерело сканування")
        if folder: self.catalog_source_edit.setText(folder); self.log(f"Обрано джерело перевірки каталогу: {folder}"); self._save_settings()
    def get_target_names(self): return [x for x in [l.strip() for l in self.names_text.toPlainText().splitlines()] if x]
    def _list_from_text(self, text): return [x for x in [l for l in text.toPlainText().splitlines()] if x.strip()]

    def validate_root_dir(self):
        r=self.root_dir_edit.text().strip()
        if not r: QMessageBox.warning(self,"Увага","Оберіть головну директорію."); return False
        if not os.path.isdir(r): QMessageBox.warning(self,"Увага","Вказана директорія не існує."); return False
        if self._is_protected_directory(r): QMessageBox.warning(self,"Захищена директорія","Сканування скасовано: обрана директорія належить до захищених."); return False
        return True
    def validate_catalog_source(self):
        r=self.catalog_source_edit.text().strip()
        if not r: QMessageBox.warning(self,"Увага","Оберіть джерело сканування."); return False
        if not os.path.isdir(r): QMessageBox.warning(self,"Увага","Вказана директорія не існує."); return False
        return True

    def start_scan(self):
        if not self.validate_root_dir(): return
        targets=self.get_target_names();
        if not targets: QMessageBox.warning(self,"Увага","Додайте хоча б одну назву папки для пошуку."); return
        self._save_settings(); self.clear_results(False); self.current_root_dir=self.root_dir_edit.text().strip(); self.log("Початок сканування")
        self.scan_btn.setEnabled(False); self.stop_scan_btn.setEnabled(True); self.delete_btn.setEnabled(False); self.export_btn.setEnabled(False)
        self.scan_thread=QThread(); self.scan_worker=ScanWorker(self.current_root_dir, targets, self.ignored_files_enabled, self.ignored_files); self.scan_worker.moveToThread(self.scan_thread)
        self.scan_thread.started.connect(self.scan_worker.run); self.scan_worker.progress.connect(self._append_result); self.scan_worker.log.connect(self.log); self.scan_worker.finished.connect(self._scan_finished); self.scan_worker.finished.connect(self.scan_thread.quit); self.scan_worker.finished.connect(self.scan_worker.deleteLater); self.scan_thread.finished.connect(self.scan_thread.deleteLater); self.scan_thread.start()
    def stop_scan(self):
        if self.scan_worker: self.scan_worker.cancel(); self.stop_scan_btn.setEnabled(False); self.log("Запит на зупинку сканування надіслано...")
    def start_catalog_scan(self):
        if not self.validate_catalog_source(): return
        targets=self._list_from_text(self.target_catalogs_text); required=self._list_from_text(self.required_folders_text)
        if not targets: QMessageBox.warning(self,"Увага","Додайте хоча б один каталог для пошуку."); return
        if not required: QMessageBox.warning(self,"Увага","Додайте хоча б одну обов'язкову папку."); return
        self.clear_catalog_results(False); self._save_settings(); self.current_catalog_source=self.catalog_source_edit.text().strip()
        self.log("Початок перевірки каталогу"); self.log(f"Обране джерело: {self.current_catalog_source}"); self.log(f"Кількість каталогів для пошуку: {len(targets)}"); self.log(f"Кількість обов'язкових папок: {len(required)}")
        self.catalog_scan_btn.setEnabled(False); self.catalog_stop_btn.setEnabled(True); self.catalog_export_btn.setEnabled(False)
        self.catalog_thread=QThread(); self.catalog_worker=CatalogCheckWorker(self.current_catalog_source, targets, required, self.ignore_case_checkbox.isChecked(), self.trim_spaces_checkbox.isChecked(), self.full_paths_checkbox.isChecked(), self.direct_children_checkbox.isChecked()); self.catalog_worker.moveToThread(self.catalog_thread)
        self.catalog_thread.started.connect(self.catalog_worker.run); self.catalog_worker.progress.connect(self._append_catalog_result); self.catalog_worker.log.connect(self.log); self.catalog_worker.finished.connect(self._catalog_finished); self.catalog_worker.finished.connect(self.catalog_thread.quit); self.catalog_worker.finished.connect(self.catalog_worker.deleteLater); self.catalog_thread.finished.connect(self.catalog_thread.deleteLater); self.catalog_thread.start()
    def stop_catalog_scan(self):
        if self.catalog_worker: self.catalog_worker.cancel(); self.catalog_stop_btn.setEnabled(False); self.log("Зупинка перевірки користувачем")

    @Slot(list, bool)
    def _scan_finished(self, results, cancelled): self.results=results; self.scan_btn.setEnabled(True); self.stop_scan_btn.setEnabled(False); self.log("Завершення сканування"); self._update_action_buttons_after_scan()
    @Slot(list, dict, bool)
    def _catalog_finished(self, results, stats, cancelled):
        self.catalog_results=results; self.catalog_scan_btn.setEnabled(True); self.catalog_stop_btn.setEnabled(False); self._update_catalog_buttons()
        self.log(f"Кількість знайдених каталогів: {stats['found']}"); self.log(f"Кількість не знайдених каталогів: {stats['not_found']}"); self.log(f"Кількість повних: {stats['full']}"); self.log(f"Кількість неповних: {stats['incomplete']}"); self.log(f"Кількість із зайвими: {stats['with_extra']}"); self.log(f"Кількість дублікатів: {stats['duplicates']}"); self.log(f"Кількість помилок: {stats['errors']}" )
        if cancelled: self.log("Перевірку каталогу зупинено користувачем")
        self.log("Завершення перевірки каталогу")

    def _append_result(self, r):
        row=self.table.rowCount(); self.table.insertRow(row)
        for i,v in enumerate([r.path,r.folder_name,r.status,r.action]): self.table.setItem(row,i,QTableWidgetItem(str(v)))
    def _append_catalog_result(self, r):
        row=self.catalog_table.rowCount(); self.catalog_table.insertRow(row)
        values=[r.target_catalog,r.status,r.path,r.found_required,r.total_required,r.missing_folders,r.extra_folders,r.comment]
        for i,v in enumerate(values):
            text=str(v); item=QTableWidgetItem(text); item.setToolTip(text); self.catalog_table.setItem(row,i,item)

    def _update_action_buttons_after_scan(self):
        self.export_btn.setEnabled(bool(self.results)); self.delete_btn.setEnabled(any(r.status in {"порожня","умовно порожня"} and r.action=="кандидат на видалення" for r in self.results))
    def _update_catalog_buttons(self): self.catalog_export_btn.setEnabled(bool(self.catalog_results))

    def _recheck_for_deletion(self, folder_path):
        ignored=[]
        with os.scandir(folder_path) as entries:
            for e in entries:
                if e.is_dir(follow_symlinks=False): return False, [], "містить підпапки"
                if e.is_file(follow_symlinks=False):
                    if self.ignored_files_enabled and e.name.casefold() in {n.casefold() for n in self.ignored_files if n.strip()}: ignored.append(e.path)
                    else: return False, [], f"знайдено неігнорований файл: {e.name}"
                else: return False, [], f"містить неочікуваний тип елемента: {e.name}"
        return True, ignored, ""

    def delete_empty(self):
        if not self.validate_root_dir(): return
        root=os.path.abspath(self.root_dir_edit.text().strip()); norm_root=self._normalize(root); dry=self.dry_run_checkbox.isChecked(); candidates=[r for r in self.results if r.status in {"порожня","умовно порожня"} and r.action=="кандидат на видалення"]
        if not candidates: QMessageBox.information(self,"Інформація","Немає порожніх папок для видалення."); return
        if QMessageBox.question(self,"Підтвердження видалення",f"Ви дійсно хочете обробити {len(candidates)} папок-кандидатів?")!=QMessageBox.Yes: return
        for r in candidates:
            t=os.path.abspath(r.path)
            if self._normalize(t)==norm_root or (self.protect_directories_enabled and self._is_protected_directory(t)) or not self._is_subpath_or_same(root,t): r.action="пропущено"; continue
            try:
                can_delete, ignored, reason = self._recheck_for_deletion(t)
                if not can_delete: r.status="не порожня"; r.action="пропущено"; continue
                if dry: r.action="буде видалено"; continue
                for f in ignored: os.remove(f)
                os.rmdir(t); r.action="видалено"
            except (PermissionError, FileNotFoundError, OSError) as exc:
                r.status="помилка"; r.action="пропущено"; r.error=str(exc)
        self._refresh_table(); self._update_action_buttons_after_scan()

    def _refresh_table(self): self.table.setRowCount(0); [self._append_result(r) for r in self.results]
    def export_csv(self):
        if not self.results: return
        p,_=QFileDialog.getSaveFileName(self,"Зберегти CSV","results.csv","CSV файли (*.csv)")
        if not p: return
        with open(p,"w",newline="",encoding="utf-8-sig") as f:
            w=csv.writer(f); w.writerow(["Шлях","Назва","Статус","Дія","Помилка"]); [w.writerow([r.path,r.folder_name,r.status,r.action,r.error]) for r in self.results]
        self.log(f"Експортовано в CSV: {p}")
    def export_catalog_csv(self):
        if not self.catalog_results: return
        p,_=QFileDialog.getSaveFileName(self,"Зберегти CSV","catalog_check_results.csv","CSV файли (*.csv)")
        if not p: return
        checked_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(p,"w",newline="",encoding="utf-8-sig") as f:
            w=csv.writer(f); w.writerow(["Дата і час перевірки","Джерело","Назва каталогу","Статус","Шлях","Знайдено обов'язкових","Всього обов'язкових","Відсутні папки","Зайві папки","Коментар"])
            for r in self.catalog_results: w.writerow([checked_at,self.current_catalog_source,r.target_catalog,r.status,r.path,r.found_required,r.total_required,r.missing_folders,r.extra_folders,r.comment])
        self.log(f"Експорт перевірки каталогу в CSV: {p}")

    def clear_results(self, log_message=True): self.results=[]; self.table.setRowCount(0); self._update_action_buttons_after_scan();
    def clear_catalog_results(self, log_message=True): self.catalog_results=[]; self.catalog_table.setRowCount(0); self._update_catalog_buttons();
    def log(self, message): self.log_text.appendPlainText(message)

    def open_settings(self):
        d=SettingsDialog(self, self.protect_directories_enabled, self.protect_nested_directories, self.protected_directories, self.ignored_files_enabled, self.ignored_files)
        if d.exec()==QDialog.Accepted:
            self.protect_directories_enabled=d.protect_enabled_checkbox.isChecked(); self.protect_nested_directories=d.protect_nested_checkbox.isChecked(); self.protected_directories=d.get_directories(); self.ignored_files_enabled=d.ignored_enabled_checkbox.isChecked(); self.ignored_files=d.get_ignored_files(); self._save_settings(); self.log("Налаштування оновлено.")

    def _save_settings(self):
        data={"last_directory":self.root_dir_edit.text().strip(),"folder_names":self.names_text.toPlainText(),"dry_run":self.dry_run_checkbox.isChecked(),"protect_directories_enabled":self.protect_directories_enabled,"protect_nested_directories":self.protect_nested_directories,"protected_directories":self.protected_directories,"ignored_files_enabled":self.ignored_files_enabled,"ignored_files":self.ignored_files,"catalog_check":{"source_path":self.catalog_source_edit.text().strip(),"target_catalogs":self.target_catalogs_text.toPlainText(),"required_folders":self.required_folders_text.toPlainText(),"ignore_case":self.ignore_case_checkbox.isChecked(),"trim_spaces":self.trim_spaces_checkbox.isChecked(),"show_full_paths":self.full_paths_checkbox.isChecked(),"direct_children_only":self.direct_children_checkbox.isChecked()}}
        with open(SETTINGS_FILE,"w",encoding="utf-8") as f: json.dump(data,f,ensure_ascii=False,indent=2)
    def _load_settings(self):
        if not SETTINGS_FILE.exists(): return
        try:
            with open(SETTINGS_FILE,"r",encoding="utf-8") as f: data=json.load(f)
            self.root_dir_edit.setText(data.get("last_directory","")); self.names_text.setPlainText(data.get("folder_names","")); self.dry_run_checkbox.setChecked(data.get("dry_run",True)); self.protect_directories_enabled=data.get("protect_directories_enabled",False); self.protect_nested_directories=data.get("protect_nested_directories",True); self.protected_directories=data.get("protected_directories",[]); self.ignored_files_enabled=data.get("ignored_files_enabled",True); self.ignored_files=data.get("ignored_files",DEFAULT_IGNORED_FILES.copy())
            cc=data.get("catalog_check",{}); self.catalog_source_edit.setText(cc.get("source_path","")); self.target_catalogs_text.setPlainText(cc.get("target_catalogs","")); self.required_folders_text.setPlainText(cc.get("required_folders","")); self.ignore_case_checkbox.setChecked(cc.get("ignore_case",True)); self.trim_spaces_checkbox.setChecked(cc.get("trim_spaces",True)); self.full_paths_checkbox.setChecked(cc.get("show_full_paths",True)); self.direct_children_checkbox.setChecked(cc.get("direct_children_only",True))
        except (PermissionError, FileNotFoundError, OSError, json.JSONDecodeError) as exc: self.log(f"Помилка завантаження налаштувань: {exc}")
    def closeEvent(self, event):
        if self.scan_worker: self.scan_worker.cancel()
        if self.catalog_worker: self.catalog_worker.cancel()
        if self.scan_thread and self.scan_thread.isRunning(): self.scan_thread.quit(); self.scan_thread.wait(2000)
        if self.catalog_thread and self.catalog_thread.isRunning(): self.catalog_thread.quit(); self.catalog_thread.wait(2000)
        self._save_settings(); super().closeEvent(event)


def main():
    app = QApplication(sys.argv); w = MainWindow(); w.show(); sys.exit(app.exec())


if __name__ == "__main__":
    main()
