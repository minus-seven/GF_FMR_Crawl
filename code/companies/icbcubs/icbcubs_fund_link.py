#!/usr/bin/env python3
"""工银瑞信香港官方公募基金价格/基金月报目录适配器。"""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common.queue_static_adapter import build_queue_adapter
SHEET_NAME = "工银瑞信"
_INDEX = "https://www.icbcamg.com/cn/page/721852372031930380.html"
_PAGES = {name: _INDEX for name in ("ICBC UBS China Core Value Mixed Fund", "ICBC UBS China Prudent Growth Mixed Fund", "ICBC UBS RMB Cash Fund", "ICBC UBS China Focused Equity Fund", "ICBC UBS Global Fixed Income Fund")}
_fetch_catalog, _build_index, _resolve_single, main = build_queue_adapter(sheet_name=SHEET_NAME, description="按工银瑞信官方基金月报目录补全入口", downloader='python code/companies/muzinich/download_static_factsheet.py "{url}" "{english}" ICBCUBS', pages=_PAGES, noise=("Fund",))
if __name__ == "__main__": raise SystemExit(main())
