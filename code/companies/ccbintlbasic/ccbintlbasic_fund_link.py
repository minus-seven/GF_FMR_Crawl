#!/usr/bin/env python3
"""建银基金官方 HKEX IFP 入口适配器。"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common.queue_static_adapter import build_queue_adapter

SHEET_NAME = "建银"
DOWNLOADER = 'python code/companies/muzinich/download_static_factsheet.py "{url}" "{english}" ccbintlbasic'
PAGES = {
    "CCBI Global Investment Grade Short Duration Bond Fund": "https://ifp.hkex.com.hk/fund-repository/fund/BWL295",
    "CCBI RMB Money Market Fund": "https://ifp.hkex.com.hk/fund-repository/fund/BXE160",
}
_fetch_catalog, _build_index, _resolve_single, _main = build_queue_adapter(
    sheet_name=SHEET_NAME, description="按港交所 IFP 官方基金页补全建银入口", downloader=DOWNLOADER,
    pages=PAGES, noise=("ccbi", "fund"))
if __name__ == "__main__":
    raise SystemExit(_main())
