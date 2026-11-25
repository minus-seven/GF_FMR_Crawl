#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""高盛香港基金月报入口适配器。

使用 Goldman Sachs Asset Management 香港官网公开的 /api/documents 搜索接口，
只选择英文、fundDocuments、monthlyFundUpdate，并优先选择 country_s 含 hk 的
官方 PDF；搜索不到时跳过，不拼接或猜测 URL。
"""

from __future__ import annotations

import datetime as dt
import os
import re
import sys
from typing import Any

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
from common.fund_link_backfill import cli_main, normalize_name  # noqa: E402

SHEET_NAME = "高盛"
DOCUMENTS_URL = "https://am.gs.com/api/documents"
RUN_COMMAND = 'python download_goldman_factsheet.py "{url}" "{english}"'
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://am.gs.com/en-hk/institutions/documents?selectedTab=fundsDocuments",
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/json",
}


def _key(value: str) -> str:
    """归一化产品名，消除商标、品牌和常见 CORE 写法差异。"""

    text = normalize_name(value).casefold().replace("®", "")
    text = re.sub(r"goldmansachs", "", text)
    text = text.replace("core", "core")
    return re.sub(r"[^a-z0-9]+", "", text)


def _fields(record: dict[str, Any]) -> dict[str, list[str]]:
    """把 LexFields 转成字段字典。"""

    return {str(item.get("name")): [str(v) for v in item.get("values", [])] for item in record.get("LexFields", []) if isinstance(item, dict)}


def _fetch_catalog() -> list[dict[str, Any]]:
    """为每个目标名向官方搜索接口查询最新英文月报。"""

    # 目录不需要预先猜测；目标名称在 resolver 侧按需查询并缓存。
    return [{"fund_name": "__dynamic__", "key": "__dynamic__"}]


def _build_index(catalog: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """创建带会话和缓存的动态索引容器。"""

    return {"__dynamic__": [{"session": requests.Session(), "cache": {}}]}


def _resolve_single(row: dict[str, Any], index: dict[str, list[dict[str, Any]]]) -> tuple[str, str, str]:
    """按精确基金名检索官方最新英文月报。"""

    english = str(row.get("english") or "").strip()
    state = index["__dynamic__"][0]
    key = _key(english)
    if key in state["cache"]:
        return state["cache"][key]
    # Lex 搜索接口把注册商标字符当作非法查询参数；保留商标只用于本地匹配。
    query_name = english.replace("®", "")
    response = state["session"].post(DOCUMENTS_URL, json={"searchTerm": query_name, "selectedTab": "fundsDocuments"}, headers=HEADERS, timeout=90)
    response.raise_for_status()
    payload = response.json()
    candidates: list[dict[str, Any]] = []
    for raw in payload if isinstance(payload, list) else []:
        fields = _fields(raw)
        title = (fields.get("documentTitle_s") or [""])[0]
        language = (fields.get("language_s") or [""])[0].casefold()
        category = (fields.get("documentCategory_s") or [""])[0]
        dtype = (fields.get("documentType_s") or [""])[0]
        url = (fields.get("publicURL") or [""])[0]
        if language != "en" or category != "fundDocuments" or dtype != "monthlyFundUpdate" or not url:
            continue
        title_key = _key(title)
        # 查询词必须覆盖标题主体；去掉 monthlyFundUpdate、份额后缀后再做严格包含。
        if not (key in title_key or title_key in key):
            continue
        countries = {x.casefold() for x in fields.get("country_s", [])}
        audience = {x.casefold() for x in fields.get("audience_s", [])}
        date_text = (fields.get("displayDateTime_t") or [""])[0]
        try:
            report_date = dt.datetime.fromisoformat(date_text.replace("Z", "+00:00"))
        except ValueError:
            report_date = dt.datetime.min
        candidates.append({"url": url, "date": report_date, "hk": "hk" in countries, "individual": "individual" in audience})
    if not candidates:
        result = ("", "", "高盛官方文档搜索未返回同名英文月报，保留缺口")
    else:
        selected = sorted(candidates, key=lambda x: (x["hk"], x["individual"], x["date"]), reverse=True)[0]
        result = (selected["url"], RUN_COMMAND.format(url=selected["url"], english=english), "")
    state["cache"][key] = result
    return result


def main(argv: list[str] | None = None) -> int:
    """运行 Goldman CLI；支持 --probe/--backfill。"""

    return cli_main(argv, description="按高盛香港官方文档搜索补全月报入口", sheet_name=SHEET_NAME, catalog_loader=_fetch_catalog, index_builder=_build_index, resolver=_resolve_single, single_print_run_command=True)


if __name__ == "__main__":
    raise SystemExit(main())
