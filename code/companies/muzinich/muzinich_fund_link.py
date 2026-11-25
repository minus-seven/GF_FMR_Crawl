#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Muzinich & Co official product-page monthly-report adapter.

The product pages are the stable official entry points; the downloader resolves
the current PDF from the page at execution time so stale PDF URLs are not baked
into the workbook.
"""
from __future__ import annotations
import os, sys
from typing import Any
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common.fund_link_backfill import cli_main
from common.static_adapter import build_static_index, name_key, resolve_static

SHEET_NAME = "Muzinich"
DOWNLOADER = 'python code/companies/muzinich/download_static_factsheet.py "{url}" "{english}" Muzinich'
_PAGES = {
    "Muzinich Enhancedyield Short-Term Fund": "https://www.muzinich.com/funds/muzinich-enhancedyield-short-term-fund",
    "Muzinich Global Short Duration Investment Grade Fund": "https://www.muzinich.com/funds/muzinich-global-short-duration-investment-grade-fund",
    "Muzinich Global Tactical Credit Fund": "https://www.muzinich.com/funds/muzinich-global-tactical-credit-fund",
    "Muzinich Europeyield Fund": "https://www.muzinich.com/funds/muzinich-europeyield-fund",
    "Muzinich Global Market Duration Investment Grade Fund": "https://www.muzinich.com/funds/muzinich-global-market-duration-investment-grade-fund",
    "Muzinich Americayield Fund": "https://www.muzinich.com/funds/muzinich-americayield-fund",
    "Muzinich Asia Credit Opportunities Fund": "https://www.muzinich.com/funds/muzinich-asia-credit-opportunities-fund",
}
def _fetch_catalog() -> list[dict[str, Any]]:
    return [{"fund_name": n, "key": name_key(n, "fund"), "url": u} for n, u in _PAGES.items()]
def _build_index(catalog):
    return build_static_index(catalog)
def _resolve_single(row, index):
    return resolve_static(row, index, DOWNLOADER)
def main(argv=None):
    return cli_main(argv, description="按 Muzinich 官方产品页补全月报入口", sheet_name=SHEET_NAME, catalog_loader=_fetch_catalog, index_builder=_build_index, resolver=_resolve_single, single_print_run_command=True)
if __name__ == "__main__":
    raise SystemExit(main())
