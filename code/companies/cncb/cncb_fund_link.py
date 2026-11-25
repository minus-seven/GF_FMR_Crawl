#!/usr/bin/env python3
"""信银资本官方基金页适配器。"""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common.queue_static_adapter import build_queue_adapter
SHEET_NAME = "信银资本"
_PAGES = {"CNCB Capital US Dollar Money Market Fund": "https://cncbprod.hkc.website/fund.asp?pageid=79"}
_fetch_catalog, _build_index, _resolve_single, main = build_queue_adapter(sheet_name=SHEET_NAME, description="按 CNCB Capital 官方基金页补全月报入口", downloader='python code/companies/muzinich/download_static_factsheet.py "{url}" "{english}" CNCB', pages=_PAGES, noise=("Fund",))
if __name__ == "__main__": raise SystemExit(main())
