#!/usr/bin/env python3
"""Asset Management Group OPES 官方基金目录适配器。"""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common.queue_static_adapter import build_queue_adapter
SHEET_NAME = "资产管理集团"
_INDEX = "https://asset-mg.com/en/funds/"
_PAGES = {name: _INDEX for name in ("ASSET MANAGEMENT GROUP OPES Performance Fund", "ASSET MANAGEMENT GROUP OPES Balanced Fund", "ASSET MANAGEMENT GROUP OPES Growth Fund")}
_fetch_catalog, _build_index, _resolve_single, main = build_queue_adapter(sheet_name=SHEET_NAME, description="按 Asset Management Group OPES 官方目录补全入口", downloader='python code/companies/muzinich/download_static_factsheet.py "{url}" "{english}" AssetMG', pages=_PAGES, noise=("Fund",))
if __name__ == "__main__": raise SystemExit(main())
