#!/usr/bin/env python3
"""China Life Franklin official disclosure-page adapter.

Only the USD money-market product currently has a verified public disclosure
entry in the official site; the other five remain explicit, auditable gaps.
"""
from __future__ import annotations
import os, sys
from typing import Any
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common.fund_link_backfill import cli_main
from common.static_adapter import build_static_index, name_key, resolve_static
SHEET_NAME = "中国人寿富兰克林"
DOWNLOADER = 'python code/companies/muzinich/download_static_factsheet.py "{url}" "{english}" ChinaLifeFranklin'
_PAGES = {
    "China Life Franklin USD Money Market Fund": "https://www.clamc.com.hk/index.php?a=Ehome&f=product&p=1&pid=7",
}
def _fetch_catalog() -> list[dict[str, Any]]:
    return [{"fund_name": n, "key": name_key(n, "fund"), "url": u} for n, u in _PAGES.items()]
def _build_index(catalog): return build_static_index(catalog)
def _resolve_single(row, index): return resolve_static(row, index, DOWNLOADER)
def main(argv=None):
    return cli_main(argv, description="按中国人寿富兰克林官方披露页补全入口", sheet_name=SHEET_NAME, catalog_loader=_fetch_catalog, index_builder=_build_index, resolver=_resolve_single, single_print_run_command=True)
if __name__ == "__main__": raise SystemExit(main())
