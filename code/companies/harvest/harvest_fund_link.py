#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
嘉实（Harvest Global Investments 香港）基金月报爬虫补全脚本。

目录来源：官网基金总览页内嵌的转义 JSON
  https://www.harvestglobal.com.hk/hgi/index.php/funds/list-view
  （需先访问 /hgi/index.php?landing 拿 ReVisit 放行 cookie）
结构：{..., "funds":{"ACTIVE":[{fund_code,fund_name}...],"MRF":[...],"PASSIVE":[...]}}

月报判定：复用下载脚本的文档解析（Monthly Factsheet / 基金月報 / Monthly Return Report），
          官网把 MRF 基金只挂在“Quarterly Factsheet”上，因此这类基金输出 SKIP。

匹配：页表“英文”列 ↔ 官网英文名（去 Harvest 前缀、去符号后比较）。
I = https://www.harvestglobal.com.hk/hgi/index.php/funds/<group>/<fund_code>
J = python download_harvest_factsheet.py "<I>"

用法：
  python harvest_fund_link.py "Harvest ESG China Equity Fund"
  python harvest_fund_link.py --probe --excel /path/to/爬虫list.xlsx --sheet 嘉实
  python harvest_fund_link.py --backfill --excel /path/to/爬虫list.xlsx --sheet 嘉实
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
sys.path.insert(0, HERE)

from common.fund_link_backfill import (  # noqa: E402
    cli_main,
)
from download_harvest_factsheet import (  # noqa: E402
    BASE_URL,
    fetch_page,
    parse_monthly_documents,
)


SHEET_NAME = "嘉实"
LIST_URL = BASE_URL + "/hgi/index.php/funds/list-view"
PAGE_URL = BASE_URL + "/hgi/index.php/funds/{group}/{fund_code}"
# 官网 fund_type → URL 路径段。当前官网没有单独的 virtualasset-etf 页面，
# BTCETF/ETHETF 的公开资料仍由 active 路由提供，因此显式纳入而不是静默漏掉。
GROUP_PATHS = {
    "ACTIVE": "active",
    "MRF": "mrf",
    "PASSIVE": "passive",
    "VIRTUALASSET-ETF": "active",
}


def _en(value: str) -> str:
    """英文名归一化：去 Harvest 品牌词与所有非字母数字。"""
    text = str(value or "").strip().lower().replace("harvest", "")
    return re.sub(r"[^a-z0-9]", "", text)


def _extract_funds(page_html: str) -> dict[str, list[dict[str, Any]]]:
    """从总览页内嵌 JSON 中取出 {"ACTIVE":[...]} 结构。"""
    text = html.unescape(page_html)
    marker = '"funds":{'
    start = text.find(marker)
    if start < 0:
        raise RuntimeError("嘉实总览页未找到内嵌 funds JSON，请检查页面结构是否变更")
    start = text.find("{", start)
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : index + 1])
    raise RuntimeError("嘉实总览页 funds JSON 解析失败（括号不匹配）")


def _fetch_catalog() -> list[dict[str, Any]]:
    """返回官网基金目录（仅保留路径可用的分组，含是否有月报）。"""
    groups = _extract_funds(fetch_page(LIST_URL))
    catalog: list[dict[str, Any]] = []
    for group, funds in groups.items():
        path = GROUP_PATHS.get(str(group).upper())
        if not path:
            continue
        for fund in funds:
            fund_code = str(fund.get("fund_code") or "").strip()
            fund_name = str(fund.get("fund_name") or "").strip()
            if not fund_code or not fund_name:
                continue
            page_url = PAGE_URL.format(group=path, fund_code=fund_code)
            try:
                has_monthly = bool(parse_monthly_documents(fetch_page(page_url)))
            except Exception:  # noqa: BLE001 - 单支基金页面异常不应中断整体补全
                has_monthly = False
            catalog.append(
                {
                    "fund_code": fund_code,
                    "fund_name": fund_name,
                    "page_url": page_url,
                    "has_monthly": has_monthly,
                }
            )
    if not catalog:
        raise RuntimeError("嘉实基金目录为空，请检查总览页结构是否变更")
    return catalog


def _build_index(catalog: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """按归一化英文名建立索引。"""
    index: dict[str, dict[str, Any]] = {}
    for record in catalog:
        index.setdefault(_en(record["fund_name"]), record)
    return index


def _resolve_single(
    row: dict[str, Any], index: dict[str, dict[str, Any]]
) -> tuple[str, str, str | None]:
    """解析一支基金，返回 (I月报链接, J运行方式, 跳过原因)；不认领时原因为 None。"""
    record = index.get(_en(str(row.get("english") or "")))
    if not record:
        return "", "", "官网基金总览未收录该基金"
    if not record.get("has_monthly"):
        return "", "", "官网该基金无月报（仅季度/年度报告）"
    link = record["page_url"]
    return link, f'python download_harvest_factsheet.py "{link}"', ""


def main(argv: list[str] | None = None) -> int:
    """命令行入口：单基金查询 / --probe / --backfill。"""
    return cli_main(
        argv,
        description="按爬虫list.xlsx嘉实表 B 列基金名（英文列）查询官网页面链接（含批量补全）",
        sheet_name=SHEET_NAME,
        catalog_loader=_fetch_catalog,
        index_builder=_build_index,
        resolver=_resolve_single,
        single_print_run_command=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
