#!/usr/bin/env python3
"""华安香港官方基金资料入口适配器。"""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common.queue_static_adapter import build_queue_adapter
SHEET_NAME = "华安"
_PAGES = {"HuaAn Investment Grade Bond Fund": "https://www.huaan.com.hk/en/fund/file/stamp/1290.html"}
_fetch_catalog, _build_index, _resolve_single, main = build_queue_adapter(sheet_name=SHEET_NAME, description="按华安香港官方基金文件页补全入口", downloader='python code/companies/muzinich/download_static_factsheet.py "{url}" "{english}" HuaAn', pages=_PAGES, noise=("Fund",))
if __name__ == "__main__": raise SystemExit(main())
