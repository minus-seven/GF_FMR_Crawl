from __future__ import annotations

import importlib.util
from pathlib import Path

from openpyxl import load_workbook


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _load(name: str, path: Path):
    """加载指定 Python 模块；参数 name/path 为模块名和文件路径。"""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


csop = _load("csop_fund_link", PROJECT_ROOT / "code/companies/csop/csop_fund_link.py")
download = _load(
    "download_csop_factsheet",
    PROJECT_ROOT / "code/companies/csop/download_csop_factsheet.py",
)


def test_csop_workbook_covers_70_manager_rows_with_three_report_commands() -> None:
    """南方东英页表应覆盖 HKEX 70 行且每行有三类报告入口。"""

    sheet = load_workbook(PROJECT_ROOT / "爬虫list.xlsx", read_only=True, data_only=True)["南方东英"]
    rows = list(sheet.iter_rows(min_row=2, values_only=True))
    assert len(rows) == 70
    assert all(row[8] and row[9] and row[10] and row[11] and row[12] and row[13] for row in rows)


def test_csop_catalog_fallback_is_stable_and_report_kind_is_explicit() -> None:
    """目录不可访问时仍生成稳定分类入口，并支持半年报/年报参数。"""

    row = {"english": "CSOP Hang Seng TECH Index ETF"}
    page = csop._stable_catalog_page(row)
    assert page.startswith("https://www.csopasset.com/en/products/etf?")
    link, command, reason = csop._resolve_single(row, {"en": {}})
    assert link == page
    assert " monthly" in command
    assert not reason
    assert download.resolve_args([page, row["english"], "semiannual"])[2] == "semiannual"
    assert download.resolve_args([page, row["english"], "annual"])[2] == "annual"
