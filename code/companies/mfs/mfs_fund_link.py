#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""美富信 MFS Meridian Funds 香港 Factsheet 入口适配器。"""

from __future__ import annotations

import os
import re
import sys
from typing import Any

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
from common.fund_link_backfill import cli_main, normalize_name  # noqa: E402

SHEET_NAME = "美富信"
FILTER_URL = "https://services.mfs.com/MFSServices/products/v1/reports/meridian/reportfilter"
DETAIL_URL = "https://services.mfs.com/MFSServices/products/v1/product/{isin}/productdetails4MeridianMicro"
RUN_COMMAND = 'python download_mfs_factsheet.py "{url}" "{english}"'
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Referer": "https://www.mfs.com/",
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Content-Type": "application/json",
}


def _key(value: str) -> str:
    """归一化 MFS 产品名，去掉品牌、伞基金和商标。"""

    text = normalize_name(value).casefold().replace("®", "")
    text = re.sub(r"mfsmeridianfunds", "", text)
    text = re.sub(r"[^a-z0-9]+", "", text)
    return text


def _fetch_catalog() -> list[dict[str, Any]]:
    """读取香港区域产品清单，并为每只产品解析英文 Factsheet 直链。"""

    session = requests.Session()
    response = session.get(FILTER_URL, params={"groupIdentifier": "PlpMeridianReports", "productLineCode": "WEB_MERIDIAN_FUNDS"}, headers=HEADERS, timeout=90)
    response.raise_for_status()
    payload = response.json()
    region = (payload.get("portRegionMap") or {}).get("hk", {}) if isinstance(payload, dict) else {}
    if not isinstance(region, dict) or not region:
        raise RuntimeError("MFS Meridian 香港产品 API 未返回基金目录")
    records: list[dict[str, Any]] = []
    for isin, raw_name in region.items():
        name = str(raw_name or "").strip()
        isin = str(isin or "").strip()
        if not name or not isin:
            continue
        detail = session.get(DETAIL_URL.format(isin=isin), params={"langCode": "en", "shareCode": "A1USD", "roleCode": "PlpMeridianReports", "locationCode": "hk", "productLineCode": "WEB_MERIDIAN_FUNDS", "reportLanguage": "English (Hong Kong)"}, headers=HEADERS, timeout=90)
        detail.raise_for_status()
        data = detail.json()
        reports = ((data.get("additionalDocuments") or {}).get("reportsList") or []) if isinstance(data, dict) else []
        candidates = [r for r in reports if isinstance(r, dict) and str(r.get("reportAlias") or "").casefold() == "fact sheet" and str(r.get("url") or "").strip()]
        candidates.sort(key=lambda r: (str(r.get("reportLanguage") or "") != "English (Hong Kong)", str(r.get("url") or "")))
        if not candidates:
            continue
        url = str(candidates[0]["url"])
        if url.startswith("/"):
            url = "https://www.mfs.com" + url
        records.append({"fund_name": name, "key": _key(name), "url": url, "isin": isin})
    if not records:
        raise RuntimeError("MFS Meridian 香港产品目录没有可用英文 Factsheet")
    return records


def _build_index(catalog: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """按归一化 MFS 产品名建立索引。"""

    index: dict[str, list[dict[str, Any]]] = {}
    for item in catalog:
        index.setdefault(str(item.get("key") or ""), []).append(item)
    return index


def _resolve_single(row: dict[str, Any], index: dict[str, list[dict[str, Any]]]) -> tuple[str, str, str]:
    """按精确产品名回填官方英文 Factsheet。"""

    english = str(row.get("english") or "").strip()
    records = index.get(_key(english)) or []
    if not records:
        return "", "", "MFS Meridian 香港产品 API 未返回同名英文 Factsheet，保留缺口"
    url = str(records[0]["url"])
    return url, RUN_COMMAND.format(url=url, english=english), ""


def main(argv: list[str] | None = None) -> int:
    """运行 MFS CLI；支持 --probe/--backfill。"""

    return cli_main(argv, description="按 MFS Meridian 香港官方 Factsheet 补全月报入口", sheet_name=SHEET_NAME, catalog_loader=_fetch_catalog, index_builder=_build_index, resolver=_resolve_single, single_print_run_command=True)


if __name__ == "__main__":
    raise SystemExit(main())
