#!/usr/bin/env python3
"""铭基亚洲 Matthews Asia 香港基金文件目录适配器。"""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common.queue_static_adapter import build_queue_adapter
SHEET_NAME = "铭基环球"
_INDEX = "https://hk.matthewsasia.com/resources/docs/fund-documents/"
_PAGES = {name: _INDEX for name in ("Matthews Asia Funds - China Innovators Fund", "Matthews Asia Funds - Asia Discovery Fund", "Matthews Asia Funds - Asia ex Japan Total Return Equity Fund")}
_fetch_catalog, _build_index, _resolve_single, main = build_queue_adapter(sheet_name=SHEET_NAME, description="按 Matthews Asia 官方基金文件目录补全入口", downloader='python code/companies/muzinich/download_static_factsheet.py "{url}" "{english}" Matthews', pages=_PAGES, noise=("Fund", "Funds"))
if __name__ == "__main__": raise SystemExit(main())
