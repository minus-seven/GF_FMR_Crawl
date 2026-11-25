#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""中金香港资产管理基金月报链接补全脚本。

I 列保存中金产品文件页，而不是会随发布日期变化的 PDF 直链；J 列保存
“重新读取页面并选择最新上市类别月报”的命令。半年报使用同一个产品页，
由公司页表的“半年报链接/半年报运行方式”列单独登记。

用法：
  python cicc_fund_link.py 中金美元货币市场ETF
  python cicc_fund_link.py --probe --excel /path/to/爬虫list.xlsx --sheet 中金
  python cicc_fund_link.py --backfill --excel /path/to/爬虫list.xlsx --sheet 中金
"""

from __future__ import annotations

import html
import os
import re
import sys
from typing import Any
from urllib.parse import urljoin

import openpyxl
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))

from common.fund_link_backfill import cli_main, normalize_name  # noqa: E402


SHEET_NAME = "中金"
BASE_URL = "https://cicchkam.com"
PRODUCT_INDEX_URL = BASE_URL + "/sc/product/etfs"
KNOWN_PRODUCT_PAGES = {
    "中金美元货币市场ETF": BASE_URL + "/sc/product/documents/4?pro=1",
    "中金港元货币市场 ETF": BASE_URL + "/sc/product/documents/4?pro=7",
    "中金香港股票基金": BASE_URL + "/sc/product/documents/5?pro=5",
    "中金全球投资级别债券基金": BASE_URL + "/sc/product/documents/5?pro=11",
    "中金精选投资级债券基金": BASE_URL + "/sc/product/documents/5?pro=13",
    # 两只基金已终止；官网仍保留在基金系列文件页中的历史财务报告。
    # 该稳定页用于追溯半年报/年度报告，月报命令会在无历史月报时明确失败，
    # 不将其它存续基金的页面冒充为已终止基金页面。
    "中金碳期货ETF": BASE_URL + "/sc/product/documents/4?pro=1",
    "中金中国股票基金": BASE_URL + "/sc/product/documents/4?pro=1",
}
TERMINATED_FUNDS = {"中金碳期货ETF", "中金中国股票基金"}
DOWNLOADER = "download_cicc_factsheet.py"
TIMEOUT = 45
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
PRODUCT_LINK_RE = re.compile(
    r"href=[\"'](?P<href>/sc/product/documents/\d+(?:\?pro=\d+)?)[\"'][^>]*>(?P<text>.*?)</a>",
    re.I | re.S,
)


def _fetch(url: str) -> str:
    """抓取官网目录页；参数 url 为中金产品列表地址。"""
    response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or response.encoding
    return response.text


def _norm(value: str) -> str:
    """归一化中金基金名；参数 value 为中英文基金名称。"""
    text = normalize_name(value)
    text = html.unescape(text)
    for token in ("中金", "中金基金系列", "CICC", "FundSeries", "基金系列"):
        text = text.replace(token, "")
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", text).lower()


def _fetch_catalog() -> list[dict[str, Any]]:
    """读取中金产品目录并补入已知权威产品页。"""
    try:
        html_text = _fetch(PRODUCT_INDEX_URL)
    except requests.RequestException:
        # 产品列表页偶发受 CDN/地区策略影响；已核验的产品文件页仍可独立使用。
        html_text = ""
    catalog: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in PRODUCT_LINK_RE.finditer(html_text):
        page_url = urljoin(BASE_URL, html.unescape(match.group("href")))
        name = re.sub(r"<[^>]+>", " ", html.unescape(match.group("text")))
        name = re.sub(r"\s+", " ", name).strip()
        if not name or page_url in seen:
            continue
        seen.add(page_url)
        catalog.append({"fund_name": name, "page_url": page_url})
    for name, page_url in KNOWN_PRODUCT_PAGES.items():
        if page_url not in seen:
            catalog.append({"fund_name": name, "page_url": page_url})
    if not catalog:
        raise RuntimeError("中金产品列表未解析到产品文件页")
    return catalog


def _build_index(catalog: list[dict[str, Any]]) -> dict[str, Any]:
    """建立归一化名称索引；参数 catalog 为官网产品目录。"""
    index: dict[str, Any] = {"cn": {}, "all": catalog}
    for record in catalog:
        index["cn"].setdefault(_norm(record["fund_name"]), record)
    return index


def _match_record(row: dict[str, Any], index: dict[str, Any]) -> dict[str, Any] | None:
    """按中文/英文名称匹配产品页；参数 row/index 为页表行和目录索引。"""
    fund_name = str(row.get("fund") or "").strip()
    exact = index["cn"].get(_norm(fund_name))
    if exact:
        return exact
    english = _norm(str(row.get("english") or ""))
    if english:
        for record in index["all"]:
            if english in _norm(record.get("fund_name", "")):
                return record
    return None


def _resolve_single(
    row: dict[str, Any], index: dict[str, Any]
) -> tuple[str, str, str | None]:
    """返回稳定产品页和月报命令；参数 row/index 为待补全行和目录索引。"""
    record = _match_record(row, index)
    # 已核验的稳定产品页优先作为离线/CDN 失败时的兜底，确保批量补全不因
    # 产品列表页偶发不可访问而丢失中金基金的来源入口。
    if not record:
        known_name = str(row.get("fund") or "").strip()
        known_page = next(
            (page for name, page in KNOWN_PRODUCT_PAGES.items() if _norm(name) == _norm(known_name)),
            "",
        )
        if known_page:
            record = {"fund_name": known_name, "page_url": known_page}
    if not record:
        return "", "", "中金官网产品列表未匹配该基金"
    page_url = str(record["page_url"])
    command = f'python {DOWNLOADER} "{page_url}" monthly listed "{row.get("fund", "")}"'
    return page_url, command, ""


def _parse_option(argv: list[str], name: str, default: str) -> str:
    """读取简单命令行选项；参数 argv 为参数列表，name 为选项名，default 为默认值。"""
    try:
        index = argv.index(name)
    except ValueError:
        return default
    return argv[index + 1] if index + 1 < len(argv) else default


def _write_all_columns(
    excel_path: str,
    sheet_name: str,
    catalog: list[dict[str, Any]],
    row_numbers: set[int] | None = None,
) -> int:
    """写入中金全部基金的月报/半年报稳定页和运行方式；参数为工作簿、表名、目录及可选行号。"""
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
    required = {"基金", "月报链接", "运行方式", "半年报链接", "半年报运行方式"}
    if not required.issubset(header):
        workbook.close()
        raise RuntimeError(f"中金表缺少字段：{'、'.join(sorted(required - set(header)))}")
    index = _build_index(catalog)
    updated = 0
    for excel_row in range(2, sheet.max_row + 1):
        if row_numbers and excel_row not in row_numbers:
            continue
        fund = str(sheet.cell(excel_row, header["基金"]).value or "").strip()
        if not fund:
            continue
        row = {
            "fund": fund,
            "english": str(sheet.cell(excel_row, header.get("英文", 0)).value or "").strip()
            if header.get("英文") else "",
        }
        link, monthly_command, reason = _resolve_single(row, index)
        if not link:
            continue
        semiannual_command = f'python {DOWNLOADER} "{link}" semiannual all "{fund}"'
        sheet.cell(excel_row, header["月报链接"]).value = link
        sheet.cell(excel_row, header["运行方式"]).value = monthly_command
        sheet.cell(excel_row, header["半年报链接"]).value = link
        sheet.cell(excel_row, header["半年报运行方式"]).value = semiannual_command
        updated += 1
    workbook.save(excel_path)
    workbook.close()
    return updated


def main(argv: list[str] | None = None) -> int:
    """命令行入口；参数 argv 为单基金、probe 或 backfill 参数。"""
    args = list(sys.argv[1:] if argv is None else argv)
    result = cli_main(
        args,
        description="按爬虫list.xlsx中金表查询稳定产品文件页并回填最新月报命令",
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
        # 目录读取失败时仍使用已核验的产品页映射完成来源字段，保证来源入口不丢失。
        try:
            catalog = _fetch_catalog()
        except Exception:
            catalog = [{"fund_name": name, "page_url": page} for name, page in KNOWN_PRODUCT_PAGES.items()]
        updated = _write_all_columns(excel_path, sheet_name, catalog, row_numbers or None)
        print(f"[*] 中金月报/半年报来源字段已写入 {updated} 行", flush=True)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
