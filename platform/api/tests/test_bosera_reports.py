from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _load(name: str, path: Path):
    """按路径加载脚本模块。"""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


link = _load("bosera_fund_link", ROOT / "code/companies/bosera/bosera_fund_link.py")
download = _load("download_bosera_reports", ROOT / "code/companies/bosera/download_bosera_reports.py")


def test_bsg677_maps_to_hkex_financial_report_page() -> None:
    """BSG677 使用稳定 HKEX 基金页，而不是固化某一份 PDF。"""
    assert link._hkex_code("博时美元货币市场基金") == "BSG677"
    assert link.IFP_BASE + "BSG677" == "https://ifp.hkex.com.hk/fund-repository/fund/BSG677"


def test_ifp_selector_uses_exact_report_type() -> None:
    """半年报/年报只接受正文 docType，公告不能误选。"""
    records = [
        {"docDate": "2025-12-31", "fundFileVOList": [{"docType": "ANNUAL", "docUrl": "a", "fileName": "annual.pdf"}]},
        {"docDate": "2026-06-30", "fundFileVOList": [{"docType": "SEMIANNUAL", "docUrl": "b", "fileName": "interim.pdf"}, {"docType": "ANNOUNCEMENT", "docUrl": "c", "fileName": "notice.pdf"}]},
    ]
    assert download.select_ifp_report(records, "semiannual")["label"] == "interim.pdf"
    assert download.select_ifp_report(records, "annual")["label"] == "annual.pdf"


def test_unconfirmed_domestic_rows_are_not_guessed() -> None:
    """境内互认基金等不在香港 IFP 的行保持空白。"""
    assert link._hkex_code("博时信用债券投资基金") == ""
    assert link._hkex_code("博时美元货币市场基金") == "BSG677"
