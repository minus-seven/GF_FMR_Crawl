#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""思博瑞资管香港/卢森堡 UCITS 月报入口适配器。

Allspring 新站的产品页在 Documents 区域提供 Monthly Fact Sheet。页表 I 列保存
官方产品页，下载器运行时从该页重新解析最新月报，避免固定 PDF 过期。
"""

from __future__ import annotations

import os
import sys
from typing import Any

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common.fund_link_backfill import cli_main, normalize_name  # noqa: E402
from common.static_adapter import build_static_index, name_key, resolve_static  # noqa: E402

SHEET_NAME = "思博瑞资管"
DOWNLOADER = 'python download_allspring_factsheet.py "{url}" "{english}"'

_PAGES = {
    "Global Equity Enhanced Income Fund": "https://www.allspringglobal.com/investments/equity/ucits/global-equity-enhanced-income/",
    "Emerging Markets Equity Fund": "https://www.allspringglobal.com/investments/equity/ucits/emerging-markets-equity/",
    "USD Investment Grade Credit Fund": "https://www.allspringglobal.com/investments/fixed-income/ucits/usd-investment-grade-credit/",
    "U.S. All Cap Growth Fund": "https://www.allspringglobal.com/investments/equity/ucits/us-all-cap-growth/",
    "EUR Investment Grade Credit Fund": "https://www.allspringglobal.com/investments/fixed-income/ucits/eur-investment-grade-credit/",
    "Emerging Markets Equity Advantage Fund": "https://www.allspringglobal.com/investments/equity/ucits/emerging-markets-equity-advantage/",
    "Global Income Fund": "https://www.allspringglobal.com/investments/fixed-income/ucits/global-income/",
    "Climate Transition Global Investment Grade Credit Fund": "https://www.allspringglobal.com/investments/fixed-income/ucits/climate-transition-global-investment-grade-credit/",
    "U.S. Short-Term High Income Fund": "https://www.allspringglobal.com/investments/fixed-income/ucits/us-short-term-high-income/",
    "Global High Yield Fund": "https://www.allspringglobal.com/investments/fixed-income/ucits/global-high-yield-fund/",
    "Global Small Cap Equity Fund": "https://www.allspringglobal.com/investments/equity/ucits/global-small-cap-equity/",
}


def _fetch_catalog() -> list[dict[str, Any]]:
    """返回已核验的 Allspring 官方产品页目录；无参数。"""
    return [
        {"fund_name": name, "key": name_key(name, "Allspring (Lux) Worldwide Fund", "Fund"), "url": url}
        for name, url in _PAGES.items()
    ]


def _build_index(catalog: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """建立 Allspring 产品名称索引；参数 catalog 为官方目录记录。"""
    return build_static_index(catalog)


def _resolve_single(row: dict[str, Any], index: dict[str, list[dict[str, Any]]]) -> tuple[str, str, str]:
    """解析一只思博瑞基金；参数 row 为页表行、index 为官方产品索引。"""
    return resolve_static(row, index, DOWNLOADER)


def main(argv: list[str] | None = None) -> int:
    """运行思博瑞适配器 CLI；参数 argv 支持单基金、--probe、--backfill。"""
    return cli_main(argv, description="按 Allspring 官方产品页补全思博瑞月报入口", sheet_name=SHEET_NAME, catalog_loader=_fetch_catalog, index_builder=_build_index, resolver=_resolve_single, single_print_run_command=True)


if __name__ == "__main__":
    raise SystemExit(main())
