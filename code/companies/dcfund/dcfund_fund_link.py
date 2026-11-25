#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
大成香港基金月报爬虫补全脚本。

以“爬虫list.xlsx”大成表的 B 列基金名（结合 C 伞）为索引，在大成香港官网
认可基金 / 互认基金列表中精确匹配基金，输出要回填的 I（月报资源页链接）
与 J（运行方式）。

官方页面规律（与第 7 行示例一致）：
  I = https://www.dcfund.com.hk/cn/<基金代码>/resource/?cid=33
  J = python download_dcfund_factsheet.py "<I>"

通用流程（Excel 扫描、进度条、probe/backfill、命令行入口）都在
code/common/fund_link_backfill.py；月报页解析复用同目录下载脚本
download_dcfund_factsheet.py 的 monthly_reports()，本文件只保留大成官网特有的
基金列表抓取与月报可用性校验。

用法：
  python dcfund_fund_link.py 大成全球股票基金
  python dcfund_fund_link.py --probe --excel /path/to/爬虫list.xlsx --sheet 大成
  python dcfund_fund_link.py --backfill --excel /path/to/爬虫list.xlsx --sheet 大成
"""

from __future__ import annotations

import html as html_mod
import os
import re
import sys
from typing import Any

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
sys.path.insert(0, HERE)

from common.fund_link_backfill import (  # noqa: E402
    build_index,
    cli_main,
    normalize_name,
    pick_share_class,
)
from download_dcfund_factsheet import monthly_reports  # noqa: E402


SHEET_NAME = "大成"
BASE_URL = "https://www.dcfund.com.hk"
LIST_URLS = (
    f"{BASE_URL}/cn/AuthorizedFund/",
    f"{BASE_URL}/cn/MutualRecognitionFund/",
)
MONTHLY_REPORT_CID = 33
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 30
_REPORT_CACHE: dict[str, bool] = {}


def _resource_url(code: str) -> str:
    """由基金代码拼出月报资源页链接（cid=33 为基金月报页签）。"""
    return f"{BASE_URL}/cn/{code}/resource/?cid={MONTHLY_REPORT_CID}"


def _fetch_catalog() -> list[dict[str, Any]]:
    """抓取大成官网基金目录：认可基金 + 互认基金。"""
    session = requests.Session()
    session.trust_env = False
    catalog: list[dict[str, Any]] = []
    seen: set[str] = set()
    for list_url in LIST_URLS:
        resp = session.get(list_url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        for match in re.finditer(
            r'<a[^>]+href="/cn/([A-Za-z0-9_]+)/summary/?"[^>]*>(.*?)</a>',
            resp.text,
            re.S | re.I,
        ):
            code = match.group(1)
            if code in seen:
                continue
            name_match = re.search(r"<h3[^>]*>(.*?)</h3>", match.group(2), re.S | re.I)
            if not name_match:
                continue
            fund_name = html_mod.unescape(
                re.sub(r"<[^>]+>", "", name_match.group(1))
            ).strip()
            if not code or not fund_name:
                continue
            seen.add(code)
            catalog.append({"code": code, "fund_name": fund_name})
    if not catalog:
        raise RuntimeError("大成官网基金目录为空，请检查页面结构是否变更")
    return catalog


def _has_monthly_report(code: str) -> bool:
    """检查该基金月报页签下是否已有月报（复用下载脚本的解析逻辑）。"""
    if code in _REPORT_CACHE:
        return _REPORT_CACHE[code]
    try:
        reports = monthly_reports(_resource_url(code))
    except Exception:
        reports = []
    _REPORT_CACHE[code] = bool(reports)
    return _REPORT_CACHE[code]


def _build_index(catalog: list[dict[str, Any]]) -> dict[str, Any]:
    """按归一化基金名建立大成目录索引。"""
    return build_index(catalog, key="fund_name")


def _resolve_single(
    row: dict[str, Any], index: dict[str, Any]
) -> tuple[str, str, str]:
    """解析一支基金，返回 (I月报资源页链接, J运行方式, 跳过原因)。"""
    fund_name = str(row.get("fund") or "")
    umbrella = str(row.get("umbrella") or "")
    matched = index.get(normalize_name(fund_name)) or []
    if not matched:
        return "", "", "官网未收录，跳过"
    code = str(pick_share_class(matched).get("code") or "")
    if not code:
        return "", "", "官网未返回基金代码，跳过"
    if not _has_monthly_report(code):
        return "", "", "官网暂未发布月报，跳过"
    link = _resource_url(code)
    return link, f'python download_dcfund_factsheet.py "{link}"', ""


def main(argv: list[str] | None = None) -> int:
    """命令行入口：单基金查询 / --probe / --backfill。"""
    return cli_main(
        argv,
        description="按爬虫list.xlsx大成表 B 列基金名查询官网月报链接（含批量补全）",
        sheet_name=SHEET_NAME,
        catalog_loader=_fetch_catalog,
        index_builder=_build_index,
        resolver=_resolve_single,
        single_print_run_command=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
