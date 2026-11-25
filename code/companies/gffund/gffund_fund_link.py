#!/usr/bin/env python3
"""广发基金官方 HKEX IFP 入口适配器。"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common.queue_static_adapter import build_queue_adapter

SHEET_NAME = "广发基金"
DOWNLOADER = 'python code/companies/muzinich/download_static_factsheet.py "{url}" "{english}" gffund'
PAGES = {
    "GF Industry Leaders Mixed Assets Fund": "https://ifp.hkex.com.hk/fund-repository/fund/BFT955",
    "GF Juyou Mixed Assets Fund": "https://ifp.hkex.com.hk/fund-repository/fund/BFT957",
}
_fetch_catalog, _build_index, _resolve_single, _main = build_queue_adapter(
    sheet_name=SHEET_NAME, description="按港交所 IFP 官方基金页补全广发基金入口", downloader=DOWNLOADER,
    pages=PAGES, noise=("gf", "fund"))
if __name__ == "__main__":
    raise SystemExit(_main())
