#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
海通（GTHT 资管 / gthtam.com.hk）基金月报爬虫补全脚本。

目录来源：官网资料库页面服务端渲染的 “FundsDocSelect” 下拉（id → 基金名）
  https://gthtam.com.hk/asm/zh-chs/Home/library/38?series=Funds
月报判定：该基金资料页必须存在“基金概览”条目（与下载脚本默认 label 一致），
          否则视为已终止/无月报，输出 SKIP。

匹配：中文核心名优先 → 英文核心名兜底（官网仅提供中文名，英文用页表“英文”列固化）。
I = https://gthtam.com.hk/asm/zh-chs/Home/library/<id>?series=Funds
J = python download_gthtam_factsheet.py "<I>" "基金概览"

用法：
  python gthtam_fund_link.py 海通亚洲总收益债券基金
  python gthtam_fund_link.py --probe --excel /path/to/爬虫list.xlsx --sheet 海通
  python gthtam_fund_link.py --backfill --excel /path/to/爬虫list.xlsx --sheet 海通
"""

from __future__ import annotations

import os
import re
import sys
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
sys.path.insert(0, HERE)

from common.fund_link_backfill import (  # noqa: E402
    cli_main,
    normalize_name,
)
from download_gthtam_factsheet import (  # noqa: E402
    GthtamLibraryParser,
    fetch_page,
)


SHEET_NAME = "海通"
BASE_URL = "https://gthtam.com.hk"
LIBRARY_URL = f"{BASE_URL}/asm/zh-chs/Home/library/38?series=Funds"
MONTHLY_LABEL = "基金概览"
SELECT_RE = re.compile(r'<select[^>]*name="FundsDocSelect"[^>]*>(.*?)</select>', re.S)
OPTION_RE = re.compile(r'<option value="([^"]+)"[^>]*>([^<]*)</option>')
# 官网只有中文名，英文名取自爬虫list“海通”表“英文”列（2026-10 核对），仅作兜底
EN_ALIASES = {
    "38": "Haitong Asian Total Return Bond Fund",
    "99": "Haitong US Dollar Money Market Fund",
    "96": "Haitong HK Dollar Money Market Fund",
    "52": "Haitong Global RMB Fixed Income Fund",
    "102": "Tabula Haitong Asia Pacific Select Bond Fund",
    "87": "Haitong Greater China Vision Fund",
}


def _norm(value: str) -> str:
    """名称归一化：简繁统一、去空格，并剥离 Tabula/海通 等品牌前缀。"""
    text = normalize_name(value)
    text = re.sub(r"^[A-Za-z]+", "", text)
    for prefix in ("Tabula", "tabula", "海通", "國泰海通", "国泰海通"):
        text = text.replace(prefix, "")
    return text


def _en(value: str) -> str:
    """英文名归一化：去 Haitong/Tabula 等词，仅保留字母数字。"""
    text = str(value or "").strip().lower()
    for token in ("tabula", "haitong", "guotai"):
        text = text.replace(token, "")
    return re.sub(r"[^a-z0-9]", "", text)


def _parse_fund_options(page_html: str) -> list[dict[str, str]]:
    """从资料库页面解析 “FundsDocSelect” 下拉，返回 [{fund_id, fund_name}]。"""
    block = SELECT_RE.search(page_html)
    if not block:
        raise RuntimeError("未在 gthtam 资料库页面找到 FundsDocSelect 下拉，官网结构可能已变更")
    funds = []
    for fund_id, fund_name in OPTION_RE.findall(block.group(1)):
        name = fund_name.strip()
        if fund_id.strip().isdigit() and name:
            funds.append({"fund_id": fund_id.strip(), "fund_name": name})
    return funds


def _has_monthly_document(fund_id: str) -> bool:
    """抓取该基金资料页，判断是否存在带 PDF 的“基金概览”（月报）条目。"""
    parser = GthtamLibraryParser()
    parser.feed(fetch_page(f"{BASE_URL}/asm/zh-chs/Home/library/{fund_id}?series=Funds"))
    for doc in parser.docs:
        if MONTHLY_LABEL in doc["label"] and doc["options"]:
            return True
    return False


def _fetch_catalog() -> list[dict[str, Any]]:
    """返回官网“基金”系列目录（含每支基金是否有月报）。"""
    catalog: list[dict[str, Any]] = []
    for item in _parse_fund_options(fetch_page(LIBRARY_URL)):
        catalog.append(
            {
                "fund_id": item["fund_id"],
                "fund_name": item["fund_name"],
                "english": EN_ALIASES.get(item["fund_id"], ""),
                "has_monthly": _has_monthly_document(item["fund_id"]),
            }
        )
    if not catalog:
        raise RuntimeError("gthtam 资料库未解析到基金目录，请检查页面结构是否变更")
    return catalog


def _build_index(catalog: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """建立 (语言, 归一化名) → 基金记录 的索引。"""
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for record in catalog:
        index.setdefault(("cn", _norm(record["fund_name"])), record)
        if record.get("english"):
            index.setdefault(("en", _en(record["english"])), record)
    return index


def _resolve_single(
    row: dict[str, Any], index: dict[tuple[str, str], dict[str, Any]]
) -> tuple[str, str, str | None]:
    """解析一支基金，返回 (I月报链接, J运行方式, 跳过原因)；不认领时原因为 None。"""
    record = index.get(("cn", _norm(str(row.get("fund") or ""))))
    if not record:
        record = index.get(("en", _en(str(row.get("english") or ""))))
    if not record:
        return "", "", "官网资料库未收录该基金"
    if not record.get("has_monthly"):
        return "", "", f"官网无“{MONTHLY_LABEL}”文档（基金已终止或无月报）"
    link = f"{BASE_URL}/asm/zh-chs/Home/library/{record['fund_id']}?series=Funds"
    return link, f'python download_gthtam_factsheet.py "{link}" "{MONTHLY_LABEL}"', ""


def main(argv: list[str] | None = None) -> int:
    """命令行入口：单基金查询 / --probe / --backfill。"""
    return cli_main(
        argv,
        description="按爬虫list.xlsx海通表 B 列基金名查询官网月报链接（含批量补全）",
        sheet_name=SHEET_NAME,
        catalog_loader=_fetch_catalog,
        index_builder=_build_index,
        resolver=_resolve_single,
        single_print_run_command=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
