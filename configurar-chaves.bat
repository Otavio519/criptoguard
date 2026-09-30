@echo off
chcp 65001 >nul
title Configurar chaves
cd /d "%~dp0backend"
.venv\Scripts\python configurar_chaves.py
if errorlevel 1 (
  echo.
  pause
  exit /b 1
)
echo.
echo Reiniciando o CriptoGuard com as chaves novas...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do taskkill /PID %%p /F >nul 2>nul
timeout /t 2 >nul
start "" "%~dp0iniciar.bat"
echo Pode fechar esta janela.
pause
