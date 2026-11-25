#!/usr/bin/env python3
"""交银国际官方 Monthly Fund Factsheet 目录适配器。"""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common.queue_static_adapter import build_queue_adapter
SHEET_NAME = "交银国际"
_INDEX = "https://www.bankcomm.com/BankCommSite/shtml/jygj/en/16638/16690/16695/list.shtml?channelId=16642"
_PAGES = {name: _INDEX for name in ("BOCOM International Money Market Fund (USD)", "BOCOM International Dragon Core Growth Fund", "BOCOM International China Dynamic Fund", "Global Bond Fund")}
_fetch_catalog, _build_index, _resolve_single, main = build_queue_adapter(sheet_name=SHEET_NAME, description="按 BOCOM International 官方月报目录补全入口", downloader='python code/companies/muzinich/download_static_factsheet.py "{url}" "{english}" BOCOM', pages=_PAGES, noise=("Fund",))
if __name__ == "__main__": raise SystemExit(main())
