#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""野村基金爱尔兰官方 Factsheet 月报入口适配器。"""

from __future__ import annotations

import os
import sys
from typing import Any

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common.fund_link_backfill import cli_main  # noqa: E402
from common.static_adapter import build_static_index, name_key, resolve_static  # noqa: E402

SHEET_NAME = "野村"
DOWNLOADER = 'python download_nomura_factsheet.py "{url}" "{english}"'
BASE = "https://www.nomura-asset.co.uk/download/factsheets/"

_FACTSHEETS = {
    "Nomura Funds Ireland - US High Yield Bond Fund": BASE + "US_High_Yield_Bond_Fund_Class_I_USD_EN.pdf",
    "Nomura Funds Ireland - Japan Strategic Value Fund": BASE + "Japan_Strategic_Value_Fund_Class_I_USD_EN.pdf",
    "Nomura Funds Ireland - Asia ex Japan High Conviction Fund": BASE + "Asia_Ex_Japan_High_Conviction_Fund_Class_I_USD_EN.pdf",
    "Nomura Funds Ireland - Asia Investment Grade Bond Fund": BASE + "Asia_Investment_Grade_Bond_Fund_Class_I_USD_EN.pdf",
    "Nomura Funds Ireland - India Equity Fund": BASE + "India_Equity_Fund_Class_I_USD_EN.pdf",
    "Nomura Funds Ireland - Global Multi-Theme Equity Fund": BASE + "Global_Multi-Theme_Equity_Fund_Class_A_USD_EN.pdf",
    "Nomura Funds Ireland - Global High Yield Bond Fund": BASE + "Global_High_Yield_Bond_Fund_Class_I_USD_EN.pdf",
    "Nomura Funds Ireland - Taiwan Equity Fund": BASE + "Taiwan_Equity_Fund_Class_I_USD_EN.pdf",
}


def _fetch_catalog() -> list[dict[str, Any]]:
    """返回野村已核验官方 Factsheet 目录；无参数。"""
    return [{"fund_name": name, "key": name_key(name, "Nomura Funds Ireland", "Fund"), "url": url} for name, url in _FACTSHEETS.items()]


def _build_index(catalog: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """建立野村基金名称索引；参数 catalog 为官方目录记录。"""
    return build_static_index(catalog)


def _resolve_single(row: dict[str, Any], index: dict[str, list[dict[str, Any]]]) -> tuple[str, str, str]:
    """解析一只野村基金；参数 row 为页表行、index 为目录索引。"""
    return resolve_static(row, index, DOWNLOADER)


def main(argv: list[str] | None = None) -> int:
    """运行野村适配器 CLI；参数 argv 支持单基金、--probe、--backfill。"""
    return cli_main(argv, description="按 Nomura Funds Ireland 官方 Factsheet 补全月报入口", sheet_name=SHEET_NAME, catalog_loader=_fetch_catalog, index_builder=_build_index, resolver=_resolve_single, single_print_run_command=True)


if __name__ == "__main__":
    raise SystemExit(main())
