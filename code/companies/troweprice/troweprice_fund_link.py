#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""普信（T. Rowe Price）香港 SICAV Monthly Factsheet 入口补全适配器。

官方香港基金表由页面公开的产品 API 驱动；本适配器运行时读取 API key，
按英文基金名选择一个可验证的 A/A6p 等份额，并使用官方 Literature Service
的稳定月报地址。当前目录未列出的历史或已终止产品不做相似产品猜测。
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))

from common.fund_link_backfill import cli_main, normalize_name  # noqa: E402


SHEET_NAME = "普信"
FUND_TABLE_URL = "https://www.troweprice.com/financial-intermediary/hk/en/funds.table.html"
API_URL = (
    "https://api.public.troweprice.com/iid-product/all-funds-table-rest/"
    "iid-product-rest/product/audience/intermediary/country/hk/language/en/"
    "table-performance/filter?productCodes=GAFIU&vehicle=SICAV&inactive=false"
)
LITERATURE_BASE = (
    "https://www.troweprice.com/literature/public/country/hk/language/en/"
    "literature-type/monthly-factsheet/sub-type/SICAV-institutional-multi-class"
)
RUN_COMMAND = 'python download_troweprice_factsheet.py "{url}" "{english}"'
API_KEY_FALLBACK = "OwTtEgNRAwsKVS2lHlkGTgM1A7iu7OIK"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Accept": "application/json",
    "X-TRP-Requested-With": "XMLHttpRequest",
}
UNSUPPORTED_REASONS = {
    "Emerging Local Markets Bond Fund": "当前香港基金 API 未列出该产品；仅核验到其他地区产品页，不写香港月报链接",
    "Global High Yield Bond Fund": "当前香港基金 API 未列出该产品；不映射到名称相近的 High Yield Bond Fund",
    "Asian Opportunities Equity Fund": "官方公告显示该基金已进入终止/清算流程，不认领相近的 Asian ex-Japan Equity Fund",
    "Retirement Allocation Fund - 3": "官方招股书仍提及该子基金，但当前香港产品 API 没有可验证的单只 Monthly Factsheet",
}
CURRENCY_RANK = {"HKD": 0, "USD": 1}


def _key(value: str) -> str:
    """归一化英文基金名；参数 value 为页表或官方目录名称。"""

    text = normalize_name(str(value or "")).casefold()
    return re.sub(r"[^a-z0-9]+", "", text)


def _api_key(html_text: str) -> str:
    """从官方页面读取 API key；参数 html_text 为基金表页面 HTML。"""

    match = re.search(r'data-apikey=["\']([^"\']+)', html_text, re.I)
    return str(match.group(1)).strip() if match else API_KEY_FALLBACK


def _fetch_catalog() -> list[dict[str, Any]]:
    """读取官方香港基金目录 API；本函数无参数。"""

    session = requests.Session()
    page = session.get(FUND_TABLE_URL, headers=HEADERS, timeout=120)
    page.raise_for_status()
    headers = {**HEADERS, "apikey": _api_key(page.text)}
    response = session.get(API_URL, headers=headers, timeout=120)
    response.raise_for_status()
    payload = response.json()
    records = (((payload.get("funds") or {}).get("Monthly") or {}).get("allFundsData"))
    if not isinstance(records, list) or not records:
        raise RuntimeError("普信香港官方基金目录为空或格式异常")
    return [item for item in records if isinstance(item, dict)]


def _pick_record(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    """选择可下载份额；参数 records 为同一基金的官方份额列表。"""

    if not records:
        return None
    return sorted(
        records,
        key=lambda item: (
            0 if str(item.get("shareClass") or "") == "A" else 1,
            CURRENCY_RANK.get(str(item.get("baseCurrency") or "").upper(), 2),
            str(item.get("productCode") or ""),
        ),
    )[0]


def _build_index(catalog: list[dict[str, Any]]) -> dict[str, Any]:
    """按归一化英文基金名建立份额索引；参数 catalog 为官方目录。"""

    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in catalog:
        name = str(record.get("fundName") or "").strip()
        if name and str(record.get("productCode") or "").strip():
            grouped.setdefault(_key(name), []).append(record)
    return {"index": grouped, "catalog": catalog}


def _resolve_single(row: dict[str, Any], index: dict[str, Any]) -> tuple[str, str, str | None]:
    """解析一行普信月报入口；参数 row 为页表行、index 为官方目录索引。"""

    english = str(row.get("english") or row.get("fund") or "").strip()
    records = index["index"].get(_key(english)) or []
    if not records:
        reason = next(
            (detail for name, detail in UNSUPPORTED_REASONS.items() if _key(name) == _key(english)),
            "当前普信香港官方基金 API 未列出同名产品；不映射到相近策略",
        )
        return "", "", reason
    selected = _pick_record(records)
    if not selected:
        return "", "", "NO_FACTSHEET：官方目录存在但没有可用份额代码"
    product_code = str(selected.get("productCode") or "").strip()
    page_url = str(selected.get("fundLink") or FUND_TABLE_URL).strip()
    pdf_url = f"{LITERATURE_BASE}?productCode={product_code}"
    return page_url, RUN_COMMAND.format(url=pdf_url, english=english), ""


def main(argv: list[str] | None = None) -> int:
    """运行普信 CLI；参数 argv 支持单基金、--probe、--backfill。"""

    return cli_main(
        argv,
        description="按普信香港官方基金目录补全 SICAV Monthly Factsheet 入口",
        sheet_name=SHEET_NAME,
        catalog_loader=_fetch_catalog,
        index_builder=_build_index,
        resolver=_resolve_single,
        single_print_run_command=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
