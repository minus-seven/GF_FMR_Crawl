#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
睿远基金（香港）Foresight —— 基金月报爬虫补全脚本。

目录来源：官网公开 API（纯 requests 可用）
  https://webhk.foresightfund.com.hk/ry-api/prod/prodinfo?id=<id>
返回 PROD_NAME(简)/PROD_NAME_TC(繁)/PROD_NAME_EN 等字段；
月报判定：/ry-api/prod/report?id=<id>&type=yb 返回非空列表。

匹配：中文核心名（简繁统一、去“睿远/Foresight”前缀）优先 → 英文名兜底。
I = https://www.foresightfund.com.hk/gmjj
J = python download_foresight_factsheet.py <id>

用法：
  python foresight_fund_link.py 睿远中国股票基金
  python foresight_fund_link.py --probe --excel /path/to/爬虫list.xlsx --sheet 睿远
  python foresight_fund_link.py --backfill --excel /path/to/爬虫list.xlsx --sheet 睿远
"""

from __future__ import annotations

import os
import re
import sys
from typing import Any

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
sys.path.insert(0, HERE)

from common.fund_link_backfill import cli_main, normalize_name  # noqa: E402


SHEET_NAME = "睿远"
BASE_URL = "https://www.foresightfund.com.hk"
API_BASE = "https://webhk.foresightfund.com.hk/ry-api/prod"
MONTHLY_TYPE = "yb"
ID_RANGE = range(1, 21)  # 官网产品 id 为小整数，扫描上限足够覆盖现有产品
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json",
}
TIMEOUT = 30


def _cn(value: str) -> str:
    """中文核心名：简繁统一、去空格符号与“睿远/Foresight”品牌词。"""
    text = normalize_name(value)
    for token in ("睿遠", "睿远", "Foresight", "基金"):
        text = text.replace(token, "")
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", text)


def _en(value: str) -> str:
    """英文核心名：去 Foresight 与 Fund 等词，仅保留字母数字。"""
    text = str(value or "").strip().lower()
    for token in ("foresight", "fund"):
        text = text.replace(token, "")
    return re.sub(r"[^a-z0-9]", "", text)


def _fetch_catalog() -> list[dict[str, Any]]:
    """扫描官网产品 id，返回基金目录（含是否有月报）。"""
    catalog: list[dict[str, Any]] = []
    session = requests.Session()
    for product_id in ID_RANGE:
        try:
            response = session.get(
                f"{API_BASE}/prodinfo?id={product_id}", headers=HEADERS, timeout=TIMEOUT
            )
            if response.status_code != 200:
                continue
            data = (response.json() or {}).get("data") or {}
        except Exception:  # noqa: BLE001 - 单个 id 失败不影响整体
            continue
        name_cn = str(data.get("PROD_NAME") or data.get("PROD_NAME_TC") or "").strip()
        if not name_cn:
            continue
        has_monthly = False
        try:
            report = session.get(
                f"{API_BASE}/report?id={product_id}&type={MONTHLY_TYPE}",
                headers=HEADERS,
                timeout=TIMEOUT,
            )
            has_monthly = bool((report.json() or {}).get("list"))
        except Exception:  # noqa: BLE001
            has_monthly = False
        catalog.append(
            {
                "product_id": str(product_id),
                "name_cn": name_cn,
                "name_cn_tc": str(data.get("PROD_NAME_TC") or "").strip(),
                "name_en": str(data.get("PROD_NAME_EN") or "").strip(),
                "has_monthly": has_monthly,
            }
        )
    if not catalog:
        raise RuntimeError("睿远 prodinfo 未返回任何产品，请检查接口是否变更")
    return catalog


def _build_index(catalog: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """建立 (语言, 核心名) → 基金记录 的索引。"""
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for record in catalog:
        if record["name_cn"]:
            index.setdefault(("cn", _cn(record["name_cn"])), record)
        if record["name_cn_tc"]:
            index.setdefault(("cn", _cn(record["name_cn_tc"])), record)
        if record["name_en"]:
            index.setdefault(("en", _en(record["name_en"])), record)
    return index


def _resolve_single(
    row: dict[str, Any], index: dict[tuple[str, str], dict[str, Any]]
) -> tuple[str, str, str | None]:
    """解析一支基金，返回 (I月报链接, J运行方式, 跳过原因)；不认领时原因为 None。"""
    record = index.get(("cn", _cn(str(row.get("fund") or ""))))
    if not record:
        record = index.get(("en", _en(str(row.get("english") or ""))))
    if not record:
        return "", "", "官网产品库未收录该基金"
    if not record.get("has_monthly"):
        return "", "", "官网该基金暂无月报（type=yb 为空）"
    return (
        f"{BASE_URL}/gmjj",
        f"python download_foresight_factsheet.py {record['product_id']}",
        "",
    )


def main(argv: list[str] | None = None) -> int:
    """命令行入口：单基金查询 / --probe / --backfill。"""
    return cli_main(
        argv,
        description="按爬虫list.xlsx睿远表 B 列基金名查询官网月报链接（含批量补全）",
        sheet_name=SHEET_NAME,
        catalog_loader=_fetch_catalog,
        index_builder=_build_index,
        resolver=_resolve_single,
        single_print_run_command=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
