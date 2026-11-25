#!/usr/bin/env python3
"""从 Sheet2 复制高盛行，创建正式适配器页；不写正式 I/J。"""
from __future__ import annotations
import copy
import shutil
from pathlib import Path
import openpyxl

ROOT = Path(__file__).resolve().parents[1]
WORKBOOK = ROOT / "爬虫list.xlsx"
BACKUP = ROOT / "爬虫list.xlsx.goldman-before-20261125.bak"

def main() -> None:
    """创建或重建高盛页。"""
    shutil.copy2(WORKBOOK, BACKUP)
    book = openpyxl.load_workbook(WORKBOOK)
    if "高盛" in book.sheetnames:
        del book["高盛"]
    source = book["Sheet2"]
    at = book.sheetnames.index("美富信") + 1 if "美富信" in book.sheetnames else len(book.sheetnames)
    target = book.create_sheet("高盛", at)
    for col in range(1, source.max_column + 1):
        letter = openpyxl.utils.get_column_letter(col)
        target.column_dimensions[letter].width = source.column_dimensions[letter].width
    for out, row in enumerate((r for r in source.iter_rows(min_row=1, max_col=source.max_column) if r[0].row == 1 or str(r[5].value or "").strip() == "高盛"), 1):
        for cell in row:
            new = target.cell(out, cell.column, cell.value)
            if cell.has_style:
                new._style = copy.copy(cell._style)
            new.number_format = cell.number_format
        if out > 1:
            target.cell(out, 1, out - 1)
            target.cell(out, 10, None)
            target.cell(out, 11, None)
    target.freeze_panes = "A2"
    target.auto_filter.ref = f"A1:K{target.max_row}"
    book.save(WORKBOOK)
    print(f"created={target.max_row - 1} backup={BACKUP}")

if __name__ == "__main__":
    main()
