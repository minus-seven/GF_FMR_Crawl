#!/usr/bin/env python3
"""凯基香港官方基金详情/月报入口适配器。"""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common.queue_static_adapter import build_queue_adapter
SHEET_NAME = "凯基"
_PAGES = {"KGI Diversified Growth Income Fund": "https://www.kgi.com.hk/zh-cn/asset-management/kgi-diversified-growth-income-fund"}
_fetch_catalog, _build_index, _resolve_single, main = build_queue_adapter(sheet_name=SHEET_NAME, description="按 KGI 官方基金详情页补全入口", downloader='python code/companies/muzinich/download_static_factsheet.py "{url}" "{english}" KGI', pages=_PAGES, noise=("Fund",))
if __name__ == "__main__": raise SystemExit(main())
