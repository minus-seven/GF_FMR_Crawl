#!/usr/bin/env python3
"""Create the 恒生 adapter sheet from Sheet2 without writing formal I/J values."""

from __future__ import annotations

import copy
import shutil
from pathlib import Path

import openpyxl


ROOT = Path(__file__).resolve().parents[1]
WORKBOOK = ROOT / "爬虫list.xlsx"
BACKUP = ROOT / "爬虫list.xlsx.hangseng-before-20261125.bak"


def main() -> None:
    """Copy Sheet2 恒生 rows into a styled 恒生 sheet; no arguments."""

    shutil.copy2(WORKBOOK, BACKUP)
    book = openpyxl.load_workbook(WORKBOOK)
    if "恒生" in book.sheetnames:
        del book["恒生"]
    source = book["Sheet2"]
    target = book.create_sheet("恒生", book.sheetnames.index("安本标准") + 1)
    for col in range(1, source.max_column + 1):
        target.column_dimensions[openpyxl.utils.get_column_letter(col)].width = source.column_dimensions[openpyxl.utils.get_column_letter(col)].width
    for row in source.iter_rows(min_row=1, max_row=1, max_col=source.max_column):
        for cell in row:
            new = target.cell(cell.row, cell.column, cell.value)
            if cell.has_style:
                new._style = copy.copy(cell._style)
            if cell.number_format:
                new.number_format = cell.number_format
    output_row = 2
    for row in source.iter_rows(min_row=2, max_col=source.max_column):
        if str(row[5].value or "").strip() != "恒生":
            continue
        for cell in row:
            new = target.cell(output_row, cell.column, cell.value)
            if cell.has_style:
                new._style = copy.copy(cell._style)
            if cell.number_format:
                new.number_format = cell.number_format
        target.cell(output_row, 1, output_row - 1)
        target.cell(output_row, 10, None)
        target.cell(output_row, 11, None)
        output_row += 1
    target.freeze_panes = "A2"
    target.auto_filter.ref = f"A1:K{output_row - 1}"
    book.save(WORKBOOK)
    print(f"created={output_row - 2} backup={BACKUP}")


if __name__ == "__main__":
    main()
