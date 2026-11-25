#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""广发国际（GF International）基金来源链接补全脚本。"""

from __future__ import annotations

import os
import sys
from copy import copy
from typing import Any

import openpyxl

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
from common.fund_link_backfill import cli_main, normalize_name  # noqa: E402

SHEET_NAME = "广发国际"
DOWNLOADER = "download_gfintl_factsheet.py"
IFP_BASE = "https://ifp.hkex.com.hk/fund-repository/fund/"
MONTHLY_PAGE = "https://www.gffunds.com.hk/ch/product/funds/"
FUND_MAP = {
    "BPC697": ("广发全球入息债券基金", "GFI Global Income Bond Fund"),
    "BSG488": ("广发全球精选股票基金", "GFI Global Select Equity Fund"),
    "BTE502": ("广发美元货币市场基金", "GF USD Money Market Fund"),
    "BTV055": ("广发中国精选股票基金", "GFI China Select Equity Fund"),
    "BTT545": ("广发精选债券基金", "GFI Select Bond Fund"),
    "BVG225": ("广发新视角债券基金", "GFI New Perspective Bond Fund"),
    "BWE554": ("广发港元货币市场基金", "GF HKD Money Market Fund"),
    "BXB473": ("广发短期投资级债券基金", "GF Short Term Investment-Grade Bond Fund"),
    "BXV822": ("广发纳斯达克100指数基金", "GFI NASDAQ-100 Index Fund"),
    "BXW016": ("广发人民币货币市场基金", "GF RMB Money Market Fund"),
    "BYG136": ("广发香港交易所科技及美国科技100指数ETF", "GF HKEX Tech & US Tech 100 Index ETF"),
    "BYG137": ("广发MSCI全球战略金属精选指数ETF", "GF MSCI Global Strategic Metals Select ETF"),
}
NAME_TO_CODE = {name: code for code, (name, _) in FUND_MAP.items()}
NAME_TO_CODE.update({english: code for code, (_, english) in FUND_MAP.items()})


def _norm(value: str) -> str:
    """归一化中英文名称；参数 value 为基金名。"""
    return "".join(ch for ch in normalize_name(value).lower() if ch.isalnum())


def _fetch_catalog() -> list[dict[str, Any]]:
    """返回已核验的 HKEX 稳定基金页目录。"""
    return [{"code": code, "fund_name": name, "english": english, "page_url": IFP_BASE + code} for code, (name, english) in FUND_MAP.items()]


def _build_index(catalog: list[dict[str, Any]]) -> dict[str, Any]:
    """建立名称/代码索引；参数 catalog 为基金目录。"""
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
        return "", "", "广发国际 HKEX 稳定页未匹配该基金"
    code = record["code"]
    link = record["page_url"]
    command = f'python {DOWNLOADER} "{MONTHLY_PAGE}" "{record["fund_name"]}" monthly {code} "{record["english"]}"'
    return link, command, ""


def _parse_option(argv: list[str], name: str, default: str) -> str:
    """读取命令行选项；参数 argv 为参数列表，name 为选项名。"""
    try:
        pos = argv.index(name)
    except ValueError:
        return default
    return argv[pos + 1] if pos + 1 < len(argv) else default


def _ensure_sheet(excel_path: str, sheet_name: str) -> None:
    """创建广发国际页表；参数 excel_path 为工作簿路径，sheet_name 为页表名。"""
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
    categories = {"货币": "货币", "债券": "固收", "入息": "固收", "全球精选股票": "权益", "中国精选股票": "权益", "纳斯达克": "权益", "ETF": "权益", "金属": "权益"}
    for target_row, (code, (fund, english)) in enumerate(FUND_MAP.items(), 2):
        values = by_fund.get(fund, [code, fund, "", "", english, "广发国际", next((v for k, v in categories.items() if k in fund), "混合"), "", ""])
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
        sheet.cell(row, header["月报链接"]).value = link
        sheet.cell(row, header["运行方式"]).value = monthly_command
        sheet.cell(row, header["半年报链接"]).value = link
        code = link.rsplit("/", 1)[-1]
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
    result = cli_main(args, description="按 HKEX 稳定基金页查询广发国际来源", sheet_name=sheet_name, catalog_loader=_fetch_catalog, index_builder=_build_index, resolver=_resolve_single, single_print_run_command=True)
    if "--backfill" in args:
        excel_path = _parse_option(args, "--excel", os.path.join(HERE, "..", "..", "..", "爬虫list.xlsx"))
        print(f"[*] 广发国际三类来源字段已写入 {_write_all_columns(excel_path, sheet_name)} 行", flush=True)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
