#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""信安香港官方 mutual-funds API 月报入口适配器。

信安当前 API 返回多个 share class；本适配器按产品名去掉 share-class 后缀，
优先选择 HKD、再选择 USD 的官方 Fact Sheet。API 未返回的历史/已下架产品
明确保留缺口，不构造链接。
"""

from __future__ import annotations

import html
import os
import re
import sys
from typing import Any

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
from common.fund_link_backfill import cli_main, normalize_name  # noqa: E402

SHEET_NAME = "信安"
CATALOG_URL = "https://www.principal.com.hk/api/mutual-funds"
BASE_URL = "https://www.principal.com.hk"
RUN_COMMAND = 'python download_principal_factsheet.py "{url}" "{english}"'
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def _key(value: str) -> str:
    """归一化产品名并剥离品牌、份额、币种和脚注。"""

    text = html.unescape(str(value or "")).strip().casefold()
    text = re.sub(r"\*.*$", "", text)
    # API 名称的第一段为产品名，连字符后通常是 share class；产品名本身没有连字符。
    text = re.split(r"\s*[-–—]\s*", text, maxsplit=1)[0]
    text = re.sub(r"\bprincipal\b", "", text)
    text = re.sub(r"\b(?:funds?|class|units?|retail|acc(?:umulation)?|inc(?:ome)?|plus|monthly|annual|rmb|hkd|usd|eur|gbp|aud|jpy)\b", "", text)
    return re.sub(r"[^a-z0-9]+", "", text)


def _fetch_catalog() -> list[dict[str, Any]]:
    """读取信安官方 API，并展开 Fact-Sheet 文件记录。"""

    response = requests.get(CATALOG_URL, headers=HEADERS, timeout=90)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise RuntimeError("信安 mutual-funds API 返回格式不是数组")
    records: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        files = item.get("Fact-Sheet") or []
        for file_item in files:
            if not isinstance(file_item, dict):
                continue
            path = str(file_item.get("FactSheet") or "").strip()
            if not path.lower().endswith(".pdf"):
                continue
            records.append(
                {
                    "fund_name": html.unescape(str(item.get("Name") or "")).strip(),
                    "key": _key(str(item.get("Name") or "")),
                    "currency": str(item.get("Currency") or "").upper(),
                    "url": path if path.startswith("http") else BASE_URL + path,
                }
            )
    if not records:
        raise RuntimeError("信安 API 未返回可用 Fact Sheet")
    return records


def _build_index(catalog: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """按产品键建立候选索引。"""

    index: dict[str, list[dict[str, Any]]] = {}
    for item in catalog:
        if item.get("key"):
            index.setdefault(str(item["key"]), []).append(item)
    return index


def _resolve_single(row: dict[str, Any], index: dict[str, list[dict[str, Any]]]) -> tuple[str, str, str]:
    """选择官方产品的 HKD/USD Fact Sheet 并返回运行命令。"""

    english = str(row.get("english") or "").strip()
    candidates = list(index.get(_key(english), []))
    if not candidates:
        return "", "", "信安官方 API 当前未返回同名 Fact Sheet（可能为历史/已下架产品）"
    rank = {"HKD": 0, "USD": 1}
    selected = sorted(candidates, key=lambda item: (rank.get(str(item.get("currency") or ""), 2), str(item.get("url") or "")))[0]
    url = str(selected["url"])
    return url, RUN_COMMAND.format(url=url, english=english), ""


def main(argv: list[str] | None = None) -> int:
    """运行信安适配器 CLI。"""

    return cli_main(
        argv,
        description="按信安香港官方 mutual-funds API 补全 Factsheet 月报入口",
        sheet_name=SHEET_NAME,
        catalog_loader=_fetch_catalog,
        index_builder=_build_index,
        resolver=_resolve_single,
        single_print_run_command=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
