#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
兴证国际（XYZQ International）基金月报爬虫补全脚本。

目录来源：官网公开 CMS 接口（纯 requests 可用）
  GET /official-website/cms/list?path_key=zcgl:jjlb&pageSize=1000&pageIndex=1&lang=1
  返回 4 支基金（cols JSON 内含 fundId / fundCode / fundName）。
月报判定：复用下载脚本的 fetch_documents(fund_id)，检查是否存在标题含“月报”的 PDF。

匹配：伞名+基金名 → 中文核心名（简繁统一、去“兴证国际/核心精选系列/基金”，
      并统一“环球=全球”“级别=级”）→ 英文名兜底。
I = https://www.xyzq.com.hk/detail/asset-manage/fund-detail/<fundId>/download
J = python download_xyzq_factsheet.py <fundId>

用法：
  python xyzq_fund_link.py 中国核心资产基金
  python xyzq_fund_link.py --probe --excel /path/to/爬虫list.xlsx --sheet 兴证国际
  python xyzq_fund_link.py --backfill --excel /path/to/爬虫list.xlsx --sheet 兴证国际
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
sys.path.insert(0, HERE)

from common.fund_link_backfill import cli_main, normalize_name  # noqa: E402
from download_xyzq_factsheet import (  # noqa: E402
    DEFAULT_PAGE_URL,
    fetch_documents,
)


SHEET_NAME = "兴证"
LIST_API = "https://www.xyzq.com.hk/official-website/cms/list"
DETAIL_URL = "https://www.xyzq.com.hk/detail/asset-manage/fund-detail/{fund_id}/download"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://www.xyzq.com.hk/detail/asset-manage/fund-list",
}
TIMEOUT = 40


def _cn(value: str) -> str:
    """中文核心名：简繁统一、同义词归一（环球/全球、级别/级），去品牌与系列词。"""
    text = normalize_name(value)
    text = text.replace("環球", "全球").replace("环球", "全球")
    text = text.replace("級別", "級").replace("级别", "级")
    for token in ("興證國際", "兴证国际", "核心精選系列", "核心精选系列", "系列", "基金"):
        text = text.replace(token, "")
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", text)


def _en(value: str) -> str:
    """英文核心名：去 CISI/XYZQ/Fund 等词，仅保留字母数字。"""
    text = str(value or "").strip().lower()
    for token in ("cisi", "xyzq", "fund", "funds", "series"):
        text = text.replace(token, "")
    return re.sub(r"[^a-z0-9]", "", text)


def _fetch_catalog() -> list[dict[str, Any]]:
    """返回官网基金目录（fundId / 基金名 / 代码）。"""
    response = requests.get(
        LIST_API,
        params={"path_key": "zcgl:jjlb", "pageSize": 1000, "pageIndex": 1, "lang": "1"},
        headers=HEADERS,
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    catalog: list[dict[str, Any]] = []
    for row in payload.get("data") or []:
        try:
            cols = json.loads(row.get("cols") or "{}")
        except json.JSONDecodeError:
            continue
        fund_id = str(cols.get("fundId") or "").strip()
        name = str(cols.get("fundName") or "").strip()
        if fund_id and name:
            catalog.append(
                {"fund_id": fund_id, "fund_name": name, "fund_code": str(cols.get("fundCode") or "")}
            )
    if not catalog:
        raise RuntimeError("兴证国际基金列表接口未返回基金")
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


_DOC_CACHE: dict[str, bool] = {}


def _has_monthly(fund_id: str) -> bool:
    """该基金是否存在标题含“月报”的 PDF 文档。"""
    if fund_id not in _DOC_CACHE:
        try:
            documents = fetch_documents(fund_id, DETAIL_URL.format(fund_id=fund_id))
            _DOC_CACHE[fund_id] = any(
                "月报" in str(item.get("title") or item.get("fundFileName") or "")
                for item in documents or []
            )
        except Exception:  # noqa: BLE001 - 接口异常按无月报处理
            _DOC_CACHE[fund_id] = False
    return _DOC_CACHE[fund_id]


def _resolve_single(row: dict[str, Any], index: dict[str, Any]) -> tuple[str, str, str | None]:
    """解析一支基金，返回 (I月报链接, J运行方式, 跳过原因)；不认领时原因为 None。"""
    record = _match_record(row, index)
    if not record:
        return "", "", "基金列表接口未找到该基金（名称/伞名不匹配）"
    if not _has_monthly(record["fund_id"]):
        return "", "", "官网该基金暂无月报文档"
    return (
        DETAIL_URL.format(fund_id=record["fund_id"]),
        f"python download_xyzq_factsheet.py {record['fund_id']}",
        "",
    )


def main(argv: list[str] | None = None) -> int:
    """命令行入口：单基金查询 / --probe / --backfill。"""
    return cli_main(
        argv,
        description="按爬虫list.xlsx兴证国际表 B 列基金名查询官网月报链接（含批量补全）",
        sheet_name=SHEET_NAME,
        catalog_loader=_fetch_catalog,
        index_builder=_build_index,
        resolver=_resolve_single,
        single_print_run_command=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
