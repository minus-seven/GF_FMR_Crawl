#!/usr/bin/env python3
"""信达国际 CIAM 官方资产管理基金页适配器。"""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common.queue_static_adapter import build_queue_adapter
SHEET_NAME = "信达"
_INDEX = "https://www.cinda.com.hk/en/asset_management.php/1000"
_PAGES = {"CIAM Value Fixed Income Fund": _INDEX, "Shinya USD Money Market Fund": "https://www.cinda.com.hk/tc/asset_management.php?id=2"}
_fetch_catalog, _build_index, _resolve_single, main = build_queue_adapter(sheet_name=SHEET_NAME, description="按信达国际官方基金页补全月报入口", downloader='python code/companies/muzinich/download_static_factsheet.py "{url}" "{english}" CIAM', pages=_PAGES, noise=("Fund",))
if __name__ == "__main__": raise SystemExit(main())
