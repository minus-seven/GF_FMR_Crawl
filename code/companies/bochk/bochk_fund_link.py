#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
中银香港资产管理（BOCHK AM）基金月报爬虫补全脚本。

目录来源：官网基金列表页（服务端渲染）
  https://www.bochkam.com/cn/   → 18 个 fund-detail-*.html 链接（横幅图，无文字）
  逐个打开详情页，基金名在 `<h1 class="extendedWidth">中银香港全天候<br>短期债券基金</h1>`。
月报判定：详情页存在 `FFS_*.pdf`（Fund Fact Sheet，月报）链接。

匹配：中文核心名（去“中银香港/基金/系列”）→ 英文兜底 → 包含兜底。
I = https://www.bochkam.com/cn/fund-detail-*.html
J = python download_bochk_factsheet.py "<I>"

用法：
  python bochk_fund_link.py 中银香港全天候短期债券基金
  python bochk_fund_link.py --probe --excel /path/to/爬虫list.xlsx --sheet 中银香港
  python bochk_fund_link.py --backfill --excel /path/to/爬虫list.xlsx --sheet 中银香港
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
sys.path.insert(0, HERE)

from common.fund_link_backfill import cli_main, normalize_name  # noqa: E402


SHEET_NAME = "中银香港"
BASE_URL = "https://www.bochkam.com/cn/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 30
DETAIL_RE = re.compile(r'href="(fund-detail-[^"]+\.html)"', re.I)
H1_RE = re.compile(r'<h1[^>]*>(.*?)</h1>', re.S | re.I)
FFS_RE = re.compile(r'FFS_[^"\']+\.pdf', re.I)


def _cn(value: str) -> str:
    """中文核心名：简繁统一、去“中银香港/基金/系列”与符号。"""
    text = normalize_name(value)
    for token in ("中銀香港", "中银香港", "基金", "系列"):
        text = text.replace(token, "")
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", text)


def _en(value: str) -> str:
    """英文核心名：去 BOCHK/Fund 等词。"""
    text = str(value or "").strip().lower()
    for token in ("bochk", "boc", "fund", "funds", "series"):
        text = text.replace(token, "")
    return re.sub(r"[^a-z0-9]", "", text)


def _fetch_catalog() -> list[dict[str, Any]]:
    """抓列表页的详情链接，再逐个解析基金名与是否有 FFS 月报。"""
    index_html = requests.get(BASE_URL, headers=HEADERS, timeout=TIMEOUT).text
    detail_links = sorted(set(DETAIL_RE.findall(index_html)))
    catalog: list[dict[str, Any]] = []
    for link in detail_links:
        url = BASE_URL + link
        try:
            response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            response.encoding = response.apparent_encoding or "utf-8"
            page = response.text
        except Exception:  # noqa: BLE001 - 单个页面失败跳过
            continue
        match = H1_RE.search(page)
        if not match:
            continue
        name = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", match.group(1))).split())
        if not name:
            continue
        catalog.append(
            {"fund_name": name, "page_url": url, "has_monthly": bool(FFS_RE.search(page))}
        )
    if not catalog:
        raise RuntimeError("中银香港官网未解析到基金详情页")
    return catalog


def _build_index(catalog: list[dict[str, Any]]) -> dict[str, Any]:
    """建立中文核心名索引并保留全量目录。"""
    index: dict[str, Any] = {"cn": {}, "all": catalog}
    for record in catalog:
        index["cn"].setdefault(_cn(record["fund_name"]), record)
    return index


def _match_record(row: dict[str, Any], index: dict[str, Any]) -> dict[str, Any] | None:
    """匹配基金：伞名+基金名 → 中文核心名 → 英文名 → 包含兜底。"""
    fund = str(row.get("fund") or "").strip()
    umbrella = str(row.get("umbrella") or "").strip()
    keys = [_cn(fund)]
    if umbrella:
        keys.append(_cn(f"{umbrella}{fund}"))
    for key in keys:
        if key and key in index["cn"]:
            return index["cn"][key]
    english = _en(str(row.get("english") or ""))
    if english:
        for record in index["all"]:
            if english and english in _en(record["fund_name"]):
                return record
    for key in keys:
        if len(key) < 4:
            continue
        for candidate_key, record in index["cn"].items():
            if candidate_key and (key in candidate_key or candidate_key in key):
                return record
    return None


def _resolve_single(row: dict[str, Any], index: dict[str, Any]) -> tuple[str, str, str | None]:
    """解析一支基金，返回 (I月报链接, J运行方式, 跳过原因)；不认领时原因为 None。"""
    record = _match_record(row, index)
    if not record:
        return "", "", "官网基金列表未收录该基金（可能为内地/已终止产品）"
    if not record.get("has_monthly"):
        return "", "", "官网该基金详情页无 FFS 月报文件"
    link = record["page_url"]
    return link, f'python download_bochk_factsheet.py "{link}"', ""


def main(argv: list[str] | None = None) -> int:
    """命令行入口：单基金查询 / --probe / --backfill。"""
    return cli_main(
        argv,
        description="按爬虫list.xlsx中银香港表 B 列基金名查询官网月报链接（含批量补全）",
        sheet_name=SHEET_NAME,
        catalog_loader=_fetch_catalog,
        index_builder=_build_index,
        resolver=_resolve_single,
        single_print_run_command=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
