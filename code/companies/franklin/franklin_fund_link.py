#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""富兰克林邓普顿香港基金月报入口补全适配器。

官网的 Fund Documents 页面是动态目录；其公开 literature API 每次返回当前
Factsheet 文档，因此工作簿 I 列只保存稳定的文档入口，J 列下载脚本在运行时
重新读取 API，避免把带 UUID 的 Widen PDF 地址固化。
"""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime
from typing import Any
from urllib.parse import urljoin

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))

from common.fund_link_backfill import cli_main, normalize_name  # noqa: E402
from common_paths import configure_session  # noqa: E402


SHEET_NAME = "富兰克林邓普顿"
BASE_URL = "https://www.franklintempleton.com.hk"
DOCUMENTS_URL = BASE_URL + "/en-hk/our-funds/literature/fund-documents"
LITERATURE_API = BASE_URL + "/api/literature/v1/documents?channel=en-hk&cache=true&audience=investor"
RUN_COMMAND = 'python download_franklin_factsheet.py "{url}" "{english}"'
TIMEOUT = 90
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}
SESSION = configure_session(requests.Session())


def _key(value: str) -> str:
    """将英文基金名归一化，参数 value 为工作簿或官网原始名称。"""
    text = str(value or "").strip().lower()
    text = text.replace("&", " and ").replace("–", "-").replace("—", "-")
    text = re.sub(r"^fact\s*sheet\s*[-:：]?\s*", "", text)
    # 目录标题有时带系列前缀，页表英文列保留该前缀；比较时同时剥离。
    for prefix in (
        "franklin templeton investment funds -",
        "franklin templeton global funds plc -",
        "franklin templeton asia fund series -",
    ):
        text = text.replace(prefix, "")
    text = re.sub(r"\b(ftgf|franklin templeton investment funds)\b\s*[-:]?", "", text)
    text = re.sub(r"[^a-z0-9]+", "", text)
    return text


def _date_key(item: dict[str, Any]) -> tuple[int, int, int]:
    """按官方文档参考日期排序，参数 item 为 literature API 文档。"""
    value = str(item.get("frkReferenceDate") or item.get("fileUploadDate") or "")
    match = re.search(r"(20\d{2})[-/.](\d{1,2})(?:[-/.](\d{1,2}))?", value)
    if match:
        return int(match.group(1)), int(match.group(2)), int(match.group(3) or 1)
    return 0, 0, 0


def _fetch_catalog() -> list[dict[str, Any]]:
    """读取官方文档目录中的 Factsheet 记录。"""
    response = SESSION.get(LITERATURE_API, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    documents = payload.get("document") or []
    if not isinstance(documents, list):
        raise RuntimeError("富兰克林文档目录格式异常：document 不是数组")
    result: list[dict[str, Any]] = []
    for item in documents:
        if not isinstance(item, dict):
            continue
        doc_type = str(item.get("dctermsType") or "").strip().lower()
        title = str(item.get("dctermsTitle") or "").strip()
        if "factsheet" not in doc_type or not title:
            continue
        fund_name = re.sub(r"^fact\s*sheet\s*[-:：]?\s*", "", title, flags=re.I).strip()
        literature_href = str(item.get("literatureHref") or "").strip()
        result.append(
            {
                **item,
                "fund_name": fund_name,
                "literature_url": urljoin(BASE_URL, literature_href) if literature_href else DOCUMENTS_URL,
            }
        )
    if not result:
        raise RuntimeError("富兰克林官方文档目录未返回 Factsheet")
    return result


def _catalog_loader() -> list[dict[str, Any]]:
    return _fetch_catalog()


def _build_index(catalog: list[dict[str, Any]]) -> dict[str, Any]:
    """为同一基金保留全部日期，匹配时选择最新一份 Factsheet。"""
    index: dict[str, list[dict[str, Any]]] = {}
    for item in catalog:
        index.setdefault(_key(item.get("fund_name", "")), []).append(item)
    for records in index.values():
        records.sort(key=_date_key, reverse=True)
    return {"index": index, "catalog": catalog}


def _match(row: dict[str, Any], index: dict[str, Any]) -> list[dict[str, Any]]:
    english = _key(row.get("english") or "")
    if not english:
        return []
    exact = index["index"].get(english) or []
    if exact:
        return exact
    # 允许页表含 FTGF/系列前缀，但至少要求 12 个字符并且只能有一个强候选。
    if len(english) < 12:
        return []
    candidates = [
        item
        for key, records in index["index"].items()
        if english in key or key in english
        for item in records
    ]
    return candidates if len({str(x.get("fund_name")) for x in candidates}) == 1 else []


def _resolve_single(row: dict[str, Any], index: dict[str, Any]) -> tuple[str, str, str | None]:
    records = _match(row, index)
    if not records:
        return "", "", "NO_CURRENT_FACTSHEET：官方文档目录未找到可验证的当前 Factsheet（月报）"
    record = records[0]
    page_url = str(record.get("literature_url") or DOCUMENTS_URL)
    english = str(row.get("english") or row.get("fund") or "").strip()
    return page_url, RUN_COMMAND.format(url=page_url, english=english), ""


def main(argv: list[str] | None = None) -> int:
    return cli_main(
        argv,
        description="按富兰克林邓普顿香港官方文档目录补全爬虫list.xlsx月报入口",
        sheet_name=SHEET_NAME,
        catalog_loader=_catalog_loader,
        index_builder=_build_index,
        resolver=_resolve_single,
        single_print_run_command=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
