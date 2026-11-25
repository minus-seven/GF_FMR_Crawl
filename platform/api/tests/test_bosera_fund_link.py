import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT / "code"))

from companies.bosera import bosera_fund_link as bosera  # noqa: E402


def test_hkd_money_market_etf_uses_shared_hkdmmf_monthly_report_page(monkeypatch):
    """上市 ETF 的月报入口固定在 HKDMMF 子基金页，不应误用 HKDMMETF。"""

    monkeypatch.setattr(bosera, "_has_factsheet", lambda code: code == "HKDMMF")
    index = bosera._build_index(
        [
            {
                "code": "HKDMMF",
                "fund_name": "博時港元貨幣市場ETF （非上市類別）",
                "share_class": "類別A港元",
                "currency": "HKD",
            },
            {
                "code": "HKDMMETF",
                "fund_name": "博時港元貨幣市場ETF",
                "share_class": "03152",
                "currency": "HKD",
            },
        ]
    )

    page_url, command, reason = bosera._resolve_single(
        {"fund": "博时港元货币市场ETF", "umbrella": "博时全球公众基金开放式基金型公司"},
        index,
    )

    assert reason == ""
    assert page_url.endswith("/fund/detail/HKDMMF")
    assert command == "python download_bosera_factsheet.py HKDMMF 中文"


def test_new_bosera_fund_without_monthly_report_is_skipped(monkeypatch):
    monkeypatch.setattr(bosera, "_has_factsheet", lambda code: False)
    index = bosera._build_index(
        [
            {
                "code": "NEWFUND",
                "fund_name": "博時新基金",
                "share_class": "類別A港元",
                "currency": "HKD",
            }
        ]
    )

    page_url, command, reason = bosera._resolve_single(
        {"fund": "博时新基金", "umbrella": ""},
        index,
    )

    assert page_url == ""
    assert command == ""
    assert "暂未发布月报" in reason
