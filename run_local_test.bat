@echo off
cd /d %~dp0
python -m pip install -r requirements.txt
python scripts\auto_chem_wechat_pipeline.py --force --days 7 --max-articles 1
pause
