#!/usr/bin/env python3
"""安本基金官方 HKEX IFP 入口适配器。"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common.queue_static_adapter import build_queue_adapter

SHEET_NAME = "安本"
DOWNLOADER = 'python code/companies/muzinich/download_static_factsheet.py "{url}" "{english}" aberdeenbasic'
PAGES = {
    "abrdn SICAV II - Global Short Dated Corporate Bond Fund": "https://ifp.hkex.com.hk/fund-repository/fund/BWO745",
    "abrdn SICAV I - Global Enhanced Yield Bond Fund": "https://ifp.hkex.com.hk/fund-repository/fund/BXI864",
}
_fetch_catalog, _build_index, _resolve_single, _main = build_queue_adapter(
    sheet_name=SHEET_NAME, description="按港交所 IFP 官方基金页补全安本入口", downloader=DOWNLOADER,
    pages=PAGES, noise=("abrdn", "sicav", "fund"))
if __name__ == "__main__":
    raise SystemExit(_main())
