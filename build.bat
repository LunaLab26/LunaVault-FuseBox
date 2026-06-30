@echo off
title LunaVault FuseBox v1.3 — Windows Build
color 0A
setlocal

echo.
echo  ╔══════════════════════════════════════════════╗
echo  ║   LunaVault FuseBox v1.3 — Windows Build     ║
echo  ╚══════════════════════════════════════════════╝
echo.

:: ── 0a. Kill any running instance (releases file locks on DLLs) ──────────────
echo  [pre] Closing any running LunaVaultFuseBox instances...
taskkill /F /IM LunaVaultFuseBox.exe >nul 2>&1
timeout /t 1 /nobreak >nul

:: ── 0b. Force-delete old dist folder to avoid WinError 5 locks ───────────────
if exist "dist\LunaVaultFuseBox" (
    echo  [pre] Removing old dist\LunaVaultFuseBox...
    rd /s /q "dist\LunaVaultFuseBox" >nul 2>&1
    if exist "dist\LunaVaultFuseBox" (
        echo  [WARN] Could not fully remove dist folder — close the app and retry.
    )
)

:: ── 0c. Remove PyQt6 if present (conflicts with PySide6 in PyInstaller) ───────
echo  [0/5] Removing PyQt6 if installed (conflicts with PySide6)...
pip show PyQt6 >nul 2>&1
if not errorlevel 1 (
    echo        PyQt6 found — uninstalling...
    pip uninstall -y PyQt6 PyQt6-Qt6 PyQt6-sip >nul 2>&1
    echo        PyQt6 removed.
) else (
    echo        PyQt6 not present — good.
)
echo.

:: ── 1. Install dependencies ──────────────────────────────────────────────────
echo  [1/5] Installing Python dependencies...
pip install -r requirements.txt
if errorlevel 1 ( echo  [ERROR] pip install failed. & pause & exit /b 1 )
echo.

:: ── 2. Check ffmpeg ───────────────────────────────────────────────────────────
echo  [2/5] Checking bundled ffmpeg...
if not exist "bin\ffmpeg.exe" (
    echo  [ERROR] bin\ffmpeg.exe not found.
    echo         Download from https://www.gyan.dev/ffmpeg/builds/
    echo         and place ffmpeg.exe + ffprobe.exe in the bin\ folder.
    pause & exit /b 1
)
if not exist "bin\ffprobe.exe" (
    echo  [ERROR] bin\ffprobe.exe not found.
    pause & exit /b 1
)
echo  ffmpeg OK.
echo.

:: ── 3. PyInstaller (via spec file) ───────────────────────────────────────────
echo  [3/5] Building with PyInstaller...
pyinstaller --noconfirm LunaVaultFuseBox.spec
if errorlevel 1 ( echo  [ERROR] PyInstaller failed. & pause & exit /b 1 )

:: ── 4. Copy runtime data next to the exe (get_app_dir reads from here) ────────
echo.
echo  [4/5] Copying ffmpeg, luts and licenses next to the exe...
if not exist "dist\LunaVaultFuseBox\bin" mkdir dist\LunaVaultFuseBox\bin
xcopy /I /Y bin\ffmpeg.exe  dist\LunaVaultFuseBox\bin\ > nul
xcopy /I /Y bin\ffprobe.exe dist\LunaVaultFuseBox\bin\ > nul
xcopy /I /E /Y luts         dist\LunaVaultFuseBox\luts\ > nul
xcopy /I /E /Y licenses     dist\LunaVaultFuseBox\licenses\ > nul
copy /Y LICENSE dist\LunaVaultFuseBox\ > nul

:: ── 5. Done ──────────────────────────────────────────────────────────────────
echo.
echo  [5/5] Done.
echo.
echo  ══════════════════════════════════════════════
echo   Build complete!
echo   Folder:  dist\LunaVaultFuseBox\
echo   Run:     dist\LunaVaultFuseBox\LunaVaultFuseBox.exe
echo  ══════════════════════════════════════════════
echo.
pause
