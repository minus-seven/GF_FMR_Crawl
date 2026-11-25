#!/usr/bin/env python3
"""建信基金香港官方 Factsheet 适配器。"""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common.queue_static_adapter import build_queue_adapter
SHEET_NAME = "建信"
_PAGES = {"CCB Principal Selected Growth Mixed Asset Fund": "https://www.principal.com.hk/sites/default/files/general-files/Fund_Fact_Sheet_CCB_Principal_SGMAF_ZH.pdf"}
_fetch_catalog, _build_index, _resolve_single, main = build_queue_adapter(sheet_name=SHEET_NAME, description="按 CCB Principal 官方 Factsheet 补全入口", downloader='python code/companies/muzinich/download_static_factsheet.py "{url}" "{english}" CCBPrincipal', pages=_PAGES, noise=("Fund", "Asset"))
if __name__ == "__main__": raise SystemExit(main())
