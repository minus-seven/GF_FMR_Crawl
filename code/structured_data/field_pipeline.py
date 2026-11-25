#!/usr/bin/env python
"""字段流水线契约的加载、版式识别和确定性标量执行器。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

try:
    from structured_data.local_first import (
        AVERAGE_RATING_LABELS,
        DURATION_LABELS,
        RATING_LABELS,
        YTM_LABELS,
        Candidate,
        EvidenceBlock,
        LocalExtraction,
        build_evidence_index,
        extract_credit_rating,
        extract_fund_size,
        extract_metric,
        extract_report_month,
        probe_disclosures,
    )
except ImportError:  # pragma: no cover - standalone invocation
    from local_first import (  # type: ignore
        AVERAGE_RATING_LABELS,
        DURATION_LABELS,
        RATING_LABELS,
        YTM_LABELS,
        Candidate,
        EvidenceBlock,
        LocalExtraction,
        build_evidence_index,
        extract_credit_rating,
        extract_fund_size,
        extract_metric,
        extract_report_month,
        probe_disclosures,
    )


MODULE_DIR = Path(__file__).resolve().parent
DEFAULT_PIPELINES_DIR = MODULE_DIR / "pipelines"
GENERIC_LAYOUT_ID = "generic"


@dataclass(frozen=True)
class PipelineSpec:
    """一个可版本化字段流水线；raw 保留未解释的契约配置。"""

    pipeline_version: int
    layout_id: str
    detect: dict[str, Any]
    fields: dict[str, dict[str, Any]]
    source: Path
    raw: dict[str, Any]


@dataclass(frozen=True)
class PipelineSelection:
    """一次版式识别结果；覆盖率用于识别版式漂移。"""

    spec: PipelineSpec
    anchor_hit_rate: float
    matched_anchors: tuple[str, ...]
    missing_anchors: tuple[str, ...]
    layout_drift: bool
    fallback_reason: str | None = None


def _as_strings(value: Any) -> tuple[str, ...]:
    """把配置值转为非空字符串元组；参数 value 为 JSON 中的字符串或数组。"""

    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if not isinstance(value, list):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _load_one(path: Path) -> PipelineSpec | None:
    """读取并校验单份流水线 JSON；参数 path 为契约文件路径。"""

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    layout_id = str(raw.get("layout_id") or "").strip()
    fields = raw.get("fields")
    if not layout_id or not isinstance(fields, dict):
        return None
    normalized_fields = {
        str(name): value for name, value in fields.items() if isinstance(value, dict)
    }
    if not normalized_fields:
        return None
    try:
        version = int(raw.get("pipeline_version", 1))
    except (TypeError, ValueError):
        version = 1
    return PipelineSpec(
        pipeline_version=version,
        layout_id=layout_id,
        detect=raw.get("detect") if isinstance(raw.get("detect"), dict) else {},
        fields=normalized_fields,
        source=path,
        raw=raw,
    )


def load_pipelines(directory: Path | None = None) -> dict[str, PipelineSpec]:
    """加载流水线目录；参数 directory 为空时使用内置 pipelines 目录。"""

    root = directory or DEFAULT_PIPELINES_DIR
    specs: dict[str, PipelineSpec] = {}
    for path in sorted(root.glob("*.json")):
        spec = _load_one(path)
        if spec is not None:
            specs[spec.layout_id] = spec
    return specs


def _document_text(blocks: Iterable[EvidenceBlock]) -> str:
    """拼接证据块便于版式锚点匹配；参数 blocks 为页/块索引。"""

    return "\n".join(block.text.casefold() for block in blocks)


def _selection_for(spec: PipelineSpec, document: str, *, threshold: float) -> PipelineSelection:
    """计算一个契约的锚点覆盖率；参数 spec/document/threshold 为契约、文本和阈值。"""

    required = _as_strings(spec.detect.get("all"))
    optional = _as_strings(spec.detect.get("any"))
    anchors = tuple(dict.fromkeys(required + optional))
    matched = tuple(anchor for anchor in anchors if anchor.casefold() in document)
    missing = tuple(anchor for anchor in anchors if anchor not in matched)
    denominator = len(anchors)
    hit_rate = len(matched) / denominator if denominator else 1.0
    min_hits = spec.detect.get("min_hits")
    try:
        min_hits_value = int(min_hits) if min_hits is not None else len(required)
    except (TypeError, ValueError):
        min_hits_value = len(required)
    required_ok = all(anchor in matched for anchor in required)
    any_ok = not optional or any(anchor in matched for anchor in optional)
    eligible = required_ok and any_ok and len(matched) >= min_hits_value
    drift = bool(anchors) and hit_rate < threshold
    return PipelineSelection(
        spec=spec,
        anchor_hit_rate=round(hit_rate, 4),
        matched_anchors=matched,
        missing_anchors=missing,
        layout_drift=drift,
        fallback_reason=None if eligible and not drift else "anchor_coverage_below_threshold",
    )


def select_pipeline(
    blocks: Iterable[EvidenceBlock],
    *,
    preferred_layout_id: str | None = None,
    pipelines_dir: Path | None = None,
    pipeline_path: str | Path | None = None,
    drift_threshold: float = 0.8,
) -> PipelineSelection:
    """选择匹配的字段契约；参数 blocks/preferred_layout_id/pipelines_dir/pipeline_path/drift_threshold 为证据、画像和阈值。"""

    specs = load_pipelines(pipelines_dir)
    if pipeline_path:
        configured = Path(pipeline_path)
        if not configured.is_absolute():
            configured = MODULE_DIR / configured
        custom_spec = _load_one(configured)
        if custom_spec is not None:
            specs[custom_spec.layout_id] = custom_spec
    generic = specs.get(GENERIC_LAYOUT_ID)
    if generic is None:
        raise RuntimeError("missing pipelines/_generic.json")
    document = _document_text(blocks)
    candidates = [spec for key, spec in specs.items() if key != GENERIC_LAYOUT_ID]
    if preferred_layout_id and preferred_layout_id in specs and preferred_layout_id != GENERIC_LAYOUT_ID:
        candidates = [specs[preferred_layout_id]] + [
            spec for spec in candidates if spec.layout_id != preferred_layout_id
        ]
    selections = [_selection_for(spec, document, threshold=drift_threshold) for spec in candidates]
    eligible = [item for item in selections if item.fallback_reason is None]
    if eligible:
        return max(eligible, key=lambda item: (item.anchor_hit_rate, item.spec.layout_id))
    if preferred_layout_id and preferred_layout_id in specs:
        preferred = next(
            (item for item in selections if item.spec.layout_id == preferred_layout_id), None
        )
        if preferred is not None:
            return PipelineSelection(
                spec=generic,
                anchor_hit_rate=preferred.anchor_hit_rate,
                matched_anchors=preferred.matched_anchors,
                missing_anchors=preferred.missing_anchors,
                layout_drift=True,
                fallback_reason="preferred_layout_drift",
            )
    return PipelineSelection(
        spec=generic,
        anchor_hit_rate=1.0,
        matched_anchors=(),
        missing_anchors=(),
        layout_drift=False,
        fallback_reason="no_specific_layout_matched" if selections else None,
    )


def _candidate_for_field(
    name: str,
    rule: dict[str, Any],
    blocks: list[EvidenceBlock],
    *,
    category: str,
    fx_rates: dict[str, float] | None,
) -> Candidate:
    """按契约 parser 执行一个确定性字段；参数为字段规则、证据、类别和汇率。"""

    parser = str(rule.get("parser") or name)
    if parser == "fund_size":
        return extract_fund_size(blocks, fx_rates=fx_rates)
    if parser == "report_month":
        return extract_report_month(blocks, category=category)
    if parser == "duration":
        return extract_metric(
            blocks,
            field_name=name,
            aliases=_as_strings(rule.get("labels")) or DURATION_LABELS,
            exclude=YTM_LABELS,
            require_percent=False,
        )
    if parser == "ytm":
        return extract_metric(
            blocks,
            field_name=name,
            aliases=_as_strings(rule.get("labels")) or YTM_LABELS,
            exclude=DURATION_LABELS,
            require_percent=True,
        )
    if parser == "credit_rating":
        return extract_credit_rating(blocks)
    return Candidate(field=name, status="unparsed", rule="unsupported_pipeline_parser")


def _route(fields: dict[str, Candidate]) -> dict[str, list[str]]:
    """按确定性结果生成调用路由；参数 fields 为字段候选结果。"""

    accept, review, local_model, absent = [], [], [], []
    for name, candidate in fields.items():
        if candidate.status == "absent":
            absent.append(name)
        elif candidate.status == "ok" and candidate.confidence >= 0.9:
            accept.append(name)
        elif candidate.status == "ok":
            review.append(name)
        else:
            local_model.append(name)
    return {"accept": accept, "review": review, "local_model": local_model, "absent": absent}


def execute_pipeline(
    markdown: str,
    *,
    content_list: str | list[dict[str, Any]] | None = None,
    native_pages: Iterable[str] | None = None,
    category: str = "fixed",
    fx_rates: dict[str, float] | None = None,
    report_month_hint: str | None = None,
    preferred_layout_id: str | None = None,
    pipelines_dir: Path | None = None,
    pipeline_path: str | Path | None = None,
    drift_threshold: float = 0.8,
) -> tuple[LocalExtraction, dict[str, Any]]:
    """执行选中的字段契约；参数为 MinerU/原生证据、类别、汇率和画像选择信息。"""

    blocks = build_evidence_index(
        markdown,
        content_list=content_list,
        native_pages=native_pages,
        report_month_hint=report_month_hint,
    )
    selection = select_pipeline(
        blocks,
        preferred_layout_id=preferred_layout_id,
        pipelines_dir=pipelines_dir,
        pipeline_path=pipeline_path,
        drift_threshold=drift_threshold,
    )
    fields: dict[str, Candidate] = {}
    for name, rule in selection.spec.fields.items():
        candidate = _candidate_for_field(
            name,
            rule,
            blocks,
            category=category,
            fx_rates=fx_rates,
        )
        candidate.field = name
        candidate.meta["pipeline"] = {
            "layout_id": selection.spec.layout_id,
            "pipeline_version": selection.spec.pipeline_version,
            "rule_id": str(rule.get("rule_id") or name),
            "stage": str(rule.get("stage") or "deterministic"),
        }
        fields[name] = candidate
    result = LocalExtraction(
        fields=fields,
        routing=_route(fields),
        blocks=blocks,
        disclosures=probe_disclosures(blocks),
    )
    return result, {
        "pipeline_version": selection.spec.pipeline_version,
        "layout_id": selection.spec.layout_id,
        "pipeline_source": str(selection.spec.source),
        "anchor_hit_rate": selection.anchor_hit_rate,
        "matched_anchors": list(selection.matched_anchors),
        "missing_anchors": list(selection.missing_anchors),
        "layout_drift": selection.layout_drift,
        "fallback_reason": selection.fallback_reason,
    }
