"""货币基金原生 PDF 证据门控测试。"""

from pathlib import Path

from structured_data.extract_fixed_income import (
    _apply_money_market_text_facts,
    _extract_pdf_text,
    _extract_money_market_long_text,
    _finalize,
    _parse_money_market_text_facts,
    _parse_json,
    _normalize_report_month,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
BPJ877_PDF = PROJECT_ROOT / "data/jobs/75a732b1-383d-4a92-9741-a21f9a353749/staging/75a732b1-383d-4a92-9741-a21f9a353749/factsheet/货币/高腾微金美元货币基金.pdf"
BUE512_MONTHLY_MD = PROJECT_ROOT / "data/jobs/75a732b1-383d-4a92-9741-a21f9a353749/staging/75a732b1-383d-4a92-9741-a21f9a353749/markdown/货币/招银国际美元货币市场基金__c8704735bd6f/full.md"
BSG589_SEMIANNUAL_MD = PROJECT_ROOT / "data/batches/66242503-8919-417c-b6f1-9b139918e231/staging/51b42d5b-cef4-420c-b49f-ce5bb6e5ef0c/markdown/货币/泰康开泰美元货币基金__bab43b2da6a7/artifacts/full.md"
BNW892_SEMIANNUAL_MD = PROJECT_ROOT / "data/batches/94601a20-a058-41cc-8f98-f1b9becdfbf5/staging/d4a60d0d-d067-4c05-a5c8-c019ebacc501/markdown/货币/高腾微财货币基金__0b93a2fee55e/full.md"
BTM337_PDF = PROJECT_ROOT / "data/jobs/75a732b1-383d-4a92-9741-a21f9a353749/staging/75a732b1-383d-4a92-9741-a21f9a353749/factsheet/货币/中银香港全天候美元货币市场基金.pdf"
BTM337_MONTHLY_MD = PROJECT_ROOT / "data/jobs/75a732b1-383d-4a92-9741-a21f9a353749/staging/75a732b1-383d-4a92-9741-a21f9a353749/markdown/货币/中银香港全天候美元货币市场基金__a08073fac79f/full.md"
BJK348_PDF = PROJECT_ROOT / "data/jobs/75a732b1-383d-4a92-9741-a21f9a353749/staging/75a732b1-383d-4a92-9741-a21f9a353749/factsheet/货币/易方达(香港)美元货币市场基金.pdf"
BRG187_SEMIANNUAL_MD = PROJECT_ROOT / "data/jobs/45b5ae44-61b6-406e-a570-e81bc85ce91b/staging/45b5ae44-61b6-406e-a570-e81bc85ce91b/markdown/货币/泰康开泰港元货币基金__e10c12e339e3/artifacts/full.md"
BSV019_PDF = PROJECT_ROOT / "data/jobs/9c78def1-f681-4b2c-a884-1e9194e1e8ca/supplementary_input/input.pdf"
BSV019_MONTHLY_MD = PROJECT_ROOT / "data/jobs/9c78def1-f681-4b2c-a884-1e9194e1e8ca/staging/9c78def1-f681-4b2c-a884-1e9194e1e8ca/markdown/货币/中银香港全天候港元货币市场基金__9fca18f76e4c/full.md"
BTE502_PDF = PROJECT_ROOT / "data/jobs/bc8cd737-f999-4eca-9f9c-9ff58c4574d1/staging/bc8cd737-f999-4eca-9f9c-9ff58c4574d1/factsheet/货币/广发美元货币市场基金.pdf"
BTE502_MONTHLY_MD = PROJECT_ROOT / "data/jobs/bc8cd737-f999-4eca-9f9c-9ff58c4574d1/staging/bc8cd737-f999-4eca-9f9c-9ff58c4574d1/markdown/货币/广发美元货币市场基金__10d63436cdee/artifacts/full.md"
BOG055_PDF = PROJECT_ROOT / "data/jobs/cf7e9398-a5da-49f8-94a3-bf0ec863cfbf/staging/cf7e9398-a5da-49f8-94a3-bf0ec863cfbf/factsheet/货币/中金美元货币市场ETF.pdf"
BOG055_MONTHLY_MD = PROJECT_ROOT / "data/jobs/cf7e9398-a5da-49f8-94a3-bf0ec863cfbf/staging/cf7e9398-a5da-49f8-94a3-bf0ec863cfbf/markdown/货币/中金美元货币市场ETF__2fd8f6384a6e/artifacts/full.md"


def test_money_market_native_chart_and_wam_are_extracted() -> None:
    text = _extract_pdf_text(BPJ877_PDF)
    facts = _parse_money_market_text_facts(text)
    assert facts["weighted_average_maturity_days"] == 57.84
    assert [item["weight_percent"] for item in facts["duration_distribution"]] == [65.82, 16.51, 3.42, 14.24]
    assert [item["weight_percent"] for item in facts["asset_type_allocation"]] == [39.21, 42.54, 18.24]


def test_btm337_report_date_holdings_ytm_wal_and_image_charts_are_recovered() -> None:
    markdown = BTM337_MONTHLY_MD.read_text(encoding="utf-8")
    facts = _parse_money_market_text_facts(markdown)
    assert facts["portfolio_ytm_percent"] == 3.9
    applied = _apply_money_market_text_facts(
        {"money_market_metrics": {}}, markdown, "money", pdf_path=BTM337_PDF
    )
    assert applied["report_date"] == "2026-08-31"
    assert applied["money_market_metrics"]["weighted_average_life_days"] == 56.1
    assert [item["weight_percent"] for item in applied["top_holdings"]] == [0.9] * 5
    assert [item["weight_percent"] for item in applied["money_market_metrics"]["asset_type_allocation"]] == [1.7, 77.2, 21.1]
    assert [item["weight_percent"] for item in applied["duration_distribution"]] == [17.1, 20.1, 15.0, 23.9, 2.8, 21.1]


def test_money_market_semiannual_html_wam_and_holdings_are_extracted() -> None:
    text = """
    ## INVESTMENT PORTFOLIO (UNAUDITED)
    <table>
      <tr><td>Holding</td><td>Fair value HKD</td><td>% of net assets</td></tr>
      <tr><td>Example Bank Ltd 3.10% 2027</td><td>500,000,000</td><td>1.42%</td></tr>
      <tr><td>Second Bank Ltd 3.00% 2026</td><td>400,000,000</td><td>1.14%</td></tr>
      <tr><td>Total investments</td><td>900,000,000</td><td>2.56%</td></tr>
      <tr><td>Weighted average maturity</td><td>40.04 days</td></tr>
    </table>
    """
    facts = _parse_money_market_text_facts(text)
    assert facts["weighted_average_maturity_days"] == 40.04
    assert [item["name"] for item in facts["top_holdings"]] == [
        "Example Bank Ltd 3.10% 2027",
        "Second Bank Ltd 3.00% 2026",
    ]


def test_brg187_semiannual_portfolio_table_keeps_top_ten_and_liquidity_percentages() -> None:
    markdown = BRG187_SEMIANNUAL_MD.read_text(encoding="utf-8")
    facts = _parse_money_market_text_facts(markdown)
    # 该英文半年报的组合表把百分号放在列标题，并把持仓名称与小计
    # 拼在同一行；应保留真实持仓并生成完整的组合结构，而不是返回空表。
    assert [item["name"] for item in facts["top_holdings"]] == [
        "TAIKANG HONG KONG HKD MONEY MARKET ETF-CLASS M - UNLISTED CLASS",
    ]
    assert facts["top_holdings"][0]["holding_quantity"] == 1987021.0
    assert facts["top_holdings"][0]["market_value"] == 200598491.0
    assert [(item["type"], item["weight_percent"]) for item in facts["asset_type_allocation"]] == [
        ("Unlisted/quoted investments", 1.22),
        ("Other net assets", 98.78),
    ]
    applied = _apply_money_market_text_facts({"money_market_metrics": {"daily_liquid_assets_percent": 190.31}}, markdown, "money")
    assert applied["money_market_metrics"]["daily_liquid_assets_percent"] == 20.28
    assert applied["money_market_metrics"]["weekly_liquid_assets_percent"] == 64.46


def test_bsv019_monthly_recovers_holdings_class_date_and_image_distributions() -> None:
    markdown = BSV019_MONTHLY_MD.read_text(encoding="utf-8")
    facts = _parse_money_market_text_facts(markdown)
    assert facts["inception_date"] == "2023-02-09"
    assert [(item["name"], item["weight_percent"]) for item in facts["top_holdings"]] == [
        ("永豐銀行 2.98%2026年10月29日", 1.2),
        ("台新銀行 2.99%2026年10月21日", 1.2),
        ("上海浦東發展銀行2.95%2026年9月25日", 1.1),
        ("平安銀行3.09%2026年7月12日", 1.1),
        ("平安銀行2.94%2026年10月9日", 1.1),
    ]
    applied = _apply_money_market_text_facts({"money_market_metrics": {}}, markdown, "money", pdf_path=BSV019_PDF)
    assert [(item["type"], item["weight_percent"]) for item in applied["money_market_metrics"]["asset_type_allocation"]] == [
        ("定期存款", 84.3),
        ("現金及等值", 15.7),
    ]
    assert [item["weight_percent"] for item in applied["duration_distribution"]] == [27.7, 25.6, 24.4, 0.7, 6.0, 15.6]


def test_bte502_monthly_recovers_launch_date_duration_and_top_five() -> None:
    # 广发月报的图表数值在 PDF 原生文本层，HTML/Markdown 只保留图片；
    # 实际流水线会把两者合并后再进入货币基金事实提取器。
    markdown = BTE502_MONTHLY_MD.read_text(encoding="utf-8")
    text = markdown + "\n" + _extract_pdf_text(BTE502_PDF)
    facts = _parse_money_market_text_facts(text)
    assert facts["inception_date"] == "2023-07-06"
    assert [item["weight_percent"] for item in facts["duration_distribution"]] == [40.42, 34.04, 25.54]
    assert [(item["name"], item["weight_percent"]) for item in facts["top_holdings"]] == [
        ("Time deposit, 定期存款, 2026/11/27", 0.98),
        ("Time deposit, 定期存款, 2026/11/30", 0.98),
        ("Time deposit, 定期存款, 2026/12/03", 0.9),
        ("Time deposit, 定期存款, 2026/12/17", 0.9),
        ("Time deposit, 定期存款, 2026/10/07", 0.9),
    ]
    applied = _apply_money_market_text_facts({"money_market_metrics": {}}, text, "money", pdf_path=BTE502_PDF)
    assert applied["inception_date"] == "2023-07-06"
    assert [item["weight_percent"] for item in applied["duration_distribution"]] == [40.42, 34.04, 25.54]


def test_bog055_cicc_listing_assets_wal_and_image_duration_are_recovered() -> None:
    text = BOG055_MONTHLY_MD.read_text(encoding="utf-8") + "\n" + _extract_pdf_text(BOG055_PDF)
    applied = _apply_money_market_text_facts({"money_market_metrics": {}}, text, "money", pdf_path=BOG055_PDF)
    assert applied["listing_date"] == "2019-06-18"
    assert applied["money_market_metrics"]["weighted_average_maturity_days"] == 47.68
    assert applied["money_market_metrics"]["weighted_average_life_days"] == 94.22
    assert [(item["type"], item["weight_percent"]) for item in applied["money_market_metrics"]["asset_type_allocation"]] == [
        ("现金", 16.44),
        ("银行存款", 17.53),
        ("其他货币市场工具", 65.64),
        ("货币市场基金", 0.39),
    ]
    assert [item["weight_percent"] for item in applied["duration_distribution"]] == [18.86, 6.36, 10.79, 10.29, 16.49, 18.46, 18.76]


def test_bte502_monthly_recovers_bilingual_objective_ytm_note_and_holdings() -> None:
    markdown = BTE502_MONTHLY_MD.read_text(encoding="utf-8")
    long_text = _extract_money_market_long_text(markdown)
    facts = _parse_money_market_text_facts(markdown)
    assert long_text["investment_objective"]
    assert "投資目標" in long_text["investment_objective"]
    assert long_text["portfolio_ytm_note"]
    assert "並不代表基金的回報" in long_text["portfolio_ytm_note"]
    assert facts["portfolio_ytm_percent"] == 4.25
    assert len(facts["top_holdings"]) == 5


def test_semiannual_date_and_truncated_model_json_are_recovered() -> None:
    # 英文半年报常见的“期间至/截至”写法不应回退到持仓到期年份。
    assert _normalize_report_month(
        "Interim Report\nFor the six months ended 30 June 2026\n"
        "For the year ended 31 December 2025"
    ) == "2026-06-30"
    assert _normalize_report_month(
        "For the period from 1 January 2026 to 30 June 2026"
    ) == "2026-06-30"
    assert _normalize_report_month("As at 30 June 2026") == "2026-06-30"

    # DeepSeek 达到 completion 上限时，末尾可能缺少逗号和闭括号；只恢复
    # 已完整返回的成员，不为缺失字段制造值。
    recovered = _parse_json(
        '{"fund_name":"BNY627","report_date":"2026-06-30",'
        '"money_market_metrics":{"weighted_average_maturity_days":51.19}'
    )
    assert recovered["fund_name"] == "BNY627"
    assert recovered["report_date"] == "2026-06-30"


def test_pdf_subject_monthly_report_overrides_inception_date() -> None:
    # 该 PPT 导出的 PDF 正文只有成立日期 2017-11-08，报告期在
    # ``Subject: 202609 Monthly Report`` 元数据中；不能把成立日期当月报期。
    assert _normalize_report_month(_extract_pdf_text(BJK348_PDF)) == "2026-09-30"


def test_bsg589_semiannual_holdings_keep_registration_type_and_quantity() -> None:
    facts = _parse_money_market_text_facts(BSG589_SEMIANNUAL_MD.read_text(encoding="utf-8"))
    assert facts["top_holdings"][0] == {
        "name": "STATE BANK OF INDIA/HK 4.24% 04DEC2026 FXCD",
        "weight_percent": 1.32,
        "holding_quantity": 60000000.0,
        "market_value": 60093600.0,
        "registration_place": "INDIA",
        "holding_type": "Unlisted/quoted investments Fixed Income Securities",
    }
    assert any(
        item["holding_type"] == "Listed/quoted investments Fixed Income Securities"
        and item["registration_place"] == "VIRGIN ISL, BT"
        for item in facts["top_holdings"]
    )


def test_semiannual_portfolio_structure_is_parsed_from_english_summary_tables() -> None:
    bnw = _parse_money_market_text_facts(BNW892_SEMIANNUAL_MD.read_text(encoding="utf-8"))
    bsg = _parse_money_market_text_facts(BSG589_SEMIANNUAL_MD.read_text(encoding="utf-8"))
    assert [(item["type"], item["weight_percent"]) for item in bnw["asset_type_allocation"]] == [
        ("Debt securities", 23.59),
        ("Money market funds", 0.62),
        ("Other net assets", 75.79),
    ]
    assert [(item["type"], item["weight_percent"]) for item in bsg["asset_type_allocation"]] == [
        ("Debt securities", 24.58),
        ("Money market funds", 1.1),
        ("Other net assets", 74.32),
    ]


def test_bue512_monthly_rejects_duration_chart_as_ytm_and_keeps_wam() -> None:
    text = BUE512_MONTHLY_MD.read_text(encoding="utf-8")
    facts = _parse_money_market_text_facts(text)
    assert facts["weighted_average_maturity_days"] == 56.27
    # 21.28% is a duration-distribution chart value, not a YTM value.
    assert facts["portfolio_ytm_percent"] is None
    final = _apply_money_market_text_facts(
        {"portfolio_ytm_percent": 21.28, "money_market_metrics": {"weighted_average_maturity_days": 145}},
        text,
        "money",
    )
    assert final["portfolio_ytm_percent"] is None
    assert final["money_market_metrics"]["weighted_average_maturity_days"] == 56.27


def test_bue512_native_pdf_fields_are_recovered() -> None:
    pdf = PROJECT_ROOT / "data/jobs/75a732b1-383d-4a92-9741-a21f9a353749/staging/75a732b1-383d-4a92-9741-a21f9a353749/factsheet/货币/招银国际美元货币市场基金.pdf"
    text = _extract_pdf_text(pdf)
    facts = _parse_money_market_text_facts(text)
    assert facts["portfolio_ytm_percent"] == 4.10
    assert facts["inception_date"] == "2024-02-02"
    assert [item["weight_percent"] for item in facts["duration_distribution"]] == [21.28, 13.41, 36.19, 29.12]
    assert [item["weight_percent"] for item in facts["asset_type_allocation"]] == [40.03, 36.94, 23.03]


def test_money_market_long_text_keeps_source_language_and_ytm_note() -> None:
    markdown = """## 投資目標

本子基金的主要投資目標是尋求資本保值。

## 投資策略

本子基金旨在投資於短期優質貨幣市場工具。

## 投資組合特點

*到期收益率（YTM）是在假設所有利息和本金都按期支付並按當前利率再投資的情況下，相關資產在持有至到期時基於當前價格獲得的內部收益率。YTM不能代表本子基金的業績。
"""
    facts = _extract_money_market_long_text(markdown)
    assert facts["investment_objective"] == "本子基金的主要投資目標是尋求資本保值。"
    assert facts["investment_strategy"] == "本子基金旨在投資於短期優質貨幣市場工具。"
    assert facts["portfolio_ytm_note"].startswith("到期收益率（YTM）是在假設")


def test_money_model_template_values_are_cleared_without_anchors() -> None:
    text = _extract_pdf_text(BPJ877_PDF)
    raw = {
        "report_date": "2026-09",
        "top_holdings": [{"name": "cross fund", "weight_percent": 28}],
        "geographic_allocation": [{"region": "cross fund", "weight_percent": 98.5}],
        "dividend": {"currency": "HKD"},
        "money_market_metrics": {
            "weighted_average_maturity_days": 145,
            "weighted_average_life_days": 142,
            "daily_liquid_assets_percent": 32.7,
            "weekly_liquid_assets_percent": 28.5,
            "asset_type_allocation": [{"type": "cross fund", "weight_percent": 100}],
            "bank_exposure": [{"name": "cross fund", "weight_percent": 45}],
        },
    }
    final = _finalize(raw, BPJ877_PDF, text)
    assert final["report_date"] == "2026-09-30"
    assert final["top_holdings"] == []
    assert final["geographic_allocation"] == []
    assert final["dividend"] is None
    assert final["money_market_metrics"]["weighted_average_maturity_days"] == 57.84
    assert final["money_market_metrics"]["weighted_average_life_days"] is None
    assert final["money_market_metrics"]["asset_type_allocation"][0]["type"] == "定期存款"


def test_money_category_gates_lists_even_when_metrics_object_is_missing() -> None:
    text = _extract_pdf_text(BPJ877_PDF)
    final = _finalize(
        {
            "top_holdings": [{"name": "cross fund", "weight_percent": 28}],
            "geographic_allocation": [{"region": "Hong Kong", "weight_percent": 98.5}],
            "dividend": {"currency": "HKD", "dividend_per_unit": 0.05},
        },
        BPJ877_PDF,
        text,
        category="money",
    )
    assert final["top_holdings"] == []
    assert final["geographic_allocation"] == []
    assert final["dividend"] is None
