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


pingan = _load("pinganhk_fund_link", ROOT / "code/companies/pinganhk/pinganhk_fund_link.py")
download = _load("download_pinganhk_factsheet", ROOT / "code/companies/pinganhk/download_pinganhk_factsheet.py")


def test_pinganhk_catalog_covers_bum340_and_all_manager_funds() -> None:
    """平安资管目录覆盖 BUM340 及主数据 11 只基金。"""
    assert len(pingan.FUND_MAP) == 11
    assert pingan.FUND_MAP["BUM340"][0] == "平安港元货币基金"


def test_bum340_maps_to_official_product_page() -> None:
    """BUM340 使用平安官网 PAHKMMF 稳定页。"""
    index = pingan._build_index(pingan._fetch_catalog())
    link, command, reason = pingan._resolve_single({"fund": "BUM340", "english": ""}, index)
    assert not reason
    assert link.endswith("/PACSIF-PAHKMMF")
    assert "monthly" in command


def test_pingan_prefers_body_over_notice() -> None:
    """半年报正文优先于较新的公告。"""
    documents = [
        {"label": "Interim Report 2026", "url": "https://x/interim_2026.pdf"},
        {"label": "Notice to Unitholders - Interim Report 2026", "url": "https://x/notice_2026.pdf"},
    ]
    assert download.select_latest_document(documents, "semiannual")["url"].endswith("interim_2026.pdf")


def test_pingan_rejects_notice_only() -> None:
    """只有公告时应明确失败。"""
    try:
        download.select_latest_document([{ "label": "Notice to Unitholders - Interim Report 2026", "url": "https://x/notice.pdf"}], "semiannual")
    except RuntimeError as exc:
        assert "仅找到semiannual公告" in str(exc)
    else:
        raise AssertionError("公告不应被当作半年报正文")
