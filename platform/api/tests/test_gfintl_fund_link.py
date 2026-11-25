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


gf = _load("gfintl_fund_link", ROOT / "code/companies/gfintl/gfintl_fund_link.py")
downloader = _load("download_gfintl_factsheet", ROOT / "code/companies/gfintl/download_gfintl_factsheet.py")


def test_gfintl_catalog_contains_bwe554_and_all_manager_funds() -> None:
    """广发国际目录覆盖 BWE554 及 HKEX 主数据中的 12 只基金。"""
    assert len(gf.FUND_MAP) == 12
    assert gf.FUND_MAP["BWE554"] == ("广发港元货币市场基金", "GF HKD Money Market Fund")


def test_bwe554_stable_page_and_commands() -> None:
    """BWE554 使用 HKEX 稳定页，报告命令携带基金代码。"""
    index = gf._build_index(gf._fetch_catalog())
    link, command, reason = gf._resolve_single({"fund": "BWE554", "english": ""}, index)
    assert not reason
    assert link.endswith("/BWE554")
    assert "BWE554" in command
    assert "monthly" in command


def test_ifp_selects_latest_report_by_exact_type() -> None:
    """半年报和年报选择器按 docType 精确匹配。"""
    records = [
        {"docDate": "2025-12-31", "fundFileVOList": [{"docType": "ANNUAL", "docUrl": "a", "fileName": "annual.pdf"}]},
        {"docDate": "2026-06-30", "fundFileVOList": [{"docType": "SEMIANNUAL", "docUrl": "b", "fileName": "interim.pdf"}]},
    ]
    assert downloader.select_ifp_report(records, "semiannual")["label"] == "interim.pdf"
    assert downloader.select_ifp_report(records, "annual")["label"] == "annual.pdf"


def test_ifp_missing_body_fails_explicitly() -> None:
    """没有正文报告时必须明确失败。"""
    try:
        downloader.select_ifp_report([], "semiannual")
    except RuntimeError as exc:
        assert "没有匹配" in str(exc)
    else:
        raise AssertionError("缺少正文时不应静默成功")
