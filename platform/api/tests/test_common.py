"""Unit tests for deterministic D1 import helpers."""

from datetime import date

from app.importers.common import normalize_name, parse_report_month, stable_uuid


def test_normalize_name_handles_full_width_and_spacing() -> None:
    """基金名称规范化应统一全角字符、大小写和空白。"""

    assert normalize_name(" ＡＢＣ 基金 ") == normalize_name("abc基金")


def test_parse_report_month_returns_month_end_when_day_is_absent() -> None:
    """结构化 YYYY-MM 应转换为自然月月末；明确日时保留原日。"""

    assert parse_report_month("2026-05") == date(2026, 5, 31)
    assert parse_report_month("截至 2026年9月") == date(2026, 9, 30)
    assert parse_report_month("截至 2026年9月30日") == date(2026, 9, 30)
    assert parse_report_month("unknown") is None


def test_stable_uuid_is_repeatable_and_context_sensitive() -> None:
    """相同输入应生成相同 UUID，不同输入必须分离。"""

    assert stable_uuid("fund", "a") == stable_uuid("fund", "a")
    assert stable_uuid("fund", "a") != stable_uuid("fund", "b")
