#!/bin/bash
set -e

# このファイルがある場所へ移動（Windowsの cd /d "%~dp0" 相当）
cd "$(dirname "$0")"

# venvがあるなら有効化（無ければ無視）
if [ -f "venv/bin/activate" ]; then
  source "venv/bin/activate"
fi

# Flask 起動（別ターミナル相当 → バックグラウンド）
python app.py &
FLASK_PID=$!

# 少し待つ（サーバー立ち上がり待ち）
sleep 2

# cloudflared 起動（バックグラウンド）
cloudflared tunnel --url http://localhost:7860 &
CF_PID=$!

# 少し待つ（URL表示待ち）
sleep 3

# ローカルを開く
open "http://localhost:7860"

echo "PixiText Server PID: $FLASK_PID"
echo "Cloudflared PID: $CF_PID"
echo "終了するにはこのウィンドウで Ctrl+C"
wait
