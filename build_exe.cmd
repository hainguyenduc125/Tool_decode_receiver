@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "PYTHON=%~1"
if not defined PYTHON set "PYTHON=python"

set "BUILD_VENV=%LOCALAPPDATA%\ToolDecodeLoraBuildEnv"
if not exist "%BUILD_VENV%\Scripts\python.exe" (
    "%PYTHON%" -X utf8 -m venv "%BUILD_VENV%"
    if errorlevel 1 (
        echo Failed to create the isolated build environment.
        exit /b 1
    )
)
set "BUILD_PYTHON=%BUILD_VENV%\Scripts\python.exe"

"%BUILD_PYTHON%" -X utf8 -m pip install -r requirements-build.txt
if errorlevel 1 (
    echo Failed to install build dependencies.
    exit /b 1
)

"%BUILD_PYTHON%" -X utf8 -m PyInstaller --clean --noconfirm --onefile --windowed --name LoRaDecoder --specpath build --workpath build\work --distpath dist main.py
if errorlevel 1 (
    echo PyInstaller build failed.
    exit /b 1
)

if not exist "dist\LoRaDecoder.exe" (
    echo Build completed without dist\LoRaDecoder.exe.
    exit /b 1
)

echo Created: "%~dp0dist\LoRaDecoder.exe"
