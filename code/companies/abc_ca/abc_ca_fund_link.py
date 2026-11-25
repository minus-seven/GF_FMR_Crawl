#!/usr/bin/env python3
"""农银汇理（ABC-CA）基金官方 HKEX IFP 入口适配器。"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common.queue_static_adapter import build_queue_adapter

SHEET_NAME = "农银汇理"
DOWNLOADER = 'python code/companies/muzinich/download_static_factsheet.py "{url}" "{english}" abc-ca'
PAGES = {
    "ABC-CA Sector Growth Mixed Securities Investment Fund": "https://ifp.hkex.com.hk/fund-repository/fund/BHK958",
    "ABC-CA Consumption Theme Mixed Securities Investment Fund": "https://ifp.hkex.com.hk/fund-repository/fund/BHK957",
}
_fetch_catalog, _build_index, _resolve_single, _main = build_queue_adapter(
    sheet_name=SHEET_NAME, description="按港交所 IFP 官方基金页补全农银汇理入口", downloader=DOWNLOADER,
    pages=PAGES, noise=("abc-ca", "fund"))
if __name__ == "__main__":
    raise SystemExit(_main())
