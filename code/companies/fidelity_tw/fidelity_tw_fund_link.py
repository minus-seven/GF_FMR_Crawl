#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
富达台湾基金月报爬虫补全脚本（TW 站）。

以“爬虫list.xlsx”富达表的 B 列基金名（结合 C 伞）为索引，在富达台湾官网
基金列表中匹配基金，输出要回填的 I（基金文件页链接）与 J（运行方式）：
  I = https://www.fidelity.com.tw/fund-and-service/fundui/funds/<slug>/documents
  J = python download_fidelity_tw_factsheet.py "<I>"

目录来源：渲染 https://www.fidelity.com.tw/fund-and-service/fundui/funds/
（真实浏览器渠道），解析每个股份类别的链接与名称；同一基金多类别时按
港元 > 美元 > 其他、默认第一条选择，并渲染文档页确认确有月报。

本脚本只“认领”能在 TW 目录匹配到的基金；匹配不到时返回“不认领”，
留给 HK 脚本或由 run_all 汇总为跳过项。

用法：
  python fidelity_tw_fund_link.py "富达基金 - 环球焦点基金"
  python fidelity_tw_fund_link.py --probe --excel /path/to/爬虫list.xlsx --sheet 富达
  python fidelity_tw_fund_link.py --backfill --excel /path/to/爬虫list.xlsx --sheet 富达
"""

from __future__ import annotations

import os
import re
import sys
from typing import Any

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common.browser_fetch import (  # noqa: E402
    is_access_denied,
    launch_browser,
    new_context,
)
from common.fund_link_backfill import (  # noqa: E402
    cli_main,
    normalize_name,
)
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402


SHEET_NAME = "富达"
BASE_URL = "https://www.fidelity.com.tw"
LIST_PAGE = f"{BASE_URL}/fund-and-service/fundui/funds/"
TIMEOUT_MS = 60000
# 名称中出现的货币关键字（用于币种优先级）
CURRENCY_KEYWORDS = (
    "港元",
    "美元",
    "歐元",
    "日圓",
    "英鎊",
    "澳元",
    "人民幣",
    "新臺幣",
    "加元",
    "紐元",
)
_REPORT_CACHE: dict[str, bool] = {}


def _extract_currency(class_text: str) -> str:
    """从股份类别文本中提取货币关键字。"""
    for token in CURRENCY_KEYWORDS:
        if token in class_text:
            return token
    return ""


def _fetch_catalog() -> list[dict[str, Any]]:
    """渲染 TW 基金列表页，解析每个股份类别的名称与链接 slug。"""
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        context = new_context(browser, locale="zh-TW")
        page = context.new_page()
        try:
            page.goto(LIST_PAGE, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
            if is_access_denied(page):
                raise RuntimeError("富达台湾站点返回 Access Denied，请确认已安装 Chrome/Edge")
            page.wait_for_timeout(6000)
            pairs = page.eval_on_selector_all(
                "a[href*='/fundui/funds/']",
                """as => as.map(a => ({t: (a.innerText || '').trim(), h: a.getAttribute('href')}))
                        .filter(x => x.h && !x.h.includes('watchlist')
                                     && !x.h.includes('compare')
                                     && !x.h.includes('dividendrecords'))""",
            )
        finally:
            browser.close()
    records: list[dict[str, Any]] = []
    for pair in pairs:
        text = re.sub(r"\s+", " ", pair.get("t") or "").strip()
        href = (pair.get("h") or "").strip()
        if not text or not href:
            continue
        parts = re.split(r"[（(]", text, maxsplit=1)
        fund_name = parts[0].strip()
        class_text = parts[1].rstrip("）)").strip() if len(parts) > 1 else ""
        records.append(
            {
                "fund_name": fund_name,
                "class_text": class_text,
                "currency": _extract_currency(class_text),
                "slug": href.rstrip("/"),
            }
        )
    if not records:
        raise RuntimeError("富达台湾基金目录为空，请检查页面结构")
    return records


def _build_index(catalog: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """按归一化基金名建立索引。"""
    index: dict[str, list[dict[str, Any]]] = {}
    for record in catalog:
        index.setdefault(_norm(record["fund_name"]), []).append(record)
    return index


def _norm(value: str) -> str:
    """富达专用归一化：在公共规则上再去掉连字符/破折号等分隔符。"""
    text = normalize_name(value)
    for token in ("-", "－", "–", "—", "−", "─", "〜", "~"):
        text = text.replace(token, "")
    return text


def _name_variants(fund_name: str, umbrella: str) -> list[str]:
    """生成匹配用名称变体：全名、去掉伞前缀、去掉尾部括号说明。"""
    base = _norm(fund_name)
    umbrella_key = _norm(umbrella)
    variants: list[str] = [base]
    if umbrella_key and base.startswith(umbrella_key) and len(base) > len(umbrella_key):
        variants.append(base[len(umbrella_key):])
    for text in list(variants):
        trimmed = re.sub(r"\([^()]*\)$", "", text)
        if trimmed and trimmed not in variants:
            variants.append(trimmed)
    return [item for item in variants if item]


def _has_monthly_report(slug: str) -> bool:
    """渲染文档页，确认存在 HFFS 月报链接。"""
    if slug in _REPORT_CACHE:
        return _REPORT_CACHE[slug]
    ok = False
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        context = new_context(browser, locale="zh-TW")
        page = context.new_page()
        try:
            page.goto(f"{BASE_URL}{slug}/documents", wait_until="domcontentloaded", timeout=TIMEOUT_MS)
            if not is_access_denied(page):
                try:
                    page.wait_for_selector("a[href*='hffs.']", timeout=15000)
                except PlaywrightTimeoutError:
                    pass
                links = page.eval_on_selector_all(
                    "a[href]",
                    "as => as.map(a => a.href || a.getAttribute('href') || '')",
                )
                ok = any(
                    "hffs." in str(link).lower() and ".pdf" in str(link).lower()
                    for link in links
                )
        finally:
            browser.close()
    _REPORT_CACHE[slug] = ok
    return ok


def _resolve_single(row: dict[str, Any], index) -> tuple[str, str, str | None]:
    """解析一支基金，返回 (I, J, 跳过原因)；不认领时原因为 None。"""
    fund_name = str(row.get("fund") or "")
    umbrella = str(row.get("umbrella") or "")
    candidates: list[dict[str, Any]] = []
    for key in _name_variants(fund_name, umbrella):
        candidates.extend(index.get(key) or [])
    if not candidates:
        return "", "", None
    for record in sorted(
        candidates,
        key=lambda item: (
            0 if item.get("currency") == "港元" else 1 if item.get("currency") == "美元" else 2
        ),
    ):
        slug = record["slug"]
        if _has_monthly_report(slug):
            link = f"{BASE_URL}{slug}/documents"
            return link, f'python download_fidelity_tw_factsheet.py "{link}"', ""
    return "", "", "官网暂未发布月报，跳过"


def main(argv: list[str] | None = None) -> int:
    """命令行入口：单基金查询 / --probe / --backfill。"""
    return cli_main(
        argv,
        description="按爬虫list.xlsx富达表 B 列基金名查询富达台湾月报链接（含批量补全）",
        sheet_name=SHEET_NAME,
        catalog_loader=_fetch_catalog,
        index_builder=_build_index,
        resolver=_resolve_single,
        single_print_run_command=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
