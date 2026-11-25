#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""首源投资香港月报入口适配器。

官网文学页同时承载 FSSA、First Sentier、Stewart Investors、RQI 四个品牌；
按工作簿英文基金名精确生成文学页 + 关键词下载命令。
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))

from common.fund_link_backfill import cli_main  # noqa: E402


SHEET_NAME = "首源投资"
LITERATURE_URL = "https://www.firstsentierinvestors.com/hk/en/retail/performance/literature.html"
PRODUCTS = {
    "FSSA Asian Equity Plus Fund", "FSSA China Growth Fund", "First Sentier Asian Quality Bond Fund",
    "FSSA Greater China Growth Fund", "First Sentier Global Listed Infrastructure Fund",
    "FSSA Indian Subcontinent Fund", "FSSA Asia Pacific Equity Fund", "Stewart Investors Asia Pacific Leaders Fund",
    "FSSA Asian Growth Fund", "RQI Global Value Fund", "Stewart Investors Global Emerging Markets All Cap Fund",
    "First Sentier Asian Bridge Fund", "FSSA Hong Kong Growth Fund", "Stewart Investors Asia Pacific All Cap Fund",
    "FSSA China Focus Fund", "FSSA Global Emerging Markets Focus Fund",
    "Stewart Investors Global Emerging Markets Leaders Fund", "FSSA Asia Opportunities Fund",
    "First Sentier Asia Strategic Bond Fund", "FSSA ASEAN All Cap Fund", "FSSA China A Shares Fund",
    "First Sentier Global Bond Fund", "First Sentier Global Property Securities Fund", "FSSA Japan Equity Fund",
    "FSSA China All Cap Fund", "First Sentier Global Resources Fund", "Stewart Investors Worldwide Leaders Fund",
}


def _catalog_loader() -> list[dict[str, str]]:
    """返回首源官网文学页可检索的静态产品目录；无参数。"""

    return [{"name": name, "page_url": LITERATURE_URL, "keyword": name} for name in sorted(PRODUCTS)]


def _build_index(catalog: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    """按英文基金名建立索引；参数 catalog 为静态产品目录。"""

    return {str(item["name"]).casefold(): item for item in catalog}


def _resolve_single(row: dict[str, str], index: dict[str, dict[str, str]]) -> tuple[str, str, str]:
    """解析单只首源投资月报入口；参数 row 为页表行、index 为产品索引。"""

    english = str(row.get("english") or "").strip()
    item = index.get(english.casefold())
    if not item:
        return "", "", "首源官网文学页未登记该英文基金名"
    command = f'python download_firstsentier_global_factsheet.py "{item["page_url"]}" "{item["keyword"]}" "{english}"'
    return item["page_url"], command, ""


def main(argv: list[str] | None = None) -> int:
    """运行首源投资 CLI；参数 argv 支持单基金、--probe、--backfill。"""

    return cli_main(
        argv,
        description="按 First Sentier 香港文学页补全首源投资 Factsheet 月报入口",
        sheet_name=SHEET_NAME,
        catalog_loader=_catalog_loader,
        index_builder=_build_index,
        resolver=_resolve_single,
        single_print_run_command=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
