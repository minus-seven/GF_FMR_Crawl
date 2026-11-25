#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""安联环球投资（Allianz Global Investors）香港基金月报补全适配器。

安联香港站点分为两套目录：

* ORSO / Allianz Global Investors Choice Fund；
* Retail Funds / Allianz Global Investors Fund 等零售基金。

本适配器把两套公开目录合并，按英文基金核心名匹配爬虫表，优先选
HKD、再 USD、再其它币种的份额。I 列写入稳定的基金详情页，而不是把
带 hash 的 PDF 地址固化到工作簿；J 列的下载脚本会在每次运行时从详情页
重新发现最新 Factsheet。这满足“官网更新后自动取新报告”的要求。

用法：
  python allianz_fund_link.py "Allianz Global Artificial Intelligence"
  python allianz_fund_link.py --probe --excel 爬虫list.xlsx --sheet 安联环球
  python allianz_fund_link.py --backfill --excel 爬虫list.xlsx --sheet 安联环球
"""

from __future__ import annotations

import os
import re
import sys
from typing import Any
from urllib.parse import urljoin

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))

from common.fund_link_backfill import cli_main, normalize_name  # noqa: E402


SHEET_NAME = "安联环球"
BASE_URL = "https://hk.allianzgi.com"
ORSO_API = (
    BASE_URL
    + "/en-HK/api/funddata/multi/funds/"
    + "7167ecce-2bdf-4564-be0d-a917a2c3e58d/"
    + "0f6e4bfb-4f7c-4950-8df0-ec24c6b50ff1"
)
RETAIL_API = (
    BASE_URL
    + "/en-HK/api/funddata/multi/funds/"
    + "3ae9273d-954b-4631-85a7-387c5f5d7286/"
    + "9c59f8e2-7f0f-4f6f-90bd-c6e63201f374"
)
DETAIL_PREFIX = {
    "orso": "/en-hk/retirement/products-solutions/orso/",
    "retail": "/en-hk/retail/products-solutions/retail-funds/",
}
RUN_COMMAND = 'python download_allianz_factsheet.py "{url}"'
# 官网在 2026-09-22 将旧产品改名；该基金当前目录偶尔仍未回显旧名称，
# 通过官网公开的当前详情页保留一条强匹配映射（不是猜测随机 URL）。
LEGACY_PAGES = {
    "allianzgemequityhighdividend": {
        "slug": "allianz-high-dividend-emerging-markets-equity-amg-hkd",
        "kind": "retail",
    },
}
# 目录 API 未返回的缺口不能一律记为“适配器故障”。这些原因来自 2026-11-24
# 的官方目录、改名表和公司行动复核；只影响审计提示，不会把非月报文件写入 I/J 列。
GAP_REASONS = {
    "allianzeurobalanced": "RENAME_NOT_RECONCILED：主数据改名为 Allianz Dynamic Multi Asset European Balanced，旧名未进入当前目录",
    "allianzglobaldiversifiedcredit": "DOC_ONLY_NO_MONTHLY：KFS/招股书有产品资料，当前香港 Retail 目录无可复现详情页",
    "allianzusinvestmentgradecredit": "DOC_ONLY_NO_MONTHLY：招股书有产品资料，当前香港 Retail 目录无可复现详情页",
    "allianzemergingmarketsshortdurationbond": "DOC_ONLY_NO_MONTHLY：2026 股东通知引用该子基金，当前香港 Retail 目录无可复现详情页",
    "allianzpositivechange": "DISTRIBUTOR_DOC_ONLY：Dealing Guide 有份额记录，当前香港 Retail 目录无可复现详情页",
    "allianzuslargecapvalue": "DOC_ONLY_NO_MONTHLY：招股书/股东通知有产品资料，当前香港 Retail 目录无可复现详情页",
    "allianzglobalcredit": "DOC_ONLY_NAME_AMBIGUOUS：招股书有 Global Credit，历史资料另有 Global Credit SRI，不能直接映射详情页",
    "allianzfoodsecurity": "CORPORATE_ACTION_DELISTED：当前 Retail 查询返回 Listing 0，官网列出 2026-09 合并公司行动",
    "allianzglobalmultiassetbalanced": "FUND_CLOSED：官方清算通知确定最后交易日 2025-11-07",
}
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}
TIMEOUT = 60
SESSION = requests.Session()


def _fetch_catalog(url: str, kind: str) -> list[dict[str, Any]]:
    """请求安联目录。

    参数 url：安联目录 API 地址；参数 kind：`orso` 或 `retail` 产品线标记。
    """
    response = SESSION.get(url, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    records = payload.get("FundList") or []
    if not isinstance(records, list):
        raise RuntimeError(f"安联 {kind} 基金目录格式异常：FundList 不是数组")
    result = []
    for item in records:
        if not isinstance(item, dict):
            continue
        name = str(item.get("FUND_NAME_TRANSLATED") or "").strip()
        slug = str(item.get("viewdetail") or "").strip()
        if not name or not slug:
            continue
        result.append({**item, "fund_name": name, "kind": kind})
    if not result:
        raise RuntimeError(f"安联 {kind} 基金目录为空")
    return result


def _catalog_loader() -> list[dict[str, Any]]:
    """读取 ORSO 与 Retail 两个公开目录，按详情 slug 去重。"""
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for url, kind in ((ORSO_API, "orso"), (RETAIL_API, "retail")):
        for item in _fetch_catalog(url, kind):
            merged[(kind, str(item.get("viewdetail")))] = item
    return list(merged.values())


def _en_key(value: str) -> str:
    """生成英文匹配键；参数 value 为工作簿或官网原始基金名。"""
    """英文核心名：大小写、引号、币种和类别标点统一。"""
    text = str(value or "").strip().lower()
    text = text.replace("&", "and").replace("ex-china", "ex china")
    return re.sub(r"[^a-z0-9]+", "", text)


def _cn_key(value: str) -> str:
    """生成中文匹配键；参数 value 为工作簿中文基金名。"""
    text = normalize_name(value)
    for token in ("安联", "安聯", "基金"):
        text = text.replace(token, "")
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", text)


def _build_index(catalog: list[dict[str, Any]]) -> dict[str, Any]:
    """建立基金名索引；参数 catalog 为两套官网目录的合并记录。"""
    index: dict[str, Any] = {"en": {}, "cn": {}, "all": catalog}
    for item in catalog:
        index["en"].setdefault(_en_key(item.get("fund_name")), []).append(item)
        # 目录目前以英文为主；保留 fund_name 的中文索引位，便于以后官网增加中文字段。
        if item.get("fund_name_zh"):
            index["cn"].setdefault(_cn_key(item["fund_name_zh"]), []).append(item)
    return index


def _share_rank(item: dict[str, Any]) -> tuple[int, int, str]:
    """计算份额优先级；参数 item 为官网份额记录。"""
    currency = str(item.get("DisplayCurrency") or "").upper()
    if "HKD" in currency or "港元" in currency or "HK$" in currency:
        currency_rank = 0
    elif "USD" in currency or "美元" in currency or "US$" in currency:
        currency_rank = 1
    else:
        currency_rank = 2
    # 同币种优先零售目录；否则按官网默认排序稳定取第一条。
    kind_rank = 0 if item.get("kind") == "retail" else 1
    return currency_rank, kind_rank, str(item.get("viewdetail") or "")


def _match_records(row: dict[str, Any], index: dict[str, Any]) -> list[dict[str, Any]]:
    """匹配官网记录；参数 row 为爬虫表行，参数 index 为目录索引。"""
    english = _en_key(row.get("english") or "")
    if english:
        exact = index["en"].get(english) or []
        if exact:
            return exact
        # 兼容官网把 Fund / 旧引号或份额后缀写入名称的情况；要求至少 8 个字符，
        # 避免用过短通用词误配其它产品。
        if len(english) >= 8:
            candidates = [
                item
                for key, items in index["en"].items()
                if (english in key or key in english)
                for item in items
            ]
            if candidates:
                return candidates
    chinese = _cn_key(row.get("fund") or "")
    return index["cn"].get(chinese) or []


def _resolve_single(row: dict[str, Any], index: dict[str, Any]) -> tuple[str, str, str | None]:
    """解析单行入口；参数 row 为爬虫表行，参数 index 为目录索引。"""
    records = _match_records(row, index)
    if not records:
        legacy = LEGACY_PAGES.get(_en_key(row.get("english") or ""))
        if legacy:
            detail_url = urljoin(BASE_URL, DETAIL_PREFIX[legacy["kind"]] + legacy["slug"])
            return detail_url, RUN_COMMAND.format(url=detail_url), ""
    if not records:
        reason = GAP_REASONS.get(
            _en_key(row.get("english") or ""),
            "NO_FUND_PAGE：安联当前 ORSO/零售目录未找到该基金英文名",
        )
        return "", "", reason
    record = sorted(records, key=_share_rank)[0]
    slug = str(record.get("viewdetail") or "").strip()
    kind = str(record.get("kind") or "retail")
    if kind not in DETAIL_PREFIX or not slug:
        return "", "", "NO_VERIFIABLE_FILE：官网目录缺少详情页 slug"
    detail_url = urljoin(BASE_URL, DETAIL_PREFIX[kind] + slug)
    return detail_url, RUN_COMMAND.format(url=detail_url), ""


def main(argv: list[str] | None = None) -> int:
    return cli_main(
        argv,
        description="按安联环球 ORSO/零售基金目录补全爬虫list.xlsx 月报入口",
        sheet_name=SHEET_NAME,
        catalog_loader=_catalog_loader,
        index_builder=_build_index,
        resolver=_resolve_single,
        single_print_run_command=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
