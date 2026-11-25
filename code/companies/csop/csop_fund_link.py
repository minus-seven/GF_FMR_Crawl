#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
南方东英（CSOP）基金月报爬虫补全脚本。

目录来源：官网英文产品分类页（服务端对 requests 返回 403，必须走真实浏览器）
  https://www.csopasset.com/en/products/etf?FundName=<分类>&type=ETF
分类页表格里的产品链接形如 https://www.csopasset.com/en/products/<slug>，
抓取各分类页即可得到「产品名 ↔ 产品页 URL」全量目录（实测 71 个产品）。

匹配：页表“英文”列 ↔ 官网产品名（去 CSOP/ETF/基金 等词、仅留字母数字后的包含匹配）。
I = 产品页 URL
J = python download_csop_factsheet.py "<I>" "<官网产品名>"
     （第二个参数是下载脚本用来定位“Monthly Newsletter”的基金名，这里用官网自己
       的名字，比页表英文名更稳）

用法：
  python csop_fund_link.py "CSOP Select US Dollar Bond Fund"
  python csop_fund_link.py --probe --excel /path/to/爬虫list.xlsx --sheet 南方
  python csop_fund_link.py --backfill --excel /path/to/爬虫list.xlsx --sheet 南方
"""

from __future__ import annotations

import os
import re
import sys
from typing import Any

import openpyxl

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
sys.path.insert(0, HERE)

from common.browser_fetch import launch_browser, new_context  # noqa: E402
from common.fund_link_backfill import cli_main  # noqa: E402
from download_csop_factsheet import latest_monthly_newsletter  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402


SHEET_NAME = "南方东英"
BASE_URL = "https://www.csopasset.com"
CATEGORY_URL = BASE_URL + "/en/products/etf?FundName={name}&type={kind}"
# 实测有产品的分类（name, type）
CATEGORIES = (
    ("Commodity%20ETFs", "ETF"),
    ("Covered%20Call%20ETFs", "ETF"),
    ("Thematic%20ETFs", "ETF"),
    ("Regional%20ETFs", "ETF"),
    ("Fixed%20Income%20ETFs", "ETF"),
    ("Money%20Market%20ETFs", "ETF"),
    ("HK%20Index%20ETFs", "ETF"),
    ("HK%20Index%20L%26I%20Products", "ETF"),
    ("A-Share%20Index%20L%26I%20Products", "ETF"),
    ("US%20Index%20L%26I%20Products", "ETF"),
    ("Japan%20Index%20L%26I%20Products", "ETF"),
    ("Commodity%20L%26I%20Products", "ETF"),
    ("Virtual%20Asset%20Futures%20L%26I%20Products", "ETF"),
    ("Single%20Stock%20L%26I%20Products", "ETF"),
    ("Hong%20Kong%20Authorized%20Funds", "FUND"),
    ("Mainland%20-%20Hong%20Kong%20Mutual%20Recognition%20of%20Funds", "FUND"),
)
PRODUCT_LINK_RE = re.compile(
    r'<a[^>]+href="(https://www\.csopasset\.com/en/products/[^"#?]+)"[^>]*>(.*?)</a>',
    re.S,
)
PAGE_TIMEOUT_MS = 60000
SETTLE_MS = 5000
# 复用下载脚本的浏览器检查（判断该产品是否有 Monthly Newsletter），按产品名缓存结果
_PAGE_STATE: dict[str, Any] = {}
_MONTHLY_CACHE: dict[str, bool] = {}
KNOWN_PRODUCT_PAGES = {
    "CSOP Hong Kong Dollar Money Market ETF": BASE_URL + "/en/products/money-market-etf",
    "CSOP US Dollar Money Market ETF": BASE_URL + "/en/products/money-market-etf",
    "CSOP RMB Money Market ETF": BASE_URL + "/en/products/money-market-etf",
    "CSOP Select US Dollar Bond Fund": BASE_URL + "/en/products/us-dollar-bond",
    "CSOP Enhanced Bond Fund": BASE_URL + "/en/products/hk-cehb",
    "China New Balance Opportunity Fund": BASE_URL + "/en/products/china-new-balance",
}


def _page():
    """惰性创建一个常驻浏览器页（整个补全进程复用，进程结束由系统回收）。"""
    if not _PAGE_STATE:
        playwright = sync_playwright().start()
        browser = launch_browser(playwright, headless=True)
        context = new_context(browser, locale="en-US")
        page = context.new_page()
        page.goto(BASE_URL + "/en/products", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        _PAGE_STATE.update(playwright=playwright, browser=browser, page=page)
    return _PAGE_STATE["page"]


def _has_monthly(product_name: str) -> bool:
    """判断官网是否给出该产品的 Monthly Newsletter（ETF 类通常没有）。"""
    if product_name not in _MONTHLY_CACHE:
        try:
            _MONTHLY_CACHE[product_name] = bool(latest_monthly_newsletter(_page(), product_name))
        except Exception:  # noqa: BLE001 - 判定失败按“无月报”处理，避免误回填
            _MONTHLY_CACHE[product_name] = False
    return _MONTHLY_CACHE[product_name]


def _en(value: str) -> str:
    """英文名归一化：去 CSOP/ETF/基金 等通用词，仅保留字母数字。"""
    text = str(value or "").strip().lower()
    for token in ("csop", "etf", "fund", "index", "active", "synthetic", "this is a"):
        text = text.replace(token, "")
    text = re.sub(r"\([^()]*\)", "", text)
    return re.sub(r"[^a-z0-9]", "", text)


def _fetch_catalog() -> list[dict[str, Any]]:
    """用浏览器逐个分类页抓取产品目录（产品名 + URL）。"""
    catalog: dict[str, str] = {}
    with sync_playwright() as playwright:
        browser = launch_browser(playwright, headless=True)
        context = new_context(browser, locale="en-US")
        page = context.new_page()
        for name, kind in CATEGORIES:
            url = CATEGORY_URL.format(name=name, kind=kind)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
                page.wait_for_timeout(SETTLE_MS)
                html = page.content()
            except Exception:  # noqa: BLE001 - 单个分类失败不影响其余分类
                continue
            for href, inner in PRODUCT_LINK_RE.findall(html):
                product = " ".join(re.sub(r"<[^>]+>", " ", inner).split())
                if href.rstrip("/").endswith("/etf") or not product or len(product) <= 6:
                    continue
                catalog.setdefault(href, product)
        browser.close()
    if not catalog:
        raise RuntimeError("CSOP 产品目录为空：页面结构变化或站点拦截（需真实浏览器渠道）")
    return [{"fund_name": name, "page_url": url} for url, name in catalog.items()]


def _build_index(catalog: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """按归一化产品名建立索引。"""
    index: dict[str, dict[str, Any]] = {}
    for record in catalog:
        index.setdefault(_en(record["fund_name"]), record)
    return index


def _stable_catalog_page(row: dict[str, Any]) -> str:
    """生成官网目录页兜底入口；参数 row 为爬虫表行，英文名用于判断 ETF/基金类别。"""
    english = str(row.get("english") or "").lower()
    if "fund" in english and "etf" not in english and "product" not in english:
        category = "Hong%20Kong%20Authorized%20Funds"
        kind = "FUND"
    else:
        category = ""
        if "money market" in english:
            category = "Money%20Market%20ETFs"
        elif "commodity" in english or "gold" in english:
            category = "Commodity%20ETFs"
        elif "bond" in english or "treasury" in english:
            category = "Fixed%20Income%20ETFs"
        elif "covered call" in english:
            category = "Covered%20Call%20ETFs"
        elif "leveraged" in english or "inverse" in english or "daily" in english:
            category = ""
        else:
            category = ""
        kind = "ETF"
    return CATEGORY_URL.format(name=category, kind=kind)


def _resolve_single(
    row: dict[str, Any], index: dict[str, dict[str, Any]]
) -> tuple[str, str, str | None]:
    """解析一支基金，返回 (I月报链接, J运行方式, 跳过原因)；不认领时原因为 None。"""
    key = _en(str(row.get("english") or ""))
    record = index.get(key)
    if not record and len(key) >= 6:
        # 官网名与页表名常有补充词差异，仅接受“前缀包含”（避免 Enhanced Bond 这类误配）
        for index_key, candidate in index.items():
            if len(index_key) < 6:
                continue
            if key.startswith(index_key) or index_key.startswith(key):
                record = candidate
                break
    if not record:
        # 分类目录本身是稳定入口；即使 CDN 暂时阻断产品清单，也可由下载器
        # 在浏览器会话内按 productName 查询报告。这里不伪造具体产品 slug。
        link = _stable_catalog_page(row)
        return link, f'python download_csop_factsheet.py "{link}" "{row.get("english", "")}" monthly', ""
    link = record["page_url"]
    return link, f'python download_csop_factsheet.py "{link}" "{record["fund_name"]}" monthly', ""


def _parse_option(argv: list[str], name: str, default: str) -> str:
    """读取简单命令行选项；参数 argv 为参数列表，name 为选项名，default 为默认值。"""
    try:
        index = argv.index(name)
    except ValueError:
        return default
    return argv[index + 1] if index + 1 < len(argv) else default


def _write_all_report_sources(
    excel_path: str,
    sheet_name: str,
    catalog: list[dict[str, Any]],
    row_numbers: set[int] | None = None,
) -> int:
    """写入南方东英全部基金的三类稳定产品页和运行方式；参数为工作簿、表名、目录及可选行号。"""
    workbook = openpyxl.load_workbook(excel_path)
    if sheet_name not in workbook.sheetnames:
        workbook.close()
        return 0
    sheet = workbook[sheet_name]
    header = {
        str(cell.value).strip(): cell.column
        for cell in sheet[1]
        if cell.value not in (None, "")
    }
    required = {"基金", "月报链接", "运行方式", "半年报链接", "半年报运行方式", "年报链接", "年报运行方式"}
    if not required.issubset(header):
        workbook.close()
        raise RuntimeError(f"南方东英表缺少字段：{'、'.join(sorted(required - set(header)))}")
    index = _build_index(catalog)
    updated = 0
    for excel_row in range(2, sheet.max_row + 1):
        if row_numbers and excel_row not in row_numbers:
            continue
        fund = str(sheet.cell(excel_row, header["基金"]).value or "").strip()
        english = str(sheet.cell(excel_row, header.get("英文", 0)).value or "").strip() if header.get("英文") else ""
        if not fund:
            continue
        link, monthly_command, reason = _resolve_single({"fund": fund, "english": english}, index)
        if not link:
            record = next((item for name, item in KNOWN_PRODUCT_PAGES.items() if _en(name) == _en(english)), None)
            if record:
                link = record
                monthly_command = f'python download_csop_factsheet.py "{link}" "{english}" monthly'
        if not link:
            continue
        sheet.cell(excel_row, header["月报链接"]).value = link
        sheet.cell(excel_row, header["运行方式"]).value = monthly_command
        sheet.cell(excel_row, header["半年报链接"]).value = link
        sheet.cell(excel_row, header["半年报运行方式"]).value = f'python download_csop_factsheet.py "{link}" "{english}" semiannual'
        sheet.cell(excel_row, header["年报链接"]).value = link
        sheet.cell(excel_row, header["年报运行方式"]).value = f'python download_csop_factsheet.py "{link}" "{english}" annual'
        updated += 1
    workbook.save(excel_path)
    workbook.close()
    return updated


def main(argv: list[str] | None = None) -> int:
    """命令行入口：单基金查询 / --probe / --backfill。"""
    args = list(sys.argv[1:] if argv is None else argv)
    result = cli_main(
        args,
        description="按爬虫list.xlsx南方表 B 列基金名查询官网产品页链接（含批量补全）",
        sheet_name=SHEET_NAME,
        catalog_loader=_fetch_catalog,
        index_builder=_build_index,
        resolver=_resolve_single,
        single_print_run_command=True,
    )
    if "--backfill" in args:
        excel_path = _parse_option(args, "--excel", os.path.join(HERE, "..", "..", "..", "爬虫list.xlsx"))
        sheet_name = _parse_option(args, "--sheet", SHEET_NAME)
        raw_rows = _parse_option(args, "--rows", "")
        row_numbers = {int(value) for value in raw_rows.split(",") if value.strip().isdigit()}
        try:
            catalog = _fetch_catalog()
        except Exception:
            catalog = [{"fund_name": name, "page_url": page} for name, page in KNOWN_PRODUCT_PAGES.items()]
        updated = _write_all_report_sources(excel_path, sheet_name, catalog, row_numbers or None)
        print(f"[*] 南方东英月报/半年报/年报来源字段已写入 {updated} 行", flush=True)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
