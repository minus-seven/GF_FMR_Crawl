#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
贝莱德香港基金月报爬虫补全脚本。

以“爬虫list.xlsx”贝莱德表的 B 列基金名（结合 C 伞）为索引，在贝莱德香港官网
产品目录中精确匹配基金，按 港元 > 美元 > 其他、同币种默认第一条 选股份类别，
再打开产品页取“查看所有文件”链接，输出要回填的 I（月报链接）与 J（运行方式）。

通用流程（Excel 扫描、进度条、probe/backfill、命令行入口）都在
code/common/fund_link_backfill.py；本文件只保留贝莱德官网特有的部分：
官网目录抓取、基金名候选规则、产品页文件链接解析、J 列短参数。

用法：
  python blackrock_fund_link.py 环球资产配置基金
  python blackrock_fund_link.py --probe --excel /path/to/爬虫list.xlsx --sheet 贝莱德
  python blackrock_fund_link.py --backfill --excel /path/to/爬虫list.xlsx --sheet 贝莱德
"""

from __future__ import annotations

import html as html_mod
import os
import re
import sys
from typing import Any
from urllib.parse import unquote, urljoin

import requests
from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common.fund_link_backfill import (  # noqa: E402
    build_index,
    cli_main,
    normalize_name,
    pick_share_class,
)


SHEET_NAME = "贝莱德"
LIST_URL = "https://www.blackrock.com/hk/zh/products/products-list"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
TIMEOUT = 30
# 薈萃伞的标准化 key，用于判断是否属于“贝莱德荟萃基金”
PREMIER_UMBRELLA_KEY = normalize_name("贝莱德荟萃基金")


def _clean_cell(raw: str) -> str:
    """去掉单元格里的 HTML 标签并还原实体，返回去空白文本。"""
    text = re.sub(r"<[^>]+>", "", raw)
    return html_mod.unescape(text).strip()


def _fetch_catalog() -> list[dict[str, Any]]:
    """抓取贝莱德官网基金目录：每支基金/股份类别一条记录。"""
    session = requests.Session()
    session.trust_env = False
    resp = session.get(LIST_URL, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    table_match = re.search(
        r'<table class="products-table">(.*?)</table>', resp.text, re.S
    )
    if not table_match:
        raise RuntimeError("未能在 BlackRock 产品列表页找到基金目录表")
    records: list[dict[str, Any]] = []
    for row_chunk in re.split(r"<tr[^>]*>", table_match.group(1)):
        if "</tr>" not in row_chunk:
            continue
        row = row_chunk.split("</tr>")[0]
        hrefs = re.findall(r'<a[^>]*href="([^"]*)"', row)
        page_path = ""
        product_id: str | None = None
        for href in hrefs:
            product_match = re.search(r"/products/(\d+)/", href)
            if product_match:
                product_id = product_match.group(1)
                page_path = href
                break
        if product_id is None:
            continue
        cells = [
            _clean_cell(match.group(1))
            for match in re.finditer(r"<(?:th|td)[^>]*>(.*?)</(?:th|td)>", row, re.S)
        ]
        if len(cells) < 5:
            continue
        records.append(
            {
                "product_id": product_id,
                "page_path": page_path,
                "code": cells[0],
                "fund_name": cells[1],
                "share_class": cells[2],
                "currency": cells[3],
                "isin": cells[4],
            }
        )
    if not records:
        raise RuntimeError("BlackRock 基金目录解析结果为空")
    return records


def _site_name_candidates(fund_name: str, umbrella: str) -> list[str]:
    """根据表格的基金名和伞名生成官网显示名候选（繁体）。"""
    base = normalize_name(fund_name)
    if not base:
        return []
    candidates: list[str] = []
    core = base
    for prefix in ("貝萊德", "iShares安碩", "iShares", "安碩"):
        if core.startswith(prefix):
            core = core[len(prefix):]
            break
    if normalize_name(umbrella) == PREMIER_UMBRELLA_KEY:
        # 薈萃伞下官网名通常带“貝萊德薈萃”，iShares 等以原名展示
        candidates.append("貝萊德薈萃" + core)
        if base.startswith("iShares"):
            candidates.append(base)
        candidates.append("貝萊德" + core)
    elif base.startswith("貝萊德") or base.startswith("iShares"):
        candidates.append(base)
    else:
        candidates.append("貝萊德" + core)
    result: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in result:
            result.append(candidate)
    return result


def _literature_url_from_product_page(product_page_url: str) -> str:
    """进入基金产品页，等待“查看所有文件”链接出现并返回其绝对地址。"""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(
                user_agent=HEADERS["User-Agent"], ignore_https_errors=True
            )
            page.goto(product_page_url, wait_until="domcontentloaded", timeout=60000)
            # 文案可能是中文也可能是英文，统一按 href 特征识别：
            # 带 productIdentifier 且不是 Key Fact Statement 的链接即“查看所有文件”。
            anchor = page.locator(
                'a[href*="productIdentifier"]:not([href*="MaterialType"])'
            ).first
            anchor.wait_for(state="visible", timeout=30000)
            href = anchor.get_attribute("href")
            return urljoin(product_page_url, str(href)) if href else ""
        finally:
            browser.close()


def _short_j_argument(literature_url: str) -> str:
    """从月报链接中提取“ISIN~产品ID”短参数（省去中间的产品代码）。"""
    match = re.search(r"productIdentifier=([^&\s]+)", literature_url or "")
    identifier = unquote(match.group(1)) if match else ""
    tokens = [token for token in identifier.split("~") if token]
    if len(tokens) >= 2:
        return f"{tokens[0]}~{tokens[-1]}"
    return identifier


def _blackrock_j_missing_argument(link: str, run_command: str) -> bool:
    """判断运行方式是否只有脚本名、缺少产品参数（短参数或链接）。"""
    text = str(run_command or "").strip()
    if "download_blackrock_factsheet.py" not in text.lower():
        return False
    return not ("http://" in text.lower() or "https://" in text.lower() or "~" in text)


def _build_index(catalog: list[dict[str, Any]]) -> dict[str, Any]:
    """按归一化基金名建立贝莱德目录索引。"""
    return build_index(catalog, key="fund_name")


def _resolve_single(
    row: dict[str, Any], index: dict[str, Any]
) -> tuple[str, str, str]:
    """解析一支基金，返回 (I月报链接, J运行方式, 跳过原因)。"""
    fund_name = str(row.get("fund") or "")
    umbrella = str(row.get("umbrella") or "")
    for candidate in _site_name_candidates(fund_name, umbrella):
        matched_rows = index.get(candidate)
        if not matched_rows:
            continue
        chosen = pick_share_class(matched_rows)
        product_page_url = "https://www.blackrock.com" + chosen["page_path"]
        link = _literature_url_from_product_page(product_page_url)
        if not link:
            return "", "", "官网未找到月报文件，跳过"
        short_arg = _short_j_argument(link)
        run_command = (
            f"python download_blackrock_factsheet.py {short_arg}" if short_arg else ""
        )
        return link, run_command, ""
    return "", "", "官网未收录，跳过"


def main(argv: list[str] | None = None) -> int:
    """命令行入口：单基金查询 / --probe / --backfill。"""
    return cli_main(
        argv,
        description="按爬虫list.xlsx贝莱德表 B 列基金名查询官网月报链接（含批量补全）",
        sheet_name=SHEET_NAME,
        catalog_loader=_fetch_catalog,
        index_builder=_build_index,
        resolver=_resolve_single,
        extra_check=_blackrock_j_missing_argument,
    )


if __name__ == "__main__":
    raise SystemExit(main())
