from __future__ import annotations

import importlib.util
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = PROJECT_ROOT / "code/companies/cicc/cicc_fund_link.py"
SPEC = importlib.util.spec_from_file_location("cicc_fund_link", MODULE_PATH)
assert SPEC and SPEC.loader
cicc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cicc)
DOWNLOAD_SPEC = importlib.util.spec_from_file_location(
    "download_cicc_factsheet",
    PROJECT_ROOT / "code/companies/cicc/download_cicc_factsheet.py",
)
assert DOWNLOAD_SPEC and DOWNLOAD_SPEC.loader
cicc_download = importlib.util.module_from_spec(DOWNLOAD_SPEC)
DOWNLOAD_SPEC.loader.exec_module(cicc_download)


def test_cicc_known_catalog_covers_all_hkex_funds() -> None:
    """已核验的中金页表应覆盖 HKEX 主数据中的 7 只基金。"""

    assert len(cicc.KNOWN_PRODUCT_PAGES) == 7
    for fund_name, page_url in cicc.KNOWN_PRODUCT_PAGES.items():
        link, monthly_command, reason = cicc._resolve_single(
            {"fund": fund_name, "english": ""},
            {"cn": {}, "all": []},
        )
        assert not reason
        assert link == page_url
        assert page_url in monthly_command


def test_cicc_backfill_commands_are_stable_page_commands() -> None:
    """月报命令不应固化易变的 PDF 文件名。"""

    for page_url in cicc.KNOWN_PRODUCT_PAGES.values():
        assert ".pdf" not in page_url.lower()


def test_cicc_semiannual_prefers_target_subfund_and_defaults_to_all_classes() -> None:
    """同页存在系列报告和子基金报告时应选目标子基金，半年报不受份额类别过滤。"""

    documents = [
        {"label": "截至2026年6月30日止的未经审核中期报告 (基金系列)", "url": "https://x/series.pdf"},
        {"label": "截至2026年6月30日止的未经审核中期报告 (中金香港股票基金)", "url": "https://x/equity.pdf"},
    ]
    selected = cicc_download.select_latest_document(
        documents,
        "semiannual",
        "all",
        "中金香港股票基金",
    )
    assert selected["url"].endswith("equity.pdf")


def test_cicc_semiannual_prefers_report_body_over_newer_announcement() -> None:
    """同页公告发布时间更新时，仍应选择中期报告正文而非公告 PDF。"""

    documents = [
        {
            "label": "截至2026年6月30日止的未经审核中期报告 (基金系列) 下載 C_CICC Fund Series Final 6.30.2026.pdf",
            "url": "https://x/C_CICC%20Fund%20Series%20Final%206.30.2026.pdf",
        },
        {
            "label": "2026-08-20-公告2026中期报告 (基金系列) 下載 C_Announcement_CICC Fund Series (Unaudited interim report 30 June 2026)_CLEAN.pdf",
            "url": "https://x/C_Announcement_CICC%20Fund%20Series.pdf",
        },
    ]
    selected = cicc_download.select_latest_document(documents, "semiannual")
    assert "Announcement" not in selected["url"]
    assert "Fund%20Series%20Final" in selected["url"]


def test_cicc_rejects_announcement_when_report_body_is_missing() -> None:
    """产品页只有公告时应失败，不应把一页公告当作半年报正文。"""

    documents = [
        {
            "label": "2026-08-20-公告2026中期报告 C_Announcement.pdf",
            "url": "https://x/C_Announcement.pdf",
        }
    ]
    try:
        cicc_download.select_latest_document(documents, "semiannual")
    except RuntimeError as exc:
        assert "仅找到semiannual公告" in str(exc)
    else:
        raise AssertionError("公告不应被选择为半年报正文")
