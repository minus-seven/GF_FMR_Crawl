#!/usr/bin/env python3
"""按基金身份校验单个适配器临时回填，并晋升到正式爬虫工作簿。

临时副本由 ``apply_adapter_backfill.py`` 生成。本工具只写入同名页表的
“月报链接/运行方式”，并以基金、伞、英文三元组做行级身份校验；不接受
临时副本新增的基金行，避免适配器误把官网其他产品混入正式主数据。
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[1]


def _headers(sheet) -> dict[str, int]:
    """读取页表首行；参数 sheet 为 openpyxl 工作表对象。"""

    return {
        str(cell.value or "").strip(): cell.column
        for cell in sheet[1]
        if cell.value not in (None, "")
    }


def _identity(sheet, header: dict[str, int], row: int) -> tuple[str, str, str]:
    """构造基金身份键；参数 sheet 为工作表、header 为列索引、row 为行号。"""

    return tuple(
        str(sheet.cell(row, header.get(name, 0)).value or "").strip()
        if header.get(name)
        else ""
        for name in ("基金", "伞", "英文")
    )


def main() -> int:
    """解析临时副本和正式工作簿参数并执行安全晋升。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--sheet", required=True)
    parser.add_argument("--temp", required=True, type=Path)
    parser.add_argument("--formal", default=str(ROOT / "爬虫list.xlsx"), type=Path)
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    if not args.temp.is_file():
        raise SystemExit(f"临时副本不存在：{args.temp}")
    backup = args.backup or args.formal.with_name(
        f"{args.formal.name}.before-{args.sheet}-promote-20261125.bak"
    )
    shutil.copy2(args.formal, backup)
    formal = openpyxl.load_workbook(args.formal)
    temp = openpyxl.load_workbook(args.temp, data_only=True)
    if args.sheet not in formal.sheetnames or args.sheet not in temp.sheetnames:
        raise SystemExit(f"正式/临时工作簿缺少页表：{args.sheet}")
    dst, src = formal[args.sheet], temp[args.sheet]
    dh, sh = _headers(dst), _headers(src)
    required = {"基金", "月报链接", "运行方式"}
    if not required.issubset(dh) or not required.issubset(sh):
        raise SystemExit(f"页表缺少字段：{sorted(required - set(dh))} / {sorted(required - set(sh))}")
    dst_by_identity = {
        _identity(dst, dh, row): row
        for row in range(2, dst.max_row + 1)
        if _identity(dst, dh, row)[0]
    }
    promoted = rejected = 0
    for row in range(2, src.max_row + 1):
        identity = _identity(src, sh, row)
        target_row = dst_by_identity.get(identity)
        if not target_row:
            rejected += 1
            continue
        link = str(src.cell(row, sh["月报链接"]).value or "").strip()
        command = str(src.cell(row, sh["运行方式"]).value or "").strip()
        if link and command:
            dst.cell(target_row, dh["月报链接"], link)
            dst.cell(target_row, dh["运行方式"], command)
            promoted += 1
    temp.close()
    formal.save(args.formal)
    print(f"promoted={promoted} rejected={rejected} backup={backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
