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


orientam = _load("orientam_fund_link", ROOT / "code/companies/orientam/orientam_fund_link.py")
download = _load("download_orientam_factsheet", ROOT / "code/companies/orientam/download_orientam_factsheet.py")


def test_orientam_catalog_covers_bvb640_and_all_manager_funds() -> None:
    """东方资管目录覆盖 BVB640 及主数据中的 3 只基金。"""
    assert len(orientam.FUND_MAP) == 3
    assert orientam.FUND_MAP["BVB640"][0] == "东方红人民币货币市场基金"


def test_bvb640_maps_to_hkex_page_and_official_product_page() -> None:
    """BVB640 使用 HKEX 稳定页，月报命令使用东方官网产品页。"""
    index = orientam._build_index(orientam._fetch_catalog())
    link, command, reason = orientam._resolve_single({"fund": "BVB640", "english": ""}, index)
    assert not reason
    assert link.endswith("/BVB640")
    assert "dfzq.com.hk" in command
    assert "monthly" in command


def test_ifp_report_selector_uses_exact_doc_type() -> None:
    """半年报、年报严格按 IFP docType 选择正文。"""
    records = [
        {"docDate": "2025-12-31", "fundFileVOList": [{"docType": "ANNUAL", "docUrl": "a", "fileName": "annual.pdf"}]},
        {"docDate": "2026-06-30", "fundFileVOList": [{"docType": "SEMIANNUAL", "docUrl": "b", "fileName": "interim.pdf"}]},
    ]
    assert download.select_ifp_report(records, "semiannual")["label"] == "interim.pdf"
    assert download.select_ifp_report(records, "annual")["label"] == "annual.pdf"
