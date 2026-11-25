#!/usr/bin/env python3
"""按基金身份校验嘉实临时回填并写入正式工作簿。"""

from __future__ import annotations

import shutil
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
FORMAL = ROOT / "爬虫list.xlsx"
TEMP = Path("/tmp/harvest-backfill-20261125.xlsx")
BACKUP = ROOT / "爬虫list.xlsx.before-harvest-promote-20261125.bak"


def main() -> None:
    """校验基金中文名、英文名和伞名后写入月报链接/运行方式。"""

    if not TEMP.exists():
        raise SystemExit(f"临时副本不存在：{TEMP}")
    shutil.copy2(FORMAL, BACKUP)
    formal = openpyxl.load_workbook(FORMAL)
    temp = openpyxl.load_workbook(TEMP, data_only=True)
    dst = formal["嘉实"]
    src = temp["嘉实"]
    dh = {str(c.value or "").strip(): c.column for c in dst[1]}
    sh = {str(c.value or "").strip(): c.column for c in src[1]}
    promoted = 0
    rejected = 0
    for row in range(2, src.max_row + 1):
        identity = tuple(str(src.cell(row, sh[name]).value or "").strip() for name in ("基金", "伞", "英文"))
        target_identity = tuple(str(dst.cell(row, dh[name]).value or "").strip() for name in ("基金", "伞", "英文"))
        if identity != target_identity:
            rejected += 1
            continue
        link = str(src.cell(row, sh["月报链接"]).value or "").strip()
        command = str(src.cell(row, sh["运行方式"]).value or "").strip()
        if link and command:
            dst.cell(row, dh["月报链接"], link)
            dst.cell(row, dh["运行方式"], command)
            promoted += 1
    temp.close()
    formal.save(FORMAL)
    print(f"promoted={promoted} rejected={rejected} backup={BACKUP}")


if __name__ == "__main__":
    main()
