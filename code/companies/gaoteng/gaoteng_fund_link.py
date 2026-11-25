#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
高腾（GaoTeng）香港基金月报爬虫补全脚本。

目录来源：官网公开接口
  https://api.gaotengasset.com/gt/website/productService?lang=<lang>&fund_id=10001
该接口一次返回“高腾微基金”伞下全部基金（2026-10 实测 5 支，均未清算）。
官方英文站（lang=en-us）无产品数据，故英文名用页表“英文”列做兜底匹配。

匹配：伞优先 → 中文核心名 → 英文核心名兜底。
I = https://www.gaotengasset.com/zh-HK/product-services/fund-details?fund_id=<fundId>#3
J = python download_gaoteng_factsheet.py <fundId>
"""

from __future__ import annotations

import os
import re
import sys
from typing import Any

import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common.fund_link_backfill import (  # noqa: E402
    cli_main,
    normalize_name,
)


SHEET_NAME = "高腾"
WEB_BASE_URL = "https://www.gaotengasset.com"
API_BASE_URL = "https://api.gaotengasset.com/gt/website"
DETAIL_URL_TEMPLATE = WEB_BASE_URL + "/zh-HK/product-services/fund-details?fund_id={fund_id}#3"
RUN_COMMAND_TEMPLATE = "python download_gaoteng_factsheet.py {fund_id}"
# 目录接口按“伞”返回，取任意一支基金的 fund_id 即可拿到伞内全量列表
CATALOG_FUND_ID = "10001"
CATALOG_LANGS = ("zh-hk", "zh-cn")
TIMEOUT = 30
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Origin": WEB_BASE_URL,
    "Referer": WEB_BASE_URL + "/",
}
# 官方英文站无产品数据，用页表“英文”列固化英文名，仅作中文匹配失败后的兜底
EN_ALIASES = {
    "10018": "GaoTeng WeValue USD Money Market Fund",
    "10008": "GaoTeng WeInvest Money Market Fund",
    "10001": "GaoTeng Asian Income Fund",
    "10039": "GaoTeng Select Investment Grade Bond Fund",
    "10029": "GaoTeng China RMB Bond Fund",
}


def _cn(value: str) -> str:
    """中文名归一化：简繁统一、去空格、统一括号连字符。"""
    text = normalize_name(value)
    for token in ("-", "－", "–", "—", "−", "─", "（", "）", "(", ")", " "):
        text = text.replace(token, "")
    return text


def _en(value: str) -> str:
    """英文名归一化：去 GaoTeng 前缀与系列词，仅保留字母数字。"""
    text = str(value or "").strip().lower()
    for prefix in ("gaoteng wevalue", "gaoteng weinvest", "gaoteng"):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    return re.sub(r"[^a-z0-9]", "", text)


def _split_series(fund_name: str) -> tuple[str, str]:
    """把接口返回的“伞-基金名”拆成 (伞, 基金名)；无前缀时伞回落“高腾微基金”。"""
    text = str(fund_name or "").replace("\u3000", " ").strip()
    for sep in ("-", "－", "–", "—"):
        if sep in text:
            series, _, name = text.partition(sep)
            return series.strip(), name.strip()
    return "高腾微基金", text


def _fetch_catalog(lang: str) -> list[dict[str, Any]]:
    """抓取指定语言的官网目录，返回基金记录列表（含伞、基金名、是否清算）。"""
    response = requests.get(
        f"{API_BASE_URL}/productService",
        params={"lang": lang, "fund_id": CATALOG_FUND_ID},
        headers=HEADERS,
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") != 1000:
        raise RuntimeError(f"GaoTeng API returned {payload.get('status')}: {payload.get('message')}")
    records = []
    for item in (payload.get("data") or {}).get("list") or []:
        fund_id = str(item.get("fundId") or "").strip()
        if not fund_id:
            continue
        series, name = _split_series(item.get("fundName"))
        records.append(
            {
                "fund_id": fund_id,
                "series": series,
                "name": name,
                "english": EN_ALIASES.get(fund_id, ""),
                "closed": item.get("closed"),
                "lang": lang,
            }
        )
    return records


def _catalog_loader() -> list[dict[str, Any]]:
    """合并繁体/简体两套目录（按 fundId 去重），保证简繁页表名都能命中。"""
    merged: dict[str, dict[str, Any]] = {}
    for lang in CATALOG_LANGS:
        for record in _fetch_catalog(lang):
            merged.setdefault(record["fund_id"], record)
    return list(merged.values())


def _build_index(catalog: list[dict[str, Any]]):
    """建立 (伞, 中文/英文核心名) → 基金记录 的索引。"""
    index: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for record in catalog:
        series = _cn(record["series"])
        index.setdefault((series, "cn", _cn(record["name"])), []).append(record)
        if record.get("english"):
            index.setdefault((series, "en", _en(record["english"])), []).append(record)
    return index


def _resolve_single(row: dict[str, Any], index) -> tuple[str, str, str | None]:
    """解析一支基金，返回 (I月报链接, J运行方式, 跳过原因)；不认领时原因为 None。"""
    umbrella = _cn(str(row.get("umbrella") or ""))
    fund = _cn(str(row.get("fund") or ""))
    if umbrella and fund.startswith(umbrella):
        fund = fund[len(umbrella):]
    fund = re.sub(r"^(高騰|高腾|GaoTeng)", "", fund)
    english = _en(str(row.get("english") or ""))
    for key in ((umbrella, "cn", fund), (umbrella, "en", english)):
        records = index.get(key)
        if not records:
            continue
        fund_id = records[0]["fund_id"]
        return (
            DETAIL_URL_TEMPLATE.format(fund_id=fund_id),
            RUN_COMMAND_TEMPLATE.format(fund_id=fund_id),
            "",
        )
    return "", "", None


def main(argv: list[str] | None = None) -> int:
    """命令行入口：单基金查询 / --probe / --backfill。"""
    return cli_main(
        argv,
        description="按爬虫list.xlsx高腾表 B 列基金名查询官网月报链接（含批量补全）",
        sheet_name=SHEET_NAME,
        catalog_loader=_catalog_loader,
        index_builder=_build_index,
        resolver=_resolve_single,
        single_print_run_command=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
