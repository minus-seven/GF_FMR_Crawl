#!/usr/bin/env python3
"""把法盛资本/威灵顿临时回填按基金身份校验后落到正式工作簿。"""

from __future__ import annotations

import shutil
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
FORMAL = ROOT / "爬虫list.xlsx"
TEMP = Path("/tmp/natixis-wellington-backfill-20261125b.xlsx")
BACKUP = ROOT / "爬虫list.xlsx.before-natixis-wellington-promote-20261125.bak"
SHEETS = ("法盛资本", "威灵顿")


def _identity(sheet: openpyxl.worksheet.worksheet.Worksheet, row: int) -> tuple[str, str, str]:
    """返回工作表中用于防错的基金身份；参数 sheet/row 为目标行。"""

    return tuple(str(sheet.cell(row, col).value or "").strip() for col in (2, 3, 5))


def main() -> None:
    """校验临时结果并写入正式工作簿。"""

    if not TEMP.exists():
        raise SystemExit(f"临时回填不存在：{TEMP}")
    shutil.copy2(FORMAL, BACKUP)
    formal = openpyxl.load_workbook(FORMAL)
    temp = openpyxl.load_workbook(TEMP, data_only=True)
    promoted = 0
    rejected: list[str] = []
    for sheet_name in SHEETS:
        dst = formal[sheet_name]
        src = temp[sheet_name]
        for row in range(2, src.max_row + 1):
            if _identity(dst, row) != _identity(src, row):
                rejected.append(f"{sheet_name}!{row}: identity mismatch")
                continue
            link = str(src.cell(row, 10).value or "").strip()
            command = str(src.cell(row, 11).value or "").strip()
            if not link or not command:
                continue
            dst.cell(row, 10, link)
            dst.cell(row, 11, command)
            promoted += 1
    temp.close()
    formal.save(FORMAL)
    print(f"promoted={promoted} rejected={len(rejected)} backup={BACKUP}")
    for item in rejected:
        print(f"REJECT {item}")


if __name__ == "__main__":
    main()
