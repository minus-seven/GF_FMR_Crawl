#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
博时香港基金月报爬虫补全脚本。

以“爬虫list.xlsx”博时表的 B 列基金名（结合 C 伞）为索引，在博时香港官网
基金目录中精确匹配基金，按 港元 > 美元 > 其他、同币种默认第一条 选类别，
输出要回填的 I（基金详情页链接）与 J（运行方式）。

官网数据源：
  1. /api/fundinfo/fundmenutree.json   基金名 ↔ 基金代码目录；
  2. /api/fundinfo/fundnavlist.json    各基金的类别/币种行（含基金代码）。

通用流程（Excel 扫描、进度条、probe/backfill、命令行入口）都在
code/common/fund_link_backfill.py；本文件只保留博时官网特有的部分：
接口抓取、月报可用性校验、J 列命令模板。

用法：
  python bosera_fund_link.py 博时中国机会债券基金
  python bosera_fund_link.py --probe --excel /path/to/爬虫list.xlsx --sheet 博时
  python bosera_fund_link.py --backfill --excel /path/to/爬虫list.xlsx --sheet 博时
"""

from __future__ import annotations

import os
import sys
from typing import Any

import requests
import openpyxl

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common.fund_link_backfill import (  # noqa: E402
    build_index,
    cli_main,
    normalize_name,
    pick_share_class,
)


SHEET_NAME = "博时"
BASE_URL = "https://www.bosera.com.hk"
IFP_BASE = "https://ifp.hkex.com.hk/fund-repository/fund/"
REPORT_DOWNLOADER = "download_bosera_reports.py"
MENU_TREE_API = f"{BASE_URL}/api/fundinfo/fundmenutree.json"
NAV_LIST_API = f"{BASE_URL}/api/fundinfo/fundnavlist.json"
FACTSHEET_API = f"{BASE_URL}/api/fundinfo/factsheet.json"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Content-Type": "application/json",
    "Origin": BASE_URL,
    "Referer": f"{BASE_URL}/zh-HK/products/list/hk",
}
TIMEOUT = 30
_FACTSHEET_CACHE: dict[str, bool] = {}

# 部分上市类别与非上市类别共用同一份子基金月报。博时港元货币市场
# ETF 的交易代码为 HKDMMETF，但官网“基金月报”栏目挂在 HKDMMF
# 详情页；固定来源应使用月报页代码，下载器会继续从该页选择最新附件。
MONTHLY_REPORT_PAGE_OVERRIDES = {
    normalize_name("博时港元货币市场ETF", roman=True): "HKDMMF",
}

# 爬虫页表中的基金名 → HKEX 基金代码。境内互认基金/未纳入香港 IFP 的行
# 保持空白，不根据相似名称猜测代码，避免将错误的半年报关联到基金。
HKEX_CODE_BY_FUND = {
    "博时中国机会债券基金": "BCY423",
    "博时精选新兴市场债券基金": "BDI575",
    "博时大中华债券基金": "BLS840",
    "博时港股增利基金": "BRK536",
    "博时大中华增强回报债券基金": "BRR570",
    "博时全球短久期投资级债券基金": "BVN008",
    "博时稳健入息18个月定期开放债券基金 - I": "BRS611",
    "博时稳健入息18个月定期开放债券基金 - II": "BRS612",
    "博时稳健入息18个月定期开放债券基金 - III": "BRS613",
    "博时美元货币市场基金": "BSG677",
    "博时港元货币市场ETF": "BTE097",
    "博时标普500指数基金": "BXU445",
    "博时纳斯达克100指数基金": "BXU444",
}


def _fetch_catalog() -> list[dict[str, Any]]:
    """抓取博时官网基金目录：每支基金的每个类别/币种一条记录。"""
    session = requests.Session()
    session.trust_env = False
    tree_resp = session.get(MENU_TREE_API, headers=HEADERS, timeout=TIMEOUT)
    tree_resp.raise_for_status()
    tree = (tree_resp.json().get("data") or {}).get("returnList") or []

    categories: list[str] = []
    leaves: list[dict[str, Any]] = []

    def walk(nodes):
        """递归拆分菜单树：非叶子为类别，叶子为基金（含代码）。"""
        for node in nodes or []:
            value = str(node.get("value") or "").strip()
            children = node.get("children") or []
            if node.get("isLeaf"):
                leaves.append(
                    {
                        "code": value,
                        "fund_name": str(node.get("label") or "").strip(),
                        "share_class": "",
                        "currency": "",
                    }
                )
            elif children:
                if value:
                    categories.append(value)
                walk(children)

    walk(tree)

    catalog: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for category in categories:
        resp = session.post(
            NAV_LIST_API,
            headers=HEADERS,
            json={"fundType": category, "dataType": "fundNav"},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        rows = (resp.json().get("data") or {}).get("returnList") or []
        for row in rows:
            if not isinstance(row, list) or len(row) < 4:
                continue
            code, fund_name, share_class, currency = (
                str(row[0]).strip(),
                str(row[1]).strip(),
                str(row[2]).strip(),
                str(row[3]).strip(),
            )
            if not code or code == "基金名稱":
                continue
            key = (code, share_class, currency)
            if key in seen:
                continue
            seen.add(key)
            catalog.append(
                {
                    "code": code,
                    "fund_name": fund_name,
                    "share_class": share_class,
                    "currency": currency,
                }
            )
    # 菜单树里存在但没有净值行的基金（例如清算中的基金）也纳入目录
    for leaf in leaves:
        key = (leaf["code"], leaf["share_class"], leaf["currency"])
        if key in seen or not leaf["code"]:
            continue
        seen.add(key)
        catalog.append(leaf)
    if not catalog:
        raise RuntimeError("博时官网基金目录为空，请检查接口是否变更")
    return catalog


def _has_factsheet(code: str) -> bool:
    """检查官网是否已发布该基金月报（与下载脚本使用同一接口）。"""
    if code in _FACTSHEET_CACHE:
        return _FACTSHEET_CACHE[code]
    session = requests.Session()
    session.trust_env = False
    resp = session.post(
        FACTSHEET_API, headers=HEADERS, json={"fundCode": code}, timeout=TIMEOUT
    )
    resp.raise_for_status()
    items = (resp.json().get("data") or {}).get("returnList") or []
    _FACTSHEET_CACHE[code] = bool(items)
    return _FACTSHEET_CACHE[code]


def _build_index(catalog: list[dict[str, Any]]) -> dict[str, Any]:
    """按归一化基金名建立博时目录索引（含罗马数字归一化）。"""
    return build_index(catalog, key="fund_name", roman=True)


def _resolve_single(
    row: dict[str, Any], index: dict[str, Any]
) -> tuple[str, str, str]:
    """解析一支基金，返回 (I详情页链接, J运行方式, 跳过原因)。"""
    fund_name = str(row.get("fund") or "")
    umbrella = str(row.get("umbrella") or "")
    matched = index.get(normalize_name(fund_name, roman=True)) or []
    if not matched:
        return "", "", "官网未收录，跳过"
    if any("清算" in str(item.get("fund_name") or "") for item in matched):
        return "", "", "官网显示清算中，跳过"
    normalized_name = normalize_name(fund_name, roman=True)
    override_code = MONTHLY_REPORT_PAGE_OVERRIDES.get(normalized_name)
    if override_code:
        override = next(
            (item for item in matched if str(item.get("code") or "") == override_code),
            None,
        )
    else:
        override = None
    code = str((override or pick_share_class(matched)).get("code") or "")
    if not code:
        return "", "", "官网未返回基金代码，跳过"
    if not _has_factsheet(code):
        return "", "", "官网暂未发布月报，跳过"
    link = f"{BASE_URL}/zh-HK/products/fund/detail/{code}"
    language_arg = " 中文" if override else ""
    return link, f"python download_bosera_factsheet.py {code}{language_arg}", ""


def _hkex_code(fund_name: str) -> str:
    """按爬虫页表中文基金名返回 HKEX 代码；无法确认则返回空串。"""
    return HKEX_CODE_BY_FUND.get(str(fund_name or "").strip(), "")


def _ensure_report_columns(excel_path: str, sheet_name: str) -> None:
    """为既有博时页表追加半年报/年报入口字段。"""
    workbook = openpyxl.load_workbook(excel_path)
    sheet = workbook[sheet_name]
    existing = {str(cell.value or "").strip() for cell in sheet[1]}
    for header in ("半年报链接", "半年报运行方式", "年报链接", "年报运行方式", "HKEX代码"):
        if header not in existing:
            sheet.cell(1, sheet.max_column + 1, header)
    workbook.save(excel_path)
    workbook.close()


def _write_report_columns(excel_path: str, sheet_name: str) -> tuple[int, int]:
    """写入半年报/年报稳定入口；返回 (已写入, 缺少代码)。"""
    workbook = openpyxl.load_workbook(excel_path)
    sheet = workbook[sheet_name]
    header = {str(cell.value).strip(): cell.column for cell in sheet[1] if cell.value not in (None, "")}
    updated = missing = 0
    for row in range(2, sheet.max_row + 1):
        fund_name = str(sheet.cell(row, header["基金"]).value or "").strip()
        code = _hkex_code(fund_name)
        sheet.cell(row, header["HKEX代码"]).value = code
        if not code:
            missing += 1
            continue
        link = IFP_BASE + code
        sheet.cell(row, header["半年报链接"]).value = link
        sheet.cell(row, header["半年报运行方式"]).value = f"python {REPORT_DOWNLOADER} {code} --kind semiannual --lang EN"
        sheet.cell(row, header["年报链接"]).value = link
        sheet.cell(row, header["年报运行方式"]).value = f"python {REPORT_DOWNLOADER} {code} --kind annual --lang EN"
        updated += 1
    workbook.save(excel_path)
    workbook.close()
    return updated, missing


def _parse_option(argv: list[str], name: str, default: str) -> str:
    """读取命令行选项。"""
    try:
        pos = argv.index(name)
    except ValueError:
        return default
    return argv[pos + 1] if pos + 1 < len(argv) else default


def main(argv: list[str] | None = None) -> int:
    """命令行入口：单基金查询 / --probe / --backfill。"""
    args = list(sys.argv[1:] if argv is None else argv)
    if "--backfill" in args or "--probe" in args:
        excel_path = _parse_option(args, "--excel", os.path.join(os.path.dirname(__file__), "..", "..", "..", "爬虫list.xlsx"))
        _ensure_report_columns(excel_path, _parse_option(args, "--sheet", SHEET_NAME))
    result = cli_main(
        args,
        description="按爬虫list.xlsx博时表 B 列基金名查询官网详情页链接（含批量补全）",
        sheet_name=SHEET_NAME,
        catalog_loader=_fetch_catalog,
        index_builder=_build_index,
        resolver=_resolve_single,
        single_print_run_command=True,
    )
    if "--backfill" in args:
        excel_path = _parse_option(args, "--excel", os.path.join(os.path.dirname(__file__), "..", "..", "..", "爬虫list.xlsx"))
        updated, missing = _write_report_columns(excel_path, _parse_option(args, "--sheet", SHEET_NAME))
        print(f"[*] 博时半年报/年报字段已写入 {updated} 行，缺少 HKEX 代码 {missing} 行", flush=True)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
