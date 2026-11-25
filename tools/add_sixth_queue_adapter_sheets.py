#!/usr/bin/env python3
"""为第六批无适配器管理人创建独立页表。"""
from __future__ import annotations
import shutil
from copy import copy
from pathlib import Path
import openpyxl

ROOT = Path(__file__).resolve().parents[1]
WORKBOOK = ROOT / "爬虫list.xlsx"
MANAGERS = ("民银资本", "永明", "申万宏源", "罗安贝", "英仕曼", "融通基金", "誉信", "兴证国际", "华富建业", "国信", "国投证券", "国泰", "国泰全球", "国海富兰克林", "小飞", "工银", "巴克莱", "弘收投资", "日兴资产", "日兴资管", "时富金融", "民银", "永丰金", "法盛", "简坤", "融通国际", "资威", "辉立资本", "银河", "长盛基金", "鹏格斯资本")


def _copy_sheet(book, manager: str) -> int:
    """复制管理人行；参数 book 为工作簿、manager 为管理人名称。"""
    if manager in book.sheetnames:
        return book[manager].max_row - 1
    source = book["Sheet2"]
    header = {str(c.value or "").strip(): c.column for c in source[1]}
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


def main() -> None:
    """创建页表并保留备份。"""
    backup = WORKBOOK.with_name("爬虫list.xlsx.before-31-sixth-queue-adapters-20261126.bak")
    if not backup.exists():
        shutil.copy2(WORKBOOK, backup)
    book = openpyxl.load_workbook(WORKBOOK)
    counts = {manager: _copy_sheet(book, manager) for manager in MANAGERS}
    book.save(WORKBOOK)
    book.close()
    print(" ".join(f"{name}={count}" for name, count in counts.items()))


if __name__ == "__main__":
    main()
