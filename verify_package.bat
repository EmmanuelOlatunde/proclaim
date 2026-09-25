@echo off
setlocal
rem Proclaim package self-test.
rem Run it from the portable Proclaim folder (dist\Proclaim) on a machine that
rem has NO Python installed, to simulate a clean church computer.
rem
rem IMPORTANT: this test stops any running "Proclaim.exe" on the machine.
rem Run it only when no other Proclaim instance is in use.

cd /d "%~dp0"

echo ============================================
echo   Proclaim -- package self-test
echo ============================================
echo.

if not exist "Proclaim.exe" (
    echo FAIL: Proclaim.exe not found in this folder.
    goto :fail
)

echo [1/6] Checking that Proclaim is self-contained (no host python needed)...
where python >nul 2>nul
if not errorlevel 1 (
    echo        note: a python.exe was found on this machine, so this is not
    echo        a 100 percent clean test, but the exe still uses its own
    echo        bundled Python runtime.
) else (
    echo        good: no python on this machine.
)

echo [2/6] Copying the whole package to %TEMP%\ProclaimVerify (proves it
echo        works from a different folder)...
set "TESTDIR=%TEMP%\ProclaimVerify"
if exist "%TESTDIR%" rmdir /s /q "%TESTDIR%"
mkdir "%TESTDIR%"
xcopy /e /i /y /q "%~dp0." "%TESTDIR%" >nul
if errorlevel 1 goto :fail

echo [3/6] Starting Proclaim.exe (test port 3999)...
start "" "%TESTDIR%\Proclaim.exe" --port 3999
echo        waiting for the server...
"%SYSTEMROOT%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -Command "$ok=$false; for($i=0;$i -lt 60;$i++){ try { $r=Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 http://127.0.0.1:3999/ -ErrorAction Stop; if($r.StatusCode -eq 200){$ok=$true; break} } catch {} Start-Sleep -Milliseconds 500 }; if(-not $ok){ exit 1 }"
if errorlevel 1 (
    echo FAIL: the server did not answer on http://127.0.0.1:3999/
    goto :failcleanup
)

echo [4/6] Testing pages, static files and scripture data...
"%SYSTEMROOT%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -Command "$paths=@('/','/control','/display/CHURCH1','/static/presentation/css/app.css','/api/scripture/index'); $bad=@(); foreach($p in $paths){ try { $r=Invoke-WebRequest -UseBasicParsing -TimeoutSec 20 -ErrorAction Stop ('http://127.0.0.1:3999'+$p); if($r.StatusCode -ne 200){ $bad+=$p } } catch { $bad+=$p } }; if($bad.Count -gt 0){ 'FAILED on: '+($bad -join ', '); exit 1 } else { '        all endpoints returned HTTP 200' }"
if errorlevel 1 goto :failcleanup

echo [5/6] Stopping Proclaim...
taskkill /IM Proclaim.exe /F >nul 2>nul

echo [6/6] Cleaning up the temporary copy...
rmdir /s /q "%TESTDIR%" >nul 2>nul

echo.
echo ============================================
echo   SELF-TEST PASSED
echo ============================================
echo   - Proclaim starts with no Python installed
echo   - Daphne serves real HTTP requests
echo   - pages, static files and scripture data load
echo.
echo   Final manual checks on the church LAN:
echo     control : http://LAN-IP:3000/control
echo     display : http://LAN-IP:3000/display/CHURCH1
echo   (WebSockets are used by those pages; presenting from
echo    Control should update Display in real time.)
echo ============================================
endlocal
exit /b 0

:failcleanup
taskkill /IM Proclaim.exe /F >nul 2>nul
:fail
echo.
echo SELF-TEST FAILED. See the messages above.
endlocal
exit /b 1