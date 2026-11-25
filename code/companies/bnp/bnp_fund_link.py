#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""法巴香港 Fundsearch / Doclib Factsheet（月报）适配器。

法巴香港官网的基金搜索和文档库由公开 API 提供。Fundsearch 返回基金级
默认份额信息，Doclib 的 ``DOC_FACTSHEET`` 返回单只基金的最新 Factsheet
直链。本适配器不将 KFS、FP、REPA/REPS 或合订报告当成月报。
"""

from __future__ import annotations

import html
import os
import re
import sys
from typing import Any
from urllib.parse import quote

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))

from common.fund_link_backfill import cli_main  # noqa: E402
from common_paths import configure_session  # noqa: E402


SHEET_NAME = "法巴"
BASE_SITE = "https://www.bnpparibas-am.com/en-hk"
API_BASE = "https://api.bnpparibas-am.com"
FUND_API = API_BASE + "/push/fundsearchv2/IP_HK-FSE/HKG"
DOC_API = API_BASE + "/push/doclib/IP_HK-FSE?language=ENG"
RUN_COMMAND = 'python download_bnp_factsheet.py "{url}" "{english}" "{compartment_id}" "{doc_url}"'
TIMEOUT = 120
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": BASE_SITE + "/fundexplorer/",
}
SESSION = configure_session(requests.Session())


def _strip_html(value: str) -> str:
    """去除 HTML 标记；参数 value 为官网返回的文本。"""

    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", str(value or ""))).split())


def _key(value: str) -> str:
    """规范化英文基金名；参数 value 为页表或官网基金名称。"""

    text = _strip_html(value).upper()
    text = re.sub(r"^BNP\s+PARIBAS\s+(?:FUNDS|A\s+FUND)\s+", "", text)
    return re.sub(r"[^A-Z0-9]+", "", text)


def _fundsheet_url(record: dict[str, Any]) -> str:
    """生成官方基金详情页；参数 record 为 Fundsearch 基金记录。"""

    asset_class = str(record.get("eng_asset_class") or "na").strip().lower()
    legal_name = str(record.get("default_share_name") or record.get("name") or "").strip()
    isin = str(record.get("default_share_isin_code") or "").strip()
    slug = quote(f"{legal_name}-{isin}", safe="")
    return f"{BASE_SITE}/fundsheet/{quote(asset_class, safe='')}/{slug}/"


def _fetch_catalog() -> list[dict[str, Any]]:
    """读取法巴基金及文档目录；本函数无参数，返回合并后的基金记录。"""

    fund_response = SESSION.get(FUND_API, headers=HEADERS, timeout=TIMEOUT)
    fund_response.raise_for_status()
    fund_payload = fund_response.json()
    funds = fund_payload.get("funds") if isinstance(fund_payload, dict) else None
    if not isinstance(funds, list) or not funds:
        raise RuntimeError("法巴 Fundsearch 接口为空或格式异常")

    doc_response = SESSION.get(DOC_API, headers=HEADERS, timeout=TIMEOUT)
    doc_response.raise_for_status()
    documents = doc_response.json()
    if not isinstance(documents, list):
        raise RuntimeError("法巴 Doclib 接口为空或格式异常")
    latest: dict[str, dict[str, Any]] = {}
    for document in documents:
        if not isinstance(document, dict) or document.get("type") != "DOC_FACTSHEET":
            continue
        url = str(document.get("direct_url") or "").strip()
        compartment_id = str(document.get("compartment_id") or "").strip()
        if not url or not compartment_id:
            continue
        current = latest.get(compartment_id)
        if current is None or str(document.get("date") or "") > str(current.get("date") or ""):
            latest[compartment_id] = document

    records: list[dict[str, Any]] = []
    for fund in funds:
        if not isinstance(fund, dict):
            continue
        item = dict(fund)
        item["factsheet_doc"] = latest.get(str(fund.get("compartment_id") or "").strip())
        records.append(item)
    return records


def _build_index(catalog: list[dict[str, Any]]) -> dict[str, Any]:
    """建立英文核心名索引；参数 catalog 为合并后的官网基金记录。"""

    index: dict[str, list[dict[str, Any]]] = {}
    for record in catalog:
        key = _key(str(record.get("name") or ""))
        if key:
            index.setdefault(key, []).append(record)
    return {"index": index, "catalog": catalog}


def _resolve_single(row: dict[str, Any], index: dict[str, Any]) -> tuple[str, str, str | None]:
    """解析一行法巴链接；参数 row 为页表行、index 为官网索引。"""

    key = _key(str(row.get("english") or row.get("fund") or ""))
    records = index["index"].get(key) or []
    if not records:
        return "", "", "NO_CURRENT_FUND：法巴香港当前 Fundsearch 未找到可验证基金页"
    record = records[0]
    document = record.get("factsheet_doc")
    if not isinstance(document, dict) or not str(document.get("direct_url") or "").strip():
        return "", "", "NO_FACTSHEET：基金页有目录记录但 Doclib 没有单只 DOC_FACTSHEET（月报）"
    page_url = _fundsheet_url(record)
    compartment_id = str(record.get("compartment_id") or "").strip()
    english = str(row.get("english") or row.get("fund") or "").strip()
    command = RUN_COMMAND.format(
        url=page_url,
        english=english,
        compartment_id=compartment_id,
        doc_url=str(document["direct_url"]).strip(),
    )
    return page_url, command, ""


def main(argv: list[str] | None = None) -> int:
    """运行法巴 CLI；参数 argv 支持单基金、--probe、--backfill。"""

    return cli_main(
        argv,
        description="按法巴香港 Fundsearch/Doclib 补全 Factsheet 月报入口",
        sheet_name=SHEET_NAME,
        catalog_loader=_fetch_catalog,
        index_builder=_build_index,
        resolver=_resolve_single,
        single_print_run_command=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
