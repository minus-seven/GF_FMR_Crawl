from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _load(name: str, path: Path):
    """加载测试模块；参数 name 为模块名，path 为路径。"""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gtjai = _load("gtjai_fund_link", ROOT / "code/companies/gtjai/gtjai_fund_link.py")
download = _load("download_gtjai_factsheet", ROOT / "code/companies/gtjai/download_gtjai_factsheet.py")


def test_gtjai_catalog_covers_btf710_and_all_manager_funds() -> None:
    """国泰君安目录覆盖 BTF710 及主数据 5 只基金。"""
    assert len(gtjai.FUND_MAP) == 5
    assert gtjai.FUND_MAP["BTF710"][0] == "国泰君安港元货币市场基金"


def test_btf710_maps_to_hkex_page_and_gtjai_report_page() -> None:
    """BTF710 使用 HKEX 稳定页和 GTJAI 月报目录。"""
    index = gtjai._build_index(gtjai._fetch_catalog())
    link, command, reason = gtjai._resolve_single({"fund": "BTF710", "english": ""}, index)
    assert not reason
    assert link.endswith("/BTF710")
    assert "fund_gjhkdmmf_report" in command
    assert "monthly" in command


def test_ifp_report_selector_uses_exact_doc_type() -> None:
    """半年报和年报按 IFP docType 精确匹配。"""
    records = [
        {"docDate": "2025-12-31", "fundFileVOList": [{"docType": "ANNUAL", "docUrl": "a", "fileName": "annual.pdf"}]},
        {"docDate": "2026-06-30", "fundFileVOList": [{"docType": "SEMIANNUAL", "docUrl": "b", "fileName": "interim.pdf"}]},
    ]
    assert download.select_ifp_report(records, "semiannual")["label"] == "interim.pdf"
    assert download.select_ifp_report(records, "annual")["label"] == "annual.pdf"


def test_month_label_accepts_dedicated_gtjai_page() -> None:
    """GTJAI 独立基金页的“8月”链接可作为月报候选。"""
    assert download._looks_like_month_label("8月")
    assert download._looks_like_month_label("Monthly Report 2026-08")
