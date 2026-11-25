#!/usr/bin/env python3
"""Create dedicated crawler sheets for the fourth no-adapter queue."""
from __future__ import annotations
import shutil
from copy import copy
from pathlib import Path
import openpyxl
ROOT = Path(__file__).resolve().parents[1]
WORKBOOK = ROOT / "爬虫list.xlsx"
MANAGERS = ("招证资管", "瑞士盈丰", "资产管理集团", "铭基环球", "中欧", "信达", "信银资本", "光大")
def _copy_sheet(book, manager):
    if manager in book.sheetnames: return book[manager].max_row - 1
    source = book["Sheet2"]; header = {str(c.value or "").strip(): c.column for c in source[1]}
    target = book.create_sheet(manager)
    for col in range(1, source.max_column + 1):
        target.cell(1, col).value = source.cell(1, col).value
        if source.cell(1, col).has_style: target.cell(1, col)._style = copy(source.cell(1, col)._style)
    count = 0
    for row in range(2, source.max_row + 1):
        if str(source.cell(row, header["管理人"]).value or "").strip() != manager: continue
        count += 1
        for col in range(1, source.max_column + 1):
            target.cell(count + 1, col).value = source.cell(row, col).value
            if source.cell(row, col).has_style: target.cell(count + 1, col)._style = copy(source.cell(row, col)._style)
    return count
def main():
    backup = WORKBOOK.with_name("爬虫list.xlsx.before-eight-fourth-queue-adapters-20261126.bak")
    if not backup.exists(): shutil.copy2(WORKBOOK, backup)
    book = openpyxl.load_workbook(WORKBOOK)
    counts = {manager: _copy_sheet(book, manager) for manager in MANAGERS}
    book.save(WORKBOOK); book.close()
    print(" ".join(f"{k}={v}" for k, v in counts.items()))
if __name__ == "__main__": main()
