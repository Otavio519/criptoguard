@echo off
chcp 65001 >nul
title Teste do stop na testnet
cd /d "%~dp0backend"
.venv\Scripts\python testar_stop.py
echo.
pause
