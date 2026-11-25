#!/usr/bin/env python3
"""M&G Investments official monthly fund review adapter."""
from __future__ import annotations
import os, sys
from typing import Any
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common.fund_link_backfill import cli_main
from common.static_adapter import build_static_index, name_key, resolve_static
SHEET_NAME = "英卓"
DOWNLOADER = 'python code/companies/muzinich/download_static_factsheet.py "{url}" "{english}" MG'
_PAGES = {
    "M&G Total Return Credit Investment Fund": "https://www.mandginvestments.com/dam/investments/professional-investor/shared/en/documents/funds-literature/factsheet/total-return-credit-investment-fund_usd_ph_acc_sg_monthly-fund-review_eng_pe_lu3082798045.pdf",
    "M&G (Lux) Optimal Income Fund": "https://www.mandginvestments.com/dam/investments/professional-investor/shared/en/documents/funds-literature/factsheet/%28lux%29-optimal-income-fund_usd_ah_acc_sg_monthly-fund-review_eng_pe_lu1670725347.pdf",
    "M&G (Lux) Global Dividend Fund": "https://www.mandginvestments.com/dam/investments/common/gr/en/documents/funds-literature/factsheet/%28lux%29-global-dividend-fund_eur_a_acc_gr_factsheet_eng_pe_lu1670710075.pdf",
    "M&G (Lux) Japan Fund": "https://www.mandginvestments.com/dam/investments/professional-investor/shared/en/documents/funds-literature/factsheet/%28lux%29-japan-fund_usd_c_acc_sg_monthly-fund-review_eng_pe_lu1684384354.pdf",
}
def _fetch_catalog() -> list[dict[str, Any]]:
    return [{"fund_name": n, "key": name_key(n, "fund"), "url": u} for n, u in _PAGES.items()]
def _build_index(catalog): return build_static_index(catalog)
def _resolve_single(row, index): return resolve_static(row, index, DOWNLOADER)
def main(argv=None):
    return cli_main(argv, description="按 M&G 官方 Monthly Fund Review 补全月报入口", sheet_name=SHEET_NAME, catalog_loader=_fetch_catalog, index_builder=_build_index, resolver=_resolve_single, single_print_run_command=True)
if __name__ == "__main__": raise SystemExit(main())
