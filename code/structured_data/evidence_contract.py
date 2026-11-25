#!/usr/bin/env python
"""输出 Schema、Token 预算与页/块证据引用契约。"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any, Iterable

try:
    from structured_data.local_first import EvidenceBlock, compact_text
except ImportError:  # pragma: no cover - standalone invocation
    from local_first import EvidenceBlock, compact_text  # type: ignore


EVIDENCE_REFERENCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["page_idx", "block_index"],
    "properties": {
        "page_idx": {"type": "integer", "minimum": 0},
        "block_index": {"type": "integer", "minimum": 0},
    },
}

ARRAY_LIMITS = {
    "top_holdings": 20,
    "geographic_allocation": 40,
    "sector_allocation": 40,
    "duration_distribution": 20,
    "credit_rating_distribution": 20,
}

OUTPUT_TOKEN_BUDGETS = {
    # 半年报的持仓名称和 field_evidence 会明显长于月报；1800 tokens
    # 会让 DeepSeek 在合法 JSON 尚未闭合时达到上限，最终表现为
    # ``JSONDecodeError: Expecting ',' delimiter``。适当放宽上限，仍由
    # 子调用的专用 schema 限制输出范围。
    "primary": 3000,
    "combined_text_refinement": 2200,
    "matrix": 1800,
    "vision": 2200,
    # Small local models often truncate the repair response while closing a
    # nested evidence object. Give the repair pass enough room to emit a
    # complete object; this is still below the primary extraction budget.
    "json_repair": 3600,
    "default": 1200,
}


def max_tokens_for_call(call_name: str) -> int:
    """按调用名称返回输出上限；参数 call_name 为调用审计名称。"""

    name = str(call_name or "").casefold()
    if "json_repair" in name:
        return OUTPUT_TOKEN_BUDGETS["json_repair"]
    if "evidence_ref_retry" in name:
        return 300
    if "primary_extraction" in name:
        return OUTPUT_TOKEN_BUDGETS["primary"]
    if "combined_text_refinement" in name:
        return OUTPUT_TOKEN_BUDGETS["combined_text_refinement"]
    if "matrix" in name:
        return OUTPUT_TOKEN_BUDGETS["matrix"]
    if "vision" in name or "visual" in name:
        return OUTPUT_TOKEN_BUDGETS["vision"]
    return OUTPUT_TOKEN_BUDGETS["default"]


def evidence_schema(fields: Iterable[str]) -> dict[str, Any]:
    """构建字段到最多三条块引用的 Schema；参数 fields 为本次允许的字段名。"""

    reference_or_null = {
        "anyOf": [
            {"type": "null"},
            deepcopy(EVIDENCE_REFERENCE_SCHEMA),
            {
                "type": "array",
                "maxItems": 3,
                "items": deepcopy(EVIDENCE_REFERENCE_SCHEMA),
            },
        ]
    }
    names = list(dict.fromkeys(str(field) for field in fields))
    return {
        "type": "object",
        "additionalProperties": False,
        "required": names,
        "properties": {name: deepcopy(reference_or_null) for name in names},
    }


def build_subschema(
    base_schema: dict[str, Any],
    fields: Iterable[str] | None = None,
    *,
    evidence_fields: Iterable[str] | None = None,
) -> dict[str, Any]:
    """从完整 Schema 建字段子集；参数 base_schema/fields/evidence_fields 为基准与字段集合。"""

    properties = base_schema.get("properties") if isinstance(base_schema, dict) else {}
    if not isinstance(properties, dict):
        raise ValueError("base schema properties must be an object")
    selected = list(dict.fromkeys(fields or properties.keys()))
    result_properties = {
        name: deepcopy(properties[name]) for name in selected if name in properties
    }
    for name, definition in result_properties.items():
        if name in ARRAY_LIMITS and isinstance(definition, dict):
            definition["maxItems"] = ARRAY_LIMITS[name]
    evidence_names = list(dict.fromkeys(evidence_fields or selected))
    result_properties["field_evidence"] = evidence_schema(evidence_names)
    required = [name for name in selected if name in result_properties]
    required.append("field_evidence")
    return {
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": result_properties,
    }


def schema_sha256(schema: dict[str, Any]) -> str:
    """计算 Schema 的稳定摘要；参数 schema 为待审计的 JSON Schema。"""

    encoded = json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _references(value: Any) -> list[dict[str, int]]:
    """解析单个模型证据值；参数 value 为引用对象、数组或空值。"""

    items = value if isinstance(value, list) else [value]
    references: list[dict[str, int]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        page = item.get("page_idx", item.get("page"))
        index = item.get("block_index", item.get("block"))
        if isinstance(page, int) and isinstance(index, int) and page >= 0 and index >= 0:
            references.append({"page_idx": page, "block_index": index})
    return references[:3]


def _enriched_reference(block: EvidenceBlock) -> dict[str, Any]:
    """将内部证据块转为输出引用；参数 block 为已校验的证据块。"""

    return {
        "page_idx": block.page,
        "block_index": block.index,
        "source": block.source,
        "kind": block.kind,
        "quote": block.text[:240],
    }


def resolve_evidence(
    raw_evidence: Any,
    blocks: Iterable[EvidenceBlock],
    *,
    fields: Iterable[str],
    local_evidence: dict[str, Any] | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """校验并回填字段证据；参数为模型引用、证据索引、字段和本地证据。"""

    block_list = list(blocks)
    by_ref = {(block.page, block.index): block for block in block_list}
    raw = raw_evidence if isinstance(raw_evidence, dict) else {}
    local = local_evidence if isinstance(local_evidence, dict) else {}
    resolved: dict[str, list[dict[str, Any]]] = {}
    invalid: dict[str, list[dict[str, int]]] = {}
    for field in dict.fromkeys(str(name) for name in fields):
        references = _references(raw.get(field))
        valid = [
            _enriched_reference(by_ref[(ref["page_idx"], ref["block_index"])] )
            for ref in references
            if (ref["page_idx"], ref["block_index"]) in by_ref
        ]
        rejected = [
            ref for ref in references if (ref["page_idx"], ref["block_index"]) not in by_ref
        ]
        if rejected:
            invalid[field] = rejected
        elif not references and (
            raw.get(field) not in (None, "", []) or raw.get(field) is not None
        ):
            # 旧版模型会复述短句，或在有字段值时漏掉引用；两者都需定向重试。
            invalid[field] = []
        if not valid:
            fallback = local.get(field) or (
                local.get("fund_size") if field == "fund_size_original" else None
            )
            if isinstance(fallback, dict):
                fallback = fallback.get("evidence")
            elif fallback is not None:
                fallback = getattr(fallback, "evidence", None)
            fallback_items = fallback if isinstance(fallback, list) else []
            for item in fallback_items[:3]:
                if not isinstance(item, dict):
                    continue
                page = item.get("page", item.get("page_idx"))
                index = item.get("block", item.get("block_index"))
                if isinstance(page, int) and isinstance(index, int):
                    block = by_ref.get((page, index))
                    if block is not None:
                        valid.append(_enriched_reference(block))
        if valid:
            invalid.pop(field, None)
        resolved[field] = valid
    requested = sum(len(_references(raw.get(field))) for field in resolved)
    valid_count = sum(len(items) for items in resolved.values())
    expected_fields = [
        field
        for field in resolved
        if raw.get(field) is not None or bool(resolved[field])
    ]
    resolved_fields = sum(1 for field in expected_fields if resolved[field])
    return resolved, {
        "requested_refs": requested,
        "valid_refs": valid_count,
        "invalid_refs": invalid,
        "valid_rate": round(valid_count / requested, 4) if requested else None,
        "field_coverage_rate": (
            round(resolved_fields / len(expected_fields), 4) if expected_fields else None
        ),
    }


def evidence_retry_fields(audit: dict[str, Any]) -> list[str]:
    """返回需要低成本证据重试的字段；参数 audit 为 resolve_evidence 审计结果。"""

    invalid = audit.get("invalid_refs") if isinstance(audit, dict) else {}
    return sorted(invalid) if isinstance(invalid, dict) else []
