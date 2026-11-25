# Build dist\BatteryTray.exe (one file, no console) from source.
# Requires Python 3.12+. Run from the repository root:  .\build.ps1
$ErrorActionPreference = 'Stop'
if (-not (Test-Path .venv)) { python -m venv .venv }
.\.venv\Scripts\python.exe -m pip install --quiet -r requirements.txt pyinstaller
.\.venv\Scripts\python.exe make_icon.py
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --noconsole `
    --name BatteryTray --icon assets\battery_tray.ico --hidden-import pystray._win32 app.py
Write-Host "OK: dist\BatteryTray.exe"
