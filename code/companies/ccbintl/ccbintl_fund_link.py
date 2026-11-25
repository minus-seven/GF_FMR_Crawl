#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
建银国际（CCB International）基金月报爬虫补全脚本。

目录来源：官网繁体站的左侧产品导航（服务端渲染）
  https://www.ccbintl.com.hk/Big5/management3.html
导航里每个产品页写成 <li class="on|"><div><a href="/Big5/managementN.html">基金名</a></div></li>，
一次请求即可拿到全部产品页的 基金名 ↔ URL。
月报判定：该产品页里必须存在“基金月刊”文档入口（下载脚本默认标签）。

站点证书链不完整，requests 统一 verify=False（与下载脚本一致）。
匹配：中文核心名（去“建银国际/建銀國際”前缀与分隔符，简繁归一）。
I = https://www.ccbintl.com.hk/Big5/managementN.html
J = python download_ccbintl_factsheet.py "<I>" "基金月刊"

用法：
  python ccbintl_fund_link.py 建银国际美元货币市场基金
  python ccbintl_fund_link.py --probe --excel /path/to/爬虫list.xlsx --sheet 建银国际
  python ccbintl_fund_link.py --backfill --excel /path/to/爬虫list.xlsx --sheet 建银国际
"""

from __future__ import annotations

import html
import os
import re
import sys
from typing import Any

import requests
import urllib3

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
sys.path.insert(0, HERE)

from common.fund_link_backfill import (  # noqa: E402
    cli_main,
    normalize_name,
)


urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SHEET_NAME = "建银国际"
BASE_URL = "https://www.ccbintl.com.hk"
INDEX_URL = BASE_URL + "/Big5/management3.html"
MONTHLY_LABEL = "基金月刊"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": BASE_URL + "/",
}
TIMEOUT = 30
NAV_RE = re.compile(
    r'<li[^>]*>\s*<div>\s*<a\s+href="(/Big5/management\d+\.html)"\s*>\s*([^<]{2,60}?)\s*</a>',
    re.I,
)


def _fetch(page_url: str) -> str:
    """抓取页面（忽略站点证书链问题），返回解码后的 HTML。"""
    response = requests.get(page_url, headers=HEADERS, timeout=TIMEOUT, verify=False)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return response.text


def _norm(value: str) -> str:
    """中文名归一化：简繁统一、去空格符号，并剥离“建银国际”品牌前缀。"""
    text = normalize_name(value)
    for token in ("建銀國際", "建银国际", "CCB International", "CCBI"):
        text = text.replace(token, "")
    for token in ("-", "－", "–", "—", "−", "─", "（", "）", "(", ")", "＋", "+", " ", "\u3000"):
        text = text.replace(token, "")
    return text


def _fetch_catalog() -> list[dict[str, Any]]:
    """返回官网产品页目录（基金名 + URL + 是否有“基金月刊”）。"""
    index_html = _fetch(INDEX_URL)
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path, name in NAV_RE.findall(index_html):
        fund_name = html.unescape(name).strip()
        if not fund_name or path in seen:
            continue
        seen.add(path)
        page_url = BASE_URL + path
        page_html = _fetch(page_url)
        records.append(
            {
                "fund_name": fund_name,
                "page_url": page_url,
                "has_monthly": MONTHLY_LABEL in page_html,
            }
        )
    if not records:
        raise RuntimeError("建银国际产品导航未解析到基金页，请检查页面结构是否变更")
    return records


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
    record = index.get(_norm(str(row.get("fund") or "")))
    if not record:
        return "", "", "官网产品导航未收录该基金"
    if not record.get("has_monthly"):
        return "", "", f"官网该基金无“{MONTHLY_LABEL}”文件"
    link = record["page_url"]
    return link, f'python download_ccbintl_factsheet.py "{link}" "{MONTHLY_LABEL}"', ""


def main(argv: list[str] | None = None) -> int:
    """命令行入口：单基金查询 / --probe / --backfill。"""
    return cli_main(
        argv,
        description="按爬虫list.xlsx建银国际表 B 列基金名查询官网月报链接（含批量补全）",
        sheet_name=SHEET_NAME,
        catalog_loader=_fetch_catalog,
        index_builder=_build_index,
        resolver=_resolve_single,
        single_print_run_command=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
