#!/usr/bin/env python3
"""光大证券国际官方资产管理基金页适配器。"""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common.queue_static_adapter import build_queue_adapter
SHEET_NAME = "光大"
_INDEX = "https://www.ebshk.com/asset.php?lang=tc"
_PAGES = {"Everbright Smart Select Global Equity Fund": _INDEX, "Everbright Hong Kong Bond Fund": _INDEX}
_fetch_catalog, _build_index, _resolve_single, main = build_queue_adapter(sheet_name=SHEET_NAME, description="按光大证券国际官方资产管理页补全入口", downloader='python code/companies/muzinich/download_static_factsheet.py "{url}" "{english}" Everbright', pages=_PAGES, noise=("Fund",))
if __name__ == "__main__": raise SystemExit(main())
