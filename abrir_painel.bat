@echo off
rem Atualiza as ofertas (rápida, ou completa na 1ª vez do dia) e abre o painel no navegador.
title Radar de Ofertas
cd /d "%~dp0"
python -I monitor.py
start "" "%~dp0painel.html"
