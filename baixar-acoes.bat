@echo off
chcp 65001 >nul
title Baixar historico de acoes
cd /d "%~dp0backend"
.venv\Scripts\python baixar_acoes.py
timeout /t 5 >nul
