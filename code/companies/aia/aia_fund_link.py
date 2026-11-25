#!/usr/bin/env python3
"""AIA Investment Management HK official factsheet adapter."""
from __future__ import annotations
import os, sys
from typing import Any
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common.fund_link_backfill import cli_main
from common.static_adapter import build_static_index, name_key, resolve_static
SHEET_NAME = "友邦"
DOWNLOADER = 'python code/companies/muzinich/download_static_factsheet.py "{url}" "{english}" AIA'
# The search index exposed an AIA Singapore/UAT factsheet, but its URL now
# returns 404.  Do not promote it; the catalog remains empty until AIMHK
# publishes a currently verifiable HK factsheet endpoint.
_PAGES = {}
def _fetch_catalog() -> list[dict[str, Any]]:
    return [{"fund_name": n, "key": name_key(n, "fund"), "url": u} for n, u in _PAGES.items()]
def _build_index(catalog): return build_static_index(catalog)
def _resolve_single(row, index): return resolve_static(row, index, DOWNLOADER)
def main(argv=None):
    return cli_main(argv, description="按 AIA Investment Management 官方 factsheet 补全入口", sheet_name=SHEET_NAME, catalog_loader=_fetch_catalog, index_builder=_build_index, resolver=_resolve_single, single_print_run_command=True)
if __name__ == "__main__": raise SystemExit(main())
