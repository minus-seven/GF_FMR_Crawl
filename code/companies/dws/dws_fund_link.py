#!/usr/bin/env python3
"""德意志资管 DWS Invest 官方 HKEX IFP 入口适配器。"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common.queue_static_adapter import build_queue_adapter

SHEET_NAME = "德意志资管"
DOWNLOADER = 'python code/companies/muzinich/download_static_factsheet.py "{url}" "{english}" dws'
PAGES = {
    "DWS Invest Asian Bonds": "https://ifp.hkex.com.hk/fund-repository/fund/BNG751",
    "DWS Invest Chinese Equities": "https://ifp.hkex.com.hk/fund-repository/fund/APU615",
}
_fetch_catalog, _build_index, _resolve_single, _main = build_queue_adapter(
    sheet_name=SHEET_NAME, description="按港交所 IFP 官方基金页补全德意志资管入口", downloader=DOWNLOADER,
    pages=PAGES, noise=("dws", "invest", "fund"))
if __name__ == "__main__":
    raise SystemExit(_main())
