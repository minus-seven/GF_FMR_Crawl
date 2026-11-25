#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""中国平安资产管理（香港）基金来源链接补全脚本。"""

from __future__ import annotations

import os
import sys
from copy import copy
from typing import Any

import openpyxl

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
from common.fund_link_backfill import cli_main, normalize_name  # noqa: E402

SHEET_NAME = "平安资管"
DOWNLOADER = "download_pinganhk_factsheet.py"
BASE = "https://asset.pingan.com.hk/en/"
FUND_MAP = {
    "AWO198": ("中国平安精选人民币债券基金", "Ping An of China SIF - RMB Bond Fund", "PACSIF-RMBBF", "固收"),
    "AXL009": ("中国平安CSI香港高息股ETF", "Ping An of China CSI HK Dividend ETF", "PACT-PACCHKD", "权益"),
    "BBS387": ("中国平安精选中国健康护理基金", "Ping An of China SIF - China Health Care Fund", "PACSIF-PACHCF", "混合"),
    "BCE463": ("投资评级人民币债券基金", "Ping An of China SIF - Investment Grade RMB Bond Fund", "PACSIF-IGRMBBF", "固收"),
    "BQN919": ("平安货币基金", "Ping An Money Market Fund", "PACSIF-PAMMF", "货币"),
    "BTU900": ("平安稳定基金", "Ping An Stable Fund", "PACSIF-PASF", "固收"),
    "BUM340": ("平安港元货币基金", "Ping An Hong Kong Dollar Money Market Fund", "PACSIF-PAHKMMF", "货币"),
    "BWH864": ("平安东西方股票精选ETF", "Ping An East-West Select ETF", "PACT-PAEWS", "权益"),
    "BWH865": ("平安科技精选ETF", "Ping An Technology Select ETF", "PACT-PATECHS", "权益"),
    "BYM560": ("平安医疗保健50精选ETF", "Ping An Healthcare 50 Select ETF", "PACT-PAHEALTH", "权益"),
    "BYM561": ("平安人工智能精选ETF", "Ping An AI Select ETF", "PACT-PAAI", "权益"),
}


def _norm(value: str) -> str:
    """归一化中英文名称；参数 value 为基金名。"""
    return "".join(ch for ch in normalize_name(value).lower() if ch.isalnum())


def _fetch_catalog() -> list[dict[str, Any]]:
    """返回平安官网稳定产品页目录。"""
    return [{"code": code, "fund_name": values[0], "english": values[1], "page_url": BASE + values[2], "category": values[3]} for code, values in FUND_MAP.items()]


def _build_index(catalog: list[dict[str, Any]]) -> dict[str, Any]:
    """建立名称/代码索引；参数 catalog 为产品目录。"""
    index: dict[str, dict[str, Any]] = {}
    for item in catalog:
        for key in (item["code"], item["fund_name"], item["english"]):
            index[_norm(key)] = item
    return index


def _resolve_single(row: dict[str, Any], index: dict[str, Any]) -> tuple[str, str, str]:
    """返回稳定入口和月报命令；参数 row 为页表行，index 为目录索引。"""
    keys = (str(row.get("fund") or "").strip(), str(row.get("english") or "").strip())
    record = next((index.get(_norm(key)) for key in keys if key and index.get(_norm(key))), None)
    if not record:
        return "", "", "平安官网稳定产品页未匹配该基金"
    link = record["page_url"]
    command = f'python {DOWNLOADER} "{link}" "{record["fund_name"]}" monthly'
    return link, command, ""


def _parse_option(argv: list[str], name: str, default: str) -> str:
    """读取命令行选项；参数 argv 为参数列表，name 为选项名。"""
    try:
        pos = argv.index(name)
    except ValueError:
        return default
    return argv[pos + 1] if pos + 1 < len(argv) else default


def _ensure_sheet(excel_path: str, sheet_name: str) -> None:
    """创建平安资管页表；参数 excel_path 为工作簿路径，sheet_name 为页表名。"""
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
    for target_row, (code, (fund, english, _slug, category)) in enumerate(FUND_MAP.items(), 2):
        values = by_fund.get(fund, [code, fund, "中国平安精选投资基金系列", "", english, "平安资管", category, "", ""])
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
        sheet.cell(row, header["半年报运行方式"]).value = f'python {DOWNLOADER} "{link}" "{fund}" semiannual'
        sheet.cell(row, header["年报链接"]).value = link
        sheet.cell(row, header["年报运行方式"]).value = f'python {DOWNLOADER} "{link}" "{fund}" annual'
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
    result = cli_main(args, description="按平安官网稳定产品页查询报告来源", sheet_name=sheet_name, catalog_loader=_fetch_catalog, index_builder=_build_index, resolver=_resolve_single, single_print_run_command=True)
    if "--backfill" in args:
        excel_path = _parse_option(args, "--excel", os.path.join(HERE, "..", "..", "..", "爬虫list.xlsx"))
        print(f"[*] 平安资管三类来源字段已写入 {_write_all_columns(excel_path, sheet_name)} 行", flush=True)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
