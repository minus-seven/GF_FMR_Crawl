#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""联博香港基金月报入口补全适配器。

联博香港基金页由官方 ``webapi.alliancebernstein.com`` 提供数据。接口返回
基金份额及其产品文档；本适配器按基金编号去重，选择 ``Fact Sheet``（不把
通用的 ``Fact Sheet Book`` 当成单只基金月报），并把稳定基金页和当前 PDF
地址写入临时回填结果。正式工作簿仍遵循 SOP 保持 I/J 为空。
"""

from __future__ import annotations

import os
import re
import sys
import unicodedata
from datetime import datetime
from typing import Any
from urllib.parse import urljoin

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))

from common.fund_link_backfill import cli_main  # noqa: E402
from common_paths import configure_session  # noqa: E402


SHEET_NAME = "联博资产"
BASE_URL = "https://www.abfunds.com.hk"
FUND_API_BASE = "https://webapi.alliancebernstein.com/v2/funds/hk/en/investor"
PRODUCT_DOCS_API = FUND_API_BASE + "/product-docs"
RUN_COMMAND = (
    'python download_alliancebernstein_factsheet.py "{url}" "{english}" '
    '"{fund_number}" "{doc_url}"'
)
TIMEOUT = 90
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.abfunds.com.hk/hk/en/investor/funds.html",
}
SESSION = configure_session(requests.Session())


def _key(value: str) -> str:
    """规范化页表/官网英文核心名；参数 value 为基金名称。"""

    text = unicodedata.normalize("NFKC", str(value or "").casefold())
    text = text.replace("&", " and ")
    # 官网名称带 AB / AB (HK) 前缀，页表通常省略；Portfolio/Fund 也不是
    # 区分同名基金的有效字段，因此仅剥离这些固定词。
    text = re.sub(r"\bab\s*\(hk\)\b", " ", text)
    text = re.sub(r"\bab\b", " ", text)
    text = re.sub(r"\bportfolio\b", " ", text)
    text = re.sub(r"\bfund\b", " ", text)
    return re.sub(r"[^a-z0-9]+", "", text)


def _date_key(item: dict[str, Any]) -> tuple[int, int, int]:
    """按文档发布日期排序；参数 item 为 productDocs 文档。"""

    value = str(item.get("publishDate") or "")
    match = re.search(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})", value)
    if match:
        return tuple(int(x) for x in match.groups())
    return 0, 0, 0


def _fetch_product_docs() -> list[dict[str, Any]]:
    """读取联博官方当前基金及文档目录。"""

    response = SESSION.post(PRODUCT_DOCS_API, json={}, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise RuntimeError("联博 product-docs 接口格式异常：返回值不是数组")
    return [item for item in payload if isinstance(item, dict) and isinstance(item.get("fundInfo"), dict)]


def _catalog_loader() -> list[dict[str, Any]]:
    """按 fundNumber 去重为基金目录；不展开每个股份类别。"""

    records: dict[str, dict[str, Any]] = {}
    for item in _fetch_product_docs():
        info = item["fundInfo"]
        number = str(info.get("fundNumber") or "").strip()
        page = str(info.get("pageURL") or "").strip()
        if not number or not page:
            continue
        current = records.get(number)
        if current is None:
            records[number] = {"fundInfo": dict(info), "productDocs": list(item.get("productDocs") or [])}
            continue
        # 同一基金的每个 share class 都会重复返回产品文档；合并并去重 docId，
        # 让目录保留所有可选文档但只回填最新单只 Factsheet。
        seen = {str(doc.get("docId")) for doc in current["productDocs"] if isinstance(doc, dict)}
        for doc in item.get("productDocs") or []:
            if isinstance(doc, dict) and str(doc.get("docId")) not in seen:
                current["productDocs"].append(doc)
                seen.add(str(doc.get("docId")))
    if not records:
        raise RuntimeError("联博官方 product-docs 目录为空")
    return list(records.values())


def _build_index(catalog: list[dict[str, Any]]) -> dict[str, Any]:
    """按英文核心名建立精确索引；参数 catalog 为去重后的基金目录。"""

    index: dict[str, list[dict[str, Any]]] = {}
    for item in catalog:
        info = item["fundInfo"]
        record = {
            **item,
            "fund_name": str(info.get("fundName") or "").strip(),
            "fund_number": str(info.get("fundNumber") or "").strip(),
        }
        index.setdefault(_key(record["fund_name"]), []).append(record)
    return {"index": index, "catalog": catalog}


def _factsheet(record: dict[str, Any]) -> dict[str, Any] | None:
    """选择最新单只 Factsheet，不接受通用 Fact Sheet Book。"""

    docs = [
        doc
        for doc in record.get("productDocs") or []
        if isinstance(doc, dict)
        and doc.get("docType") == "productdocdetails-doctype-fact-sheet"
        and str(doc.get("docURL") or "").strip()
    ]
    return sorted(docs, key=_date_key, reverse=True)[0] if docs else None


def _resolve_single(row: dict[str, Any], index: dict[str, Any]) -> tuple[str, str, str | None]:
    """解析一行基金并返回 I/J；参数 row/index 为页表行和官方索引。"""

    key = _key(row.get("english") or row.get("fund") or "")
    records = index["index"].get(key) or []
    if not records:
        return "", "", "NO_CURRENT_FUND：联博官方当前目录未找到可验证基金页"
    # 正常情况下核心英文名唯一；若历史目录产生同名候选，伞名仅用于
    # 识别 AB(HK) Unit Trust，否则不凭相似名称猜测。
    umbrella = str(row.get("umbrella") or "")
    if "香港" in umbrella or "单位信托" in umbrella:
        records = [r for r in records if "AB (HK)" in str(r.get("fund_name") or "")] or records
    record = records[0]
    document = _factsheet(record)
    if not document:
        return "", "", "NO_FACTSHEET：基金页没有单只 Fact Sheet（月报），仅有通用/监管文档"
    info = record["fundInfo"]
    page_url = urljoin(BASE_URL, str(info.get("pageURL") or ""))
    doc_url = urljoin(BASE_URL, str(document["docURL"]))
    english = str(row.get("english") or row.get("fund") or "").strip()
    command = RUN_COMMAND.format(
        url=page_url,
        english=english,
        fund_number=record["fund_number"],
        doc_url=doc_url,
    )
    return page_url, command, ""


def main(argv: list[str] | None = None) -> int:
    """运行联博 CLI；参数 argv 支持单基金、--probe、--backfill。"""

    return cli_main(
        argv,
        description="按联博香港官方基金页和 Fact Sheet 目录补全月报入口",
        sheet_name=SHEET_NAME,
        catalog_loader=_catalog_loader,
        index_builder=_build_index,
        resolver=_resolve_single,
        single_print_run_command=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
