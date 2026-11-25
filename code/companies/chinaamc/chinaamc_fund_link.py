#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
华夏基金（香港）www.chinaamc.com.hk —— 基金月报爬虫补全脚本。

目录来源：官网公开的综合信息接口（实测免登录可用）
  /jeecg-boot/mainFundPerformance/tMainFundPerformance/getAllFundComprehensiveInfo
月报判定：复用下载脚本的文档接口
  /jeecg-boot/mainWebDailyDocument/tMainWebDailyDocument/listByFundId?fundId=<id>
  含“基金月报 / 基金月報 / Factsheet”类别才认为是可下载月报。

匹配：中文核心名（简体/繁体）优先 → 英文核心名兜底。
I = https://www.chinaamc.com.hk/detail?fundId=<id>&fundName=<英文名用连字符连接>
J = python download_chinaamc_factsheet.py <id>

用法：
  python chinaamc_fund_link.py 华夏中国机会基金
  python chinaamc_fund_link.py --probe --excel /path/to/爬虫list.xlsx --sheet 华夏
  python chinaamc_fund_link.py --backfill --excel /path/to/爬虫list.xlsx --sheet 华夏
"""

from __future__ import annotations

import os
import re
import sys
from typing import Any

import requests
import openpyxl

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
sys.path.insert(0, HERE)

from common.fund_link_backfill import (  # noqa: E402
    cli_main,
    normalize_name,
)
from download_chinaamc_factsheet import (  # noqa: E402
    FACTSHEET_CATEGORY,
    fetch_documents,
)


SHEET_NAME = "华夏"
BASE_URL = "https://www.chinaamc.com.hk"
IFP_BASE = "https://ifp.hkex.com.hk/fund-repository/fund/"
REPORT_DOWNLOADER = "download_chinaamc_reports.py"
CATALOG_API = (
    BASE_URL
    + "/jeecg-boot/mainFundPerformance/tMainFundPerformance/getAllFundComprehensiveInfo"
)
DETAIL_URL_TEMPLATE = BASE_URL + "/detail?fundId={fund_id}&fundName={fund_slug}"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json",
}
TIMEOUT = 30
HKEX_CODE_BY_ENGLISH = {
    "ChinaAMC Select USD Money Market Fund": "BSR551",
    "ChinaAMC Select Money Market Fund": "BNY627",
    "ChinaAMC Select Fixed Income Allocation Fund": "BNB520",
    "ChinaAMC USD Digital Money Market Fund": "BWG918",
    "ChinaAMC Select RMB Investment Grade Income Fund": "BSG024",
    "ChinaAMC Strategic Fixed Income Fund": "BVI319",
    "ChinaAMC Select Asia Bond Fund": "BHN797",
    "ChinaAMC Global Investment Grade Bond Fund": "BPL369",
    "ChinaAMC HKD Digital Money Market Fund": "BVQ026",
    "ChinaAMC RMB Digital Money Market Fund": "BWG919",
    "ChinaAMC China Focus Fund": "AVK670",
    "ChinaAMC China Opportunities Fund": "BCC109",
    "ChinaAMC HK-US AI ETF": "BXK995",
    "ChinaAMC Select Stable Income Fund": "BDG861",
    "ChinaAMC Select Greater China Technology Fund": "BBQ016",
    "ChinaAMC Select Offshore RMB Income Bond Fund": "BWN549",
    "ChinaAMC Choice Offshore RMB Bond Fund": "BXO493",
    "ChinaAMC New Horizon China A Share Fund": "BRS967",
    "ChinaAMC Digital Gold ETF": "BXU205",
    "ChinaAMC Select Flexible Cycle Income Fund": "BSX517",
    "ChinaAMC Select RMB Bond Fund": "AVZ072",
    "ChinaAMC Select Dynamic Fixed Income Fund": "BTM866",
    "ChinaAMC Global Multi Income Fund": "BNE701",
    "ChinaAMC Global Fixed Income Plus Fund": "BXV485",
    "ChinaAMC Asia ESG Bond Fund": "BSW666",
}


def _norm(value: str) -> str:
    """中文名归一化：简繁统一、去空格符号，并剥离“华夏/ChinaAMC”等品牌词。"""
    text = normalize_name(value)
    # 官网部分基金名带“（此基金并不是货币基金）*”等尾部标注，需反复剥离括注与星号
    text = text.replace("*", "").replace("＊", "")
    while True:
        trimmed = re.sub(r"\([^()]*\)$", "", text)
        if trimmed == text:
            break
        text = trimmed
    for token in ("華夏基金", "华夏基金", "華夏", "华夏", "ChinaAMC", "chinaamc"):
        text = text.replace(token, "")
    for token in ("-", "－", "–", "—", "−", "─", "（", "）", "(", ")", "＋", "+", " "):
        text = text.replace(token, "")
    return text


def _en(value: str) -> str:
    """英文名归一化：去 ChinaAMC 等品牌词，仅保留字母数字。"""
    text = str(value or "").strip().lower().replace("chinaamc", "")
    return re.sub(r"[^a-z0-9]", "", text)


def _fetch_catalog() -> list[dict[str, Any]]:
    """返回官网综合信息接口里的全部基金（含中英文名）。"""
    response = requests.get(CATALOG_API, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    if not payload.get("success"):
        raise RuntimeError(f"ChinaAMC 目录接口返回失败：{payload.get('message')}")
    catalog: list[dict[str, Any]] = []
    for item in payload.get("result") or []:
        info = item.get("fundInfo") or {}
        fund_id = str(info.get("ssFundId") or "").strip()
        if not fund_id:
            continue
        catalog.append(
            {
                "fund_id": fund_id,
                "name_sc": str(info.get("ssFundNameSc") or "").strip(),
                "name_tc": str(info.get("ssFundNameTc") or "").strip(),
                "name_en": str(info.get("ssFundNameEn") or "").strip(),
            }
        )
    if not catalog:
        raise RuntimeError("ChinaAMC 目录接口未返回任何基金，请检查接口是否变更")
    return catalog


def _build_index(catalog: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """建立 (语言, 归一化名) → 基金记录 的索引（简繁各一份）。"""
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for record in catalog:
        for language, name in (("cn", record["name_sc"]), ("cn", record["name_tc"])):
            if name:
                index.setdefault((language, _norm(name)), record)
        if record["name_en"]:
            index.setdefault(("en", _en(record["name_en"])), record)
    return index


def _has_monthly_document(fund_id: str) -> bool:
    """判断该基金在文档接口里是否存在月报（基金月报/基金月報/Factsheet）。"""
    categories = {value for value in FACTSHEET_CATEGORY.values()}
    for document in fetch_documents(fund_id):
        if str(document.get("ssCategory") or "").strip() in categories:
            return True
    return False


def _detail_link(record: dict[str, Any]) -> str:
    """拼基金详情页链接（fundName 用英文名连字符形式，与页表现有写法一致）。"""
    slug = re.sub(r"[^A-Za-z0-9]+", "-", record.get("name_en") or "").strip("-")
    if not slug:
        return f"{BASE_URL}/detail?fundId={record['fund_id']}"
    return DETAIL_URL_TEMPLATE.format(fund_id=record["fund_id"], fund_slug=slug)


def _resolve_single(
    row: dict[str, Any], index: dict[tuple[str, str], dict[str, Any]]
) -> tuple[str, str, str | None]:
    """解析一支基金，返回 (I月报链接, J运行方式, 跳过原因)；不认领时原因为 None。"""
    fund = _norm(str(row.get("fund") or ""))
    record = index.get(("cn", fund))
    if not record:
        record = index.get(("en", _en(str(row.get("english") or ""))))
    if not record:
        return "", "", "官网基金库未收录该基金"
    if not _has_monthly_document(record["fund_id"]):
        return "", "", "官网该基金无月报文档"
    return (
        _detail_link(record),
        f"python download_chinaamc_factsheet.py {record['fund_id']}",
        "",
    )


def _hkex_code(english: str) -> str:
    """按英文基金名返回 HKEX 代码；参数 english 为页表英文名。"""
    return HKEX_CODE_BY_ENGLISH.get(str(english or "").strip(), "")


def _ensure_report_columns(excel_path: str, sheet_name: str) -> None:
    """为既有华夏页表追加半年报/年报字段；参数为工作簿和页表名。"""
    workbook = openpyxl.load_workbook(excel_path)
    sheet = workbook[sheet_name]
    existing = {str(cell.value or "").strip() for cell in sheet[1]}
    for header in ("半年报链接", "半年报运行方式", "年报链接", "年报运行方式", "HKEX代码"):
        if header not in existing:
            sheet.cell(1, sheet.max_column + 1, header)
    workbook.save(excel_path)
    workbook.close()


def _write_report_columns(excel_path: str, sheet_name: str) -> tuple[int, int]:
    """写入半年报/年报稳定入口；返回 (已写入, 缺少 HKEX 代码)。"""
    workbook = openpyxl.load_workbook(excel_path)
    sheet = workbook[sheet_name]
    header = {str(cell.value).strip(): cell.column for cell in sheet[1] if cell.value not in (None, "")}
    updated = 0
    missing = 0
    for row in range(2, sheet.max_row + 1):
        english = str(sheet.cell(row, header["英文"]).value or "").strip()
        fund = str(sheet.cell(row, header["基金"]).value or "").strip()
        code = _hkex_code(english)
        sheet.cell(row, header["HKEX代码"]).value = code
        if not code:
            missing += 1
            continue
        link = IFP_BASE + code
        sheet.cell(row, header["半年报链接"]).value = link
        sheet.cell(row, header["半年报运行方式"]).value = f'python {REPORT_DOWNLOADER} {code} --kind semiannual --lang EN'
        sheet.cell(row, header["年报链接"]).value = link
        sheet.cell(row, header["年报运行方式"]).value = f'python {REPORT_DOWNLOADER} {code} --kind annual --lang EN'
        updated += 1
    workbook.save(excel_path)
    workbook.close()
    return updated, missing


def _parse_option(argv: list[str], name: str, default: str) -> str:
    """读取命令行选项；参数 argv 为参数列表，name 为选项名。"""
    try:
        pos = argv.index(name)
    except ValueError:
        return default
    return argv[pos + 1] if pos + 1 < len(argv) else default


def main(argv: list[str] | None = None) -> int:
    """命令行入口：单基金查询 / --probe / --backfill。"""
    args = list(sys.argv[1:] if argv is None else argv)
    if "--backfill" in args or "--probe" in args:
        excel_path = _parse_option(args, "--excel", os.path.join(HERE, "..", "..", "..", "爬虫list.xlsx"))
        _ensure_report_columns(excel_path, _parse_option(args, "--sheet", SHEET_NAME))
    result = cli_main(
        args,
        description="按爬虫list.xlsx华夏表 B 列基金名查询官网月报链接（含批量补全）",
        sheet_name=SHEET_NAME,
        catalog_loader=_fetch_catalog,
        index_builder=_build_index,
        resolver=_resolve_single,
        single_print_run_command=True,
    )
    if "--backfill" in args:
        excel_path = _parse_option(args, "--excel", os.path.join(HERE, "..", "..", "..", "爬虫list.xlsx"))
        updated, missing = _write_report_columns(excel_path, _parse_option(args, "--sheet", SHEET_NAME))
        print(f"[*] 华夏半年报/年报字段已写入 {updated} 行，缺少 HKEX 代码 {missing} 行", flush=True)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
