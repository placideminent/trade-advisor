@echo off
cd /d %~dp0
echo.
echo [1] 이 컴퓨터에서 Streamlit 이 켜져 있어야 합니다.
echo     안 켜져 있으면 run.bat 을 먼저 실행하세요.
echo [2] ngrok 이 https 주소를 보여 주면 그 주소로 밖에서 접속합니다.
echo     컴퓨터를 끄거나 이 창을 닫으면 외부 접속이 끊깁니다.
echo.
ngrok http 8501
