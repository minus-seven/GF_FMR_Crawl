#!/usr/bin/env python3
"""为本轮五家管理人建立独立页表并保留主数据身份。"""

from __future__ import annotations

import shutil
from copy import copy
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
WORKBOOK = ROOT / "爬虫list.xlsx"
MANAGERS = ("思博瑞资管", "摩根斯坦利", "工银资管", "未来", "野村")


def _copy_sheet(book: openpyxl.Workbook, manager: str) -> int:
    """从 Sheet2 复制指定管理人行；参数 book 为工作簿、manager 为管理人名。"""
    if manager in book.sheetnames:
        return book[manager].max_row - 1
    source = book["Sheet2"]
    header = {str(cell.value or "").strip(): cell.column for cell in source[1]}
    target = book.create_sheet(manager)
    for col in range(1, source.max_column + 1):
        target.cell(1, col).value = source.cell(1, col).value
        if source.cell(1, col).has_style:
            target.cell(1, col)._style = copy(source.cell(1, col)._style)
    count = 0
    for row in range(2, source.max_row + 1):
        if str(source.cell(row, header["管理人"]).value or "").strip() != manager:
            continue
        count += 1
        for col in range(1, source.max_column + 1):
            target.cell(count + 1, col).value = source.cell(row, col).value
            if source.cell(row, col).has_style:
                target.cell(count + 1, col)._style = copy(source.cell(row, col)._style)
    return count


def main() -> int:
    """创建备份并追加五家管理人页表；无参数。"""
    backup = WORKBOOK.with_name("爬虫list.xlsx.before-five-target-adapters-20261125.bak")
    if not backup.exists():
        shutil.copy2(WORKBOOK, backup)
    book = openpyxl.load_workbook(WORKBOOK)
    counts = {manager: _copy_sheet(book, manager) for manager in MANAGERS}
    book.save(WORKBOOK)
    book.close()
    print(" ".join(f"{key}={value}" for key, value in counts.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
