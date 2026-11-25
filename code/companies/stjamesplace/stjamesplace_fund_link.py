#!/usr/bin/env python3
"""St James's Place adapter placeholder with explicit no-source outcomes.

The Hong Kong public site does not expose a verified monthly factsheet URL for
these unit trusts.  Keeping an adapter with an empty catalog makes that gap
visible to the SOP and prevents guessed links from entering the workbook.
"""
from __future__ import annotations
import os, sys
from typing import Any
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common.fund_link_backfill import cli_main
from common.static_adapter import build_static_index, resolve_static
SHEET_NAME = "圣詹姆斯广场"
DOWNLOADER = 'python code/companies/muzinich/download_static_factsheet.py "{url}" "{english}" StJamesPlace'
def _fetch_catalog() -> list[dict[str, Any]]: return []
def _build_index(catalog): return build_static_index(catalog)
def _resolve_single(row, index): return resolve_static(row, index, DOWNLOADER)
def main(argv=None):
    return cli_main(argv, description="按 St James's Place 官方目录核验月报入口", sheet_name=SHEET_NAME, catalog_loader=_fetch_catalog, index_builder=_build_index, resolver=_resolve_single, single_print_run_command=True)
if __name__ == "__main__": raise SystemExit(main())
