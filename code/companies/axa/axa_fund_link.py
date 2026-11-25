#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""安盛香港基金入口适配器。

AXA IM 香港站已并入 BNP Paribas AM。目录使用官方 Fund Explorer API；报告
使用官方 DocFinder 文档目录。优先选择 DOC_FACTSHEET，若 AXA 产品当前没有
事实表，则选择官方最新 DOC_REPS（截至 30 June 的半年报），并在命令中保留
报告类型，避免伪造一个不存在的月报链接。
"""

from __future__ import annotations

import os
import re
import sys
from datetime import date
from typing import Any

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
from common.fund_link_backfill import cli_main, normalize_name  # noqa: E402

SHEET_NAME = "安盛"
PROFILE = "IP_HK-FSE"
LANGUAGE = "ENG"
FUND_API = f"https://api.bnpparibas-am.com/push/fundsearchv2/{PROFILE}/{LANGUAGE}"
DOC_API = f"https://api.bnpparibas-am.com/push/doclib/{PROFILE}?language={LANGUAGE}"
RUN_COMMAND = 'python download_axa_factsheet.py "{url}" "{english}" "{kind}"'
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def _key(value: str) -> str:
    """统一 AXA World Funds/AXA WF、E.P.I.C 等历史名称写法。"""

    text = normalize_name(str(value or "")).casefold()
    text = text.replace("axaworldfunds", "axawf").replace("e.p.i.c", "epic")
    return re.sub(r"[^a-z0-9]+", "", text)


def _date_key(value: str) -> str:
    """用于选择 DocFinder 最新文档的日期排序键。"""

    return str(value or "0000-00-00")


def _fetch_catalog() -> list[dict[str, Any]]:
    """读取官方基金目录和 DocFinder 文档目录并合并。"""

    fund_response = requests.get(FUND_API, headers=HEADERS, timeout=90)
    fund_response.raise_for_status()
    fund_payload = fund_response.json()
    funds = fund_payload.get("funds") if isinstance(fund_payload, dict) else None
    if not isinstance(funds, list):
        raise RuntimeError("BNPP AM Fund Explorer 未返回 funds 数组")
    doc_response = requests.get(DOC_API, headers=HEADERS, timeout=120)
    doc_response.raise_for_status()
    documents = doc_response.json()
    if not isinstance(documents, list):
        raise RuntimeError("BNPP AM DocFinder 未返回文档数组")
    by_compartment: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for document in documents:
        if not isinstance(document, dict) or not document.get("direct_url"):
            continue
        key = (str(document.get("compart_id") or ""), _key(str(document.get("compart_name") or "")))
        by_compartment.setdefault(key, []).append(document)
    records: list[dict[str, Any]] = []
    for fund in funds:
        if not isinstance(fund, dict):
            continue
        name = str(fund.get("compart_legal_name") or fund.get("name") or "").strip()
        if not name:
            continue
        key = (str(fund.get("compart_id") or ""), _key(name))
        docs = by_compartment.get(key, [])
        # factsheet 是月度事实表；REPS 是官方截至 30 June 的半年报 fallback。
        factsheets = [item for item in docs if item.get("type") == "DOC_FACTSHEET"]
        semiannual = [item for item in docs if item.get("type") == "DOC_REPS"]
        selected = max(factsheets or semiannual, key=lambda item: (_date_key(item.get("date")), str(item.get("direct_url")))) if (factsheets or semiannual) else None
        if selected is None:
            continue
        records.append(
            {
                "fund_name": name,
                "key": _key(name),
                "url": str(selected["direct_url"]),
                "kind": "monthly" if selected.get("type") == "DOC_FACTSHEET" else "semiannual",
                "date": str(selected.get("date") or ""),
                "document_type": str(selected.get("type") or ""),
            }
        )
    if not records:
        raise RuntimeError("BNPP AM DocFinder 未返回可用安盛基金报告")
    return records


def _build_index(catalog: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """按当前产品名建立报告索引。"""

    index: dict[str, list[dict[str, Any]]] = {}
    for item in catalog:
        index.setdefault(str(item.get("key") or ""), []).append(item)
    return index


def _resolve_single(row: dict[str, Any], index: dict[str, list[dict[str, Any]]]) -> tuple[str, str, str]:
    """返回安盛/BNPP AM 官方事实表或半年报入口。"""

    english = str(row.get("english") or "").strip()
    candidates = index.get(_key(english), [])
    if not candidates:
        return "", "", "BNPP AM Fund Explorer/DocFinder 当前未返回同名报告"
    selected = sorted(candidates, key=lambda item: (_date_key(item.get("date")), str(item.get("url") or "")), reverse=True)[0]
    url = str(selected["url"])
    return url, RUN_COMMAND.format(url=url, english=english, kind=selected.get("kind") or "monthly"), ""


def main(argv: list[str] | None = None) -> int:
    """运行安盛适配器 CLI。"""

    return cli_main(
        argv,
        description="按 BNP Paribas AM 香港 Fund Explorer/DocFinder 补全安盛报告入口",
        sheet_name=SHEET_NAME,
        catalog_loader=_fetch_catalog,
        index_builder=_build_index,
        resolver=_resolve_single,
        single_print_run_command=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
