# -*- coding: utf-8 -*-
"""基金月报爬虫补全的公共逻辑（贝莱德 / 博时等公司共用）。

公司脚本（<公司>_fund_link.py）只需提供三件事：
  1. catalog_loader()：抓取官网“基金名 → 基金代码/类别”的目录数据；
  2. index_builder(catalog)：把目录转成“归一化基金名 → 该基金的所有类别行”；
  3. resolver(fund, umbrella, index)：返回该基金要回填的
     (I月报链接, J运行方式, 跳过原因)。

本模块提供：名称归一化、币种优先级、Excel 扫描、进度条渲染、
probe/backfill 批量流程，以及统一的命令行入口。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from typing import Any, Callable

import openpyxl
import requests
from opencc import OpenCC


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEFAULT_EXCEL = os.path.join(PROJECT_ROOT, "爬虫list.xlsx")
PROGRESS_BAR_WIDTH = 20
CC = OpenCC("s2t")
# 币种优先级：港元最优先，其次美元，最后其他
CURRENCY_RANK = {"HKD": 0, "港元": 0, "港幣": 0, "USD": 1, "美元": 1}
ROMAN_MAP = {"I": "Ⅰ", "II": "Ⅱ", "III": "Ⅲ", "IV": "Ⅳ", "V": "Ⅴ"}

# resolver 返回类型：(I月报链接, J运行方式, 跳过原因)
Resolver = Callable[[str, str, dict[str, Any]], tuple[str, str, str]]
# 目录索引加载/构建回调
CatalogLoader = Callable[[], list[dict[str, Any]]]
IndexBuilder = Callable[[list[dict[str, Any]]], dict[str, Any]]


def _retry_backfill_call(
    func: Callable[[], Any],
    *,
    attempts: int = 3,
    base_delay: float = 2.0,
) -> Any:
    """重试目录或单行解析的瞬时网络错误；参数 func/attempts/base_delay。"""

    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return func()
        except (requests.RequestException, TimeoutError, ConnectionError) as exc:
            last_error = exc
            if attempt >= attempts:
                break
            time.sleep(base_delay * (2 ** (attempt - 1)))
    assert last_error is not None
    raise last_error


def normalize_name(value: str, *, roman: bool = False) -> str:
    """名称归一化：简→繁、去空格、统一连字符/括号。

    参数 value：原始名称；参数 roman：是否把 “- I/- II/- III” 统一成 Ⅰ/Ⅱ/Ⅲ。
    """
    text = CC.convert(str(value or ""))
    text = text.replace("\u3000", "").replace(" ", "").replace("\u00a0", "")
    for src, dst in (
        ("埸", "場"),
        ("巿", "市"),
        ("鉤", "鈎"),
        ("（", "("),
        ("）", ")"),
        ("－", "-"),
        ("–", "-"),
        ("—", "-"),
        ("‐", "-"),
    ):
        text = text.replace(src, dst)
    if roman:
        def _roman(match):
            """把 “-I/-II/-III” 转成官网常用的 Ⅰ/Ⅱ/Ⅲ 写法。"""
            token = match.group(1)
            return "-" + ROMAN_MAP.get(token.upper(), token)

        text = re.sub(r"-(I{1,3}|IV|V)\b", _roman, text)
    # 去掉“（清算中）”等结尾括注，仅用于比较
    return re.sub(r"\([^()]*\)$", "", text)


def currency_rank(record: dict[str, Any], *, field: str = "currency") -> int:
    """返回币种优先级数值，越小越优先。"""
    return CURRENCY_RANK.get(str(record.get(field) or "").upper(), 2)


def pick_share_class(
    records: list[dict[str, Any]], *, field: str = "currency"
) -> dict[str, Any]:
    """按币种优先级选一支类别；同币种内取目录默认第一条。"""
    return sorted(records, key=lambda item: currency_rank(item, field=field))[0]


def build_index(
    catalog: list[dict[str, Any]], *, key: str = "fund_name", roman: bool = False
) -> dict[str, Any]:
    """按归一化基金名建立目录索引。"""
    index: dict[str, list[dict[str, Any]]] = {}
    for record in catalog:
        index.setdefault(
            normalize_name(record.get(key, ""), roman=roman), []
        ).append(record)
    return index


def load_fund_rows(excel_path: str, sheet_name: str) -> list[dict[str, Any]]:
    """读取工作表的 B/C 及可选的“全称/英文”列，返回行号、基金、伞、全称、英文名。"""
    workbook = openpyxl.load_workbook(excel_path, read_only=True, data_only=True)
    if sheet_name not in workbook.sheetnames:
        available = "、".join(workbook.sheetnames)
        workbook.close()
        raise RuntimeError(f"爬虫表里没有工作表：{sheet_name}（可用：{available}）")
    sheet = workbook[sheet_name]
    header = [str(cell.value).strip() if cell.value is not None else "" for cell in sheet[1]]
    english_col = None
    for name in ("英文", "英文名", "English"):
        if name in header:
            english_col = header.index(name)
            break
    # “全称”列可选（后续按表头定位，不再假设固定第 4 列）
    full_name_col = header.index("全称") if "全称" in header else None
    rows: list[dict[str, Any]] = []
    for row_index in range(2, sheet.max_row + 1):
        fund = sheet.cell(row_index, 2).value
        if fund is None or str(fund).strip() == "":
            continue
        rows.append(
            {
                "row": row_index,
                "fund": str(fund).strip(),
                "umbrella": str(sheet.cell(row_index, 3).value or "").strip(),
                "full_name": (
                    str(sheet.cell(row_index, full_name_col + 1).value or "").strip()
                    if full_name_col is not None
                    else ""
                ),
                "english": (
                    str(sheet.cell(row_index, english_col + 1).value or "").strip()
                    if english_col is not None
                    else ""
                ),
            }
        )
    workbook.close()
    return rows


def scan_incomplete_rows(
    excel_path: str,
    sheet_name: str,
    extra_check: Callable[[str, str], bool] | None = None,
) -> list[dict[str, Any]]:
    """扫描“月报链接或运行方式不完整”的行。

    参数 extra_check：公司自定义判定，入参 (I月报链接, J运行方式)，返回 True 视为需补全。
    """
    workbook = openpyxl.load_workbook(excel_path, data_only=False)
    if sheet_name not in workbook.sheetnames:
        available = "、".join(workbook.sheetnames)
        workbook.close()
        raise RuntimeError(f"爬虫表里没有工作表：{sheet_name}（可用：{available}）")
    sheet = workbook[sheet_name]
    header = [str(c.value).strip() if c.value is not None else "" for c in sheet[1]]
    index = {name: pos for pos, name in enumerate(header)}

    def column(name: str, fallback: int) -> int:
        """按表头找列序号，找不到时用固定列序号兜底。"""
        return index.get(name, fallback)

    fund_col = column("基金", 1)
    umbrella_col = column("伞", 2)
    link_col = column("月报链接", 8)
    run_col = column("运行方式", 9)
    english_col = None
    for name in ("英文", "英文名", "English"):
        if name in index:
            english_col = index[name]
            break
    incomplete: list[dict[str, Any]] = []
    for row_index in range(2, sheet.max_row + 1):
        fund = sheet.cell(row_index, fund_col + 1).value
        if fund is None or str(fund).strip() == "":
            continue
        link = str(sheet.cell(row_index, link_col + 1).value or "").strip()
        run_command = str(sheet.cell(row_index, run_col + 1).value or "").strip()
        if link and run_command and not (extra_check and extra_check(link, run_command)):
            continue
        incomplete.append(
            {
                "excel_row": row_index,
                "fund": str(fund).strip(),
                "umbrella": str(sheet.cell(row_index, umbrella_col + 1).value or "").strip(),
                "link": link,
                "run_command": run_command,
                "english": (
                    str(sheet.cell(row_index, english_col + 1).value or "").strip()
                    if english_col is not None
                    else ""
                ),
            }
        )
    workbook.close()
    return incomplete


def format_progress_line(
    completed: int, total: int, succeeded: int, failed: int, current_fund: str = ""
) -> str:
    """渲染与下载进度同款的补全进度行（含字符串进度条与正在处理基金）。"""
    ratio = completed / total if total else 1
    filled = min(PROGRESS_BAR_WIDTH, int(round(ratio * PROGRESS_BAR_WIDTH)))
    bar = "█" * filled + "░" * (PROGRESS_BAR_WIDTH - filled)
    line = (
        f"下载进度 - [{bar}] {completed}/{total}"
        f"  成功 {succeeded}  失败 {failed}"
    )
    if current_fund:
        line += f"  正在：{current_fund}"
    return line


def print_ok(row: int, fund: str, umbrella: str, link: str, run_command: str) -> None:
    """输出一行补全成功结果（run_all_downloaders 按此契约回填 I/J）。"""
    print(f"OK\t{row}\t{fund}\t{umbrella}\t{link}\t{run_command}", flush=True)


def print_skip(row: int, fund: str, umbrella: str, reason: str) -> None:
    """输出一行补全跳过结果（原因汇总进日志与 GUI 结果明细）。"""
    print(f"SKIP\t{row}\t{fund}\t{umbrella}\t\t{reason}", flush=True)


def run_probe(
    excel_path: str,
    sheet_name: str,
    extra_check: Callable[[str, str], bool] | None = None,
    row_numbers: set[int] | None = None,
) -> int:
    """探测模式：只输出指定待补全行（行号/基金/伞），不做网络请求。"""
    rows = scan_incomplete_rows(excel_path, sheet_name, extra_check)
    if row_numbers is not None:
        rows = [item for item in rows if item["excel_row"] in row_numbers]
    for item in rows:
        print(f"{item['excel_row']}\t{item['fund']}\t{item['umbrella']}")
    return len(rows)


def run_backfill(
    excel_path: str,
    sheet_name: str,
    *,
    catalog_loader: CatalogLoader,
    index_builder: IndexBuilder,
    resolver: Resolver,
    extra_check: Callable[[str, str], bool] | None = None,
    row_numbers: set[int] | None = None,
) -> int:
    """批量补全：扫描指定待补全行，逐行输出 I/J 回填值或跳过原因。"""
    incomplete = scan_incomplete_rows(excel_path, sheet_name, extra_check)
    if row_numbers is not None:
        incomplete = [
            item for item in incomplete if item["excel_row"] in row_numbers
        ]
    if not incomplete:
        print(f"[*] {sheet_name}表所选行没有需要补全的行")
        return 0
    total = len(incomplete)
    succeeded = 0
    failed = 0
    try:
        index = _retry_backfill_call(
            lambda: index_builder(catalog_loader())
        )
    except Exception as exc:  # noqa: BLE001
        reason = (
            "官网目录读取失败"
            f"（{type(exc).__name__}: {str(exc)[:160]}）"
        )
        for item in incomplete:
            print_skip(
                item["excel_row"],
                item["fund"],
                item["umbrella"],
                reason,
            )
        print(
            format_progress_line(total, total, 0, total),
            flush=True,
        )
        return 0
    for position, item in enumerate(incomplete, start=1):
        print(
            format_progress_line(position - 1, total, succeeded, failed, item["fund"]),
            flush=True,
        )
        try:
            link, run_command, reason = _retry_backfill_call(
                lambda item=item: resolver(item, index)
            )
        except Exception as exc:  # noqa: BLE001
            link, run_command = "", ""
            reason = (
                "单基金网络查询失败"
                f"（{type(exc).__name__}: {str(exc)[:160]}）"
            )
        if not link:
            if reason is None:
                # 该脚本不认领这一行（例如把富达页表交给其它公司的脚本处理），
                # 不输出 SKIP，由调用方汇总为最终跳过项。
                continue
            failed += 1
            print_skip(
                item["excel_row"],
                item["fund"],
                item["umbrella"],
                reason or "未匹配到官网基金，跳过",
            )
            continue
        succeeded += 1
        print_ok(item["excel_row"], item["fund"], item["umbrella"], link, run_command)
    print(format_progress_line(total, total, succeeded, failed), flush=True)
    return succeeded


def run_single(
    fund_name: str,
    *,
    umbrella: str,
    excel_path: str,
    sheet_name: str,
    catalog_loader: CatalogLoader,
    index_builder: IndexBuilder,
    resolver: Resolver,
    print_run_command: bool = False,
) -> int:
    """单基金查询：打印匹配到的 I/J（多伞时按“伞<TAB>I<TAB>J”输出）。"""
    rows = [
        row
        for row in load_fund_rows(excel_path, sheet_name)
        if row["fund"] == fund_name and (not umbrella or row["umbrella"] == umbrella)
    ]
    if not rows:
        print(f"[!] {sheet_name}表未找到基金名：{fund_name}", file=sys.stderr)
        return 1
    index = index_builder(catalog_loader())
    results = []
    for row in rows:
        results.append(
            (row["umbrella"], *resolver(row, index))
        )
    for item_umbrella, link, run_command, reason in results:
        if not link:
            print(f"[!] {item_umbrella or sheet_name}：{reason or '未匹配到官网基金'}", file=sys.stderr)
            continue
        if len(results) == 1:
            print(link)
            if print_run_command:
                print(run_command)
        else:
            print(f"{item_umbrella}\t{link}\t{run_command}")
    return 0


def cli_main(
    argv: list[str] | None,
    *,
    description: str,
    sheet_name: str,
    catalog_loader: CatalogLoader,
    index_builder: IndexBuilder,
    resolver: Resolver,
    extra_check: Callable[[str, str], bool] | None = None,
    single_print_run_command: bool = False,
) -> int:
    """统一的命令行入口：单基金查询 / --probe / --backfill。"""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("fund_name", nargs="?", default="", help="B 列基金名")
    parser.add_argument("--umbrella", default="", help="C 列伞名，用于同名精确匹配")
    parser.add_argument("--excel", default=DEFAULT_EXCEL, help="爬虫list.xlsx 路径")
    parser.add_argument("--probe", action="store_true", help="只探测待补全行")
    parser.add_argument("--backfill", action="store_true", help="批量输出 I/J 回填值")
    parser.add_argument("--sheet", default=sheet_name, help="处理的工作表名")
    parser.add_argument(
        "--rows",
        default="",
        help="仅处理逗号分隔的 Excel 行号；平台任务用于限制补全范围。",
    )
    args = parser.parse_args(argv)
    try:
        row_numbers = {
            int(value.strip())
            for value in str(args.rows or "").split(",")
            if value.strip()
        }
        if any(value < 2 for value in row_numbers):
            parser.error("--rows 中的 Excel 行号必须大于等于 2")
        if args.probe:
            run_probe(
                args.excel,
                args.sheet,
                extra_check,
                row_numbers=row_numbers or None,
            )
            return 0
        if args.backfill:
            run_backfill(
                args.excel,
                args.sheet,
                catalog_loader=catalog_loader,
                index_builder=index_builder,
                resolver=resolver,
                extra_check=extra_check,
                row_numbers=row_numbers or None,
            )
            return 0
        if not args.fund_name:
            parser.error("请提供 B 列基金名，或使用 --probe/--backfill")
        return run_single(
            args.fund_name,
            umbrella=args.umbrella,
            excel_path=args.excel,
            sheet_name=args.sheet,
            catalog_loader=catalog_loader,
            index_builder=index_builder,
            resolver=resolver,
            print_run_command=single_print_run_command,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[!] 查询失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
