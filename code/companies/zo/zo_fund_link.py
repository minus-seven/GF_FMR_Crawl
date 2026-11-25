#!/usr/bin/env python3
"""中欧基金国际汇智官方基金入口适配器。"""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common.queue_static_adapter import build_queue_adapter
SHEET_NAME = "中欧"
_PAGES = {"ZO Neo-Horizons Bond Fund": "https://ifp.hkex.com.hk/fund-repository/zh-CN/fund/BTU985"}
_fetch_catalog, _build_index, _resolve_single, main = build_queue_adapter(sheet_name=SHEET_NAME, description="按中欧基金官方 HKEX 产品入口补全月报", downloader='python code/companies/muzinich/download_static_factsheet.py "{url}" "{english}" ZO', pages=_PAGES, noise=("Fund",))
if __name__ == "__main__": raise SystemExit(main())
