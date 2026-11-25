#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""国泰君安资产管理（亚洲）基金来源链接补全脚本。"""

from __future__ import annotations

import os
import sys
from copy import copy
from typing import Any

import openpyxl

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
from common.fund_link_backfill import cli_main, normalize_name  # noqa: E402

SHEET_NAME = "国泰君安"
DOWNLOADER = "download_gtjai_factsheet.py"
REPORT_PAGE = "https://www.gtjai.com/tc/fund_gugcfa_report"
IFP_BASE = "https://ifp.hkex.com.hk/fund-repository/fund/"
REPORT_PAGES = {
    "AOM552": "https://www.gtjai.com/tc/fund_gugcfa_report",
    "BTF710": "https://www.gtjai.com/tc/fund_gjhkdmmf_report",
    "BTF711": "https://www.gtjai.com/tc/fund_gjusdmmf_report",
    "BVH535": "https://www.gtjai.com/tc/fund_gjigbf_report",
    "BXB327": "https://www.gtjai.com/tc/fund_gjgsf_report",
}
FUND_MAP = {
    "AOM552": ("国泰君安大中华增长基金", "Guotai Junan Greater China Growth Fund", "权益"),
    "BTF710": ("国泰君安港元货币市场基金", "Guotai Junan HKD Money Market Fund", "货币"),
    "BTF711": ("国泰君安美元货币市场基金", "Guotai Junan USD Money Market Fund", "货币"),
    "BVH535": ("国泰君安投资级债券基金", "Guotai Junan Investment Grade Bond Fund", "固收"),
    "BXB327": ("国泰君安环球精选债券基金", "Guotai Junan Global Select Bond Fund", "固收"),
}


def _norm(value: str) -> str:
    """归一化名称；参数 value 为基金名称。"""
    return "".join(ch for ch in normalize_name(value).lower() if ch.isalnum())


def _fetch_catalog() -> list[dict[str, Any]]:
    """返回已核验的 HKEX 稳定基金页目录。"""
    return [{"code": code, "fund_name": values[0], "english": values[1], "page_url": IFP_BASE + code, "report_page": REPORT_PAGES[code], "category": values[2]} for code, values in FUND_MAP.items()]


def _build_index(catalog: list[dict[str, Any]]) -> dict[str, Any]:
    """建立名称/代码索引；参数 catalog 为目录。"""
    index: dict[str, dict[str, Any]] = {}
    for item in catalog:
        for key in (item["code"], item["fund_name"], item["english"]):
            index[_norm(key)] = item
    return index


def _resolve_single(row: dict[str, Any], index: dict[str, Any]) -> tuple[str, str, str]:
    """返回稳定入口及月报命令；参数 row 为页表行，index 为目录索引。"""
    keys = (str(row.get("fund") or "").strip(), str(row.get("english") or "").strip())
    record = next((index.get(_norm(key)) for key in keys if key and index.get(_norm(key))), None)
    if not record:
        return "", "", "国泰君安 HKEX 稳定页未匹配该基金"
    command = f'python {DOWNLOADER} "{record["report_page"]}" "{record["fund_name"]}" monthly {record["code"]} "{record["english"]}"'
    return record["page_url"], command, ""


def _parse_option(argv: list[str], name: str, default: str) -> str:
    """读取命令行选项；参数 argv 为参数列表，name 为选项名。"""
    try:
        pos = argv.index(name)
    except ValueError:
        return default
    return argv[pos + 1] if pos + 1 < len(argv) else default


def _ensure_sheet(excel_path: str, sheet_name: str) -> None:
    """创建国泰君安页表；参数 excel_path 为工作簿路径，sheet_name 为页表名。"""
    workbook = openpyxl.load_workbook(excel_path)
    if sheet_name in workbook.sheetnames:
        workbook.close()
        return
    source = workbook["Sheet2"] if "Sheet2" in workbook.sheetnames else workbook.active
    sheet = workbook.create_sheet(sheet_name)
    headers = ["序号", "基金", "伞", "全称", "英文", "管理人", "大类", "小类", "是否派息", "月报链接", "运行方式", "半年报链接", "半年报运行方式", "年报链接", "年报运行方式"]
    for col, value in enumerate(headers, 1):
        cell = sheet.cell(1, col, value)
        if source.cell(1, min(col, source.max_column)).has_style:
            cell._style = copy(source.cell(1, min(col, source.max_column))._style)
    source_header = {str(source.cell(1, col).value or "").strip(): col for col in range(1, source.max_column + 1)}
    by_fund: dict[str, list[Any]] = {}
    for row in range(2, source.max_row + 1):
        fund = str(source.cell(row, source_header.get("基金", 2)).value or "").strip()
        if fund:
            by_fund[fund] = [source.cell(row, col).value for col in range(1, 10)]
    for target_row, (code, (fund, english, category)) in enumerate(FUND_MAP.items(), 2):
        values = by_fund.get(fund, [code, fund, "国泰君安投资基金开放式基金型公司", "", english, "国泰君安", category, "", ""])
        for col, value in enumerate(values, 1):
            sheet.cell(target_row, col, value)
    workbook.save(excel_path)
    workbook.close()


def _write_all_columns(excel_path: str, sheet_name: str) -> int:
    """写入三类来源入口；参数为工作簿路径和页表名。"""
    workbook = openpyxl.load_workbook(excel_path)
    sheet = workbook[sheet_name]
    header = {str(cell.value).strip(): cell.column for cell in sheet[1] if cell.value not in (None, "")}
    index = _build_index(_fetch_catalog())
    updated = 0
    for row in range(2, sheet.max_row + 1):
        fund = str(sheet.cell(row, header["基金"]).value or "").strip()
        english = str(sheet.cell(row, header["英文"]).value or "").strip() if "英文" in header else ""
        link, monthly_command, _ = _resolve_single({"fund": fund, "english": english}, index)
        if not link:
            continue
        code = link.rsplit("/", 1)[-1]
        sheet.cell(row, header["月报链接"]).value = link
        sheet.cell(row, header["运行方式"]).value = monthly_command
        sheet.cell(row, header["半年报链接"]).value = link
        sheet.cell(row, header["半年报运行方式"]).value = f'python {DOWNLOADER} "{link}" "{fund}" semiannual {code} "{english}"'
        sheet.cell(row, header["年报链接"]).value = link
        sheet.cell(row, header["年报运行方式"]).value = f'python {DOWNLOADER} "{link}" "{fund}" annual {code} "{english}"'
        updated += 1
    workbook.save(excel_path)
    workbook.close()
    return updated


def main(argv: list[str] | None = None) -> int:
    """命令行入口；参数 argv 为补全参数。"""
    args = list(sys.argv[1:] if argv is None else argv)
    sheet_name = _parse_option(args, "--sheet", SHEET_NAME)
    if "--backfill" in args or "--probe" in args:
        excel_path = _parse_option(args, "--excel", os.path.join(HERE, "..", "..", "..", "爬虫list.xlsx"))
        _ensure_sheet(excel_path, sheet_name)
    result = cli_main(args, description="按 HKEX 稳定基金页和 GTJAI 报告目录查询国泰君安来源", sheet_name=sheet_name, catalog_loader=_fetch_catalog, index_builder=_build_index, resolver=_resolve_single, single_print_run_command=True)
    if "--backfill" in args:
        excel_path = _parse_option(args, "--excel", os.path.join(HERE, "..", "..", "..", "爬虫list.xlsx"))
        print(f"[*] 国泰君安三类来源字段已写入 {_write_all_columns(excel_path, sheet_name)} 行", flush=True)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
