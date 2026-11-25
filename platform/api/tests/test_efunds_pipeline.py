"""易方达香港月报下载与本地证据回填回归测试。"""

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT / "code"))

from structured_data.extract_fixed_income import (  # noqa: E402
    _apply_money_market_text_facts,
    _extract_pdf_text,
    _finalize,
    _parse_fund_size_from_text,
    _parse_money_market_text_facts,
)
from companies.efunds_hk import efunds_fund_link as efunds  # noqa: E402


EFUND_ROOT = (
    PROJECT_ROOT
    / "data/jobs/75a732b1-383d-4a92-9741-a21f9a353749/staging/"
    / "75a732b1-383d-4a92-9741-a21f9a353749"
)


def test_efunds_monthly_report_is_recovered_from_native_evidence() -> None:
    """易方达月报不能因 Qwen 串台而丢掉原文规模、WAM/YTM、持仓。"""

    pdf = EFUND_ROOT / "factsheet/货币/易方达(香港)人民币货币市场基金.pdf"
    markdown = next(
        (EFUND_ROOT / "markdown/货币").glob("易方达(香港)人民币货币市场基金__*/full.md")
    )
    text = markdown.read_text(encoding="utf-8") + "\n" + _extract_pdf_text(pdf)
    facts = _parse_money_market_text_facts(text)
    record = _finalize({}, pdf, text, category="money")

    assert facts["weighted_average_maturity_days"] == 53.02
    assert facts["portfolio_ytm_percent"] == 1.44
    assert len(facts["top_holdings"]) == 10
    assert record["report_date"] == "2026-09-30"
    assert record["portfolio_ytm_percent"] == 1.44
    assert len(record["top_holdings"]) == 10


def test_efunds_product_size_aliases_are_supported() -> None:
    """产品规模/產品規模及混合简繁标签应能被识别。"""

    assert _parse_fund_size_from_text("产品规模：101.78億港元") == {
        "amount": 101.78,
        "currency": "港元",
        "unit": "億",
    }
    assert _parse_fund_size_from_text("產品规模：24.37億美元") == {
        "amount": 24.37,
        "currency": "美元",
        "unit": "億",
    }


def test_efunds_link_resolver_pins_monthly_document_type(monkeypatch) -> None:
    """链接补全脚本必须显式锁定 MonthlyFact，防止文档类型漂移。"""

    monkeypatch.setattr(efunds, "_has_monthly", lambda _pid: True)
    link, command, reason = efunds._resolve_single(
        {"fund": "易方达（香港）美元货币市场基金", "english": ""},
        {"cn": {"美元货币市场": {"fund_id": "42", "fund_name": "易方达（香港）美元货币市场基金"}}, "en": {}, "all": []},
    )
    assert reason == ""
    assert link.endswith("/products/42/documents/")
    assert command.endswith("42 --type MonthlyFact")


def test_money_market_clears_unanchored_model_size() -> None:
    """原文没有规模时不得沿用模型上一份报告的规模。"""

    markdown = next(
        (EFUND_ROOT / "markdown/货币").glob("易方达(香港)人民币货币市场基金__*/full.md")
    )
    raw = {
        "fund_size_original": {"amount": 999, "currency": "USD", "unit": "million"},
        "money_market_metrics": {},
    }
    corrected = _apply_money_market_text_facts(
        raw,
        markdown.read_text(encoding="utf-8"),
        "money",
    )
    assert corrected["fund_size_original"] is None

