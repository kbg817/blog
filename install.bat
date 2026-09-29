@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo [1/3] Python 가상환경 만드는 중...
python -m venv .venv || (echo Python 3.11 이상을 먼저 설치해 주세요: https://www.python.org/downloads/ & pause & exit /b 1)
echo [2/3] 필요한 프로그램 설치 중...
.venv\Scripts\python -m pip install -r requirements.txt
echo [3/3] 크롬(크로미움) 설치 중...
.venv\Scripts\python -m playwright install chromium
if not exist config.yaml copy config.example.yaml config.yaml
if not exist .env copy .env.example .env
echo.
echo 설치 끝! config.yaml 과 .env 를 채운 뒤 start.bat 을 더블클릭하세요.
pause
