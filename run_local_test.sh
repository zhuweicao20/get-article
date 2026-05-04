#!/usr/bin/env bash
set -e
python3 -m pip install -r requirements.txt
python3 scripts/auto_chem_wechat_pipeline.py --force --days 7 --max-articles 1
