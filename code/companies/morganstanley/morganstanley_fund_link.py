#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""摩根斯坦利香港官方产品页/Factsheet 月报入口适配器。"""

from __future__ import annotations

import os
import sys
from typing import Any

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common.fund_link_backfill import cli_main  # noqa: E402
from common.static_adapter import build_static_index, name_key, resolve_static  # noqa: E402

SHEET_NAME = "摩根斯坦利"
DOWNLOADER = 'python download_morganstanley_factsheet.py "{url}" "{english}"'
BASE = "https://www.morganstanley.com/im/en-hk/institutional-investor/products/morgan-stanley-investment-funds/"

_PAGES = {
    "Global Opportunity Fund": BASE + "global-equity/global-opportunity-fund.html",
    "Global Brands Fund": BASE + "global-equity/global-brands-fund.html",
    "US Advantage Fund": BASE + "us-equity/us-advantage-fund.html",
    "US Growth Fund": BASE + "us-equity/us-growth-fund.html",
    "Asia Opportunity Fund": BASE + "emerging-markets-equity/asia-opportunity-fund.html",
    "Global Bond Fund": BASE + "fixed-income/global-bond-fund.html",
    "Sustainable Emerging Markets Equity Fund": BASE + "emerging-markets-equity/sustainable-emerging-markets-equity-fund.html",
    "Global Convertible Bond Fund": BASE + "leveraged-credit/global-convertible-bond-fund.html",
    "QuantActive Global Infrastructure Fund": BASE + "thematic/quantactive-global-infrastructure-fund.html",
    "Emerging Markets Debt Fund": BASE + "fixed-income/emerging-markets-debt-fund.html",
    "NextGen Emerging Markets Fund": BASE + "emerging-markets-equity/nextgen-emerging-markets-fund.html",
}


def _fetch_catalog() -> list[dict[str, Any]]:
    """返回摩根斯坦利香港官方产品页目录；无参数。"""
    return [{"fund_name": name, "key": name_key(name, "Morgan Stanley Investment Funds", "Fund"), "url": url} for name, url in _PAGES.items()]


def _build_index(catalog: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """建立摩根斯坦利产品索引；参数 catalog 为官方目录记录。"""
    return build_static_index(catalog)


def _resolve_single(row: dict[str, Any], index: dict[str, list[dict[str, Any]]]) -> tuple[str, str, str]:
    """解析一只摩根斯坦利基金；参数 row 为页表行、index 为产品索引。"""
    return resolve_static(row, index, DOWNLOADER)


def main(argv: list[str] | None = None) -> int:
    """运行摩根斯坦利适配器 CLI；参数 argv 支持单基金、--probe、--backfill。"""
    return cli_main(argv, description="按 Morgan Stanley 香港官方产品页补全月报入口", sheet_name=SHEET_NAME, catalog_loader=_fetch_catalog, index_builder=_build_index, resolver=_resolve_single, single_print_run_command=True)


if __name__ == "__main__":
    raise SystemExit(main())
