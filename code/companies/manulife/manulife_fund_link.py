#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""宏利香港官方 Factsheet（月报）入口补全适配器。

宏利的基金文件选择器由浏览器动态渲染，批量运行环境不依赖脆弱的页面抓取；
本适配器把官方文件选择器核验后的稳定 Factsheet 资产地址登记为映射，
并保留无法从当前公开目录验证的基金为明确 SKIP，避免把产品资料误当月报。
"""

from __future__ import annotations

import os
import re
import sys
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))

from common.fund_link_backfill import cli_main, normalize_name  # noqa: E402


SHEET_NAME = "宏利"
FUND_DOCUMENTS_URL = "https://www.manulifeim.com.hk/en/resources/fund-documents.html"
RUN_COMMAND = 'python download_manulife_factsheet.py "{url}" "{english}" "{pdf_url}"'

# 由宏利官方 Fund Documents 的 Manulife / Factsheet 结果核验得到；
# URL 末尾代码是资产文件名，不把随机页面状态或搜索结果写入工作簿。
FACTSHEET_CODES: dict[str, str] = {
    "Manulife Global Fund - Global Multi-Asset Diversified Income Fund": "MLGMRH",
    "Manulife Global Fund - China Value Fund": "CVFAA",
    "Manulife Global Fund - Asia Pacific REIT Fund": "MLAPGN",
    "Manulife Global Fund - Healthcare Fund": "MLHCAA",
    "Manulife Global Fund - Preferred Securities Income Fund": "MLPI1U",
    "Manulife Global Fund - India Equity Fund": "IEAA",
    "Manulife Global Fund - Sustainable Asia Bond Fund": "MLSAAK",
    "Manulife Global Fund - Asian High Yield Fund": "MLAHAB",
    "Manulife Global Fund - Global Equity Fund": "IGFAA",
    "Manulife Global Fund - Taiwan Equity Fund": "TEAA",
    "Manulife Global Fund - Japan Equity Fund": "JGFAA",
    "Manulife Global Fund - Global Equity Diversified Income Fund": "MLED1U",
    "Manulife Global Fund - Asia Dynamic Income Fund": "MLADMD",
    "Manulife Global Fund - Global REIT Fund": "MLGP1B",
    "Manulife Global Fund - Investment Grade Preferred Securities Income Fund": "MLIGAU",
    "Manulife Global Fund - USD Income Fund": "MLUBMH",
}


def _key(value: str) -> str:
    """归一化英文基金名；参数 value 为页表或官方目录名称。"""

    text = normalize_name(str(value or "")).casefold()
    text = re.sub(r"\s+", "", text)
    return text


def _factsheet_url(code: str) -> str:
    """生成宏利官方 Factsheet 资产地址；参数 code 为文件代码。"""

    return f"https://www.manulifeim.com.hk/content/dam/wam/hk/en/funds/factsheet/{code}-factsheet.pdf"


def _fetch_catalog() -> list[dict[str, Any]]:
    """返回已核验的官方 Factsheet 目录；本函数无参数。"""

    return [
        {"english": name, "code": code, "factsheet_url": _factsheet_url(code)}
        for name, code in FACTSHEET_CODES.items()
    ]


def _build_index(catalog: list[dict[str, Any]]) -> dict[str, Any]:
    """建立归一化英文名索引；参数 catalog 为官方 Factsheet 目录。"""

    return {"index": {_key(item["english"]): item for item in catalog}, "catalog": catalog}


def _resolve_single(row: dict[str, Any], index: dict[str, Any]) -> tuple[str, str, str | None]:
    """解析一行宏利月报入口；参数 row 为页表行、index 为官网索引。"""

    english = str(row.get("english") or row.get("fund") or "").strip()
    record = index["index"].get(_key(english))
    if not record:
        return "", "", "NO_FACTSHEET：宏利当前官方文件选择器未核验到单只 Factsheet；不以 KFS/宣传册替代月报"
    factsheet_url = str(record["factsheet_url"])
    return (
        FUND_DOCUMENTS_URL,
        RUN_COMMAND.format(url=FUND_DOCUMENTS_URL, english=english, pdf_url=factsheet_url),
        "",
    )


def main(argv: list[str] | None = None) -> int:
    """运行宏利 CLI；参数 argv 支持单基金、--probe、--backfill。"""

    return cli_main(
        argv,
        description="按宏利香港官方 Fund Documents/Factsheet 补全月报入口",
        sheet_name=SHEET_NAME,
        catalog_loader=_fetch_catalog,
        index_builder=_build_index,
        resolver=_resolve_single,
        single_print_run_command=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
