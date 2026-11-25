#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
招银国际（CMB International）基金月报爬虫补全脚本。

目录来源：官网互惠基金产品页（服务端渲染）
  https://www.cmbi.com.hk/zh-HK/mutualfund?id=<id>
  基金名在 `<p class="banner-text">…</p>`，文档在 `.list-block`（标题如“基金月報”）。
产品 id 为小整数（实测 1/4/5/6/7 五支），脚本扫描 1–20 建目录。

月报判定：页面存在标题含“基金月報/基金月报”的文档块且有文件链接。
匹配：中文核心名（简繁统一、去“招銀國際/基金/系列”、统一“環球=全球”）→ 英文兜底。
I = 产品页 URL
J = python download_cmbi_factsheet.py "<I>"

用法：
  python cmbi_fund_link.py 招银国际投资级债券基金
  python cmbi_fund_link.py --probe --excel /path/to/爬虫list.xlsx --sheet 招银国际
  python cmbi_fund_link.py --backfill --excel /path/to/爬虫list.xlsx --sheet 招银国际
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


SHEET_NAME = "招银国际"
PAGE_URL = "https://www.cmbi.com.hk/zh-HK/mutualfund?id={pid}"
ID_RANGE = range(1, 21)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 30
NAME_RE = re.compile(r'class="banner-text"[^>]*>(.*?)<', re.S)
MONTHLY_RE = re.compile(r"基金月[報报]", re.S)


def _cn(value: str) -> str:
    """中文核心名：简繁统一、去“招银国际/基金/系列”，统一“环/環球=全球”。"""
    text = normalize_name(value)
    text = text.replace("環球", "全球").replace("环球", "全球")
    for token in ("招銀國際", "招银国际", "基金", "系列"):
        text = text.replace(token, "")
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", text)


def _en(value: str) -> str:
    """英文核心名：去 CMBI/CMB International/Fund 等词。"""
    text = str(value or "").strip().lower()
    for token in ("cmbi", "cmb international", "cmb", "fund", "funds"):
        text = text.replace(token, "")
    return re.sub(r"[^a-z0-9]", "", text)


def _fetch_catalog() -> list[dict[str, Any]]:
    """扫描产品 id，返回 (id, 基金名, 是否有月报)。"""
    catalog: list[dict[str, Any]] = []
    session = requests.Session()
    for pid in ID_RANGE:
        try:
            response = session.get(PAGE_URL.format(pid=pid), headers=HEADERS, timeout=TIMEOUT)
            if response.status_code != 200:
                continue
            page = response.text
        except Exception:  # noqa: BLE001 - 单个 id 失败跳过
            continue
        match = NAME_RE.search(page)
        if not match:
            continue
        name = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", match.group(1))).split())
        if not name:
            continue
        catalog.append(
            {"fund_id": str(pid), "fund_name": name, "has_monthly": bool(MONTHLY_RE.search(page))}
        )
    if not catalog:
        raise RuntimeError("招银国际产品页未解析到基金（页面结构可能已变化）")
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
        return "", "", "官网产品页未找到该基金（名称/伞名不匹配）"
    if not record.get("has_monthly"):
        return "", "", "官网该基金页面无“基金月報”文档块"
    link = PAGE_URL.format(pid=record["fund_id"])
    return link, f'python download_cmbi_factsheet.py "{link}"', ""


def main(argv: list[str] | None = None) -> int:
    """命令行入口：单基金查询 / --probe / --backfill。"""
    return cli_main(
        argv,
        description="按爬虫list.xlsx招银国际表 B 列基金名查询官网月报链接（含批量补全）",
        sheet_name=SHEET_NAME,
        catalog_loader=_fetch_catalog,
        index_builder=_build_index,
        resolver=_resolve_single,
        single_print_run_command=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
