@echo off
python -m pip install -r requirements.txt
python -m pip install pyinstaller
pyinstaller --onefile --windowed --name "Empty Folder Cleaner" main.py
