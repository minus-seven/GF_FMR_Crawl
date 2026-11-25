#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
汇添富（99Fund 香港）基金月报爬虫补全脚本。

目录来源：官网“香港认可基金”总览页（服务端渲染）
  https://www.99fund.com.hk/main/hkweb_new/hk/products/HKfunds/index.shtml
页面左栏每个基金都写成 <p fundId="XXX">基金名</p>，据此建立 基金名 → fundId 目录。
月报判定：再抓一次该基金产品页，页面里必须有“基金月報/基金月报”文档表。

I = https://www.99fund.com.hk/main/hkweb_new/hk/products/<fundId>/fundgl.shtml
J = python download_99fund_factsheet.py <fundId>

用法：
  python 99fund_fund_link.py 汇添富中港策略基金
  python 99fund_fund_link.py --probe --excel /path/to/爬虫list.xlsx --sheet 汇添富
  python 99fund_fund_link.py --backfill --excel /path/to/爬虫list.xlsx --sheet 汇添富
"""

from __future__ import annotations

import html
import os
import re
import sys
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
sys.path.insert(0, HERE)

from common.fund_link_backfill import (  # noqa: E402
    cli_main,
    normalize_name,
)
from download_99fund_factsheet import fetch_page  # noqa: E402


SHEET_NAME = "汇添富"
BASE_URL = "https://www.99fund.com.hk"
INDEX_URL = BASE_URL + "/main/hkweb_new/hk/products/HKfunds/index.shtml"
PRODUCT_URL = BASE_URL + "/main/hkweb_new/hk/products/{fund_id}/fundgl.shtml"
MONTHLY_LABELS = ("基金月報", "基金月报")
FUND_ID_RE = re.compile(r'fundId="([^"]+)"[^>]*>\s*([^<]+?)\s*<')


def _norm(value: str) -> str:
    """中文名归一化：简繁统一、去空格符号与尾部括注，并剥离“汇添富”品牌词。"""
    text = normalize_name(value)
    text = text.replace("匯", "汇").replace("滙", "汇")
    for token in ("匯添富", "汇添富", "CUAM", "cuam"):
        text = text.replace(token, "")
    while True:
        trimmed = re.sub(r"\([^()]*\)$", "", text)
        if trimmed == text:
            break
        text = trimmed
    for token in ("-", "－", "–", "—", "−", "─", "（", "）", "(", ")", "+", "＋", " ", "\u3000"):
        text = text.replace(token, "")
    return text


def _parse_fund_ids(page_html: str) -> dict[str, str]:
    """解析页面里的 fundId → 基金名（同名去重，忽略空名）。"""
    funds: dict[str, str] = {}
    for fund_id, name in FUND_ID_RE.findall(page_html):
        fund_name = html.unescape(name).strip()
        if fund_id and fund_name and fund_id not in funds:
            funds[fund_id] = fund_name
    return funds


def _fetch_catalog() -> list[dict[str, Any]]:
    """返回官网香港认可基金目录（含每支基金是否有月报）。"""
    catalog: list[dict[str, Any]] = []
    for fund_id, fund_name in _parse_fund_ids(fetch_page(INDEX_URL)).items():
        page_html = fetch_page(PRODUCT_URL.format(fund_id=fund_id))
        has_monthly = any(label in page_html for label in MONTHLY_LABELS)
        catalog.append(
            {"fund_id": fund_id, "fund_name": fund_name, "has_monthly": has_monthly}
        )
    if not catalog:
        raise RuntimeError("99Fund 总览页未解析到 fundId，请检查页面结构是否变更")
    return catalog


def _build_index(catalog: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """按归一化中文名建立索引。"""
    index: dict[str, dict[str, Any]] = {}
    for record in catalog:
        index.setdefault(_norm(record["fund_name"]), record)
    return index


def _resolve_single(
    row: dict[str, Any], index: dict[str, dict[str, Any]]
) -> tuple[str, str, str | None]:
    """解析一支基金，返回 (I月报链接, J运行方式, 跳过原因)；不认领时原因为 None。"""
    fund = _norm(str(row.get("fund") or "").strip())
    record = index.get(fund)
    if not record:
        umbrella = str(row.get("umbrella") or "").strip()
        if umbrella:
            record = index.get(_norm(f"{umbrella}{row.get('fund') or ''}"))
    if not record:
        return "", "", "官网香港认可基金未收录该基金"
    if not record.get("has_monthly"):
        return "", "", "官网该基金暂无月报文件"
    return (
        PRODUCT_URL.format(fund_id=record["fund_id"]),
        f"python download_99fund_factsheet.py {record['fund_id']}",
        "",
    )


def main(argv: list[str] | None = None) -> int:
    """命令行入口：单基金查询 / --probe / --backfill。"""
    return cli_main(
        argv,
        description="按爬虫list.xlsx汇添富表 B 列基金名查询官网月报链接（含批量补全）",
        sheet_name=SHEET_NAME,
        catalog_loader=_fetch_catalog,
        index_builder=_build_index,
        resolver=_resolve_single,
        single_print_run_command=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
