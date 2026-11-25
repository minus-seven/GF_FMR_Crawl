#!/usr/bin/env python3
"""从 Sheet2 复制威灵顿行，创建正式适配器页；I/J 初始留空。"""

from __future__ import annotations

import copy
import shutil
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
WORKBOOK = ROOT / "爬虫list.xlsx"
BACKUP = ROOT / "爬虫list.xlsx.wellington-before-20261125.bak"
SHEET_NAME = "威灵顿"


def main() -> None:
    """创建威灵顿工作表。"""

    shutil.copy2(WORKBOOK, BACKUP)
    book = openpyxl.load_workbook(WORKBOOK)
    if SHEET_NAME in book.sheetnames:
        del book[SHEET_NAME]
    source = book["Sheet2"]
    at = book.sheetnames.index("威灵顿") + 1 if "威灵顿" in book.sheetnames else len(book.sheetnames)
    target = book.create_sheet(SHEET_NAME, at)
    for col in range(1, source.max_column + 1):
        letter = openpyxl.utils.get_column_letter(col)
        target.column_dimensions[letter].width = source.column_dimensions[letter].width
    output = 0
    for row in source.iter_rows(min_row=1, max_col=source.max_column):
        if row[0].row != 1 and str(row[5].value or "").strip() != SHEET_NAME:
            continue
        output += 1
        for cell in row:
            new = target.cell(output, cell.column, cell.value)
            if cell.has_style:
                new._style = copy.copy(cell._style)
            new.number_format = cell.number_format
        if output > 1:
            target.cell(output, 1, output - 1)
            target.cell(output, 10, None)
            target.cell(output, 11, None)
    target.freeze_panes = "A2"
    target.auto_filter.ref = f"A1:K{target.max_row}"
    book.save(WORKBOOK)
    print(f"created={target.max_row - 1} backup={BACKUP}")


if __name__ == "__main__":
    main()
