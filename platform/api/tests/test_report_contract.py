"""报告契约和纠错闸门测试。"""

from structured_data.report_contract import (
    REPORT_KIND_ANNUAL,
    REPORT_KIND_MONTHLY,
    REPORT_KIND_SEMIANNUAL,
    correct_structured_record,
    detect_report_contract,
    extract_cutoff_date,
)


def test_monthly_traditional_report_detects_exact_cutoff_and_mixed_layout() -> None:
    contract = detect_report_contract(
        "<table><tr><td>投資組合特點</td></tr></table>\n"
        "資料截至 30/09/2026\n加權平均到期日 57.84 日\n[IMAGE_OCR]",
        filename="fund-202609-monthly.pdf",
        category="money",
    )
    assert contract.report_kind == REPORT_KIND_MONTHLY
    assert contract.cutoff_date == "2026-09-30"
    assert contract.language == "zh-Hant+en"
    assert contract.layout == "mixed"
    assert "wam" in contract.matched_fields


def test_english_interim_wins_over_month_heading() -> None:
    contract = detect_report_contract(
        "SEMI-ANNUAL REPORT (UNAUDITED)\nFor the six months ended 30 June 2026\n"
        "Investment portfolio\nHolding/Nominal Value % of net assets",
        filename="Interim-Report.pdf",
        category="money",
    )
    assert contract.report_kind == REPORT_KIND_SEMIANNUAL
    assert contract.cutoff_date == "2026-06-30"
    assert contract.language == "en"
    assert contract.multi_fund is False


def test_annual_marker_is_not_misclassified_as_monthly() -> None:
    contract = detect_report_contract(
        "2025年度报告 Annual Report\n截至2025年12月31日\n投资组合表",
        filename="annual.pdf",
    )
    assert contract.report_kind == REPORT_KIND_ANNUAL
    assert contract.cutoff_date == "2025-12-31"


def test_correction_clears_unanchored_money_values_and_bad_distribution() -> None:
    text = "2026年9月月报\n截至2026年9月30日\n加權平均到期日 57.84 日"
    contract = detect_report_contract(text, category="money")
    corrected, audit = correct_structured_record(
        {
            "report_date": "2026-08-31",
            "portfolio_ytm_percent": 21.28,
            "money_market_metrics": {
                "weighted_average_maturity_days": 145,
                "daily_liquid_assets_percent": 190.31,
            },
            "duration_distribution": [
                {"bucket": "1-3 months", "weight_percent": 12},
            ],
            "top_holdings": [{"name": "Holding A", "weight_percent": 2}],
        },
        text,
        contract,
        category="money",
    )
    assert corrected["report_date"] == "2026-09-30"
    assert corrected["portfolio_ytm_percent"] is None
    assert corrected["money_market_metrics"]["weighted_average_maturity_days"] == 145
    assert corrected["money_market_metrics"]["daily_liquid_assets_percent"] is None
    assert corrected["duration_distribution"] == []
    assert audit["correction_count"] >= 2


def test_named_date_helper_accepts_english_as_of() -> None:
    assert extract_cutoff_date("Data as at 30 September 2026") == "2026-09-30"


def test_correction_rejects_future_model_report_date() -> None:
    contract = detect_report_contract("Monthly report\nInvestment objective", category="money")
    corrected, audit = correct_structured_record(
        {"report_date": "2999-02-24"},
        "Monthly report\nInvestment objective",
        contract,
        category="money",
    )
    assert corrected["report_date"] is None
    assert "report_date:clear_future_date" in audit["corrections"]
