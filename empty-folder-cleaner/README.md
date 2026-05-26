# Empty Folder Cleaner

Windows desktop-програма на Python (PySide6) для пошуку та видалення **лише порожніх** папок із заданого списку назв.

## Можливості
- Вибір головної директорії.
- Ввід списку назв папок (по одній у рядку).
- Рекурсивне сканування всіх підпапок.
- Перевірка порожності (немає файлів і немає підпапок).
- Тестовий режим (увімкнено за замовчуванням): без фактичного видалення.
- Реальне видалення тільки після натискання кнопки **«Видалити порожні»** та підтвердження.
- Захист від видалення:
  - головної директорії;
  - системних шляхів `C:\Windows`, `C:\Program Files`, `C:\Users`.
- Таблиця результатів зі статусами і діями.
- Експорт результатів у CSV.
- Збереження налаштувань у `settings.json` (остання директорія + список назв).

## Вимоги
- Python 3.10+
- Windows 10/11

## Запуск (з вихідного коду)
```bat
python -m pip install -r requirements.txt
python main.py
```

## Збірка .exe
Використайте:
```bat
build_exe.bat
```

Команди в скрипті:
```bat
python -m pip install -r requirements.txt
python -m pip install pyinstaller
pyinstaller --onefile --windowed --name "Empty Folder Cleaner" main.py
```

Після збірки файл `.exe` буде в папці `dist`.

## Структура проєкту
```
empty-folder-cleaner/
  main.py
  requirements.txt
  README.md
  build_exe.bat
```
