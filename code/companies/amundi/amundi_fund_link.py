#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
东方汇理（Amundi）香港基金月报爬虫补全脚本。

以“爬虫list.xlsx”东方汇理表的 B 列基金名（结合 C 伞）为索引，在东方汇理香港
官网基金列表中精确匹配基金，输出要回填的 I（产品页链接）与 J（运行方式）：
  I = https://www.amundi.com.hk/zh_retail/product/view/<ISIN>
  J = python download_amundi_factsheet.py <ISIN>

官网数据源（product-services 接口）：
  GET /product-services/tip/shares/v2/categories/public/search.json
    grouping=true  → 基金组（含各股份类别的 ISIN / 币种 / 标签）
    grouping=false → 各股份类别文档信息（判断是否已发布基金月报）

类别选择规则：先在“已有月报”的股份类别中，按 港元 > 美元 > 其他、
同币种取默认第一条；全部类别都没有月报时按 SKIP 处理。

通用流程（Excel 扫描、进度条、probe/backfill、命令行入口）都在
code/common/fund_link_backfill.py；本文件只保留东方汇理官网特有的
接口抓取、名称归一化与月报可用性判断。

用法：
  python amundi_fund_link.py 环球股票基金
  python amundi_fund_link.py --probe --excel /path/to/爬虫list.xlsx --sheet 东方汇理
  python amundi_fund_link.py --backfill --excel /path/to/爬虫list.xlsx --sheet 东方汇理
"""

from __future__ import annotations

import difflib
import os
import re
import sys
from collections import Counter
from typing import Any

import requests
from opencc import OpenCC

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common.fund_link_backfill import (  # noqa: E402
    cli_main,
    pick_share_class,
)


SHEET_NAME = "东方汇理"
BASE_URL = "https://www.amundi.com.hk"
SEARCH_API = f"{BASE_URL}/product-services/tip/shares/v2/categories/public/search.json"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": f"{BASE_URL}/zh_retail/funds-hk_tc",
}
BASE_PARAMS = {
    "sort[0][field]": "label",
    "sort[0][order]": "ASC",
    "perimeter": 168,
    "searchText": "",
    "category": "public",
    "partnerId": 795,
    "locale": "zh-HK",
    "id": 67,
}
PAGE_LIMIT = 100
TIMEOUT = 30
MONTHLY_DOC_KEY = "npe.doctype.monthlyfactsheet"
CC = OpenCC("s2t")
# 官网标签里的系列前缀（长的在前，避免短前缀先命中）
UMBRELLA_PREFIXES = sorted(
    (
        "東方匯理香港強積金系列",
        "東方匯理香港組合",
        "東方匯理亞洲傘子基金",
        "東方匯理系列基金",
        "東方匯理收成基金",
        "東方匯理系列",
        "東方匯理香港",
        "東方匯理",
    ),
    key=len,
    reverse=True,
)
# 股份类别/货币写法等后缀的起点
CLASS_CUT = re.compile(
    r"(?:類別|普通|機構|(?<=[\u4e00-\u9fff])[A-Z]{1,4}|MTD\d?|累積|累算|分派|SCInvest)"
)
# 表格 C 列伞名 → 索引用的伞标识
WORKBOOK_UMBRELLA = {
    "东方汇理香港组合": "HK",
    "东方汇理系列基金": "SERIES",
    "东方汇理亚洲伞子基金": "ASIA",
    "东方汇理收成基金": "HARVEST",
    "东方汇理香港强积金系列": "MPF",
}
FULLWIDTH_DIGITS = "０１２３４５６７８９"


def _norm(value: str) -> str:
    """名称归一化：简→繁、去空格、统一括号/连字符/全角数字。"""
    text = CC.convert(str(value or "")).replace("\u3000", "").replace(" ", "")
    text = "".join(
        chr(ord("0") + FULLWIDTH_DIGITS.index(ch)) if ch in FULLWIDTH_DIGITS else ch
        for ch in text
    )
    for src, dst in (
        ("（", "("),
        ("）", ")"),
        ("－", "-"),
        ("–", "-"),
        ("—", "-"),
        ("−", "-"),
        ("-", ""),
    ):
        text = text.replace(src, dst)
    return text


def _fund_key(value: str) -> str:
    """从官网标签或表格基金名中提取“基金级”归一化名称。"""
    text = _norm(value)
    for prefix in UMBRELLA_PREFIXES:
        normalized = _norm(prefix)
        if text.startswith(normalized):
            text = text[len(normalized):]
            break
    match = CLASS_CUT.search(text)
    if match:
        text = text[: match.start()]
    text = re.sub(r"[\(\)]", "", text)
    text = re.sub(r"基(?=\d+$)", "基金", text)  # 官网偶发“到期保證基1”缺字
    if text.endswith("基金"):
        text = text[:-2]
    return text.strip()


def _label_umbrella(label: str) -> str:
    """从官网标签判断所属伞（与表格 C 列标识一致）。"""
    text = _norm(label)
    if text.startswith(_norm("東方匯理香港組合")):
        return "HK"
    if text.startswith(_norm("東方匯理系列")):
        return "SERIES"
    if text.startswith(_norm("東方匯理香港")):
        return "MPF"
    if text.startswith(_norm("東方匯理收成")) or text.startswith(_norm("東方匯理越南")):
        return "HARVEST"
    if text.startswith("CIO"):
        return "ASIA"
    return ""


def _fetch_json(session: requests.Session, params: dict[str, Any]) -> dict[str, Any]:
    """调用官网基金搜索接口并返回 data 字段。"""
    resp = session.get(SEARCH_API, headers=HEADERS, params=params, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json().get("data") or {}


def _fetch_catalog() -> list[dict[str, Any]]:
    """抓取官网基金目录：基金组 + 组内各股份类别（含是否有月报）。"""
    session = requests.Session()
    session.trust_env = False

    # 1) 股份类别级数据：判断每个 ISIN 是否已发布月报
    monthly_isins: set[str] = set()
    offset = 0
    while True:
        params = dict(BASE_PARAMS, limit=PAGE_LIMIT, page=1, offset=offset, grouping="false")
        response = _fetch_json(session, params).get("response") or {}
        docs = response.get("docs") or []
        for doc in docs:
            isin = str(doc.get("isin") or "")
            documents = doc.get("documents") or {}
            if isin and MONTHLY_DOC_KEY in documents:
                monthly_isins.add(isin)
        offset += len(docs)
        if not docs or offset >= int(response.get("numFound") or 0):
            break

    # 2) 基金组级数据：组内各股份类别
    groups: list[dict[str, Any]] = []
    offset = 0
    while True:
        params = dict(BASE_PARAMS, limit=PAGE_LIMIT, page=1, offset=offset, grouping="true")
        grouped = (_fetch_json(session, params).get("grouped") or {}).get("id_comp") or {}
        batch = grouped.get("groups") or []
        groups.extend(batch)
        offset += len(batch)
        if len(batch) < PAGE_LIMIT:
            break

    catalog: list[dict[str, Any]] = []
    for group in groups:
        docs = []
        for doc in (group.get("doclist") or {}).get("docs") or []:
            isin = str(doc.get("isin") or "")
            if not isin:
                continue
            docs.append(
                {
                    "isin": isin,
                    "currency": (doc.get("currency") or {}).get("label") or "",
                    "label": str(doc.get("label") or ""),
                    "has_monthly": isin in monthly_isins,
                }
            )
        keys = [
            (_label_umbrella(doc["label"]), _fund_key(doc["label"])) for doc in docs
        ]
        keys = [key for key in keys if key[0] and key[1]]
        if not keys:
            continue
        catalog.append(
            {
                "key": Counter(keys).most_common(1)[0][0],
                "group": str(group.get("groupValue") or ""),
                "docs": docs,
            }
        )
    if not catalog:
        raise RuntimeError("东方汇理官网基金目录为空，请检查接口是否变更")
    return catalog


def _build_index(catalog: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """建立 (伞, 基金名) → 股份类别列表 的索引。"""
    index: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in catalog:
        docs = index.setdefault(record["key"], [])
        known = {doc["isin"] for doc in docs}
        docs.extend(doc for doc in record["docs"] if doc["isin"] not in known)
    return index


def _resolve_single(
    row: dict[str, Any],
    index: dict[tuple[str, str], list[dict[str, Any]]],
) -> tuple[str, str, str]:
    """解析一支基金，返回 (I产品页链接, J运行方式, 跳过原因)。"""
    fund_name = str(row.get("fund") or "")
    umbrella = str(row.get("umbrella") or "")
    umbrella_key = WORKBOOK_UMBRELLA.get(str(umbrella or "").strip(), "")
    fund_key = _fund_key(fund_name)
    docs = index.get((umbrella_key, fund_key))
    if not docs:
        # 官网偶发错别字：在同一伞下按相似度做唯一兜底
        candidates = [
            key
            for key in index
            if key[0] == umbrella_key
            and difflib.SequenceMatcher(None, fund_key, key[1]).ratio() >= 0.85
        ]
        if len(candidates) == 1:
            docs = index[candidates[0]]
    if not docs:
        return "", "", "官网未收录，跳过"
    with_monthly = [doc for doc in docs if doc.get("has_monthly")]
    if not with_monthly:
        return "", "", "官网暂未发布月报，跳过"
    chosen = pick_share_class(with_monthly, field="currency")
    isin = chosen["isin"]
    link = f"{BASE_URL}/zh_retail/product/view/{isin}"
    return link, f"python download_amundi_factsheet.py {isin}", ""


def main(argv: list[str] | None = None) -> int:
    """命令行入口：单基金查询 / --probe / --backfill。"""
    return cli_main(
        argv,
        description="按爬虫list.xlsx东方汇理表 B 列基金名查询官网月报链接（含批量补全）",
        sheet_name=SHEET_NAME,
        catalog_loader=_fetch_catalog,
        index_builder=_build_index,
        resolver=_resolve_single,
        single_print_run_command=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
