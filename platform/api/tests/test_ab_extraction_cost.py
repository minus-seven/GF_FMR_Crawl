"""Unit tests for the structured extraction A/B cost benchmark."""

from __future__ import annotations

import json
from pathlib import Path
import sys

CODE_DIR = Path(__file__).resolve().parents[3] / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from structured_data import ab_extraction_cost


def _write_result(
    root: Path,
    *,
    category: str,
    stem: str,
    kind: str,
    data: dict,
    calls: list[dict],
    issues: list[str] | None = None,
) -> None:
    """写入一份合成结果；参数 root/category/stem 等为结果内容。"""

    path = root / "results" / category / f"{stem}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "pdf": f"/tmp/{stem}.pdf",
                "category": category,
                "kind": kind,
                "result": {
                    "status": "ok",
                    "data": data,
                    "_meta": {
                        "token_usage": {"calls": calls},
                        "validation_issues": issues or [],
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_select_pdfs_round_robins_categories(tmp_path: Path) -> None:
    """样本选择应按类别轮询，避免只取到固收。"""

    pdf_root = tmp_path / "factsheet"
    mineru_root = tmp_path / "markdown"
    for category, names in {
        "固收": ["a", "b"],
        "权益": ["c"],
        "混合": ["d"],
    }.items():
        directory = pdf_root / category
        directory.mkdir(parents=True)
        for name in names:
            (directory / f"{name}.pdf").write_bytes(b"%PDF-1.4")

    selected = ab_extraction_cost.select_pdfs(
        pdf_root,
        mineru_root,
        ["固收", "权益", "混合"],
        limit=4,
        require_cache=False,
    )

    assert [item["category"] for item in selected] == ["固收", "权益", "混合", "固收"]


def test_is_filled_ignores_note_only_objects() -> None:
    """只有 note 的空对象不应算作已填充字段。"""

    assert not ab_extraction_cost._is_filled(
        {"note": "未披露", "dividend_per_unit": None}
    )
    assert ab_extraction_cost._is_filled(
        {"name": "Bond", "weight_percent": 5}
    )
    assert not ab_extraction_cost._is_filled([{"note": "未披露"}])


def test_dividend_requires_amount_or_yield() -> None:
    """仅有份额类别或币种不算披露派息。"""

    assert not ab_extraction_cost._field_filled(
        "dividend",
        {"share_class": "A类美元(派息)", "currency": "USD", "frequency": "unknown"},
    )
    assert ab_extraction_cost._field_filled(
        "dividend",
        {"share_class": "A类美元(派息)", "dividend_per_unit": 0.05},
    )


def test_summarize_variant_aggregates_tokens_and_completeness(tmp_path: Path) -> None:
    """汇总应统计调用 token、缓存命中、校验问题和字段完整率。"""

    root = tmp_path / "baseline"
    _write_result(
        root,
        category="固收",
        stem="a",
        kind="fixed",
        data={
            "fund_name": "A",
            "report_date": "2026-06",
            "fund_size_hkd_100m": 10.5,
            "portfolio_duration_years": 3.2,
            "portfolio_ytm_percent": None,
            "credit_rating": "A",
            "top_holdings": [{"name": "Bond", "weight_percent": 5}],
            "duration_distribution": [],
            "geographic_allocation": [],
            "dividend": None,
        },
        calls=[
            {
                "input_tokens": 1000,
                "output_tokens": 100,
                "total_tokens": 1100,
                "cache_hit_tokens": 400,
                "cache_miss_tokens": 600,
            },
            {
                "input_tokens": 500,
                "output_tokens": 50,
                "total_tokens": 550,
                "cache_hit_tokens": 0,
                "cache_miss_tokens": 500,
            },
        ],
        issues=["missing ytm"],
    )
    _write_result(
        root,
        category="权益",
        stem="b",
        kind="equity",
        data={
            "fund_name": "B",
            "report_date": "2026-06",
            "fund_size_hkd_100m": 2,
            "top_holdings": [{"name": "Stock", "weight_percent": 3}],
            "geographic_allocation": [],
            "sector_allocation": [],
            "dividend": None,
        },
        calls=[
            {
                "input_tokens": 300,
                "output_tokens": 30,
                "total_tokens": 330,
                "cache_hit_tokens": 100,
                "cache_miss_tokens": 200,
            }
        ],
    )

    summary = ab_extraction_cost.summarize_variant(root)

    assert summary["pdfs"] == 2
    assert summary["calls"] == 3
    assert summary["input_tokens"] == 1800
    assert summary["output_tokens"] == 180
    assert summary["total_tokens"] == 1980
    assert summary["cache_hit_tokens"] == 500
    assert summary["validation_issues"] == 1
    assert summary["statuses"] == {"ok": 2}
    assert summary["by_category"]["固收"]["total_tokens"] == 1650
    assert 0 < summary["completeness"] < 1
    assert summary["avg_tokens_per_pdf"] == 990.0


def test_compare_variants_reports_field_changes(tmp_path: Path) -> None:
    """比较报告应给出 token 变化和有差异的字段。"""

    baseline = tmp_path / "baseline"
    optimized = tmp_path / "optimized"
    base_data = {
        "fund_name": "A",
        "report_date": "2026-06",
        "fund_size_hkd_100m": 10.5,
        "portfolio_duration_years": 3.2,
        "portfolio_ytm_percent": 4.1,
        "credit_rating": "A",
        "top_holdings": [{"name": "Bond", "weight_percent": 5}],
        "duration_distribution": [],
        "geographic_allocation": [],
        "dividend": None,
    }
    for root, duration, tokens in (
        (baseline, 3.2, 1000),
        (optimized, 3.4, 700),
    ):
        _write_result(
            root,
            category="固收",
            stem="a",
            kind="fixed",
            data={**base_data, "portfolio_duration_years": duration},
            calls=[
                {
                    "input_tokens": tokens,
                    "output_tokens": 100,
                    "total_tokens": tokens + 100,
                    "cache_hit_tokens": 0,
                    "cache_miss_tokens": tokens,
                }
            ],
        )

    comparison = ab_extraction_cost.compare_variants(
        ab_extraction_cost.summarize_variant(baseline),
        ab_extraction_cost.summarize_variant(optimized),
    )

    assert comparison["comparable_pdfs"] == 1
    assert comparison["changed_pdf_count"] == 1
    assert "portfolio_duration_years" in comparison["changed_fields"]
    assert comparison["delta_percent"]["total_tokens"] < 0
