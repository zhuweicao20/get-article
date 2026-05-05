@echo off
setlocal
cd /d "%~dp0"
python -m pip install -r tools\wechat_browser_importer\requirements.txt
python tools\wechat_browser_importer\importer.py --login-only
pause
