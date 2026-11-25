#!/usr/bin/env python
"""Credit-rating normalization and median-rating derivation."""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Mapping


RATING_ORDER = [
    "AAA",
    "AA+",
    "AA",
    "AA-",
    "A+",
    "A",
    "A-",
    "BBB+",
    "BBB",
    "BBB-",
    "BB+",
    "BB",
    "BB-",
    "B+",
    "B",
    "B-",
    "CCC+",
    "CCC",
    "CCC-",
    "CC",
    "C",
    "D",
    "NR",
]

RATING_RANK = {rating: index + 1 for index, rating in enumerate(RATING_ORDER)}
_ALIASES = {
    "NOT RATED": "NR",
    "N.R.": "NR",
    "N/R": "NR",
    "UNRATED": "NR",
    "未评级": "NR",
    "無評級": "NR",
}


def normalize_rating(value: object) -> str:
    text = str(value or "").strip().upper()
    text = text.replace(" ", "").replace("（", "(").replace("）", ")")
    text = text.split("(")[0].strip()
    if text in _ALIASES:
        return _ALIASES[text]
    return text if text in RATING_RANK else text


def rating_sort_key(value: object) -> int:
    rating = normalize_rating(value)
    return RATING_RANK.get(rating, len(RATING_ORDER) + 100)


def collapse_distribution(distribution: Iterable[Mapping[str, object]]) -> list[dict[str, float | str]]:
    totals: dict[str, float] = defaultdict(float)
    for item in distribution or []:
        rating = normalize_rating(item.get("rating"))
        if rating not in RATING_RANK:
            continue
        try:
            weight = float(item.get("weight_pct"))
        except (TypeError, ValueError):
            continue
        totals[rating] += weight
    return [
        {"rating": rating, "weight_pct": round(totals[rating], 6)}
        for rating in RATING_ORDER
        if rating in totals
    ]


def derive_median_rating(distribution: Iterable[Mapping[str, object]]) -> str:
    collapsed = collapse_distribution(distribution)
    cumulative = 0.0
    for item in collapsed:
        cumulative += float(item["weight_pct"])
        if cumulative >= 50.0:
            return str(item["rating"])
    return ""

