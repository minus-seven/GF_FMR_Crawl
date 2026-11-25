from __future__ import annotations

import importlib.util
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = PROJECT_ROOT / "code/companies/huatai/huatai_fund_link.py"
SPEC = importlib.util.spec_from_file_location("huatai_fund_link", MODULE_PATH)
assert SPEC and SPEC.loader
huatai = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(huatai)
DOWNLOAD_SPEC = importlib.util.spec_from_file_location(
    "download_huatai_factsheet",
    PROJECT_ROOT / "code/companies/huatai/download_huatai_factsheet.py",
)
assert DOWNLOAD_SPEC and DOWNLOAD_SPEC.loader
huatai_download = importlib.util.module_from_spec(DOWNLOAD_SPEC)
DOWNLOAD_SPEC.loader.exec_module(huatai_download)


def test_huatai_catalog_covers_all_known_hkex_funds() -> None:
    """华泰稳定目录应覆盖主数据中的 6 只基金。"""

    assert len(huatai.KNOWN_PRODUCT_PAGES) == 6
    index = huatai._build_index(huatai._fetch_catalog())
    for name, page_url in huatai.KNOWN_PRODUCT_PAGES.items():
        link, command, reason = huatai._resolve_single({"fund": name, "english": ""}, index)
        assert not reason
        assert link == page_url
        assert page_url in command


def test_huatai_bts953_maps_to_hkd_money_market_page() -> None:
    """BTS953 对应华泰港元货币市场基金 f=1 页面。"""

    assert huatai.KNOWN_PRODUCT_PAGES["华泰港元货币市场基金"] == "https://hk.htsc.com.hk/?f=1&l=en"
    index = huatai._build_index(huatai._fetch_catalog())
    link, command, reason = huatai._resolve_single({"fund": "BTS953", "english": ""}, index)
    assert not reason
    assert link.endswith("?f=1&l=en")
    assert "华泰港元货币市场基金" in command


def test_huatai_semiannual_prefers_body_over_notice() -> None:
    """较新的半年报通知不能覆盖真正的半年报正文。"""

    documents = [
        {
            "label": "2026 Semi-Annual Report",
            "url": "https://x/huatai_semiannual_2026.pdf",
        },
        {
            "label": "Notice To Unitholders - Unaudited Semi-Annual Report 2026",
            "url": "https://x/notice_semiannual_2026.pdf",
        },
    ]
    selected = huatai_download.select_latest_document(documents, "semiannual")
    assert selected["url"].endswith("huatai_semiannual_2026.pdf")


def test_huatai_rejects_notice_when_body_is_missing() -> None:
    """只有通知而没有正文时应明确失败。"""

    documents = [
        {
            "label": "Notice To Unitholders - Unaudited Semi-Annual Report 2026",
            "url": "https://x/notice_semiannual_2026.pdf",
        }
    ]
    try:
        huatai_download.select_latest_document(documents, "semiannual")
    except RuntimeError as exc:
        assert "仅找到semiannual公告" in str(exc)
    else:
        raise AssertionError("公告不应被选择为半年报正文")
