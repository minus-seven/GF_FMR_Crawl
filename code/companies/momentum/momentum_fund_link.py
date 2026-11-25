#!/usr/bin/env python3
"""Momentum MGF official factsheet-library adapter."""
from __future__ import annotations
import os, sys
from typing import Any
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common.fund_link_backfill import cli_main
from common.static_adapter import build_static_index, name_key, resolve_static
SHEET_NAME = "动量环球"
DOWNLOADER = 'python code/companies/muzinich/download_static_factsheet.py "{url}" "{english}" Momentum'
LIBRARY = "https://momentum.co.uk/mgf-fact-sheets"
_FUNDS = {
    "Harmony Portfolios Global Growth Fund", "Harmony Portfolios Global Balanced Fund",
    "Harmony Portfolios Asian Growth Fund", "Harmony Portfolios Sterling Growth Fund",
    "Harmony Portfolios Australian Dollar Growth", "Harmony Portfolios Sterling Balanced Fund",
}
def _fetch_catalog() -> list[dict[str, Any]]:
    return [{"fund_name": n, "key": name_key(n, "fund"), "url": LIBRARY} for n in sorted(_FUNDS)]
def _build_index(catalog): return build_static_index(catalog)
def _resolve_single(row, index): return resolve_static(row, index, DOWNLOADER)
def main(argv=None):
    return cli_main(argv, description="按 Momentum 官方 MGF factsheet library 补全入口", sheet_name=SHEET_NAME, catalog_loader=_fetch_catalog, index_builder=_build_index, resolver=_resolve_single, single_print_run_command=True)
if __name__ == "__main__": raise SystemExit(main())
