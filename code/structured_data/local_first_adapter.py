#!/usr/bin/env python
"""把确定性标量链路接入生产抽取器，默认只做 shadow 观测。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable

try:
    from structured_data.local_first import (
        STATUS_ABSENT,
        STATUS_OK,
        STATUS_UNPARSED,
        LocalExtraction,
        native_pages_from_pdf,
    )
    from structured_data.field_pipeline import execute_pipeline
except ImportError:  # pragma: no cover - standalone invocation
    from local_first import (  # type: ignore
        STATUS_ABSENT,
        STATUS_OK,
        STATUS_UNPARSED,
        LocalExtraction,
        native_pages_from_pdf,
    )
    from field_pipeline import execute_pipeline  # type: ignore


MODE_ENV = "FACTSHEET_LOCAL_FIRST"
VALID_MODES = {"off", "shadow", "enforce"}
NON_TEXT_RECOVERABLE_REASONS = {
    "broad_bucket_straddles_median",
    "distribution_below_50",
    "distribution_over_105",
    "median_not_reached",
}


def configured_mode() -> str:
    """读取本地优先模式；参数为空，返回 off/shadow/enforce。"""

    raw = os.environ.get(MODE_ENV, "shadow").strip().casefold()
    aliases = {
        "0": "off",
        "false": "off",
        "no": "off",
        "1": "enforce",
        "true": "enforce",
        "on": "enforce",
        "yes": "enforce",
    }
    value = aliases.get(raw, raw)
    return value if value in VALID_MODES else "shadow"


def run_local_first(
    *,
    pdf_path: Path,
    bundle: dict[str, Any] | None,
    category: str,
    fx_rates: dict[str, float] | None = None,
    manager_profile: Any | None = None,
) -> tuple[LocalExtraction | None, dict[str, Any]]:
    """运行本地标量抽取；参数 pdf_path/bundle/category/fx_rates/manager_profile 为输入上下文。"""

    markdown = str((bundle or {}).get("markdown") or "")
    content_list = (bundle or {}).get("content_list")
    report_month_hint = (bundle or {}).get("report_month_hint")
    native_pages = native_pages_from_pdf(pdf_path)
    if not markdown and not native_pages:
        return None, {"available": False, "error": "no_markdown_or_native_text"}
    try:
        pipeline_category = "fixed" if category in {"fixed", "固收", "money", "货币"} else category
        result, pipeline_meta = execute_pipeline(
            markdown,
            content_list=content_list,
            native_pages=native_pages,
            category=pipeline_category,
            fx_rates=fx_rates,
            report_month_hint=report_month_hint,
            preferred_layout_id=getattr(manager_profile, "layout_id", None),
            pipeline_path=getattr(manager_profile, "field_pipeline", None),
            drift_threshold=getattr(manager_profile, "drift_threshold", 0.8),
        )
    except Exception as exc:  # noqa: BLE001 - local extraction must never break paid path
        return None, {"available": False, "error": f"{type(exc).__name__}: {exc}"}
    text_skip_fields = []
    for name, item in result.fields.items():
        reason = str(item.meta.get("reason") or "")
        if item.status == STATUS_ABSENT or (
            item.status == STATUS_UNPARSED and reason in NON_TEXT_RECOVERABLE_REASONS
        ):
            text_skip_fields.append(name)
    # 文本层没有标题/表头时，文本补提不可能恢复矩阵或持仓；视觉复核仍保留。
    text_skip_fields.extend(
        name
        for name, probe in result.disclosures.items()
        if not probe.get("disclosed")
    )
    return result, {
        "available": True,
        "mode": configured_mode(),
        "routing": result.routing,
        "pipeline": pipeline_meta,
        "fields": {name: item.to_dict() for name, item in result.fields.items()},
        "field_provenance": {
            name: {
                "stage": (item.meta.get("pipeline") or {}).get("stage", "deterministic"),
                "rule": item.rule,
                "rule_id": (item.meta.get("pipeline") or {}).get("rule_id"),
                "status": item.status,
                "confidence": item.confidence,
                "evidence": item.evidence,
                "attempts": [
                    {
                        "stage": "deterministic",
                        "rule": item.rule,
                        "result": item.status,
                    }
                ],
            }
            for name, item in result.fields.items()
        },
        "disclosures": result.disclosures,
        "text_skip_fields": sorted(set(text_skip_fields)),
        "matrix_text_skip_fields": sorted(
            name
            for name, probe in result.disclosures.items()
            if not probe.get("disclosed")
        ),
        "native_pages": len(native_pages),
    }


def apply_local_first(
    raw: dict[str, Any],
    result: LocalExtraction | None,
    *,
    fields: Iterable[str],
    mode: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """按白名单把高置信本地值合并进模型原始结果；参数 raw/result/fields 为上下文。"""

    effective_mode = mode or configured_mode()
    applied: dict[str, Any] = {}
    if effective_mode != "enforce" or result is None:
        return raw, {"mode": effective_mode, "applied": applied}

    merged = dict(raw)
    for field in fields:
        candidate = result.fields.get(field)
        if candidate is None or candidate.status != STATUS_OK or candidate.confidence < 0.9:
            continue
        if field == "fund_size":
            value = candidate.value if isinstance(candidate.value, dict) else {}
            if not (value.get("amount") and value.get("currency") and value.get("multiplier")):
                continue
            merged["fund_size_original"] = {
                "amount": value["amount"],
                "currency": value["currency"],
                "unit": value["unit"],
            }
        elif field == "credit_rating":
            merged["credit_rating"] = candidate.value
            distribution = candidate.meta.get("distribution")
            if distribution:
                merged["credit_rating_distribution"] = distribution
        else:
            merged[field] = candidate.value
        applied[field] = {
            "value": candidate.value,
            "rule": candidate.rule,
            "confidence": candidate.confidence,
            "evidence": candidate.evidence,
        }

    if applied:
        evidence = merged.get("field_evidence")
        if not isinstance(evidence, dict):
            evidence = {}
        evidence["local_first"] = applied
        merged["field_evidence"] = evidence
    return merged, {"mode": effective_mode, "applied": applied}
