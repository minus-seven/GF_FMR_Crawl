"""Helpers for aggregating model calls and token usage."""

from __future__ import annotations

from typing import Any


def summarize_by_model(token_usage: Any) -> dict[str, dict[str, int]]:
    summary: dict[str, dict[str, int]] = {}
    calls = token_usage.get("calls") if isinstance(token_usage, dict) else []
    for call in calls or []:
        if not isinstance(call, dict):
            continue
        model = str(call.get("model") or "unknown")
        item = summary.setdefault(
            model,
            {
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "cache_hit_tokens": 0,
                "cache_miss_tokens": 0,
            },
        )
        item["calls"] += 1
        item["input_tokens"] += int(call.get("input_tokens") or 0)
        item["output_tokens"] += int(call.get("output_tokens") or 0)
        item["total_tokens"] += int(call.get("total_tokens") or 0)
        item["cache_hit_tokens"] += int(call.get("cache_hit_tokens") or 0)
        item["cache_miss_tokens"] += int(call.get("cache_miss_tokens") or 0)
    return summary


def merge_model_usage(
    target: dict[str, dict[str, int]],
    source: Any,
) -> None:
    if not isinstance(source, dict):
        return
    for model, values in source.items():
        if not isinstance(values, dict):
            continue
        item = target.setdefault(
            str(model),
            {
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "cache_hit_tokens": 0,
                "cache_miss_tokens": 0,
            },
        )
        for field in (
            "calls",
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "cache_hit_tokens",
            "cache_miss_tokens",
        ):
            item[field] += int(values.get(field) or 0)
