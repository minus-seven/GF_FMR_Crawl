#!/usr/bin/env python3
"""CITIC CLSA official fund-page/factsheet adapter."""
from __future__ import annotations
import os, sys
from typing import Any
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common.fund_link_backfill import cli_main
from common.static_adapter import build_static_index, name_key, resolve_static
SHEET_NAME = "中信"
DOWNLOADER = 'python code/companies/muzinich/download_static_factsheet.py "{url}" "{english}" CITIC'
_PAGES = {
    "CLSA US Dollar Money Market Fund": "https://www.clsa.com/wp-content/uploads/2026/06/CLSA-USD-Money-Market-Fund-Factsheet-Eng-Class_B_20260529.pdf",
    "CLSA Short Duration China Bond Fund": "https://www.clsa.com/wp-content/uploads/2025/01/CLSA-Short-Duration-China-Bond-Fund-Factsheet-Eng-_20241231.pdf",
}
def _fetch_catalog() -> list[dict[str, Any]]:
    return [{"fund_name": n, "key": name_key(n, "fund"), "url": u} for n, u in _PAGES.items()]
def _build_index(catalog): return build_static_index(catalog)
def _resolve_single(row, index): return resolve_static(row, index, DOWNLOADER)
def main(argv=None):
    return cli_main(argv, description="按 CITIC CLSA 官方入口补全月报链接", sheet_name=SHEET_NAME, catalog_loader=_fetch_catalog, index_builder=_build_index, resolver=_resolve_single, single_print_run_command=True)
if __name__ == "__main__": raise SystemExit(main())
