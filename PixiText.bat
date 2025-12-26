@echo off
cd /d "%~dp0"

REM Flask 起動（別窓）
start "PixiText Server" /min cmd /k python app.py

REM 少し待つ（サーバー立ち上がり待ち）
timeout /t 2 >nul

REM cloudflared 起動（別窓）
start "PixiText Tunnel" /min cmd /k cloudflared tunnel --url http://localhost:7860

REM 少し待つ（URL表示待ち）
timeout /t 3 >nul

REM ローカル画面を開く（公開URLは cloudflared 窓に出る）
start "" "http://localhost:7860"
