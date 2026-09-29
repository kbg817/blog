#!/bin/bash
cd "$(dirname "$0")"
python3 -m venv .venv || { echo "Python 3.11 이상을 먼저 설치해 주세요."; read; exit 1; }
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
[ -f config.yaml ] || cp config.example.yaml config.yaml
[ -f .env ] || cp .env.example .env
echo "설치 끝! config.yaml 과 .env 를 채운 뒤 start.command 를 더블클릭하세요."
read
