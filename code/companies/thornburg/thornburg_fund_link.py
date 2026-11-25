#!/usr/bin/env python3
"""尚渤投资官方 WMCube Factsheet 适配器。"""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common.queue_static_adapter import build_queue_adapter
SHEET_NAME = "尚渤投资"
_PAGES = {
    "Thornburg Limited Term Income Fund": "https://thornburg.wmcubehk.com/fund-document/fs-thornburg-limited-term-income-fund-sc.pdf",
    "Thornburg Strategic Income Fund": "https://thornburg.wmcubehk.com/fund-document/fs-thornburg-strategic-income-fund-tc.pdf",
    "Thornburg Global Opportunities Fund": "https://thornburg.wmcubehk.com/fund-document/fs-thornburg-global-opportunities-fund-tc.pdf",
}
_fetch_catalog, _build_index, _resolve_single, main = build_queue_adapter(sheet_name=SHEET_NAME, description="按尚渤官方 Factsheet 补全入口", downloader='python code/companies/muzinich/download_static_factsheet.py "{url}" "{english}" Thornburg', pages=_PAGES, noise=("Fund",))
if __name__ == "__main__": raise SystemExit(main())
