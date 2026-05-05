@echo off
setlocal
cd /d "%~dp0"
python -m pip install -r tools\wechat_browser_importer\requirements.txt
python -m playwright install chromium
python tools\wechat_browser_importer\importer.py --article-root output\articles
pause
