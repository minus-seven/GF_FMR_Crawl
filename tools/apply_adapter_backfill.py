#!/usr/bin/env python3
"""将单一适配器解析出的 I/J 写入临时工作簿副本。

正式工作簿仍由各公司的 add_*_sheet.py 创建并保持 I/J 空白；此工具只用于
SOP 阶段 4 的临时回填、run_all_downloaders 预览和抽样运行。
"""
from __future__ import annotations

import argparse
import importlib.util
import shutil
import sys
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))


def main() -> int:
    """解析参数并把可验证的官方链接写入 output 工作簿。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter", required=True, help="*_fund_link.py 相对 code 的路径")
    parser.add_argument("--input", default=str(ROOT / "爬虫list.xlsx"))
    parser.add_argument("--output", required=True)
    parser.add_argument("--sheet", default="")
    args = parser.parse_args()
    shutil.copy2(args.input, args.output)
    spec = importlib.util.spec_from_file_location("adapter_module", ROOT / "code" / args.adapter)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载适配器：{args.adapter}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sheet_name = args.sheet or str(getattr(module, "SHEET_NAME", ""))
    if not sheet_name:
        raise RuntimeError("适配器没有 SHEET_NAME，必须显式传 --sheet")
    # Adapters created before the SOP used ``_catalog_loader`` while newer ones
    # expose ``_fetch_catalog``.  Support both so a temporary backfill never
    # fails merely because of the adapter's historical naming convention.
    loader = getattr(module, "_fetch_catalog", None) or getattr(module, "_catalog_loader", None)
    if loader is None:
        raise RuntimeError(f"适配器没有 _fetch_catalog/_catalog_loader：{args.adapter}")
    catalog = loader()
    index = module._build_index(catalog)
    book = openpyxl.load_workbook(args.output)
    sheet = book[sheet_name]
    header = {str(c.value or "").strip(): c.column for c in sheet[1] if c.value not in (None, "")}
    fund_col = header.get("基金", 2)
    english_col = header.get("英文", 5)
    umbrella_col = header.get("伞", 3)
    monthly_col = header.get("月报链接", 10)
    command_col = header.get("运行方式", 11)
    for row in range(2, sheet.max_row + 1):
        name = str(sheet.cell(row, fund_col).value or "").strip()
        if not name:
            continue
        item = {
            "fund": name,
            "umbrella": str(sheet.cell(row, umbrella_col).value or "").strip(),
            "english": str(sheet.cell(row, english_col).value or "").strip(),
        }
        link, command, _ = module._resolve_single(item, index)
        if link:
            sheet.cell(row, monthly_col, link)
            sheet.cell(row, command_col, command)
    book.save(args.output)
    print(f"written={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
