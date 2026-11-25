#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
摩根资产管理（香港）am.jpmorgan.com —— 基金月报爬虫补全脚本。

目录来源：站点自身的基金清单接口（GET，仅允许浏览器上下文调用，纯 requests 返回 400）
  /FundsMarketingHandler/fund-explorer?country=hk&role=per&userLoggedIn=false&language=zh
返回 271 条“份额类别”记录，字段含 name/displayName/displayNameTranslatedName/
identifier(ISIN)/currencyCode 等。

月报判定：产品页文档来自“基金文件”页内嵌 JSON
  https://am.jpmorgan.com/hk/zh/asset-management/per/products/fund-documents/
  documents = {"<ISIN>":[{documentType, docURL, title}...]}
其中 documentType = "Fact Sheet (US EMEA)" 即月报（基金報告）。

产品页 URL 规律（实测与 Sheet1 历史写法一致）：
  /hk/zh/asset-management/per/products/<displayName 转小写连字符>-<isin 小写>#/documents
匹配：中文核心名（去尾部份额/货币括注）优先 → 英文核心名兜底；同一基金多份额按 港元>美元>其他 选一条。
I = 产品页 URL
J = python download_jpmorgan_documents.py "<I>"

用法：
  python jpmorgan_fund_link.py 摩根基金 - 环球债券收益基金
  python jpmorgan_fund_link.py --probe --excel /path/to/爬虫list.xlsx --sheet 摩根
  python jpmorgan_fund_link.py --backfill --excel /path/to/爬虫list.xlsx --sheet 摩根
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

from common.browser_fetch import launch_browser, new_context  # noqa: E402
from common.fund_link_backfill import (  # noqa: E402
    cli_main,
    normalize_name,
)
from playwright.sync_api import sync_playwright  # noqa: E402


SHEET_NAME = "摩根"
BASE_URL = "https://am.jpmorgan.com"
EXPLORER_PAGE = BASE_URL + "/hk/zh/asset-management/per/products/fund-explorer/retail-funds"
EXPLORER_API = (
    "/FundsMarketingHandler/fund-explorer"
    "?country=hk&role=per&userLoggedIn=false&language=zh"
)
# 站点按 fundType 分区返回不同基金池（2026-10 实测：零售 271 / 宜安 24 / 裕盛 22 / 互认 3）
EXPLORER_FUND_TYPES = ("retail-funds", "sar-funds", "provident-funds", "mainland-hk-mrf-funds")
DOCUMENTS_PAGE = BASE_URL + "/hk/zh/asset-management/per/products/fund-documents/"
PRODUCT_URL = BASE_URL + "/hk/zh/asset-management/per/products/{slug}#/documents"
MONTHLY_DOC_TYPE = "Fact Sheet (US EMEA)"
PAGE_TIMEOUT_MS = 90000
SETTLE_MS = 12000
TIMEOUT = 40
CURRENCY_RANK = {"港元": 0, "港币": 0, "HKD": 0, "美元": 1, "USD": 1}


def _cn(value: str) -> str:
    """中文名归一化：简繁统一、去空格符号，剥离尾部“（美元）（累計）”括注与“基金/系列/摩根”等词。"""
    text = normalize_name(value)
    text = text.replace("匯", "汇").replace("滙", "汇").replace("證", "证")
    text = text.replace("大韓", "韓國").replace("台灣", "臺灣").replace("*", "").replace("＊", "")
    while True:
        trimmed = re.sub(r"\([^()]*\)$", "", text)
        if trimmed == text:
            break
        text = trimmed
    for token in ("-", "－", "–", "—", "−", "─", "（", "）", "(", ")", "＋", "+", " ", "\u3000"):
        text = text.replace(token, "")
    for token in ("摩根", "基金", "系列", "JPMorgan", "JPM"):
        text = text.replace(token, "")
    return text


def _en(value: str) -> str:
    """英文名归一化：去 JPMorgan/JPM 词与尾部份额括注，仅保留字母数字。"""
    text = str(value or "").strip()
    text = re.sub(r"\([^()]*\)", "", text)
    text = text.split(" - ")[0]
    text = text.lower()
    for token in ("jpmorgan", "jpm", "funds", "fund", "sicav", "series"):
        text = text.replace(token, "")
    return re.sub(r"[^a-z0-9]", "", text)


def _cn_keys(value: str) -> set[str]:
    """中文名多变体：整名、首个括注/类别的“基金核名”前段、去系列前缀的末段。

    官网份额名形如 `JPM美國科技（美元）－A股（累計）`，页表名形如
    `摩根基金 - 美国科技基金`，两侧结构不同，需各自产出核心名再比对。
    """
    raw = str(value or "").strip()
    variants = {raw}
    variants.add(re.split(r"[（(]", raw)[0])          # 去掉“（美元）－A股（累計）”
    variants.add(re.split(r"[－\-–—─]", raw)[-1])      # 去掉“摩根基金 - ”系列前缀
    for chunk in re.split(r"[－\-–—─]", raw):
        variants.add(chunk)
    keys = set()
    for variant in variants:
        key = _cn(variant)
        # 允许 2 字核心名（如“美國/中國/台灣”），同一公司目录内 first-wins 且仅作兜底
        if len(key) >= 2:
            keys.add(key)
    return keys


def _en_keys(value: str) -> set[str]:
    """英文名多变体：整名、类别前段、系列后段。"""
    raw = str(value or "").strip()
    variants = {raw}
    variants.add(raw.split("(")[0])
    variants.add(raw.split(" - ")[-1])
    variants.add(re.split(r"\s+[A-Z]\s*\(", raw)[0])  # 切掉 “A (acc)” 这类份额标记
    keys = set()
    for variant in variants:
        key = _en(variant)
        if len(key) >= 5:
            keys.add(key)
        stripped = re.sub(r"[abcidef]$", "", key)  # 去掉结尾份额字母（Emerging Markets Equity A）
        if len(stripped) >= 6:
            keys.add(stripped)
    return keys


def _fetch_explorer_rows() -> list[dict[str, Any]]:
    """用浏览器上下文调用 fund-explorer 接口，返回份额记录列表。"""
    rows: list[dict[str, Any]] = []
    captured: dict[str, Any] = {}

    def _capture(response):
        """捕获站点自身请求返回的基金清单 JSON。"""
        if "fund-explorer?" in response.url and "label" not in response.url and not rows:
            captured.setdefault("url", response.url)
            captured.setdefault("headers", dict(response.request.headers))
            try:
                data = response.json()
            except Exception:  # noqa: BLE001
                return
            if isinstance(data, list) and data:
                rows.extend(data)

    with sync_playwright() as playwright:
        browser = launch_browser(playwright, headless=True)
        context = new_context(browser, locale="zh-HK")
        page = context.new_page()
        page.on("response", _capture)
        page.goto(EXPLORER_PAGE, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        page.wait_for_timeout(SETTLE_MS)
        # 站点按 fundType 分区返回不同基金池，逐个拉取并按 ISIN 去重
        headers = {
            "Accept": "application/json",
            "Referer": EXPLORER_PAGE,
        }
        seen: set[str] = set()
        for fund_type in EXPLORER_FUND_TYPES:
            url = f"{BASE_URL}{EXPLORER_API}&fundType={fund_type}"
            try:
                response = context.request.get(url, headers=headers, timeout=PAGE_TIMEOUT_MS)
            except Exception:  # noqa: BLE001 - 单个分区失败不应中断整体
                continue
            if not response.ok:
                continue
            try:
                data = response.json()
            except Exception:  # noqa: BLE001
                continue
            for item in data or []:
                isin = str(item.get("identifier") or "").strip().upper()
                if isin and isin not in seen:
                    seen.add(isin)
                    rows.append(item)
        if not rows:  # 兜底：用站点自己那次请求的响应
            for _ in range(4):
                if rows:
                    break
                page.wait_for_timeout(SETTLE_MS)
        browser.close()
    if not rows:
        raise RuntimeError("未能取到摩根 fund-explorer 基金清单，请检查站点接口是否变更")
    return rows


def _fetch_monthly_isins() -> set[str]:
    """抓“基金文件”页内嵌 JSON，返回拥有月报（Fact Sheet）的 ISIN 集合。"""
    response = requests.get(
        DOCUMENTS_PAGE, headers={"User-Agent": "Mozilla/5.0"}, timeout=TIMEOUT
    )
    response.raise_for_status()
    text = response.text
    marker = "documents = "
    start = text.find(marker)
    if start < 0:
        raise RuntimeError("摩根基金文件页未找到 documents JSON，请检查页面结构是否变更")
    start += len(marker)
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                documents = json.loads(text[start : index + 1])
                break
    else:
        raise RuntimeError("摩根基金文件页 documents JSON 解析失败")
    isins = set()
    for isin, entries in documents.items():
        for entry in entries or []:
            if MONTHLY_DOC_TYPE in str(entry.get("documentType") or ""):
                isins.add(str(isin).upper())
                break
    return isins


def _slug(display_name: str, isin: str) -> str:
    """按官网规律拼产品页 slug（displayName 小写连字符 + ISIN 小写）。"""
    stem = re.sub(r"[^a-z0-9]+", "-", str(display_name).lower()).strip("-")
    return f"{stem}-{isin.lower()}"


def _fetch_catalog() -> list[dict[str, Any]]:
    """返回官网基金目录（按基金聚合份额，保留 港元>美元>其他 的一条）。"""
    monthly_isins = _fetch_monthly_isins()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in _fetch_explorer_rows():
        isin = str(row.get("identifier") or "").strip().upper()
        if not isin:
            continue
        key = str(row.get("fundIdentifier") or row.get("displayName") or isin)
        grouped.setdefault(key, []).append(
            {
                "cn": str(row.get("displayNameTranslatedName") or "").strip(),
                "en": str(row.get("displayName") or "").strip(),
                "isin": isin,
                "currency": str(row.get("currencyCode") or "").strip(),
            }
        )
    catalog = []
    for classes in grouped.values():
        # 同一基金多份额：优先“有月报”的份额，其次 港元>美元>其他
        classes.sort(
            key=lambda item: (
                0 if item["isin"] in monthly_isins else 1,
                CURRENCY_RANK.get(item["currency"], 2),
            )
        )
        item = classes[0]
        catalog.append(
            {
                **item,
                "has_monthly": item["isin"] in monthly_isins,
                "page_url": PRODUCT_URL.format(slug=_slug(item["en"], item["isin"])),
            }
        )
    if not catalog:
        raise RuntimeError("摩根基金目录为空，请检查站点接口是否变更")
    return catalog


def _build_index(catalog: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """建立 (语言, 核心名变体) → 基金记录 的索引。"""
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for record in catalog:
        for key in _cn_keys(record["cn"]):
            index.setdefault(("cn", key), record)
        for key in _en_keys(record["en"]):
            index.setdefault(("en", key), record)
    return index


def _resolve_single(
    row: dict[str, Any], index: dict[tuple[str, str], dict[str, Any]]
) -> tuple[str, str, str | None]:
    """解析一支基金，返回 (I月报链接, J运行方式, 跳过原因)；不认领时原因为 None。"""
    umbrella = str(row.get("umbrella") or "").strip()
    fund_text = f"{umbrella}{row.get('fund') or ''}" if umbrella else str(row.get("fund") or "")
    record = None
    for key in _cn_keys(fund_text):
        record = index.get(("cn", key))
        if record:
            break
    for key in _en_keys(str(row.get("english") or "")) if not record else ():
        record = index.get(("en", key))
        if record:
            break
    if not record:
        # 兜底：核心名互相包含（取最长的目录键，降低误配概率）
        candidates = []
        for key in _cn_keys(fund_text):
            for index_key, candidate in index.items():
                language, normalized = index_key
                if language != "cn" or normalized == key:
                    continue
                if key in normalized or normalized in key:
                    candidates.append((len(normalized), candidate))
        if candidates:
            record = sorted(candidates, key=lambda item: item[0], reverse=True)[0][1]
    if not record:
        return "", "", "官网基金清单未收录该基金"
    if not record.get("has_monthly"):
        return "", "", "官网该基金无月报文件（Fact Sheet）"
    link = record["page_url"]
    return link, f'python download_jpmorgan_documents.py "{link}"', ""


def main(argv: list[str] | None = None) -> int:
    """命令行入口：单基金查询 / --probe / --backfill。"""
    return cli_main(
        argv,
        description="按爬虫list.xlsx摩根表 B 列基金名查询官网月报链接（含批量补全）",
        sheet_name=SHEET_NAME,
        catalog_loader=_fetch_catalog,
        index_builder=_build_index,
        resolver=_resolve_single,
        single_print_run_command=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
