@echo off
setlocal
title Proclaim Portable Build
cd /d "%~dp0"

echo ============================================
echo   Proclaim -- Windows Portable Build
echo ============================================
echo.

rem ---- 1. Dedicated build environment (never the project's own venv) ----
set "BUILD_VENV=%~dp0build_venv"
if not exist "%BUILD_VENV%\Scripts\python.exe" (
    echo [1/6] Creating build environment: build_venv\
    py -3 -m venv "%BUILD_VENV%"
    if errorlevel 1 goto :error
) else (
    echo [1/6] Using existing build environment: build_venv\
)
"%BUILD_VENV%\Scripts\python.exe" -m pip install --quiet --upgrade pip
if errorlevel 1 goto :error

echo [2/6] Installing application dependencies into build_venv...
"%BUILD_VENV%\Scripts\python.exe" -m pip install --quiet Django channels daphne Pillow
if errorlevel 1 goto :error

echo [3/6] Installing PyInstaller...
"%BUILD_VENV%\Scripts\python.exe" -m pip install --quiet pyinstaller
if errorlevel 1 goto :error

echo [4/6] Cleaning previous build output (dist\Proclaim and build\Proclaim)...
if exist "dist\Proclaim" rmdir /s /q "dist\Proclaim"
if exist "build\Proclaim" rmdir /s /q "build\Proclaim"

echo [5/6] Running PyInstaller (Proclaim.spec, onedir)...
"%BUILD_VENV%\Scripts\pyinstaller.exe" --noconfirm --clean --distpath dist --workpath build Proclaim.spec
if errorlevel 1 goto :error

echo [6/6] Preparing the portable folder...
if not exist "dist\Proclaim\data\hymns" mkdir "dist\Proclaim\data\hymns"
if not exist "dist\Proclaim\media" mkdir "dist\Proclaim\media"
copy /y "README-PORTABLE.txt" "dist\Proclaim\README-PORTABLE.txt" >nul
copy /y "verify_package.bat" "dist\Proclaim\verify_package.bat" >nul

if not exist "dist\Proclaim\Proclaim.exe" (
    echo.
    echo ERROR: dist\Proclaim\Proclaim.exe was not produced.
    goto :error
)

echo.
echo ============================================
echo   BUILD COMPLETE
echo ============================================
echo   Portable application: %~dp0dist\Proclaim\
echo   Start it with:        dist\Proclaim\Proclaim.exe
echo   Self-test:            dist\Proclaim\verify_package.bat
echo.
echo   To use: copy the ENTIRE Proclaim folder to any Windows 10/11
echo   computer and double-click Proclaim.exe.
echo ============================================
endlocal
exit /b 0

:error
echo.
echo BUILD FAILED. See the messages above.
endlocal
exit /b 1