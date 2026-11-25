#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""纽约梅隆香港 Fund Centre 月报入口适配器。

目录和 PDF 均来自 BNY Mellon 香港官方 Fund Centre/FE Precision Plus；不猜测
历史产品链接，若官方自动完成没有同名产品则明确跳过。
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

SHEET_NAME = "纽约梅隆"
CATALOG_URL = "https://digital.feprecisionplus.com/bnymhkenindividual/en-gb/bnym/DownloadTool/FundOptions"
PDF_BASE = "https://digital.feprecisionplus.com/documents/bnymhkenindividual/en-gb/{citi}/FS"
RUN_COMMAND = 'python download_bny_factsheet.py "{url}" "{english}"'
PARAMS = {
    "GrsProjectId": "95400164",
    "ProjectName": "bnymhkenindividual",
    "ToolId": "16",
    "LanguageId": "1",
    "LanguageCode": "en-gb",
    "forSaleIn": "",
    "FSIexclCT": "",
    "RangeCode": "92600418",
    "Category": "Indi",
}
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json,text/plain,*/*"}


def _key(value: str) -> str:
    """归一化产品名并去掉 BNY 品牌及份额后缀。"""

    text = normalize_name(value).casefold()
    text = re.sub(r"bnymellon|bny", "", text)
    text = re.split(r"-(?:usd|hkd|eur|gbp|sgd|jpy|aud|cad)", text, maxsplit=1)[0]
    text = re.sub(r"[^a-z0-9]+", "", text)
    return text.replace("fund", "")


def _fetch_catalog() -> list[dict[str, Any]]:
    """读取 BNY 官方 FundOptions HTML JSON，返回 CITI 代码和产品名。"""

    response = requests.get(CATALOG_URL, params=PARAMS, headers=HEADERS, timeout=90)
    response.raise_for_status()
    payload = response.json()
    content = payload.get("Html", "") if isinstance(payload, dict) else ""
    if not content:
        raise RuntimeError("BNY 香港 Fund Centre 未返回基金目录")
    records: list[dict[str, Any]] = []
    for match in re.finditer(r'<option\s+value=["\']([^"\']+)["\'][^>]*>(.*?)</option>', content, re.I | re.S):
        citi = html.unescape(match.group(1)).strip()
        name = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", match.group(2)))).strip()
        if not citi or not name or name.casefold() in {"select fund", "select a fund"}:
            continue
        records.append({"fund_name": name, "key": _key(name), "citi": citi})
    if not records:
        raise RuntimeError("BNY 香港 Fund Centre 目录没有可用基金")
    return records


def _build_index(catalog: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """按去品牌产品名建立索引。"""

    index: dict[str, list[dict[str, Any]]] = {}
    for item in catalog:
        index.setdefault(str(item.get("key") or ""), []).append(item)
    return index


def _resolve_single(row: dict[str, Any], index: dict[str, list[dict[str, Any]]]) -> tuple[str, str, str]:
    """仅对官方目录同名产品回填直链 PDF。"""

    english = str(row.get("english") or "").strip()
    key = _key(english)
    records = index.get(key) or []
    if not records:
        return "", "", "BNY 香港 Fund Centre 当前目录未返回同名产品，保留缺口，不猜测链接"
    selected = records[0]
    url = PDF_BASE.format(citi=selected["citi"])
    return url, RUN_COMMAND.format(url=url, english=english), ""


def main(argv: list[str] | None = None) -> int:
    """运行 BNY CLI；支持 --probe/--backfill。"""

    return cli_main(argv, description="按纽约梅隆香港官方 Fund Centre 补全 Factsheet 月报入口", sheet_name=SHEET_NAME, catalog_loader=_fetch_catalog, index_builder=_build_index, resolver=_resolve_single, single_print_run_command=True)


if __name__ == "__main__":
    raise SystemExit(main())
