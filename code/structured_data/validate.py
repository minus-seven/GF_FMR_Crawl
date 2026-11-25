#!/usr/bin/env python
"""Validation and normalization for fixed-income extraction records."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re
from typing import Any

try:
    from .rating import collapse_distribution, derive_median_rating, normalize_rating, rating_sort_key
except ImportError:  # pragma: no cover - allows direct script execution
    from rating import collapse_distribution, derive_median_rating, normalize_rating, rating_sort_key


REQUIRED_FIELDS = [
    "fund_name",
    "aum_original",
    "aum_hkd_100m",
    "portfolio_duration_years",
    "yield_to_maturity_pct",
    "credit_rating_distribution",
    "credit_rating",
    "credit_rating_method",
    "top_holdings",
    "distribution_yield_pct",
    "distribution_currency",
    "distribution_frequency",
    "distribution_note",
    "review_flags",
]


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _add_flag(flags: list[str], message: str) -> None:
    if message and message not in flags:
        flags.append(message)


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def _is_mostly_ascii(text: str) -> bool:
    letters = [char for char in text if char.isalpha()]
    return bool(letters) and sum(ord(char) < 128 for char in letters) / len(letters) > 0.8


def _parse_number(text: str) -> float | None:
    match = re.search(r"(-?[0-9][0-9,]*(?:\.[0-9]+)?)", text)
    if not match:
        return None
    return float(match.group(1).replace(",", ""))


def _compute_aum_hkd_100m(aum_original: object) -> float | None:
    text = str(aum_original or "").strip()
    if not text:
        return None
    value = _parse_number(text)
    if value is None:
        return None
    lower = text.lower()

    if any(token in text for token in ["十億", "十亿", "billion"]) or "bn" in lower:
        amount_million = value * 1000
    elif any(token in text for token in ["百萬", "百万", "million"]) or "mn" in lower or "m" in lower:
        amount_million = value
    elif any(token in text for token in ["億", "亿"]):
        amount_million = value * 100
    else:
        amount_million = value

    if any(token in text for token in ["港元", "港幣", "港币", "HKD"]) or "hk$" in lower:
        fx = 1.0
    elif any(token in text for token in ["美元", "USD", "美金"]) or "$" in text:
        fx = 7.8
    elif any(token in text for token in ["人民幣", "人民币", "RMB", "CNY"]):
        fx = 1.08
    elif any(token in text for token in ["澳元", "AUD", "A$"]):
        fx = 5.1
    else:
        return None
    return round(amount_million * fx / 100, 4)


def _numeric_or_none(value: object) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    parsed = _parse_number(str(value))
    return parsed


def normalize_record(record: dict[str, Any], source_file: str | None = None) -> dict[str, Any]:
    normalized = deepcopy(record or {})
    if isinstance(normalized, list):
        normalized = {"source_file": source_file or "", "funds": normalized}
    normalized.setdefault("source_file", source_file or normalized.get("source_file") or "")
    normalized.setdefault("report_date", None)
    normalized["funds"] = _as_list(normalized.get("funds"))
    source_stem = Path(str(normalized.get("source_file") or "")).stem.replace(".fixed_income", "")
    prefer_source_fund_name = len(normalized["funds"]) == 1 and _has_cjk(source_stem)

    for fund in normalized["funds"]:
        if not isinstance(fund, dict):
            continue
        for field in REQUIRED_FIELDS:
            if field not in fund:
                fund[field] = [] if field in {"credit_rating_distribution", "top_holdings", "review_flags"} else ""
        fund["review_flags"] = [str(flag) for flag in _as_list(fund.get("review_flags")) if str(flag).strip()]
        fund_name = str(fund.get("fund_name") or "").strip()
        if prefer_source_fund_name and (not fund_name or _is_mostly_ascii(fund_name)):
            fund["fund_name"] = source_stem
        computed_aum = _compute_aum_hkd_100m(fund.get("aum_original"))
        if computed_aum is not None:
            current_aum = fund.get("aum_hkd_100m")
            try:
                current_value = float(current_aum)
            except (TypeError, ValueError):
                current_value = None
            if current_value is None or current_value <= 0 or abs(current_value - computed_aum) / max(computed_aum, 1) > 0.2:
                fund["aum_hkd_100m"] = computed_aum
                _add_flag(fund["review_flags"], "规模按原文币种换算为亿港币")
        for field in ["aum_hkd_100m", "portfolio_duration_years", "yield_to_maturity_pct", "distribution_yield_pct"]:
            numeric = _numeric_or_none(fund.get(field))
            if numeric is not None:
                fund[field] = numeric
        fund["credit_rating_distribution"] = collapse_distribution(_as_list(fund.get("credit_rating_distribution")))
        fund["credit_rating_distribution"].sort(key=lambda item: rating_sort_key(item.get("rating")))
        fund["top_holdings"] = _as_list(fund.get("top_holdings"))
        fund.setdefault("report_date", normalized.get("report_date"))
        fund.setdefault("source_pages", [])

        rating = normalize_rating(fund.get("credit_rating"))
        method = str(fund.get("credit_rating_method") or "").strip().lower()
        if method not in {"reported", "derived", "missing"}:
            method = "missing"

        derived = derive_median_rating(fund["credit_rating_distribution"])
        if rating and rating != fund.get("credit_rating"):
            fund["credit_rating"] = rating
        if not rating and derived:
            fund["credit_rating"] = derived
            method = "derived"
            _add_flag(fund["review_flags"], "信用评级由评级分布推算")
        elif not rating:
            method = "missing"
            _add_flag(fund["review_flags"], "缺少信用评级")
        fund["credit_rating_method"] = method

    return normalized


def validate_record(record: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    funds = _as_list(record.get("funds"))
    if not funds:
        issues.append({"severity": "error", "field": "funds", "message": "未识别到任何基金"})
        return issues

    for index, fund in enumerate(funds, start=1):
        if not isinstance(fund, dict):
            issues.append({"severity": "error", "fund_index": index, "field": "fund", "message": "基金记录不是对象"})
            continue
        fund_name = str(fund.get("fund_name") or f"fund_{index}")
        for field in ["fund_name", "aum_original"]:
            if not str(fund.get(field) or "").strip():
                issues.append({"severity": "warning", "fund_name": fund_name, "field": field, "message": "字段为空"})
        for field in ["aum_hkd_100m", "portfolio_duration_years", "yield_to_maturity_pct"]:
            if fund.get(field) in ("", None):
                issues.append({"severity": "warning", "fund_name": fund_name, "field": field, "message": "数值字段为空"})

        distribution = _as_list(fund.get("credit_rating_distribution"))
        if distribution:
            total = sum(float(item.get("weight_pct") or 0) for item in distribution if isinstance(item, dict))
            if total < 90 or total > 110:
                issues.append({
                    "severity": "review",
                    "fund_name": fund_name,
                    "field": "credit_rating_distribution",
                    "message": f"评级分布合计为 {total:.2f}%，明显偏离 100%",
                })
        if fund.get("credit_rating_method") == "missing":
            issues.append({"severity": "review", "fund_name": fund_name, "field": "credit_rating", "message": "缺少最终信用评级"})
        if len(_as_list(fund.get("top_holdings"))) < 10:
            issues.append({"severity": "review", "fund_name": fund_name, "field": "top_holdings", "message": "前十大持仓不足 10 条"})
        currency = str(fund.get("distribution_currency") or "").upper()
        frequency = str(fund.get("distribution_frequency") or "")
        if fund.get("distribution_yield_pct") not in ("", None):
            if currency and currency not in {"USD", "美元"}:
                issues.append({"severity": "review", "fund_name": fund_name, "field": "distribution_currency", "message": "派息率不是美元份额"})
            if frequency and "月" not in frequency.lower() and "monthly" not in frequency.lower():
                issues.append({"severity": "review", "fund_name": fund_name, "field": "distribution_frequency", "message": "派息率不是每月派息份额"})
    return issues


def normalize_and_validate(record: dict[str, Any], source_file: str | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    normalized = normalize_record(record, source_file=source_file)
    issues = validate_record(normalized)
    for issue in issues:
        fund_name = issue.get("fund_name")
        if not fund_name:
            continue
        for fund in normalized.get("funds", []):
            if fund.get("fund_name") == fund_name:
                _add_flag(fund.setdefault("review_flags", []), str(issue.get("message") or "需要复核"))
                break
    return normalized, issues


def source_stem(source_file: str) -> str:
    return Path(source_file).stem or "unknown"
