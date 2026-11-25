#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
惠理（Value Partners）基金月报爬虫补全脚本。

目录来源：官网简体“基金总览”页（服务端渲染，静态 HTML 里有全部产品页链接）
  https://www.valuepartners-group.com/sc/products-services-sc/funds-sc/
站点证书链偶发不完整，这里统一 verify=False（与下载脚本用浏览器绕过的方式等效）。

匹配：中文核心名（去“惠理基金系列 / 智者之选基金 / ICAV”等前缀）优先 →
      英文核心名（页表英文列 ↔ 产品页 slug）兜底。
I = <产品页 URL>?country=hk&type=hkri&tab=docs
J = python download_valuepartners_factsheet.py "<I>"

用法：
  python valuepartners_fund_link.py 惠理价值基金
  python valuepartners_fund_link.py --probe --excel /path/to/爬虫list.xlsx --sheet 惠理
  python valuepartners_fund_link.py --backfill --excel /path/to/爬虫list.xlsx --sheet 惠理
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


# 站点证书链不完整，requests 直连时忽略校验（与下载脚本行为一致）
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SHEET_NAME = "惠理"
BASE_URL = "https://www.valuepartners-group.com"
FUNDS_URL = BASE_URL + "/sc/products-services-sc/funds-sc/"
QUERY = "country=hk&type=hkri&tab=docs"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 30
LINK_RE = re.compile(
    r'<a[^>]+href="(https://www\.valuepartners-group\.com/sc/investment-solutions/'
    r'institutional-funds/[^"#]+)"[^>]*>(.*?)</a>',
    re.S,
)
# 产品页标题里的伞/系列前缀（匹配时剥离）
SERIES_PREFIXES = (
    "惠理基金系列开放式基金型公司",
    "惠理基金（爱尔兰）ICAV",
    "惠理基金系列II",
    "惠理基金系列II ",
    "惠理基金系列",
    "智者之选基金",
)


def _norm(value: str) -> str:
    """中文名归一化：简繁统一、去空格符号与括号，并剥离系列前缀与尾部标记。"""
    text = normalize_name(value)
    text = text.replace("#", "").replace("*", "").replace("＊", "")
    for prefix in SERIES_PREFIXES:
        text = text.replace(prefix, "")
    text = re.sub(r"^[\s\-–—－]+", "", text)
    for token in ("-", "－", "–", "—", "−", "─", "（", "）", "(", ")", "＋", "+", " ", "\u3000"):
        text = text.replace(token, "")
    return text


def _slug_key(value: str) -> str:
    """英文名/slug 归一化：去“-sc/-en”尾缀与非字母数字。"""
    text = str(value or "").strip().lower()
    text = re.sub(r"-(sc|en|tc)$", "", text)
    return re.sub(r"[^a-z0-9]", "", text)


def _fetch_catalog() -> list[dict[str, Any]]:
    """抓取官网基金总览页，返回 [{fund_name, url}]。"""
    response = requests.get(FUNDS_URL, headers=HEADERS, timeout=TIMEOUT, verify=False)
    response.raise_for_status()
    catalog: list[dict[str, Any]] = []
    seen: set[str] = set()
    for href, inner in LINK_RE.findall(response.text):
        url = href if href.endswith("/") else href + "/"
        if url in seen:
            continue
        name = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", inner)).split())
        if not name:
            continue
        seen.add(url)
        catalog.append({"fund_name": name, "url": url, "slug": url.rstrip("/").rsplit("/", 1)[-1]})
    if not catalog:
        raise RuntimeError("惠理基金总览页未解析到产品页链接，请检查页面结构是否变更")
    return catalog


def _build_index(catalog: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """建立 (语言, 归一化名) → 产品页记录 的索引。"""
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for record in catalog:
        index.setdefault(("cn", _norm(record["fund_name"])), record)
        index.setdefault(("en", _slug_key(record["slug"])), record)
    return index


def _resolve_single(
    row: dict[str, Any], index: dict[tuple[str, str], dict[str, Any]]
) -> tuple[str, str, str | None]:
    """解析一支基金，返回 (I月报链接, J运行方式, 跳过原因)；不认领时原因为 None。"""
    umbrella = str(row.get("umbrella") or "").strip()
    fund = _norm(str(row.get("fund") or "").strip())
    candidate = _norm(f"{umbrella}{row.get('fund') or ''}") if umbrella else fund
    record = index.get(("cn", fund)) or index.get(("cn", candidate))
    if not record:
        record = index.get(("en", _slug_key(str(row.get("english") or ""))))
    if not record:
        return "", "", "官网基金总览未收录该基金"
    link = f"{record['url']}?{QUERY}"
    return link, f'python download_valuepartners_factsheet.py "{link}"', ""


def main(argv: list[str] | None = None) -> int:
    """命令行入口：单基金查询 / --probe / --backfill。"""
    return cli_main(
        argv,
        description="按爬虫list.xlsx惠理表 B 列基金名查询官网产品页链接（含批量补全）",
        sheet_name=SHEET_NAME,
        catalog_loader=_fetch_catalog,
        index_builder=_build_index,
        resolver=_resolve_single,
        single_print_run_command=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
