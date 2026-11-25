#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""资本集团（Capital Group）香港 SICAV 月报入口补全适配器。

Capital Group 香港基金中心的 ``fund-centre.html`` 页面内嵌了官方基金、份额、
ISIN 和 Factsheet 可用性目录。本适配器解析该目录，按基金英文名精确匹配，
优先选择港元、美元、欧元份额，并用 Publifund 的香港 Factsheet 地址下载。
已清算或当前目录不存在的基金不做相似产品猜测。
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
from typing import Any

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))

from common.fund_link_backfill import cli_main, normalize_name  # noqa: E402


SHEET_NAME = "资本集团"
FUND_CENTRE_URL = "https://www.capitalgroup.com/intermediaries/hk/en/investments/fund-centre.html"
FUND_PAGE_BASE = "https://www.capitalgroup.com/intermediaries/hk/en/investments/fund-centre.{acronym}.html"
FACTSHEET_BASE = "https://docs.publifund.com/12_FACT/{isin}/en_HK"
RUN_COMMAND = 'python download_capitalgroup_factsheet.py "{page_url}" "{english}" "{factsheet_url}"'
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}


def _key(value: str) -> str:
    """归一化英文基金名；参数 value 为工作簿或官网名称。"""

    text = normalize_name(str(value or "")).casefold()
    return re.sub(r"[^a-z0-9]+", "", text)


def _fetch_catalog() -> list[dict[str, Any]]:
    """读取 Capital Group 香港基金中心内嵌目录；本函数无参数。"""

    response = requests.get(FUND_CENTRE_URL, headers=HEADERS, timeout=120)
    response.raise_for_status()
    match = re.search(
        r'id="fund-data"[^>]*data-attribute-fundata="(.*?)"',
        response.text,
        flags=re.S,
    )
    if not match:
        raise RuntimeError("资本集团官方基金中心未找到 fund-data 目录")
    payload = json.loads(html.unescape(match.group(1)))
    funds = payload.get("funds")
    if not isinstance(funds, list) or not funds:
        raise RuntimeError("资本集团官方基金目录为空或格式异常")
    return [item for item in funds if isinstance(item, dict)]


def _build_index(catalog: list[dict[str, Any]]) -> dict[str, Any]:
    """按归一化基金名建立官方目录索引；参数 catalog 为官网基金记录。"""

    index: dict[str, list[dict[str, Any]]] = {}
    for record in catalog:
        name = str(record.get("name") or "").strip()
        if name:
            index.setdefault(_key(name), []).append(record)
    return {"index": index, "catalog": catalog}


def _pick_share_class(record: dict[str, Any]) -> dict[str, Any] | None:
    """选出有 Factsheet 的份额/币种；参数 record 为单只基金目录记录。"""

    currency_rank = {"HKD": 0, "USD": 1, "EUR": 2, "GBP": 3, "JPY": 4}
    choices: list[dict[str, Any]] = []
    for share in record.get("shareClasses") or []:
        if not isinstance(share, dict):
            continue
        for currency in share.get("currencies") or []:
            if not isinstance(currency, dict) or str(currency.get("hasFactsheet") or "").upper() != "YES":
                continue
            isin = str(currency.get("isin") or "").strip()
            if not isin:
                continue
            choices.append({
                "share_class": str(share.get("name") or ""),
                "currency": str(currency.get("name") or "").upper(),
                "isin": isin,
            })
    if not choices:
        return None
    return sorted(choices, key=lambda item: (currency_rank.get(item["currency"], 9), item["share_class"], item["isin"]))[0]


def _resolve_single(row: dict[str, Any], index: dict[str, Any]) -> tuple[str, str, str]:
    """解析单只资本集团月报入口；参数 row 为页表行、index 为官方目录索引。"""

    english = str(row.get("english") or row.get("fund") or "").strip()
    records = index["index"].get(_key(english)) or []
    if not records:
        return "", "", "NO_CURRENT_FUND：Capital Group 香港当前目录未返回该基金（可能已清算）"
    record = records[0]
    selected = _pick_share_class(record)
    if not selected:
        return "", "", "NO_FACTSHEET：官方基金存在，但当前目录没有可用香港 Factsheet 份额"
    acronym = str(record.get("acronym") or "").strip()
    page_url = FUND_PAGE_BASE.format(acronym=acronym)
    factsheet_url = FACTSHEET_BASE.format(isin=selected["isin"])
    return page_url, RUN_COMMAND.format(page_url=page_url, english=english, factsheet_url=factsheet_url), ""


def main(argv: list[str] | None = None) -> int:
    """运行资本集团 CLI；参数 argv 支持单基金、--probe、--backfill。"""

    return cli_main(
        argv,
        description="按 Capital Group 香港官方基金中心补全 SICAV Factsheet 月报入口",
        sheet_name=SHEET_NAME,
        catalog_loader=_fetch_catalog,
        index_builder=_build_index,
        resolver=_resolve_single,
        single_print_run_command=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
