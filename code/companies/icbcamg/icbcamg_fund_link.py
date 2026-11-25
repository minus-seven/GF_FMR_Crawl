#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工银资管（全球）官方 Factsheet 月报入口适配器。"""

from __future__ import annotations

import os
import sys
from typing import Any

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common.fund_link_backfill import cli_main  # noqa: E402
from common.static_adapter import build_static_index, name_key, resolve_static  # noqa: E402

SHEET_NAME = "工银资管"
DOWNLOADER = 'python download_icbcamg_factsheet.py "{url}" "{english}"'
BASE = "https://v.icbc.com.cn/userfiles/resources/icbc/haiwai/icbcamg/download/2020/factsheet/"

_FACTSHEETS = {
    "ICBC Asset Management China & Hong Kong Vision Fund": BASE + "CHKV.pdf",
    "ICBC Asset Management (Global) HKD Money Market Fund": BASE + "HKDMMF.pdf",
    "ICBC Asset Management (Global) USD Money Market Fund": BASE + "usdmmf.pdf",
    "ICBC Asset Management RMB Fixed Income Fund": BASE + "GRMBFI.pdf",
}


def _fetch_catalog() -> list[dict[str, Any]]:
    """返回工银资管已核验官方月报目录；无参数。"""
    return [{"fund_name": name, "key": name_key(name, "ICBC Asset Management (Global)", "Fund"), "url": url} for name, url in _FACTSHEETS.items()]


def _build_index(catalog: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """建立工银资管基金名称索引；参数 catalog 为官方目录记录。"""
    return build_static_index(catalog)


def _resolve_single(row: dict[str, Any], index: dict[str, list[dict[str, Any]]]) -> tuple[str, str, str]:
    """解析一只工银资管基金；参数 row 为页表行、index 为目录索引。"""
    return resolve_static(row, index, DOWNLOADER)


def main(argv: list[str] | None = None) -> int:
    """运行工银资管适配器 CLI；参数 argv 支持单基金、--probe、--backfill。"""
    return cli_main(argv, description="按工银资管官方 Factsheet 补全月报入口", sheet_name=SHEET_NAME, catalog_loader=_fetch_catalog, index_builder=_build_index, resolver=_resolve_single, single_print_run_command=True)


if __name__ == "__main__":
    raise SystemExit(main())
