#!/usr/bin/env python3
"""中邮国际官方 Documents 目录适配器。"""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common.queue_static_adapter import build_queue_adapter
SHEET_NAME = "中邮国际"
_INDEX = "https://chinapostglobal.com/Home/MADocuments?cc=gb&clientType=0"
_PAGES = {name: _INDEX for name in ("China Post Global Equity Select Fund", "China Post Global US Small/Mid Cap Equity Fund", "China Post Global Strategic Emerging Industries Fund", "China Post Global Fixed Income Fund")}
_fetch_catalog, _build_index, _resolve_single, main = build_queue_adapter(sheet_name=SHEET_NAME, description="按 China Post Global 官方文件目录补全入口", downloader='python code/companies/muzinich/download_static_factsheet.py "{url}" "{english}" ChinaPost', pages=_PAGES, noise=("Fund",))
if __name__ == "__main__": raise SystemExit(main())
