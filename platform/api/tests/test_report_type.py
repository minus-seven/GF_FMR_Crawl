from datetime import date

from app.services.report_type import detect_report_kind, report_kind_label


def test_monthly_factsheet_without_monthly_marker() -> None:
    text = "高騰微財貨幣基金\n2026年9月\n截至2026年9月30日\n投資組合特點"
    assert detect_report_kind(text, date(2026, 9, 30)) == "monthly"
    assert report_kind_label("monthly", date(2026, 9, 30)) == "9月月报"


def test_semiannual_marker_wins_over_date() -> None:
    text = "2026年6月30日 中期報告 Interim Report"
    assert detect_report_kind(text, date(2026, 6, 30)) == "semiannual"
    assert report_kind_label("semiannual", date(2026, 6, 30)) == "2026年半年报"


def test_semi_annual_does_not_match_annual_substring() -> None:
    text = "SEMI-ANNUAL REPORT (UNAUDITED) For the period ended 30 June 2026"
    assert detect_report_kind(text, date(2026, 6, 30)) == "semiannual"


def test_annual_marker() -> None:
    assert detect_report_kind("2025年度报告 Annual Report", date(2025, 12, 31)) == "annual"
    assert report_kind_label("annual", date(2025, 12, 31)) == "2025年年报"


def test_unknown_stays_supplementary() -> None:
    assert detect_report_kind("基金资料及风险披露", date(2026, 4, 30)) == "supplementary"


def test_termination_notice_does_not_become_monthly_from_disclaimer() -> None:
    text = "子基金终止通知书；本月报所载资料仅供参考"
    assert detect_report_kind(text, date(2026, 9, 3)) == "supplementary"


def test_report_labels_are_canonical_for_cutoff_month_and_kind_aliases() -> None:
    assert report_kind_label("monthly_report", date(2026, 8, 31)) == "8月月报"
    assert report_kind_label("semi-annual", date(2026, 6, 30)) == "2026年半年报"
    assert report_kind_label("annual_report", date(2026, 12, 31)) == "2026年年报"
