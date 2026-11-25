from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _load(name: str, path: Path):
    """加载测试模块；参数 name 为模块名，path 为文件路径。"""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


link = _load("chinaamc_fund_link", ROOT / "code/companies/chinaamc/chinaamc_fund_link.py")
download = _load("download_chinaamc_reports", ROOT / "code/companies/chinaamc/download_chinaamc_reports.py")


def test_bsr551_maps_to_hkex_page_and_report_commands() -> None:
    """BSR551 使用 HKEX 稳定页及英文报告下载命令。"""
    assert link._hkex_code("ChinaAMC Select USD Money Market Fund") == "BSR551"
    assert link.IFP_BASE + "BSR551" == "https://ifp.hkex.com.hk/fund-repository/fund/BSR551"


def test_ifp_selector_uses_exact_doc_type() -> None:
    """半年报和年报严格按 IFP docType 选择。"""
    records = [
        {"docDate": "2025-12-31", "fundFileVOList": [{"docType": "ANNUAL", "docUrl": "a", "fileName": "annual.pdf"}]},
        {"docDate": "2026-06-30", "fundFileVOList": [{"docType": "SEMIANNUAL", "docUrl": "b", "fileName": "interim.pdf"}]},
    ]
    assert download.select_ifp_report(records, "semiannual")["label"] == "interim.pdf"
    assert download.select_ifp_report(records, "annual")["label"] == "annual.pdf"


def test_report_columns_keep_three_unmapped_rows_explicit() -> None:
    """境内/未纳入 HKEX 的基金不伪造财报代码。"""
    assert link._hkex_code("ChinaAMC Return Securities Investment Fund") == ""
    assert link._hkex_code("ChinaAMC Select USD Money Market Fund") == "BSR551"
