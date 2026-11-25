#!/usr/bin/env python3
"""招商永隆资产管理官方公募基金入口适配器。"""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common.queue_static_adapter import build_queue_adapter
SHEET_NAME = "招商永隆"
_INDEX = "https://wlam.cmbwinglungbank.com/home-page/public-fund"
_PAGES = {name: _INDEX for name in ("CMB Wing Lung USD Money Market Fund", "CMB Wing Lung RMB Money Market Fund", "CMB Wing Lung HKD Money Market Fund", "CMB Wing Lung Investment Grade Bond Fund")}
_fetch_catalog, _build_index, _resolve_single, main = build_queue_adapter(sheet_name=SHEET_NAME, description="按招商永隆资管官方公募基金页补全入口", downloader='python code/companies/muzinich/download_static_factsheet.py "{url}" "{english}" CMBWingLung', pages=_PAGES, noise=("Fund",))
if __name__ == "__main__": raise SystemExit(main())
