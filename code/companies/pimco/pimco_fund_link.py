#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""品浩香港官方 Product Finder/API 月报入口适配器。"""

from __future__ import annotations

import os
import re
import sys
from typing import Any

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
from common.fund_link_backfill import cli_main, normalize_name  # noqa: E402

SHEET_NAME = "品浩"
BASE = "https://fundexp-ui.pimco.com/fund-explorer-api"
FUND_API = f"{BASE}/api/dashboard/fundDetails"
DOCUMENT_API = f"{BASE}/api/dashboard/{{cusip}}/documents?isPvit=false"
RUN_COMMAND = 'python download_pimco_factsheet.py "{url}" "{english}"'
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
    "CountryCode": "HK",
    "UserRole": "FI",
    "LangCode": "EN",
    "Referer": "https://www.pimco.com/hk/en/product-finder",
}


def _key(value: str) -> str:
    """统一 PIMCO 前缀、连字符及标点，供历史名称匹配。"""

    text = normalize_name(str(value or "")).casefold()
    text = re.sub(r"^pimco", "", text)
    return re.sub(r"[^a-z0-9]+", "", text)


def _fetch_documents(cusip: str) -> list[dict[str, Any]]:
    """读取一个 CUSIP 的官方文档列表。"""

    response = requests.get(DOCUMENT_API.format(cusip=cusip), headers=HEADERS, timeout=90)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        return []
    docs = payload.get("literatureDocuments") or []
    return [item for item in docs if isinstance(item, dict)]


def _fetch_catalog() -> list[dict[str, Any]]:
    """从官方基金详情和文档 API 选择每个产品的香港 Fact Sheet。"""

    response = requests.get(FUND_API, headers=HEADERS, timeout=120)
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError("PIMCO Product Finder 未返回 data 数组")
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if not isinstance(row, dict) or not row.get("Name") or not row.get("Cusip"):
            continue
        grouped.setdefault(_key(str(row["Name"])), []).append(row)
    records: list[dict[str, Any]] = []
    for key, candidates in grouped.items():
        # 先查港元、M Retail、E Class，直到拿到带 HK 的 Fact Sheet。
        ordered = sorted(
            candidates,
            key=lambda item: (
                0 if str(item.get("NAV Currency") or "").upper() == "HKD" else 1,
                0 if "M Retail" in str(item.get("Share Class") or "") else 1,
                0 if "E Class" in str(item.get("Share Class") or "") else 1,
                str(item.get("Cusip") or ""),
            ),
        )
        selected: dict[str, Any] | None = None
        for candidate in ordered[:8]:
            docs = _fetch_documents(str(candidate["Cusip"]))
            factsheets = [item for item in docs if str(item.get("name") or "").casefold() == "fact sheets" and str(item.get("url") or "").startswith("http")]
            hk_factsheets = [item for item in factsheets if " hk " in f" {str(item.get('title') or '').casefold()} "]
            pool = hk_factsheets or factsheets
            if pool:
                chosen_doc = sorted(pool, key=lambda item: (str(item.get("date") or "0000-00-00"), str(item.get("url") or "")), reverse=True)[0]
                selected = {
                    "fund_name": str(candidate.get("Name") or "").strip(),
                    "key": key,
                    "url": str(chosen_doc["url"]),
                    "date": str(chosen_doc.get("date") or ""),
                    "cusip": str(candidate.get("Cusip") or ""),
                    "title": str(chosen_doc.get("title") or ""),
                }
                break
        if selected:
            records.append(selected)
    if not records:
        raise RuntimeError("PIMCO Product Finder 未返回可用 Fact Sheets")
    return records


def _build_index(catalog: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """按产品键建立官方 Fact Sheet 索引。"""

    index: dict[str, list[dict[str, Any]]] = {}
    for item in catalog:
        index.setdefault(str(item.get("key") or ""), []).append(item)
    return index


def _resolve_single(row: dict[str, Any], index: dict[str, list[dict[str, Any]]]) -> tuple[str, str, str]:
    """返回 PIMCO 香港官方 Fact Sheet 直链。"""

    english = str(row.get("english") or "").strip()
    candidates = index.get(_key(english), [])
    if not candidates:
        return "", "", "PIMCO 香港 Product Finder 当前未返回同名香港 Fact Sheet"
    selected = sorted(candidates, key=lambda item: (str(item.get("date") or "0000-00-00"), str(item.get("url") or "")), reverse=True)[0]
    url = str(selected["url"])
    return url, RUN_COMMAND.format(url=url, english=english), ""


def main(argv: list[str] | None = None) -> int:
    """运行品浩适配器 CLI。"""

    return cli_main(
        argv,
        description="按 PIMCO 香港 Product Finder 官方 API 补全 Factsheet 月报入口",
        sheet_name=SHEET_NAME,
        catalog_loader=_fetch_catalog,
        index_builder=_build_index,
        resolver=_resolve_single,
        single_print_run_command=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
