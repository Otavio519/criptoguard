@echo off
chcp 65001 >nul
title CriptoGuard
cd /d "%~dp0backend"

set PY=
py -3 --version >nul 2>nul && set PY=py -3
if not defined PY python --version >nul 2>nul && set PY=python
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set PY="%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY (
  echo Python nao encontrado. Instalando o Python 3.12 pelo winget...
  winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
  if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set PY="%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
)
if not defined PY (
  echo.
  echo Nao consegui instalar o Python. Instale pelo site python.org e marque "Add Python to PATH".
  pause
  exit /b 1
)

if not exist .venv\Scripts\python.exe (
  echo Criando ambiente Python. Na primeira vez leva uns 3 minutos...
  %PY% -m venv .venv || (echo Falha ao criar o ambiente. & pause & exit /b 1)
  .venv\Scripts\python -m pip install --upgrade pip
  .venv\Scripts\python -m pip install -r requirements.txt || (echo Falha ao instalar pacotes. & pause & exit /b 1)
)
if not exist .env copy .env.example .env >nul

echo Fechando versao antiga do CriptoGuard, se estiver aberta...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do taskkill /PID %%p /F >nul 2>nul
echo.
echo CriptoGuard rodando em http://localhost:8000
echo Para desligar, feche esta janela.
start "" /min cmd /c "timeout /t 5 >nul & start http://localhost:8000"
.venv\Scripts\python -m uvicorn app.main:app --port 8000
pause
