from __future__ import annotations

import importlib.util
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = PROJECT_ROOT / "code/companies/cmsam/cmsam_fund_link.py"
SPEC = importlib.util.spec_from_file_location("cmsam_fund_link", MODULE_PATH)
assert SPEC and SPEC.loader
cmsam = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cmsam)
DOWNLOAD_SPEC = importlib.util.spec_from_file_location(
    "download_cmsam_factsheet",
    PROJECT_ROOT / "code/companies/cmsam/download_cmsam_factsheet.py",
)
assert DOWNLOAD_SPEC and DOWNLOAD_SPEC.loader
cmsam_download = importlib.util.module_from_spec(DOWNLOAD_SPEC)
DOWNLOAD_SPEC.loader.exec_module(cmsam_download)


def test_cmsam_catalog_covers_all_five_hkex_funds() -> None:
    """招商证券资产管理（香港）主数据中的 5 只基金均有稳定入口。"""

    assert len(cmsam.KNOWN_PRODUCT_PAGES) == 5
    index = cmsam._build_index(cmsam._fetch_catalog())
    for fund_name, page_url in cmsam.KNOWN_PRODUCT_PAGES.items():
        link, command, reason = cmsam._resolve_single({"fund": fund_name, "english": ""}, index)
        assert not reason
        assert link == page_url
        assert page_url in command


def test_bsx133_maps_to_cms_money_market_page() -> None:
    """BSX133 应映射到 CMS Money Market Fund CMMF 页面。"""

    index = cmsam._build_index(cmsam._fetch_catalog())
    link, command, reason = cmsam._resolve_single({"fund": "BSX133", "english": ""}, index)
    assert not reason
    assert link.endswith("FundProduct?view=CMMF")
    assert "招商货币市场基金" in command


def test_cmsam_semiannual_prefers_body_over_notice() -> None:
    """较新的半年报通知不能覆盖真正的半年报正文。"""

    documents = [
        {"label": "Semi-Annual Report 2025", "url": "https://x/cmsam_semiannual_2025.pdf"},
        {"label": "Notice to Unitholders-Semi-Annual Report 2025 (Unaudited)", "url": "https://x/notice_2025.pdf"},
    ]
    selected = cmsam_download.select_latest_document(documents, "semiannual")
    assert selected["url"].endswith("cmsam_semiannual_2025.pdf")


def test_cmsam_rejects_notice_when_body_is_missing() -> None:
    """只有通知时应明确失败。"""

    documents = [{"label": "Notice to Unitholders-Semi-Annual Report 2025", "url": "https://x/notice_2025.pdf"}]
    try:
        cmsam_download.select_latest_document(documents, "semiannual")
    except RuntimeError as exc:
        assert "仅找到semiannual公告" in str(exc)
    else:
        raise AssertionError("公告不应被选择为半年报正文")
