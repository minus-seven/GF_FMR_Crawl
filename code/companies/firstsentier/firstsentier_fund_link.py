#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
首域盈信（First Sentier Investors 香港）基金月报爬虫补全脚本。

目录来源：官网文献下载页（服务端渲染，表格里直接是 PDF 链接）
  https://www.firstsentierinvestors.com/hk/zh/retail/performance/literature.html
页表当前只有 1 支基金（首域盈信中国增长基金，下载脚本参数 FSSA-China-Growth-Fund），
故这里按官网实测结果给出静态目录；若后续新增基金，可在此补充条目。

匹配：中文核心名（去“首域盈信/基金”）→ 英文核心名兜底。
I = 文献下载页
J = python download_firstsentier_factsheet.py "<基金 slug>"

用法：
  python firstsentier_fund_link.py 首域盈信中国增长基金
  python firstsentier_fund_link.py --probe --excel /path/to/爬虫list.xlsx --sheet 首域盈信
  python firstsentier_fund_link.py --backfill --excel /path/to/爬虫list.xlsx --sheet 首域盈信
"""

from __future__ import annotations

import os
import re
import sys
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
sys.path.insert(0, HERE)

from common.fund_link_backfill import cli_main, normalize_name  # noqa: E402


SHEET_NAME = "首域盈信"
LITERATURE_URL = "https://www.firstsentierinvestors.com/hk/zh/retail/performance/literature.html"
PRODUCTS = (
    {
        "name_cn": "首域盈信中国增长基金",
        "name_en": "FSSA China Growth Fund",
        "slug": "FSSA-China-Growth-Fund",
    },
)


def _cn(value: str) -> str:
    """中文核心名：简繁统一、去“首域盈信/基金”与符号。"""
    text = normalize_name(value)
    for token in ("首域盈信", "基金"):
        text = text.replace(token, "")
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", text)


def _en(value: str) -> str:
    """英文核心名：去 FSSA/Fund 等词。"""
    text = str(value or "").strip().lower()
    for token in ("fssa", "fund"):
        text = text.replace(token, "")
    return re.sub(r"[^a-z0-9]", "", text)


def _fetch_catalog() -> list[dict[str, Any]]:
    """返回已知可下载的基金目录（静态，来自官网文献页实测）。"""
    return [
        {
            "name_cn": item["name_cn"],
            "name_en": item["name_en"],
            "page_url": LITERATURE_URL,
            "run_command": f'python download_firstsentier_factsheet.py "{item["slug"]}"',
        }
        for item in PRODUCTS
    ]


def _build_index(catalog: list[dict[str, Any]]) -> dict[str, Any]:
    """建立中英文核心名索引。"""
    index: dict[str, Any] = {"cn": {}, "en": {}, "all": catalog}
    for record in catalog:
        index["cn"][_cn(record["name_cn"])] = record
        index["en"][_en(record["name_en"])] = record
    return index


def _resolve_single(row: dict[str, Any], index: dict[str, Any]) -> tuple[str, str, str | None]:
    """解析一支基金，返回 (I月报链接, J运行方式, 跳过原因)；不认领时原因为 None。"""
    record = index["cn"].get(_cn(str(row.get("fund") or "")))
    if not record:
        record = index["en"].get(_en(str(row.get("english") or "")))
    if not record:
        return "", "", "官网文献页未配置该基金（可在本脚本 PRODUCTS 补充）"
    return record["page_url"], record["run_command"], ""


def main(argv: list[str] | None = None) -> int:
    """命令行入口：单基金查询 / --probe / --backfill。"""
    return cli_main(
        argv,
        description="按爬虫list.xlsx首域盈信表 B 列基金名查询月报链接（含批量补全）",
        sheet_name=SHEET_NAME,
        catalog_loader=_fetch_catalog,
        index_builder=_build_index,
        resolver=_resolve_single,
        single_print_run_command=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
