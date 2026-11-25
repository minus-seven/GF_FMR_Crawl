#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
瑞银（UBS 香港）基金月报爬虫补全脚本。

目录来源：官网基金表（对简单 HTTP 客户端不友好，需真实浏览器渲染）
  https://www.ubs.com/hk/tc/assetmanagement/funds/asset-class.html
表格 `#app table tbody tr` 每行：ISIN｜基金名(含份额后缀)｜日期｜币种｜淨值｜
基金章程｜基金月報｜產品資料概要｜年報｜中期報告。

匹配：中文核心名（去“瑞銀/UBS”、份额括注与 A類/B類 等类别标记）优先 → 英文名兜底。
同一基金多行（不同份额/ISIN）：优先有月报的行，其次 港元>美元>其他。
I = 基金表页 URL
J = python download_ubs_factsheet.py <ISIN> "基金月報"

用法：
  python ubs_fund_link.py 瑞银中国精选股票基金
  python ubs_fund_link.py --probe --excel /path/to/爬虫list.xlsx --sheet 瑞银
  python ubs_fund_link.py --backfill --excel /path/to/爬虫list.xlsx --sheet 瑞银
"""

from __future__ import annotations

import os
import re
import sys
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
sys.path.insert(0, HERE)

from common.browser_fetch import launch_browser, new_context  # noqa: E402
from common.fund_link_backfill import cli_main, normalize_name  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402


SHEET_NAME = "瑞银"
PAGE_URL = "https://www.ubs.com/hk/tc/assetmanagement/funds/asset-class.html"
MONTHLY_LABEL = "基金月報"
DOC_LABEL = "基金月報"
PAGE_TIMEOUT_MS = 90000
ISIN_RE = re.compile(r"\b([A-Z]{2}[A-Z0-9]{9}\d)\b")
CURRENCY_RANK = {"港元": 0, "港币": 0, "HKD": 0, "美元": 1, "USD": 1}


def _cn(value: str) -> str:
    """中文核心名：简繁统一、去括注/类别标记/品牌词与符号。"""
    text = normalize_name(value)
    while True:
        trimmed = re.sub(r"\([^()]*\)$", "", text)
        if trimmed == text:
            break
        text = trimmed
    text = re.sub(r"\([^()]*\)", "", text)
    for token in ("瑞銀", "瑞银", "UBS", "ubs"):
        text = text.replace(token, "")
    for token in ("A類", "B類", "C類", "D類", "I類", "P類", "A类", "B类", "C类", "D类", "I类", "P类",
                  "累積", "累計", "派息", "分派", "港元", "美元", "歐元", "人民币", "人民幣"):
        text = text.replace(token, "")
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", text)


def _en(value: str) -> str:
    """英文名归一化：去 UBS 与括注，仅保留字母数字。"""
    text = str(value or "").strip().lower().replace("ubs", "")
    text = re.sub(r"\([^()]*\)", "", text)
    for token in ("lux", "sicav", "fund", "funds", "series"):
        text = text.replace(token, "")
    return re.sub(r"[^a-z0-9]", "", text)


def _fetch_catalog() -> list[dict[str, Any]]:
    """渲染官网基金表，返回按基金聚合后的目录（含月报可用性）。"""
    rows_data: list[dict[str, Any]] = []
    with sync_playwright() as playwright:
        browser = launch_browser(playwright, headless=True)
        context = new_context(browser, locale="zh-HK")
        page = context.new_page()
        page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        page.wait_for_selector("#app table tbody tr", timeout=PAGE_TIMEOUT_MS)
        rows = page.locator("#app table tbody tr")
        for index in range(rows.count()):
            row = rows.nth(index)
            cells = row.locator("td")
            if cells.count() < 3:
                continue
            text = row.inner_text(timeout=5000)
            isin_match = ISIN_RE.search(text)
            if not isin_match:
                continue
            has_monthly = False
            for cell_index in range(cells.count()):
                cell = cells.nth(cell_index)
                label = cell.inner_text(timeout=3000).strip()
                anchor = cell.locator("a").first
                if anchor.count() and DOC_LABEL in label and (anchor.get_attribute("href") or "").lower().endswith(".pdf"):
                    has_monthly = True
                    break
            rows_data.append(
                {
                    "isin": isin_match.group(1).upper(),
                    "name": cells.nth(1).inner_text(timeout=3000).strip(),
                    "currency": cells.nth(3).inner_text(timeout=3000).strip() if cells.count() > 3 else "",
                    "has_monthly": has_monthly,
                }
            )
        browser.close()
    if not rows_data:
        raise RuntimeError("UBS 基金表未取到数据：页面结构变化或站点拦截")

    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in rows_data:
        grouped.setdefault(_cn(item["name"]), []).append(item)
    catalog: list[dict[str, Any]] = []
    for core, items in grouped.items():
        items.sort(
            key=lambda item: (
                0 if item["has_monthly"] else 1,
                CURRENCY_RANK.get(item["currency"], 2),
            )
        )
        best = items[0]
        catalog.append(
            {
                "core": core,
                "name": best["name"],
                "isin": best["isin"],
                "has_monthly": best["has_monthly"],
            }
        )
    return catalog


def _build_index(catalog: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """按中文核心名建立索引。"""
    index: dict[str, dict[str, Any]] = {}
    for record in catalog:
        index.setdefault(record["core"], record)
    return index


def _resolve_single(
    row: dict[str, Any], index: dict[str, dict[str, Any]]
) -> tuple[str, str, str | None]:
    """解析一支基金，返回 (I月报链接, J运行方式, 跳过原因)；不认领时原因为 None。"""
    umbrella = str(row.get("umbrella") or "").strip()
    keys = [_cn(str(row.get("fund") or ""))]
    if umbrella:
        keys.append(_cn(f"{umbrella}{row.get('fund') or ''}"))
    record = next((index[key] for key in keys if key in index), None)
    if not record:
        for key in keys:
            if len(key) < 4:
                continue
            for index_key, candidate in index.items():
                if len(index_key) >= 4 and (key in index_key or index_key in key):
                    record = candidate
                    break
            if record:
                break
    if not record:
        return "", "", "官网基金表未收录该基金"
    if not record.get("has_monthly"):
        return "", "", f"官网该基金无「{MONTHLY_LABEL}」文件"
    return PAGE_URL, f'python download_ubs_factsheet.py {record["isin"]} "{MONTHLY_LABEL}"', ""


def main(argv: list[str] | None = None) -> int:
    """命令行入口：单基金查询 / --probe / --backfill。"""
    return cli_main(
        argv,
        description="按爬虫list.xlsx瑞银表 B 列基金名查询官网月报链接（含批量补全）",
        sheet_name=SHEET_NAME,
        catalog_loader=_fetch_catalog,
        index_builder=_build_index,
        resolver=_resolve_single,
        single_print_run_command=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
