#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""木星资管香港官方 Fund Centre / DMS 月报入口适配器。

木星页面本身由 DIFRAAS 渲染；其官方 API 返回香港可售 share class，
``getDocumentCategories`` 的 Marketing documents/Factsheet 节点包含当前
及历史月度事实表。适配器只选择英文、最新可验证 PDF，未出现在官方目录的
历史产品保留缺口，不构造链接。
"""

from __future__ import annotations

import os
import re
import sys
from typing import Any

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
from common.fund_link_backfill import cli_main, normalize_name  # noqa: E402

SHEET_NAME = "木星资管"
API = "https://dfs.jupiteram.instinctcentral.io/api/v1"
LIST_API = f"{API}/getShareClassList"
CATEGORY_API = f"{API}/getDocumentCategories"
RUN_COMMAND = 'python download_jupiter_factsheet.py "{url}" "{english}"'
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def _key(value: str) -> str:
    """统一 Jupiter/Merian、基金后缀、份额与币种写法。"""

    # 先在保留空格的原文上剥离份额/币种 token；normalize_name 会移除空格，
    # 若顺序反过来，``\b`` 无法识别 ``- L EUR ACC`` 这类后缀。
    text = str(value or "").casefold()
    text = re.sub(r"\b(jupiter|fund|class|acc|inc|hsc|hk|retail|l|d|v|m|a|eur|usd|hkd|sgd|gbp|aud|irl)\b", "", text)
    text = normalize_name(text).casefold()
    return re.sub(r"[^a-z0-9]+", "", text)


def _fetch_categories(external_id: str) -> list[dict[str, Any]]:
    params: list[tuple[str, str]] = [
        ("location", "hk"),
        ("channel", "individual"),
        ("language", "en"),
        ("shareclassExternalId", external_id),
    ]
    response = requests.get(CATEGORY_API, params=params, headers=HEADERS, timeout=90)
    response.raise_for_status()
    payload = response.json()
    groups = ((payload.get("data") or {}).get("documentGroups") if isinstance(payload, dict) else None) or []
    documents: list[dict[str, Any]] = []
    for group in groups:
        for item in group.get("data") or []:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "")
            title_key = title.casefold()
            if not ("factsheet" in title_key or "monthly investment report" in title_key or "quarterly investment report" in title_key):
                continue
            for field in (item, *(item.get("historical") or [])):
                if isinstance(field, dict) and str(field.get("url") or "").lower().endswith(".pdf"):
                    documents.append({**field, "title": title, "group": str(group.get("title") or "")})
    return documents


def _date_key(value: str) -> str:
    """支持官方 DD/MM/YYYY 日期，供最新报告排序。"""

    match = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", str(value or "").strip())
    return f"{match.group(3)}-{match.group(2)}-{match.group(1)}" if match else str(value or "0000-00-00")


def _fetch_catalog() -> list[dict[str, Any]]:
    """读取香港官方 share class 目录并展开 Factsheet 报告。"""

    response = requests.get(
        LIST_API,
        params={"pageIndex": "0", "pageSize": "100", "location": "hk", "channel": "individual", "language": "en"},
        headers=HEADERS,
        timeout=90,
    )
    response.raise_for_status()
    payload = response.json()
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise RuntimeError("Jupiter Fund Centre 未返回 items 数组")
    records: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        values = item.get("values") if isinstance(item.get("values"), dict) else {}
        display_name = str(item.get("name") or values.get("name") or "").strip()
        docs = _fetch_categories(str(item["id"]))
        # 同一 share class 可能同时有月报、事实表及季报；优先月报，其次事实表，
        # 最后季报，保证只写入官方可验证 PDF，不因产品分类变化而伪造链接。
        for doc in docs:
            records.append(
                {
                    "fund_name": display_name,
                    "key": _key(display_name),
                    "external_id": str(item["id"]),
                    "url": str(doc["url"]),
                    "date": str(doc.get("date") or ""),
                    "language": str(doc.get("language") or ""),
                }
            )
    if not records:
        raise RuntimeError("Jupiter Fund Centre 未返回可用 Factsheet")
    return records


def _build_index(catalog: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    for item in catalog:
        # 英文报告优先；同一产品不同历史版本按日期在 resolver 中选择。
        index.setdefault(_key(str(item.get("fund_name") or "")), []).append(item)
    return index


def _resolve_single(row: dict[str, Any], index: dict[str, list[dict[str, Any]]]) -> tuple[str, str, str]:
    english = str(row.get("english") or "").strip()
    candidates = index.get(_key(english), [])
    if not candidates:
        return "", "", "木星香港官方 Fund Centre 当前未返回同名 Factsheet（可能为历史/已下架产品）"
    english_docs = [
        item for item in candidates
        if str(item.get("language") or "").casefold() == "english"
        or " english " in f" {str(item.get('title') or '').casefold()} "
    ]
    pool = english_docs or candidates
    def kind_rank(item: dict[str, Any]) -> int:
        title = str(item.get("title") or "").casefold()
        return 0 if "monthly investment report" in title else 1 if "factsheet" in title else 2
    # 先按日期降序，再取最高优先级种类；min 保持同类中最新记录。
    pool = sorted(pool, key=lambda item: (_date_key(str(item.get("date") or "")), str(item.get("url") or "")), reverse=True)
    selected = min(pool, key=kind_rank)
    url = str(selected["url"])
    return url, RUN_COMMAND.format(url=url, english=english), ""


def main(argv: list[str] | None = None) -> int:
    return cli_main(
        argv,
        description="按木星香港官方 Fund Centre/DMS Factsheet 补全月报入口",
        sheet_name=SHEET_NAME,
        catalog_loader=_fetch_catalog,
        index_builder=_build_index,
        resolver=_resolve_single,
        single_print_run_command=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
