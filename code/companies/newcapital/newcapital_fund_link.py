#!/usr/bin/env python3
"""瑞士盈丰 EFG New Capital 官方产品页适配器。"""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common.queue_static_adapter import build_queue_adapter
SHEET_NAME = "瑞士盈丰"
_PAGES = {
    "New Capital US Growth Fund": "https://www.newcapital.com/it/products/fund-commentaries/US-Growth-Fund.html",
    "New Capital Wealthy Nations Bond Fund": "https://www.newcapital.com/en/products/funds-and-performance/New-Capital-Wealthy-Nations-Bond-Fund.html",
    "New Capital Global Equity Income Fund": "https://www.newcapital.com/en/products/fund-commentaries/New-Capital-Global-Equity-Income-Fund.html",
}
_fetch_catalog, _build_index, _resolve_single, main = build_queue_adapter(sheet_name=SHEET_NAME, description="按 EFG New Capital 官方产品页补全入口", downloader='python code/companies/muzinich/download_static_factsheet.py "{url}" "{english}" NewCapital', pages=_PAGES, noise=("Fund",))
if __name__ == "__main__": raise SystemExit(main())
