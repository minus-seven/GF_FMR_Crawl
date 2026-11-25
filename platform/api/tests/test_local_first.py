"""Tests for deterministic local-first scalar extraction."""

from __future__ import annotations

import sys
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parents[3] / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from structured_data.local_first import extract_local_scalars
from structured_data.local_first_adapter import apply_local_first, run_local_first
from structured_data.extract_equity_fund import (
    _local_text_skip_fields,
    _needs_mineru_text_refinement,
)
from structured_data.extract_fixed_income import _needs_refinement


def test_fund_size_requires_label_amount_currency_and_unit() -> None:
    """规模必须同时命中标签、金额、币种和单位。"""

    result = extract_local_scalars(
        "基金資料\n總資產值（百萬）\n1,768.3 美元\nSEDOL 編號 B4Y3842",
        category="fixed",
        fx_rates={"USD": 7.84427},
    )

    field = result.fields["fund_size"]
    assert field.status == "ok"
    assert field.value["amount"] == 1768.3
    assert field.value["currency"] == "USD"
    assert field.value["hkd_100m"] == 138.7102
    assert field.evidence[0]["quote"].startswith("總資產值")


def test_weak_nav_labels_are_not_fund_size() -> None:
    """单位净值、最高最低和派息语境不得被当成基金规模。"""

    result = extract_local_scalars(
        "12個月之資產淨值最高 83.94美元\n每單位資產淨值 9.93美元\n派息率 5.66%",
        category="fixed",
    )

    assert result.fields["fund_size"].status == "absent"


def test_transposed_table_size_reads_next_value_row() -> None:
    """双栏表格中规模值落到下一行时仍应正确配对。"""

    markdown = """
<table>
<tr><td>總資產值（百萬）</td><td>A（累計）－美元類別：33.68美元</td></tr>
<tr><td>1,552.8美元</td><td>A（每月派息）－港元類別：13.80港元</td></tr>
</table>
"""
    result = extract_local_scalars(
        markdown,
        category="fixed",
        fx_rates={"USD": 7.84427},
    )

    field = result.fields["fund_size"]
    assert field.status == "ok"
    assert field.value["amount"] == 1552.8
    assert field.value["hkd_100m"] == 121.8058


def test_report_month_hint_wins_over_body_dates() -> None:
    """页眉月份提示应优先于正文脚注日期。"""

    result = extract_local_scalars(
        "[REPORT_MONTH_HINT] 2026-07\n基金報告 | 2026年8月31日",
        category="equity",
    )

    assert result.fields["report_date"].value == "2026-07"
    assert result.fields["report_date"].rule == "month_hint"


def test_day_month_year_report_header_is_supported() -> None:
    """月報 31/07/2026 这类日/月/年页眉应解析为 2026-07。"""

    result = extract_local_scalars("月報 31/07/2026\n基金資料", category="equity")

    assert result.fields["report_date"].value == "2026-07"


def test_duration_and_ytm_skip_footnote_markers() -> None:
    """收益率解析必须跳过 3) 之类脚注编号，并保留正确百分比。"""

    result = extract_local_scalars(
        "平均年期╱平均屆滿期（年） 6.7/10.3\n期滿收益率（%）3) 3.83\n",
        category="fixed",
    )

    assert result.fields["portfolio_duration_years"].value == 6.7
    assert result.fields["portfolio_ytm_percent"].value == 3.83


def test_rating_median_from_complete_distribution() -> None:
    """AA/A/BBB 合计越过 50% 时应得到 BBB。"""

    result = extract_local_scalars(
        "債券質素（%） AA: 9.55\nA: 14.15\nBBB: 74.34\n平均年期/平均屆滿期(年) 6.5/11.4",
        category="fixed",
    )

    field = result.fields["credit_rating"]
    assert field.status == "ok"
    assert field.value == "BBB"
    assert field.meta["distribution_total"] == 98.04


def test_rating_does_not_guess_inside_broad_bucket() -> None:
    """50% 落在 <BBB 宽桶内部时必须 unparsed，而不是猜 BBB。"""

    result = extract_local_scalars(
        "債券質素（%） AA: 2.82\nA: 1.26\nBBB: 30.58\n<BBB: 58.2\n未評級: 2.44",
        category="fixed",
    )

    field = result.fields["credit_rating"]
    assert field.status == "unparsed"
    assert field.value is None
    assert field.meta["reason"] == "broad_bucket_straddles_median"
    assert "credit_rating" in result.routing["local_model"]


def test_native_text_wins_over_misaligned_mineru_table() -> None:
    """原生页与错位 MinerU 表格冲突时，采用原生值并保留替代证据。"""

    markdown = """
<table><tr><td>期滿收益率（%）</td><td>4.7/5.6 2.48</td></tr></table>
"""
    result = extract_local_scalars(
        markdown,
        native_pages=["期滿收益率（%） 2.48"],
        category="fixed",
    )

    field = result.fields["portfolio_ytm_percent"]
    assert field.value == 2.48
    assert field.meta["resolution"] == "native_text_source_priority"


def test_absent_field_is_never_sent_to_local_model() -> None:
    """原文没有锚点时路由为 absent，不能触发模型复核。"""

    result = extract_local_scalars("基金資料\n基本貨幣 美元", category="equity")

    assert result.fields["credit_rating"].status == "absent"
    assert "credit_rating" in result.routing["absent"]
    assert "credit_rating" not in result.routing["local_model"]


def test_adapter_shadow_does_not_mutate_raw_result() -> None:
    """shadow 只记录证据，不得改变模型原始结果。"""

    result = extract_local_scalars(
        "總資產值（百萬） 1,768.3 美元",
        category="fixed",
        fx_rates={"USD": 7.84427},
    )
    raw = {"fund_size_original": None, "report_date": None}

    merged, meta = apply_local_first(
        raw,
        result,
        fields=("fund_size", "report_date"),
        mode="shadow",
    )

    assert merged == raw
    assert meta["applied"] == {}


def test_adapter_enforce_applies_only_high_confidence_fields() -> None:
    """enforce 只合并高置信字段，并写入 local_first 追溯证据。"""

    result = extract_local_scalars(
        "總資產值（百萬） 1,768.3 美元",
        category="fixed",
        fx_rates={"USD": 7.84427},
    )
    raw = {"fund_size_original": {"amount": 1, "currency": "USD", "unit": "百萬"}}

    merged, meta = apply_local_first(
        raw,
        result,
        fields=("fund_size", "report_date"),
        mode="enforce",
    )

    assert merged["fund_size_original"]["amount"] == 1768.3
    assert "fund_size" in meta["applied"]
    assert "report_date" not in meta["applied"]
    assert merged["field_evidence"]["local_first"]["fund_size"]["rule"] == "text_label_value"


def test_disclosure_probe_distinguishes_matrix_anchor_from_absence() -> None:
    """矩阵文本补提只应在存在标题/表头锚点时触发。"""

    result = extract_local_scalars(
        "十大投資項目 市場 票面利率到期日 %\nGovernment of Brazil 巴西 6.625%\n"
        "投資組合分布\n<!-- image -->",
        category="fixed",
    )

    assert result.disclosures["top_holdings"]["disclosed"] is True
    assert result.disclosures["duration_distribution"]["disclosed"] is False
    assert result.disclosures["geographic_allocation"]["disclosed"] is False


def test_local_text_skip_includes_absent_disclosures() -> None:
    """文本层没有表头时，矩阵和持仓应进入文本跳过集合。"""

    result = extract_local_scalars("基金資料\n總資產值（百萬） 10 美元", category="equity")
    assert "geographic_allocation" in result.disclosures
    assert not result.disclosures["top_holdings"]["disclosed"]

    _, meta = run_local_first(
        pdf_path=Path("/nonexistent.pdf"),
        bundle={"markdown": "基金資料\n總資產值（百萬） 10 美元", "content_list": "[]"},
        category="equity",
    )

    assert meta["available"] is True
    assert "top_holdings" in meta["text_skip_fields"]
    assert "geographic_allocation" in meta["text_skip_fields"]


def test_equity_text_refinement_can_skip_absent_fields() -> None:
    """enforce 下本地判定 absent 时，不应再为规模或持仓发文本复核。"""

    record = {
        "fund_size_hkd_100m": None,
        "top_holdings": [],
    }

    assert not _needs_mineru_text_refinement(
        record,
        None,
        "基金資料",
        skip_text_fields={"fund_size", "top_holdings"},
    )
    assert _needs_mineru_text_refinement(record, None, "基金資料")


def test_equity_local_text_skip_only_applies_in_enforce_mode() -> None:
    """shadow 模式不能改变生产复核路径。"""

    assert _local_text_skip_fields(
        {
            "text_skip_fields": ["fund_size"],
            "matrix_text_skip_fields": ["geographic_allocation"],
            "apply": {"mode": "shadow"},
        }
    ) == set()
    assert _local_text_skip_fields(
        {
            "text_skip_fields": ["fund_size"],
            "matrix_text_skip_fields": ["geographic_allocation"],
            "apply": {"mode": "enforce"},
        }
    ) == {"fund_size", "geographic_allocation"}


def test_fixed_income_refinement_skips_absent_scalars_and_matrices() -> None:
    """固收文本复核应同时尊重标量 absent 与矩阵无锚点状态。"""

    record = {
        "fund_size_hkd_100m": None,
        "credit_rating": None,
        "portfolio_duration_years": None,
        "portfolio_ytm_percent": None,
        "top_holdings": [],
        "duration_distribution": [],
        "geographic_allocation": [],
        "dividend": None,
    }
    skip = {
        "fund_size",
        "credit_rating",
        "portfolio_duration_years",
        "portfolio_ytm_percent",
        "top_holdings",
        "duration_distribution",
        "geographic_allocation",
    }

    assert not _needs_refinement(record, None, "基金資料", skip_text_fields=skip)
    assert _needs_refinement(record, None, "基金資料")
