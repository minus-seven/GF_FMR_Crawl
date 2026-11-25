#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""威灵顿香港基金月报入口适配器。

威灵顿香港页面的基金清单和文档服务是公开 JSON 接口。基金清单提供
基金 ID，文档接口按基金 ID 返回当前月度 ``FS`` Factsheet；适配器优先
选择 Retail-HK，其次 Institutional/MiFID 版本，并保留报告日期在官方
文件名中，避免把 KFS、派息记录等非月报文件当成月报。
"""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime
from typing import Any

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))

from common.fund_link_backfill import cli_main, normalize_name  # noqa: E402


SHEET_NAME = "威灵顿"
FUNDS_PAGE = "https://www.wellington.com/en-hk/individual/fund-document-list"
FUND_LIST_URL = "https://www.wellington.com/content/dam/wellington/content-fragments/en/funds/fundlisting-asset.fundListing.HK.individual.UCITS.en.contactId.json"
DOCS_URL = "https://docs.wellington.com/list/public/documents"
RUN_COMMAND = 'python download_wellington_factsheet.py "{pdf_url}" "{english}"'
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; GF-FMR-Crawl/1.0)",
    "Accept": "application/json,text/plain,*/*",
    "Referer": FUNDS_PAGE,
}


def _key(value: str) -> str:
    """归一化英文基金名；参数 value 可带 Wellington 品牌前缀。"""

    text = normalize_name(str(value or "")).casefold()
    text = re.sub(r"^wellington", "", text)
    return re.sub(r"[^a-z0-9]+", "", text)


def _doc_date(value: Any) -> datetime:
    """解析文档日期；参数 value 为官方 asOfDate 字段。"""

    text = str(value or "").strip()
    for fmt in ("%m/%d/%Y", "%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    return datetime.min


def _fetch_catalog() -> list[dict[str, Any]]:
    """读取基金清单并查询当前 FS Factsheet；无参数。"""

    session = requests.Session()
    listing = session.get(FUND_LIST_URL, headers=HEADERS, timeout=90)
    listing.raise_for_status()
    payload = listing.json()
    products = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(products, list) or not products:
        raise RuntimeError("威灵顿香港基金清单为空")
    records: list[dict[str, Any]] = []
    for product in products:
        if not isinstance(product, dict):
            continue
        name = str(product.get("fundName") or "").strip()
        fund_id = str(product.get("fundId") or "").strip()
        if not name or not fund_id:
            continue
        response = session.get(
            DOCS_URL,
            params={"query": f"FUND_ID={fund_id}&languageCd=EN", "recentMatch": "true"},
            headers=HEADERS,
            timeout=90,
        )
        response.raise_for_status()
        documents = response.json()
        candidates = [
            item for item in (documents if isinstance(documents, list) else [])
            if isinstance(item, dict)
            and str(item.get("TEMPLATE_TYP") or "").upper() == "FS"
            and str(item.get("Path") or "").startswith("http")
            and str(item.get("fileName") or "").lower().endswith(".pdf")
        ]
        if not candidates:
            continue
        # 同一基金可能有多份份额/受众，选最新日期，再优先 HK 零售版本。
        candidates.sort(
            key=lambda item: (
                _doc_date(item.get("asOfDate")),
                "Retail-HK" in str(item.get("fileName") or ""),
                "Institutional" in str(item.get("fileName") or ""),
                "MiFID" in str(item.get("fileName") or ""),
            ),
            reverse=True,
        )
        document = candidates[0]
        records.append(
            {
                "name": f"Wellington {name}",
                "key": _key(name),
                "fund_id": fund_id,
                "fund_page": FUNDS_PAGE,
                "pdf_url": str(document["Path"]).strip(),
                "file_name": str(document.get("fileName") or "").strip(),
                "as_of_date": str(document.get("asOfDate") or "").strip(),
            }
        )
    if not records:
        raise RuntimeError("威灵顿公开文档接口没有返回 FS Factsheet")
    return records


def _build_index(catalog: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """按归一化英文基金名建立索引；参数 catalog 为官方目录。"""

    index: dict[str, list[dict[str, Any]]] = {}
    for item in catalog:
        index.setdefault(str(item.get("key") or _key(item.get("name", ""))), []).append(item)
    return index


def _resolve_single(row: dict[str, Any], index: dict[str, list[dict[str, Any]]]) -> tuple[str, str, str]:
    """解析一行威灵顿基金；参数 row 为工作簿行、index 为官网索引。"""

    english = str(row.get("english") or row.get("fund") or "").strip()
    records = index.get(_key(english)) or []
    if not records:
        return "", "", "NO_CURRENT_FUND：威灵顿香港基金清单未找到同名基金或 FS Factsheet"
    record = records[0]
    return str(record["fund_page"]), RUN_COMMAND.format(pdf_url=record["pdf_url"], english=english), ""


def main(argv: list[str] | None = None) -> int:
    """运行适配器 CLI；参数 argv 支持单基金、--probe、--backfill。"""

    return cli_main(
        argv,
        description="按威灵顿香港公开文档接口补全月度 FS Factsheet",
        sheet_name=SHEET_NAME,
        catalog_loader=_fetch_catalog,
        index_builder=_build_index,
        resolver=_resolve_single,
        single_print_run_command=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
