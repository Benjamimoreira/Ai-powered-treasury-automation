@echo off
setlocal enabledelayedexpansion

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo ERRO: ambiente virtual nao foi encontrado em .venv\Scripts\python.exe
    echo Cria o ambiente com: py -m venv .venv
    pause
    exit /b 1
)

set PYTHON_EXE=%~dp0.venv\Scripts\python.exe
set API_URL=http://127.0.0.1:8000/
set DASH_URL=http://127.0.0.1:8501/

for /f "tokens=5" %%P in ('netstat -ano ^| findstr :8000 2^>nul') do (
    if not "%%P"=="" (
        echo A libertar porta 8000 usada pelo PID %%P...
        taskkill /PID %%P /F >nul 2>&1
    )
)

for /f "tokens=5" %%P in ('netstat -ano ^| findstr :8501 2^>nul') do (
    if not "%%P"=="" (
        echo A libertar porta 8501 usada pelo PID %%P...
        taskkill /PID %%P /F >nul 2>&1
    )
)

call :wait_for_url "%API_URL%" "API Tesouraria" 30
if errorlevel 1 (
    echo Iniciando API...
    start "API Tesouraria" cmd /k "%PYTHON_EXE% -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
)

call :wait_for_url "%API_URL%" "API Tesouraria" 30
if errorlevel 1 (
    echo ERRO: a API nao ficou disponivel em %API_URL%
    pause
    exit /b 1
)

call :wait_for_url "%DASH_URL%" "Dashboard Tesouraria" 30
if errorlevel 1 (
    echo Iniciando dashboard...
    start "Dashboard Tesouraria" cmd /k "%PYTHON_EXE% -m streamlit run dashboard\app.py --server.address 127.0.0.1 --server.port 8501"
)

call :wait_for_url "%DASH_URL%" "Dashboard Tesouraria" 30
if errorlevel 1 (
    echo ERRO: a dashboard nao ficou disponivel em %DASH_URL%
    pause
    exit /b 1
)

echo.
echo API em: http://127.0.0.1:8000
 echo Docs da API: http://127.0.0.1:8000/docs
 echo Dashboard: http://127.0.0.1:8501
 echo.
echo Os processos estao disponiveis e prontos a usar.
 echo Fecha esta janela quando terminares.

goto :eof

:wait_for_url
set "URL=%~1"
set "LABEL=%~2"
set /a "MAX_WAIT=%~3"
set /a "ATTEMPTS=0"
:wait_loop
set /a "ATTEMPTS+=1"
PowerShell -NoProfile -ExecutionPolicy Bypass -Command "try { $r = Invoke-WebRequest -Uri '%URL%' -UseBasicParsing -TimeoutSec 5; exit 0 } catch { exit 1 }" >nul 2>&1
if not errorlevel 1 (
    echo %LABEL% pronto em %URL%
    exit /b 0
)
if %ATTEMPTS% geq %MAX_WAIT% (
    echo %LABEL% ainda nao respondeu em %URL% (timeout %MAX_WAIT%s)
    exit /b 1
)
PING -n 2 127.0.0.1 >nul
goto wait_loop

