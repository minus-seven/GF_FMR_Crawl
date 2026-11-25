#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""华泰金融控股（香港）基金来源链接补全脚本。

华泰官网每只子基金使用稳定的 ``?f=<序号>&l=en`` 页面。I/J 记录月报入口，
半年报和年报写入各自来源字段；运行时由下载器重新读取页面并选择最新正文，
不固化易变的 PDF 附件地址。
"""

from __future__ import annotations

import os
import sys
from copy import copy
from typing import Any

import openpyxl

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))

from common.fund_link_backfill import cli_main, normalize_name  # noqa: E402


SHEET_NAME = "华泰"
BASE_URL = "https://hk.htsc.com.hk/"
DOWNLOADER = "download_huatai_factsheet.py"
KNOWN_PRODUCT_PAGES = {
    "华泰港元货币市场基金": BASE_URL + "?f=1&l=en",
    "华泰美元货币市场基金": BASE_URL + "?f=2&l=en",
    "华泰亚太目标收益基金": BASE_URL + "?f=3&l=en",
    "华泰环球精选债券基金": BASE_URL + "?f=4&l=en",
    "华泰人民币货币市场基金": BASE_URL + "?f=5&l=en",
    "华泰美国精选基金": BASE_URL + "?f=6&l=en",
}
KNOWN_FUND_CODES = {
    "BTS953": "华泰港元货币市场基金",
    "BTS952": "华泰美元货币市场基金",
    "BUF873": "华泰亚太目标收益基金",
    "BUR381": "华泰环球精选债券基金",
    "BVO540": "华泰人民币货币市场基金",
    "BWA843": "华泰美国精选基金",
}


def _norm(value: str) -> str:
    """归一化中英文基金名；参数 value 为原始基金名称。"""
    text = normalize_name(value).lower()
    for token in ("华泰", "huatai", "global investment fund", "fund", "基金", "系列"):
        text = text.replace(token, "")
    return "".join(ch for ch in text if ch.isalnum())


def _fetch_catalog() -> list[dict[str, Any]]:
    """返回已核验的华泰稳定基金页面目录。"""
    return [{"fund_name": name, "page_url": url} for name, url in KNOWN_PRODUCT_PAGES.items()]


def _build_index(catalog: list[dict[str, Any]]) -> dict[str, Any]:
    """建立归一化基金名索引；参数 catalog 为华泰官网目录。"""
    return {_norm(item["fund_name"]): item for item in catalog}


def _resolve_single(row: dict[str, Any], index: dict[str, Any]) -> tuple[str, str, str | None]:
    """返回月报入口和运行命令；参数 row 为页表行，index 为官网目录索引。"""
    fund_name = str(row.get("fund") or "").strip()
    english = str(row.get("english") or "").strip()
    fund_name = KNOWN_FUND_CODES.get(fund_name.upper(), fund_name)
    record = index.get(_norm(fund_name)) or index.get(_norm(english))
    if not record:
        for key, candidate in index.items():
            if len(key) >= 6 and (_norm(fund_name) in key or key in _norm(fund_name)):
                record = candidate
                break
    if not record:
        return "", "", "华泰官网稳定页面目录未匹配该基金"
    page_url = record["page_url"]
    command = f'python {DOWNLOADER} "{page_url}" "{fund_name}" monthly'
    return page_url, command, ""


def _parse_option(argv: list[str], name: str, default: str) -> str:
    """读取命令行选项；参数 argv 为参数列表，name 为选项名，default 为默认值。"""
    try:
        index = argv.index(name)
    except ValueError:
        return default
    return argv[index + 1] if index + 1 < len(argv) else default


def _ensure_sheet(excel_path: str, sheet_name: str) -> None:
    """创建华泰页表；参数 excel_path 为工作簿路径，sheet_name 为页表名。"""
    workbook = openpyxl.load_workbook(excel_path)
    if sheet_name in workbook.sheetnames:
        workbook.close()
        return
    source = workbook["Sheet2"] if "Sheet2" in workbook.sheetnames else workbook.active
    sheet = workbook.create_sheet(sheet_name)
    headers = [
        "序号", "基金", "伞", "全称", "英文", "管理人", "大类", "小类", "是否派息",
        "月报链接", "运行方式", "半年报链接", "半年报运行方式", "年报链接", "年报运行方式",
    ]
    for col, value in enumerate(headers, 1):
        cell = sheet.cell(1, col, value)
        source_cell = source.cell(1, min(col, source.max_column))
        if source_cell.has_style:
            cell._style = copy(source_cell._style)
        if source_cell.number_format:
            cell.number_format = source_cell.number_format
    known_names = set(KNOWN_PRODUCT_PAGES)
    target_row = 2
    source_header = {str(source.cell(1, col).value or "").strip(): col for col in range(1, source.max_column + 1)}
    for source_row in range(2, source.max_row + 1):
        fund = str(source.cell(source_row, source_header.get("基金", 2)).value or "").strip()
        if fund not in known_names:
            continue
        for col in range(1, 10):
            value = source.cell(source_row, col).value
            if col == 4:
                umbrella = str(source.cell(source_row, 3).value or "").strip()
                value = f"{umbrella}|{fund}"
            target = sheet.cell(target_row, col, value)
            source_cell = source.cell(source_row, col)
            if source_cell.has_style:
                target._style = copy(source_cell._style)
        target_row += 1
    workbook.save(excel_path)
    workbook.close()


def _write_all_columns(
    excel_path: str,
    sheet_name: str,
    catalog: list[dict[str, Any]],
    row_numbers: set[int] | None = None,
) -> int:
    """写入月报/半年报/年报来源；参数为工作簿、页表名、目录和可选行号。"""
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
        raise RuntimeError(f"华泰表缺少字段：{'、'.join(sorted(required - set(header)))}")
    index = _build_index(catalog)
    updated = 0
    for excel_row in range(2, sheet.max_row + 1):
        if row_numbers and excel_row not in row_numbers:
            continue
        fund_name = str(sheet.cell(excel_row, header["基金"]).value or "").strip()
        if not fund_name:
            continue
        english = str(sheet.cell(excel_row, header.get("英文", 0)).value or "").strip() if header.get("英文") else ""
        link, monthly_command, reason = _resolve_single({"fund": fund_name, "english": english}, index)
        if not link:
            continue
        sheet.cell(excel_row, header["月报链接"]).value = link
        sheet.cell(excel_row, header["运行方式"]).value = monthly_command
        sheet.cell(excel_row, header["半年报链接"]).value = link
        sheet.cell(excel_row, header["半年报运行方式"]).value = f'python {DOWNLOADER} "{link}" "{fund_name}" semiannual'
        sheet.cell(excel_row, header["年报链接"]).value = link
        sheet.cell(excel_row, header["年报运行方式"]).value = f'python {DOWNLOADER} "{link}" "{fund_name}" annual'
        updated += 1
    workbook.save(excel_path)
    workbook.close()
    return updated


def main(argv: list[str] | None = None) -> int:
    """命令行入口；参数 argv 为单基金、probe 或 backfill 选项。"""
    args = list(sys.argv[1:] if argv is None else argv)
    sheet_name = _parse_option(args, "--sheet", SHEET_NAME)
    if "--backfill" in args or "--probe" in args:
        excel_path = _parse_option(args, "--excel", os.path.join(HERE, "..", "..", "..", "爬虫list.xlsx"))
        _ensure_sheet(excel_path, sheet_name)
    result = cli_main(
        args,
        description="按爬虫list.xlsx华泰表查询稳定基金页并回填三类报告命令",
        sheet_name=sheet_name,
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
        updated = _write_all_columns(excel_path, sheet_name, _fetch_catalog(), row_numbers or None)
        print(f"[*] 华泰月报/半年报/年报来源字段已写入 {updated} 行", flush=True)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
