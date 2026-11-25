#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""法盛资本（Natixis Investment Managers）香港月报入口适配器。

法盛资本的香港站基金页由 Fund Explorer API 提供产品目录，基金详情 API
返回当前 Factsheet 的官方 DAM 直链。本适配器只按工作簿英文名精确匹配，
并把当前 Factsheet 作为月报入口；找不到同名产品或 Factsheet 时保留明确
的 SKIP 原因，不猜测其他管理人/其他地区的文件。
"""

from __future__ import annotations

import os
import re
import sys
from typing import Any

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))

from common.fund_link_backfill import cli_main, normalize_name  # noqa: E402


SHEET_NAME = "法盛资本"
BASE_URL = "https://www.im.natixis.com"
FUNDS_API = "https://api-esb.im.natixis.com/adobe-aem-fund-explorer/api/fundExplorer"
DETAIL_API = "https://api-esb.im.natixis.com/adobe-aem-fund-explorer/api/fundDetail"
FUNDS_PAGE = "https://www.im.natixis.com/en-hk/funds"
RUN_COMMAND = 'python download_natixis_factsheet.py "{pdf_url}" "{english}"'
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; GF-FMR-Crawl/1.0)",
    "Accept": "application/json, text/plain, */*",
    "Referer": FUNDS_PAGE,
}


def _key(value: str) -> str:
    """按英文名称归一化；参数 value 为工作簿或官网基金名。"""

    text = normalize_name(str(value or "")).casefold()
    return re.sub(r"[^a-z0-9]+", "", text)


def _fetch_catalog() -> list[dict[str, Any]]:
    """读取香港 Fund Explorer，并逐只取当前 Factsheet；无参数。"""

    session = requests.Session()
    response = session.get(
        FUNDS_API,
        params={"jurisdiction": "hong kong", "urlLanguageCode": "en-hk"},
        headers=HEADERS,
        timeout=90,
    )
    response.raise_for_status()
    products = response.json()
    if not isinstance(products, list) or not products:
        raise RuntimeError("法盛资本香港 Fund Explorer 未返回基金目录")
    records: list[dict[str, Any]] = []
    for product in products:
        if not isinstance(product, dict):
            continue
        name = str(product.get("name") or "").strip()
        share_classes = product.get("fundShareClasses") or []
        if not name or not isinstance(share_classes, list):
            continue
        # API 已标注默认份额；没有默认份额时使用第一条可识别 ISIN 的份额。
        share = next((x for x in share_classes if isinstance(x, dict) and x.get("defaultClass")), None)
        share = share or next((x for x in share_classes if isinstance(x, dict) and x.get("isinCode")), None)
        if not isinstance(share, dict):
            continue
        isin = str(share.get("isinCode") or "").strip()
        detail_path = str(share.get("relativeUrlToFundDetailPage") or "").strip()
        if not isin or not detail_path:
            continue
        detail = session.get(
            DETAIL_API,
            params={"isin": isin, "jurisdiction": "hong kong", "urlLanguageCode": "en-hk"},
            headers=HEADERS,
            timeout=90,
        )
        detail.raise_for_status()
        payload = detail.json()
        docs = payload.get("featuredDocuments") if isinstance(payload, dict) else None
        factsheets = [
            x for x in (docs or [])
            if isinstance(x, dict)
            and str(x.get("label") or "").casefold() == "factsheet"
            and str(x.get("damUrl") or "").strip()
        ]
        if not factsheets:
            continue
        factsheet = factsheets[0]
        records.append(
            {
                "name": name,
                "key": _key(name),
                "isin": isin,
                # API 的 relativeUrlToFundDetailPage 是旧前端路由；当前香港站
                # 的可访问规范页为 /en-hk/funds/fund-details.<slug>.<isin>。
                "detail_url": f"{BASE_URL}/en-hk/funds/fund-details.{detail_path.rstrip('/').split('/')[-2]}.{isin}",
                "pdf_url": str(factsheet["damUrl"]).strip(),
                "file_name": str(factsheet.get("fileName") or "").strip(),
            }
        )
    if not records:
        raise RuntimeError("法盛资本香港目录没有返回可用英文 Factsheet")
    return records


def _build_index(catalog: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """按归一化英文名建立索引；参数 catalog 为官方目录记录。"""

    index: dict[str, list[dict[str, Any]]] = {}
    for item in catalog:
        index.setdefault(str(item.get("key") or _key(item.get("name", ""))), []).append(item)
    return index


def _resolve_single(row: dict[str, Any], index: dict[str, list[dict[str, Any]]]) -> tuple[str, str, str]:
    """解析一行法盛资本基金；参数 row 为工作簿行、index 为官网索引。"""

    english = str(row.get("english") or row.get("fund") or "").strip()
    records = index.get(_key(english)) or []
    if not records:
        return "", "", "NO_CURRENT_FUND：法盛资本香港 Fund Explorer 未找到同名开放基金"
    record = records[0]
    return str(record["detail_url"]), RUN_COMMAND.format(pdf_url=record["pdf_url"], english=english), ""


def main(argv: list[str] | None = None) -> int:
    """运行适配器 CLI；参数 argv 支持单基金、--probe、--backfill。"""

    return cli_main(
        argv,
        description="按法盛资本 Natixis 香港 Fund Explorer/Factsheet 补全月报入口",
        sheet_name=SHEET_NAME,
        catalog_loader=_fetch_catalog,
        index_builder=_build_index,
        resolver=_resolve_single,
        single_print_run_command=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
