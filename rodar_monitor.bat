@echo off
rem Coleta as fontes, atualiza o banco e gera painel.html nesta pasta.
cd /d "%~dp0"
python -I monitor.py %*
