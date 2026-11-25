#!/usr/bin/env python3
"""从 Sheet2 建立信安页表；正式页表的 I/J 保持空白。"""

from __future__ import annotations

import shutil
from copy import copy
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
WORKBOOK = ROOT / "爬虫list.xlsx"
SHEET = "信安"


def main() -> int:
    """创建备份并追加信安工作表。"""

    backup = ROOT / "爬虫list.xlsx.principal-before-20261125.bak"
    if not backup.exists():
        shutil.copy2(WORKBOOK, backup)
    book = openpyxl.load_workbook(WORKBOOK)
    if SHEET in book.sheetnames:
        book.close()
        print(f"[*] {SHEET} 已存在，未覆盖")
        return 0
    source = book["Sheet2"]
    src_header = {str(c.value or "").strip(): c.column for c in source[1]}
    target = book.create_sheet(SHEET)
    for col in range(1, source.max_column + 1):
        target.cell(1, col).value = source.cell(1, col).value
        if source.cell(1, col).has_style:
            target.cell(1, col)._style = copy(source.cell(1, col)._style)
    managers = {"信安"}
    rows = []
    for row in source.iter_rows(min_row=2, values_only=False):
        manager = str(row[src_header["管理人"] - 1].value or "").strip()
        if manager in managers:
            values = [cell.value for cell in row[: source.max_column]]
            rows.append(values)
    for idx, values in enumerate(rows, start=2):
        for col, value in enumerate(values, start=1):
            target.cell(idx, col).value = value
            if source.cell(2, col).has_style:
                target.cell(idx, col)._style = copy(source.cell(2, col)._style)
        target.cell(idx, src_header.get("月报链接", 10)).value = None
        target.cell(idx, src_header.get("运行方式", 11)).value = None
    target.freeze_panes = "A2"
    book.save(WORKBOOK)
    book.close()
    print(f"[*] 已创建 {SHEET}：{len(rows)} 行；正式工作簿 I/J 保持空白")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
