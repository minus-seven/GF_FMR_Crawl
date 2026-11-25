#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
富国（Fullgoal）香港基金月报爬虫补全脚本。

目录来源：官网产品总览 https://www.fullgoal.com.hk/hk/fuguoproducts/index.html
（4 个页签实测只暴露 5 个产品页，故这里按官网页面固化映射；其余基金输出 SKIP）。

匹配：伞优先 → 中文核心名 → 英文核心名；无“英文”列时自动回落中文。
I = https://www.fullgoal.com.hk + <产品页路径>
J = python download_fullgoal_factsheet.py "<I>"
"""

from __future__ import annotations

import os
import re
import sys
from typing import Any

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common.fund_link_backfill import (  # noqa: E402
    cli_main,
    normalize_name,
)


SHEET_NAME = "富国"
BASE_URL = "https://www.fullgoal.com.hk"
# 官网产品总览页实测暴露的全部产品页（2026-10 核对）
PRODUCTS = (
    {"series": "Fullgoal国际基金(SICAV)", "zh": "中国中小盘成长基金",
     "en": "Fullgoal China Small-Mid Cap Growth Fund", "slug": "/fundDetail/smallMid/index.html"},
    {"series": "Fullgoal国际基金(SICAV)", "zh": "中国A股基金",
     "en": "Fullgoal China A Share Fund", "slug": "/fundDetail/chinaAShareFund/index.html"},
    {"series": "Fullgoal International Fund Series OFC", "zh": "精选投资级债券基金",
     "en": "Fullgoal Select Investment Grade Bond Fund", "slug": "/fundDetail/selectInvestmentGradeBondFund/index.html"},
    {"series": "Fullgoal全球基金系列", "zh": "投资级债券基金",
     "en": "Fullgoal Investment Grade Bond Fund", "slug": "/fundDetail/investmentGradeBondFund/index.html"},
    {"series": "Fullgoal全球基金系列", "zh": "亚太红利基金",
     "en": "Fullgoal Asia Pacific Equity Dividend Fund", "slug": "/fundDetail/AsiaPacificEquityDividend/index.html"},
)


def _cn(value: str) -> str:
    """中文名归一化：简繁/空格/分隔符/括号统一。"""
    text = normalize_name(value)
    for token in ("-", "－", "–", "—", "−", "─", "（", "）", "(", ")", " "):
        text = text.replace(token, "")
    return text


def _en(value: str) -> str:
    """英文名归一化：去系列前缀、统一空格符号。"""
    text = str(value or "").strip().lower()
    for prefix in ("fullgoal international fund series ofc", "fullgoal国际基金(sicav)",
                   "fullgoal global fund series", "fullgoal全球基金系列", "fullgoal"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    text = text.replace("fullgoal", "").replace("fund series", "")
    return re.sub(r"[^a-z0-9]", "", text)


def _fetch_catalog() -> list[dict[str, Any]]:
    """返回官网产品页目录（页面固定映射，不需要联网）。"""
    return [dict(item) for item in PRODUCTS]


def _build_index(catalog: list[dict[str, Any]]):
    """建立 (伞, 中文/英文核心名) → 产品 的索引。"""
    index: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for record in catalog:
        series = _cn(record["series"])
        index.setdefault((series, "cn", _cn(record["zh"])), []).append(record)
        index.setdefault((series, "en", _en(record["en"])), []).append(record)
    return index


def _resolve_single(row: dict[str, Any], index) -> tuple[str, str, str | None]:
    """解析一支基金，返回 (I, J, 跳过原因)；不认领时原因为 None。"""
    umbrella = _cn(str(row.get("umbrella") or ""))
    fund = _cn(str(row.get("fund") or ""))
    if umbrella and fund.startswith(umbrella):
        fund = fund[len(umbrella):]
    fund = re.sub(r"^(Fullgoal|富国)", "", fund)
    for key in ((umbrella, "cn", fund), (umbrella, "en", _en(str(row.get("english") or "")))):
        rows = index.get(key)
        if rows:
            link = BASE_URL + rows[0]["slug"]
            return link, f'python download_fullgoal_factsheet.py "{link}"', ""
    return "", "", "官网未收录或已下架"


def main(argv: list[str] | None = None) -> int:
    """命令行入口：单基金查询 / --probe / --backfill。"""
    return cli_main(
        argv,
        description="按爬虫list.xlsx富国表 B 列基金名查询官网产品页链接（含批量补全）",
        sheet_name=SHEET_NAME,
        catalog_loader=_fetch_catalog,
        index_builder=_build_index,
        resolver=_resolve_single,
        single_print_run_command=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
