#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
泰康资产管理（香港）基金月报爬虫补全脚本。

目录来源：官网公开接口（纯 requests 可用）
  GET https://hk.taikangasset.cn/api/product/fundListTree/hk
  返回 [{"fundName":"公募基金","childProduct":[{"fundName":"泰康開泰…","fundCode":"…"}]}]
月报来源：复用下载脚本的 latest_monthly_report()（内部 POST /api/tblProductInformation/getList），
       返回最新一期“月報”PDF 直链，再探活确认。

匹配：伞名+基金名 → 中文核心名（简繁统一、去“泰康开泰/基金”等词）→ 英文名兜底。
I = 最新月报 PDF 直链
J = python download_taikang_factsheet.py "<官网繁体基金名>"

用法：
  python taikang_fund_link.py 泰康开泰投资级债券基金
  python taikang_fund_link.py --probe --excel /path/to/爬虫list.xlsx --sheet 泰康
  python taikang_fund_link.py --backfill --excel /path/to/爬虫list.xlsx --sheet 泰康
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
from download_taikang_factsheet import (  # noqa: E402
    PAGE_URL,
    get_json,
    iter_child_funds,
    latest_monthly_report,
)


SHEET_NAME = "泰康"
TIMEOUT = 40
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


def _cn(value: str) -> str:
    """中文核心名：简繁统一、去“泰康/开泰/基金/市场”等通用词与符号。"""
    text = normalize_name(value)
    for token in ("泰康", "開泰", "开泰", "基金", "市場", "市场"):
        text = text.replace(token, "")
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", text)


def _en(value: str) -> str:
    """英文核心名：去 Taikang/Kaitai/Fund 等词，仅保留字母数字。"""
    text = str(value or "").strip().lower()
    for token in ("taikang", "kaitai", "fund", "funds"):
        text = text.replace(token, "")
    return re.sub(r"[^a-z0-9]", "", text)


def _fetch_catalog() -> list[dict[str, Any]]:
    """返回官网基金目录（繁体基金名 + 代码）。"""
    tree = get_json("/api/product/fundListTree/hk", PAGE_URL)
    catalog = []
    for child in iter_child_funds(tree):
        name = str(child.get("fundName") or "").strip()
        code = str(child.get("fundCode") or "").strip()
        if name:
            catalog.append({"fund_name": name, "fund_code": code})
    if not catalog:
        raise RuntimeError("泰康基金树接口未返回任何基金")
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
    candidates = [_cn(fund)]
    if umbrella:
        candidates.append(_cn(f"{umbrella}{fund}"))
    for key in candidates:
        if key and key in index["cn"]:
            return index["cn"][key]
    english = _en(str(row.get("english") or ""))
    if english:
        for record in index["all"]:
            if english and english in _en(record["fund_name"]):
                return record
    for key in candidates:
        if len(key) < 4:
            continue
        for candidate_key, record in index["cn"].items():
            if candidate_key and (key in candidate_key or candidate_key in key):
                return record
    return None


def _probe_pdf(url: str) -> bool:
    """探活 PDF 直链（200 且 content-type 含 pdf）。"""
    try:
        response = requests.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True, verify=False)
        ok = response.status_code == 200 and "pdf" in (response.headers.get("content-type") or "").lower()
        response.close()
        return ok
    except Exception:  # noqa: BLE001
        return False


_REPORT_CACHE: dict[str, str | None] = {}


def _latest_report_url(fund_name: str) -> str | None:
    """调用下载脚本的同一逻辑，返回该基金最新月报 PDF 直链。"""
    if fund_name not in _REPORT_CACHE:
        try:
            _code, report = latest_monthly_report(PAGE_URL, fund_name)
            _REPORT_CACHE[fund_name] = str(report.get("url") or "") or None
        except Exception:  # noqa: BLE001 - 无月报/接口异常都按不可用处理
            _REPORT_CACHE[fund_name] = None
    return _REPORT_CACHE[fund_name]


def _resolve_single(row: dict[str, Any], index: dict[str, Any]) -> tuple[str, str, str | None]:
    """解析一支基金，返回 (I月报链接, J运行方式, 跳过原因)；不认领时原因为 None。"""
    record = _match_record(row, index)
    if not record:
        return "", "", "基金树接口未找到该基金（名称/伞名不匹配）"
    url = _latest_report_url(record["fund_name"])
    if not url:
        return "", "", "官网该基金暂无月报文件"
    if not _probe_pdf(url):
        return "", "", f"月报直链不可下载：{url}"
    return url, f'python download_taikang_factsheet.py "{record["fund_name"]}"', ""


def main(argv: list[str] | None = None) -> int:
    """命令行入口：单基金查询 / --probe / --backfill。"""
    return cli_main(
        argv,
        description="按爬虫list.xlsx泰康表 B 列基金名查询官网最新月报（含批量补全）",
        sheet_name=SHEET_NAME,
        catalog_loader=_fetch_catalog,
        index_builder=_build_index,
        resolver=_resolve_single,
        single_print_run_command=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
