#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""骏利亨德森香港产品页月报入口适配器。

产品目录来自香港站公开的 WordPress 产品筛选 API；I 列写入官方产品页，
J 列由下载器在香港官方 Documents 分页中按英文基金名选择最新 Factsheet。
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


SHEET_NAME = "骏利亨德森"
PRODUCT_API = (
    "https://www.janushenderson.com/en-hk/investor/wp-json/"
    "jhenderson/v1/product_filters/get_tab_data"
)
RUN_COMMAND = 'python download_janushenderson_factsheet.py "{page_url}" "{english}"'
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
}


def _key(value: str) -> str:
    """归一化中英文产品名；参数 value 为工作簿或官网产品名称。"""

    text = normalize_name(str(value or "")).casefold()
    # 工作簿保留了 umbrella 名称中的 Horizon Fund -，官网产品目录通常省略该词。
    # normalize_name 已去掉英文空格，因此这里按连续词清理品牌前缀。
    text = text.replace("janushenderson", "")
    text = text.replace("horizon", "")
    text = text.replace("fund", "")
    return re.sub(r"[^a-z0-9]+", "", text)


def _fetch_catalog() -> list[dict[str, Any]]:
    """读取香港官网产品目录；无参数，返回产品名、产品页和份额信息。"""

    response = requests.get(
        PRODUCT_API,
        params={"content_name": "filterEMEAPerformance", "page_id": "137597"},
        headers=HEADERS,
        timeout=90,
    )
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, str):
        payload = json.loads(payload)
    products = payload.get("products") if isinstance(payload, dict) else None
    if not isinstance(products, list) or not products:
        raise RuntimeError("骏利亨德森香港产品 API 未返回产品目录")
    catalog: list[dict[str, Any]] = []
    for item in products:
        if not isinstance(item, dict):
            continue
        name = str(item.get("productName") or "").strip()
        share_classes = item.get("shareClasses") or []
        permalink = ""
        if isinstance(share_classes, list):
            for share in share_classes:
                if isinstance(share, dict) and share.get("permalink"):
                    permalink = str(share["permalink"]).strip()
                    break
        if not name or not permalink:
            continue
        catalog.append(
            {
                "name": name,
                "key": _key(name),
                "page_url": permalink,
                "product_id": str(item.get("productId") or ""),
            }
        )
    if not catalog:
        raise RuntimeError("骏利亨德森香港产品 API 没有可用产品页")
    return catalog


def _build_index(catalog: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """建立归一化产品名索引；参数 catalog 为官网产品目录。"""

    index: dict[str, list[dict[str, Any]]] = {}
    for item in catalog:
        index.setdefault(str(item.get("key") or ""), []).append(item)
    return index


def _resolve_single(
    row: dict[str, Any], index: dict[str, list[dict[str, Any]]]
) -> tuple[str, str, str]:
    """解析单只基金入口；参数 row 为页表行，index 为官网产品索引。"""

    english = str(row.get("english") or "").strip()
    key = _key(english)
    records = index.get(key) or []
    if not records:
        # 仅在一方是官网品牌后缀时允许严格包含匹配，避免相近策略误配。
        records = [
            item
            for item_key, values in index.items()
            if item_key.startswith(key) or key.startswith(item_key)
            for item in values
        ]
    if not records:
        return "", "", "骏利亨德森香港产品 API 未返回同名产品页"
    selected = records[0]
    page_url = str(selected["page_url"])
    return page_url, RUN_COMMAND.format(page_url=page_url, english=english), ""


def main(argv: list[str] | None = None) -> int:
    """运行骏利亨德森 CLI；参数 argv 支持单基金、--probe、--backfill。"""

    return cli_main(
        argv,
        description="按骏利亨德森香港官方产品页补全 Factsheet 月报入口",
        sheet_name=SHEET_NAME,
        catalog_loader=_fetch_catalog,
        index_builder=_build_index,
        resolver=_resolve_single,
        single_print_run_command=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
