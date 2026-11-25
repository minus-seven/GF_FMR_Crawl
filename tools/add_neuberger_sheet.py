#!/usr/bin/env python3
"""从 Sheet2 复制路博迈行，创建正式适配器页；不写正式 I/J。"""

from __future__ import annotations

import copy
import shutil
from pathlib import Path

import openpyxl


ROOT = Path(__file__).resolve().parents[1]
WORKBOOK = ROOT / "爬虫list.xlsx"
BACKUP = ROOT / "爬虫list.xlsx.neuberger-before-20261125.bak"


def main() -> None:
    """复制 Sheet2 中路博迈行并保持样式；无参数。"""

    shutil.copy2(WORKBOOK, BACKUP)
    book = openpyxl.load_workbook(WORKBOOK)
    if "路博迈" in book.sheetnames:
        del book["路博迈"]
    source = book["Sheet2"]
    insert_at = book.sheetnames.index("资本集团") + 1 if "资本集团" in book.sheetnames else len(book.sheetnames)
    target = book.create_sheet("路博迈", insert_at)
    for col in range(1, source.max_column + 1):
        letter = openpyxl.utils.get_column_letter(col)
        target.column_dimensions[letter].width = source.column_dimensions[letter].width
    rows = (
        row
        for row in source.iter_rows(min_row=1, max_col=source.max_column)
        if row[0].row == 1 or str(row[5].value or "").strip() == "路博迈"
    )
    for output_row, row in enumerate(rows, start=1):
        for cell in row:
            new = target.cell(output_row, cell.column, cell.value)
            if cell.has_style:
                new._style = copy.copy(cell._style)
            if cell.number_format:
                new.number_format = cell.number_format
        if output_row > 1:
            target.cell(output_row, 1, output_row - 1)
            target.cell(output_row, 10, None)
            target.cell(output_row, 11, None)
    target.freeze_panes = "A2"
    target.auto_filter.ref = f"A1:K{target.max_row}"
    book.save(WORKBOOK)
    print(f"created={target.max_row - 1} backup={BACKUP}")


if __name__ == "__main__":
    main()
