#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""保诚瀚亚（Eastspring Investments）香港月报入口适配器。

Eastspring 下载页通过官方 AJAX 接口返回基金、份额及 Factsheet 直链；本脚本
按英文基金核心名精确匹配，优先选 A 类或目录首条可用份额，不把 KFS/年报冒充月报。
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
from typing import Any
from urllib.parse import urljoin

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))

from common.fund_link_backfill import cli_main, normalize_name  # noqa: E402


SHEET_NAME = "保诚瀚亚"
DOWNLOADS_URL = "https://www.eastspring.com/hk/funds/downloads"
RUN_COMMAND = 'python download_eastspring_factsheet.py "{pdf_url}" "{english}"'
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}


def _key(value: str) -> str:
    """归一化英文基金名；参数 value 为工作簿或官网基金名称。"""

    text = normalize_name(str(value or "")).casefold()
    text = re.sub(r"^eastspringinvestments[-–—]?", "", text)
    text = re.sub(r"[-–—](?:a|adm|ah|ahdm|admc\d+|newcies).*$", "", text)
    return re.sub(r"[^a-z0-9]+", "", text)


def _fetch_catalog() -> list[dict[str, Any]]:
    """调用 Eastspring 官方下载接口；本函数无参数。"""

    session = requests.Session()
    session.headers.update(HEADERS)
    page = session.get(DOWNLOADS_URL, timeout=60)
    page.raise_for_status()
    token_match = re.search(
        r'<input[^>]+name=["\']__RequestVerificationToken["\'][^>]*value=["\']([^"\']*)',
        page.text,
        flags=re.I,
    )
    page_match = re.search(
        r'<span[^>]+class=["\'][^"\']*fundpageurl[^"\']*["\'][^>]*>(.*?)</span>',
        page.text,
        flags=re.I | re.S,
    )
    token = token_match.group(1) if token_match else ""
    fund_page = re.sub(r"<[^>]+>", "", page_match.group(1)).strip() if page_match else "/hk/funddetails/"
    response = session.post(
        f"{DOWNLOADS_URL}/GetFunds_DownloadsData_HK",
        data={
            "__RequestVerificationToken": token,
            "tblName": "hkfunds_hkfund",
            "tbl_StaticName": "hkstaticurl_hkstaticurl",
            "ccode": "HK",
            "langCode": "en",
            "fundPage": fund_page,
        },
        headers={"X-Requested-With": "XMLHttpRequest"},
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, str):
        payload = json.loads(payload)
    catalog: list[dict[str, Any]] = []
    for item in payload or []:
        name_html = html.unescape(str(item.get("FundName") or ""))
        name_match = re.search(r">([^<>]+)<", name_html)
        name = name_match.group(1).strip() if name_match else re.sub(r"<[^>]+>", " ", name_html).strip()
        factsheet_html = html.unescape(str(item.get("Factsheetname") or ""))
        href_match = re.search(r"href=[\"']([^\"']+\.pdf[^\"']*)", factsheet_html, flags=re.I)
        if not name or not href_match:
            continue
        page_href = re.search(r"href=[\"']([^\"']+)[\"']", name_html, flags=re.I)
        catalog.append({
            "name": name,
            "key": _key(name),
            "page_url": urljoin(DOWNLOADS_URL, page_href.group(1)) if page_href else DOWNLOADS_URL,
            "pdf_url": urljoin(DOWNLOADS_URL, href_match.group(1)),
        })
    if not catalog:
        raise RuntimeError("Eastspring 官方下载接口未返回可用 Factsheet")
    return catalog


def _build_index(catalog: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """按英文核心名建立索引；参数 catalog 为官方下载目录。"""

    index: dict[str, list[dict[str, Any]]] = {}
    for item in catalog:
        index.setdefault(str(item.get("key") or ""), []).append(item)
    return index


def _resolve_single(row: dict[str, Any], index: dict[str, list[dict[str, Any]]]) -> tuple[str, str, str]:
    """解析单只保诚瀚亚月报入口；参数 row 为页表行、index 为官方目录索引。"""

    english = str(row.get("english") or "").strip()
    key = _key(english)
    records = index.get(key) or []
    if not records:
        # 官方名称可能只在尾部带份额类别；允许严格的前缀匹配。
        records = [item for item_key, values in index.items() if item_key.startswith(key) or key.startswith(item_key) for item in values]
    if not records:
        return "", "", "Eastspring 官方下载目录未返回同名 Factsheet"
    selected = sorted(records, key=lambda item: (0 if re.search(r"-\s*A(?:\s|$)", item["name"], re.I) else 1, item["name"]))[0]
    return selected["page_url"], RUN_COMMAND.format(pdf_url=selected["pdf_url"], english=english), ""


def main(argv: list[str] | None = None) -> int:
    """运行 Eastspring CLI；参数 argv 支持单基金、--probe、--backfill。"""

    return cli_main(
        argv,
        description="按 Eastspring 香港官方下载目录补全保诚瀚亚 Factsheet 月报入口",
        sheet_name=SHEET_NAME,
        catalog_loader=_fetch_catalog,
        index_builder=_build_index,
        resolver=_resolve_single,
        single_print_run_command=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
