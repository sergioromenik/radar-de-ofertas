@echo off
rem Modo ao vivo: lê o feed do Promobit a cada 30 s e abre ao_vivo.html, que se atualiza sozinha.
rem Feche esta janela (ou Ctrl+C) para parar.
title Radar Ao Vivo - feche esta janela para parar
cd /d "%~dp0"
python -I monitor.py aovivo
