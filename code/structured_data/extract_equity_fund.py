#!/usr/bin/env python
"""Extract equity fund fields from MinerU artifacts or the legacy PDF file_id path."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[1]))

try:
    from structured_data.extract_fixed_income import (
        _compute_fund_size_hkd_100m,
        _effective_fund_size_original,
        _fund_size_conversion,
        _chat_context_json,
        _chat_image_json,
        _extract_pdf_text,
        _normalize_currency,
        _number,
        _parse_fund_size_from_text,
        _parse_json,
        _to_simple,
    )
    from structured_data.parse_mineru_pdf import DEFAULT_OUTDIR as DEFAULT_MINERU_OUTDIR
    from structured_data.parse_mineru_pdf import load_mineru_bundle, parse_local_pdf
    from structured_data.manager_profiles import ManagerProfile, profile_for_pdf
    from structured_data.local_first_adapter import apply_local_first, configured_mode, run_local_first
    from structured_data.evidence_contract import (
        build_subschema,
        evidence_schema,
        evidence_retry_fields,
        max_tokens_for_call,
        resolve_evidence,
        schema_sha256,
    )
    from structured_data.holding_tickers import resolve_holding_tickers
    from structured_data.qwen_client import (
        STRUCTURED_SYSTEM_PROMPT,
        QwenClient,
        image_data_url,
        retry_call,
    )
    from structured_data.validate import source_stem
except ImportError:  # pragma: no cover
    from extract_fixed_income import (
        _compute_fund_size_hkd_100m,
        _effective_fund_size_original,
        _fund_size_conversion,
        _chat_context_json,
        _chat_image_json,
        _extract_pdf_text,
        _normalize_currency,
        _number,
        _parse_fund_size_from_text,
        _parse_json,
        _to_simple,
    )
    from parse_mineru_pdf import DEFAULT_OUTDIR as DEFAULT_MINERU_OUTDIR
    from parse_mineru_pdf import load_mineru_bundle, parse_local_pdf
    from manager_profiles import ManagerProfile, profile_for_pdf
    from local_first_adapter import apply_local_first, configured_mode, run_local_first
    from evidence_contract import (  # type: ignore
        build_subschema,
        evidence_schema,
        evidence_retry_fields,
        max_tokens_for_call,
        resolve_evidence,
        schema_sha256,
    )
    from holding_tickers import resolve_holding_tickers
    from qwen_client import (
        STRUCTURED_SYSTEM_PROMPT,
        QwenClient,
        image_data_url,
        retry_call,
    )
    from validate import source_stem


MODULE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = MODULE_DIR.parents[1]
DEFAULT_PROMPT = MODULE_DIR / "equity_fund_prompt.md"
DEFAULT_SCHEMA = MODULE_DIR / "equity_fund_schema.json"
DEFAULT_OUTDIR = PROJECT_ROOT / "data" / "_structured_equity" / "per_pdf"
LOCAL_FIRST_FIELDS = ("fund_size", "report_date")

FINAL_FIELDS = [
    "fund_name",
    "report_date",
    "fund_size_hkd_100m",
    "top_holdings",
    "top_holdings_total_percent",
    "geographic_allocation",
    "sector_allocation",
    "dividend",
]

EVIDENCE_FIELDS = (
    "fund_size_original",
    "report_date",
    "top_holdings",
    "geographic_allocation",
    "sector_allocation",
    "dividend",
)


def _load_schema() -> dict[str, Any]:
    return json.loads(DEFAULT_SCHEMA.read_text(encoding="utf-8"))


def _load_prompt(path: Path, pdf_path: Path) -> str:
    prompt = path.read_text(encoding="utf-8")
    prompt = prompt.replace("<PDF文件名>", pdf_path.stem).replace("<PDF 文件名>", pdf_path.stem)
    return f"{prompt}\n\nJSON Schema:\n{json.dumps(_load_schema(), ensure_ascii=False, indent=2)}"


def _default_prompt(pdf_path: Path) -> str:
    return _load_prompt(DEFAULT_PROMPT, pdf_path)


def _clean_weighted_items(items: Any, *, name_key: str) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    cleaned: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        weight = _number(item.get("weight_percent"))
        name = item.get(name_key)
        if not name or weight is None:
            continue
        if name_key == "name":
            cleaned.append({
                "ticker": item.get("ticker") or None,
                "name": str(_to_simple(name)).strip() or None,
                "weight_percent": weight,
            })
        else:
            cleaned.append({
                name_key: str(_to_simple(name)).strip() or None,
                "weight_percent": weight,
            })
    return cleaned


_MONTH_NAMES = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


def _format_month(year: Any, month: Any) -> str | None:
    try:
        year_int = int(year)
        month_int = int(month)
    except (TypeError, ValueError):
        return None
    if 2000 <= year_int <= 2099 and 1 <= month_int <= 12:
        return f"{year_int:04d}-{month_int:02d}"
    return None


def _is_report_date_context(text: str, start: int, end: int) -> bool:
    context = text[max(0, start - 80): min(len(text), end + 80)].lower()
    negative_terms = [
        "奖",
        "獎",
        "award",
        "表现",
        "表現",
        "performance",
        "浏览",
        "瀏覽",
        "http",
        "www.",
        "起，",
        "起,",
        "改名",
        "更名",
    ]
    return not any(term in context for term in negative_terms)


def _find_report_month(text: str, *, keyword_only: bool = False) -> str | None:
    if not text:
        return None
    date_pattern = r"(20\d{2})\s*[年/-]\s*(\d{1,2})(?:\s*[月/-]\s*(\d{1,2}))?\s*日?"
    keyword_pattern = (
        r"(?:基金报告|基金報告|月报|月報|截至|截止|資料截至|资料截至|As\s+of|Data\s+as\s+at|as\s+at|Fund\s+report)"
        r".{0,40}?"
        + date_pattern
    )
    patterns = [keyword_pattern] if keyword_only else [keyword_pattern, date_pattern]
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            if not _is_report_date_context(text, match.start(), match.end()):
                continue
            formatted = _format_month(match.group(1), match.group(2))
            if formatted:
                return formatted
    english_pattern = r"(\d{1,2})\s+([A-Za-z]{3,9})\s+(20\d{2})|([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(20\d{2})"
    for match in re.finditer(english_pattern, text, flags=re.IGNORECASE):
        if not _is_report_date_context(text, match.start(), match.end()):
            continue
        if match.group(1):
            month = _MONTH_NAMES.get(match.group(2).lower())
            formatted = _format_month(match.group(3), month)
        else:
            month = _MONTH_NAMES.get(match.group(4).lower())
            formatted = _format_month(match.group(6), month)
        if formatted:
            return formatted
    slash_pattern = r"(\d{1,2})/(\d{1,2})/(20\d{2})"
    for match in re.finditer(slash_pattern, text):
        if not _is_report_date_context(text, match.start(), match.end()):
            continue
        formatted = _format_month(match.group(3), match.group(2))
        if formatted:
            return formatted
    return None


def _normalize_report_month(value: Any, fallback: Any = None) -> str | None:
    text = str(value or "").strip()
    first_page_text = text[:1500]
    fallback_month = _find_report_month(str(fallback or ""))
    hint_match = re.search(
        r"\[REPORT_MONTH_HINT\]\s*(20\d{2})-(\d{2})",
        text,
        flags=re.IGNORECASE,
    )
    report_month_hint = (
        _format_month(hint_match.group(1), hint_match.group(2))
        if hint_match
        else None
    )
    publication_match = re.search(
        r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(?:刊|報|报)",
        text,
        flags=re.IGNORECASE,
    )
    publication_month = (
        _format_month(publication_match.group(1), publication_match.group(2))
        if publication_match
        else None
    )
    return (
        report_month_hint
        or publication_month
        or _find_report_month(first_page_text, keyword_only=True)
        or _find_report_month(text, keyword_only=True)
        or fallback_month
        or _find_report_month(first_page_text[:350])
    )


def _report_date_validation(pdf_text: str, model_value: Any, final_value: Any) -> dict[str, Any]:
    hint_match = re.search(
        r"\[REPORT_MONTH_HINT\]\s*(20\d{2})-(\d{2})",
        pdf_text,
        flags=re.IGNORECASE,
    )
    rule_value = (
        _format_month(hint_match.group(1), hint_match.group(2))
        if hint_match
        else None
    )
    model_month = _find_report_month(str(model_value or ""))
    corrected = bool(rule_value and model_month and rule_value != model_month)
    return {
        "model_value": model_value,
        "model_month": model_month,
        "rule_value": rule_value,
        "final_value": final_value,
        "corrected": corrected,
        "reason": (
            "明确的月报/截至日期与模型结果不一致，采用规则校验值"
            if corrected
            else "模型结果与规则校验一致或无需校正"
        ),
    }


def _clean_dividend(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    dividend_per_unit = _number(value.get("dividend_per_unit"))
    annualized_yield = _number(value.get("annualized_dividend_yield_percent"))
    if dividend_per_unit is None and annualized_yield is None:
        return None
    frequency = value.get("frequency") or None
    if frequency not in {"monthly", "quarterly", "semiannual", "annual", "irregular", "unknown", None}:
        frequency = "unknown"
    return {
        "share_class": value.get("share_class") or None,
        "currency": _normalize_currency(value.get("currency")) or value.get("currency") or None,
        "frequency": frequency,
        "dividend_per_unit": dividend_per_unit,
        "annualized_dividend_yield_percent": annualized_yield,
    }


def _allocation_is_suspicious(items: Any) -> bool:
    if not isinstance(items, list) or not items:
        return False
    weights = [_number(item.get("weight_percent")) for item in items if isinstance(item, dict)]
    weights = [weight for weight in weights if weight is not None]
    rounded = [round(float(weight), 2) for weight in weights]
    total = sum(rounded)
    # 不以“相同比例出现多次”判断错配：真实图表完全可能有多个 0.0%、
    # 3.0% 等相同比例。项目较多且合计达到 80% 的局部披露也属于有效结果。
    if total > 102:
        return True
    if len(weights) >= 5 and total >= 80:
        return False
    if len(weights) < 3:
        return total < 80
    return total < 95


def _allocation_coverage(items: Any) -> tuple[int, float]:
    if not isinstance(items, list):
        return 0, 0.0
    weights = [
        float(weight)
        for item in items
        if isinstance(item, dict)
        and (weight := _number(item.get("weight_percent"))) is not None
    ]
    return len(weights), round(sum(weights), 4)


def _prefer_refined_allocation(current_items: Any, refined_items: Any) -> bool:
    """Prefer a materially more complete disclosed matrix, even if it totals below 100%."""
    current_count, current_total = _allocation_coverage(current_items)
    refined_count, refined_total = _allocation_coverage(refined_items)
    if refined_count == 0 or refined_total <= 0 or refined_total > 102:
        return False
    if current_count == 0:
        return True
    if _allocation_is_suspicious(current_items) and not _allocation_is_suspicious(refined_items):
        return True
    return (
        refined_count >= current_count + 2
        and refined_total >= current_total + 10
        and abs(100 - refined_total) < abs(100 - current_total)
    )


def _allocation_needs_vision_retry(items: Any) -> bool:
    """Retry only results that are genuinely sparse or impossible, not disclosed partial matrices."""
    count, total = _allocation_coverage(items)
    if count == 0:
        return False
    return total > 105 or (count <= 2 and total < 80) or (count < 5 and total < 60)


def _allocation_mirrors_holdings(record: dict[str, Any], field: str) -> bool:
    """Detect a holdings table mislabeled as a geography/sector matrix."""
    allocation = record.get(field)
    holdings = record.get("top_holdings")
    if not isinstance(allocation, list) or not isinstance(holdings, list):
        return False
    allocation_weights = [
        round(float(weight), 2)
        for item in allocation
        if isinstance(item, dict)
        and (weight := _number(item.get("weight_percent"))) is not None
    ]
    holding_weights = [
        round(float(weight), 2)
        for item in holdings
        if isinstance(item, dict)
        and (weight := _number(item.get("weight_percent"))) is not None
    ]
    smaller_count = min(len(allocation_weights), len(holding_weights))
    if smaller_count < 2:
        return False
    overlap_count = sum((Counter(allocation_weights) & Counter(holding_weights)).values())
    totals_match = abs(sum(allocation_weights) - sum(holding_weights)) <= 1.0
    if not totals_match:
        return False
    if overlap_count >= max(3, round(smaller_count * 0.8)):
        return True

    # 模型也可能先按行业把前十大持仓相加，再把这些小计误当成行业分布。
    # 对常见的前十大持仓做一次互不重叠的子集分组校验。
    if not (2 <= len(allocation_weights) < len(holding_weights) <= 14):
        return False
    targets = sorted((round(weight * 100) for weight in allocation_weights), reverse=True)
    values = [round(weight * 100) for weight in holding_weights]
    full_mask = (1 << len(values)) - 1
    memo: set[tuple[int, int]] = set()

    def can_partition(target_index: int, remaining_mask: int) -> bool:
        state = (target_index, remaining_mask)
        if state in memo:
            return False
        target = targets[target_index]
        if target_index == len(targets) - 1:
            return abs(sum(values[index] for index in range(len(values)) if remaining_mask & (1 << index)) - target) <= 10
        subset = remaining_mask
        while subset:
            subtotal = sum(values[index] for index in range(len(values)) if subset & (1 << index))
            if abs(subtotal - target) <= 10 and can_partition(target_index + 1, remaining_mask ^ subset):
                return True
            subset = (subset - 1) & remaining_mask
        memo.add(state)
        return False

    return can_partition(0, full_mask)


def _finalize(raw: dict[str, Any], pdf_path: Path, pdf_text: str = "") -> dict[str, Any]:
    size_original = _effective_fund_size_original(raw.get("fund_size_original"), pdf_text)
    holdings = _clean_weighted_items(raw.get("top_holdings"), name_key="name")
    total = _number(raw.get("top_holdings_total_percent"))
    if total is None and holdings:
        total = round(sum(float(item["weight_percent"]) for item in holdings), 4)
    geographic_allocation = _clean_weighted_items(raw.get("geographic_allocation"), name_key="region")
    sector_allocation = _clean_weighted_items(raw.get("sector_allocation"), name_key="sector")
    final = {
        "fund_name": _to_simple(pdf_path.stem),
        "report_date": _normalize_report_month(pdf_text, raw.get("report_date")),
        "fund_size_hkd_100m": _compute_fund_size_hkd_100m(size_original),
        "top_holdings": holdings,
        "top_holdings_total_percent": total,
        "geographic_allocation": geographic_allocation,
        "sector_allocation": sector_allocation,
        "dividend": _clean_dividend(raw.get("dividend")),
    }
    return _to_simple(final)


def _needs_refinement(
    record: dict[str, Any],
    profile: ManagerProfile | None = None,
    context: str = "",
) -> bool:
    """判断是否需要文本二次核对；参数 record/profile/context 为抽取上下文。"""

    def absent(field_name: str) -> bool:
        """返回字段是否可被管理人 profile 稳定跳过。"""

        return bool(profile and profile.field_is_absent(field_name, context))

    if record.get("fund_size_hkd_100m") in (None, ""):
        return True
    for field in ["top_holdings", "geographic_allocation", "sector_allocation"]:
        if not record.get(field) and not absent(field):
            return True
    for item in record.get("top_holdings") or []:
        if item.get("weight_percent") in (None, "") or not item.get("name"):
            return True
    if _missing_matrix_fields(record):
        return True
    return False


def _needs_mineru_text_refinement(
    record: dict[str, Any],
    profile: ManagerProfile | None = None,
    context: str = "",
    skip_text_fields: set[str] | None = None,
) -> bool:
    """只复核能从 MinerU 文本恢复的字段；参数 record/profile/context/skip_text_fields 为上下文。"""

    skip_text_fields = skip_text_fields or set()
    if record.get("fund_size_hkd_100m") in (None, "") and "fund_size" not in skip_text_fields:
        return True
    holdings = record.get("top_holdings") or []
    if (
        not holdings
        and "top_holdings" not in skip_text_fields
        and not (profile and profile.field_is_absent("top_holdings", context))
    ):
        return True
    return any(
        not isinstance(item, dict)
        or not item.get("name")
        or item.get("weight_percent") in (None, "")
        for item in holdings
    )


def _missing_text_refinement_fields(
    record: dict[str, Any],
    profile: ManagerProfile | None = None,
    context: str = "",
    skip_text_fields: set[str] | None = None,
) -> list[str]:
    """收敛权益文本补提字段；参数 record/profile/context/skip_text_fields 为当前结果和路由。"""

    skip_text_fields = skip_text_fields or set()

    def missing(field_name: str, output_name: str | None = None) -> bool:
        """判断字段是否还能由文本层恢复；参数为路由字段和最终输出字段。"""

        target = output_name or field_name
        return (
            field_name not in skip_text_fields
            and record.get(target) in (None, "", [])
            and not (profile and profile.field_is_absent(field_name, context))
        )

    fields = [
        field
        for field, output_name in (
            ("fund_size", "fund_size_hkd_100m"),
            ("top_holdings", None),
            ("geographic_allocation", None),
            ("sector_allocation", None),
        )
        if missing(field, output_name)
    ]
    if not record.get("report_date") and "report_date" not in skip_text_fields:
        fields.append("report_date")
    holdings = record.get("top_holdings") or []
    if any(
        not isinstance(item, dict)
        or not item.get("name")
        or item.get("weight_percent") in (None, "")
        for item in holdings
    ) and "top_holdings" not in fields:
        fields.append("top_holdings")
    return fields


def _local_text_skip_fields(local_meta: dict[str, Any]) -> set[str]:
    """返回 enforce 模式下可由文本层直接判定不复核的字段。"""

    apply_meta = local_meta.get("apply")
    if not isinstance(apply_meta, dict) or apply_meta.get("mode") != "enforce":
        return set()
    return {
        str(field)
        for field in (
            list(local_meta.get("text_skip_fields") or [])
            + list(local_meta.get("matrix_text_skip_fields") or [])
        )
        if field
    }


def _missing_matrix_fields(record: dict[str, Any]) -> list[str]:
    return [
        field
        for field in ("geographic_allocation", "sector_allocation")
        if (
            not record.get(field)
            or _allocation_is_suspicious(record.get(field))
            or _allocation_mirrors_holdings(record, field)
        )
    ]


def _missing_matrix_fields_for_profile(
    record: dict[str, Any],
    profile: ManagerProfile,
    context: str,
) -> list[str]:
    """过滤管理人稳定不披露的矩阵字段；参数 record/profile/context 为抽取上下文。"""

    return [
        field
        for field in _missing_matrix_fields(record)
        if not profile.field_is_absent(field, context)
    ]


def _matrix_text_refinement_prompt(
    pdf_path: Path,
    fields: list[str],
    alias_hint: str = "",
) -> str:
    requested = "、".join(fields)
    hint_block = f"\n\n{alias_hint}" if alias_hint else ""
    return f"""
请用更严格的方式重新阅读 MinerU Markdown，只复核这份权益基金月报中为空或异常的矩阵字段：{requested}。
基金名称固定为“{pdf_path.stem}”。

规则：
1. geographic_allocation 只读取明确的地区/国家/市场配置表；输出
   [{{"region":名称,"weight_percent":数字}}]。
2. sector_allocation 只读取明确的行业/产业配置表；输出
   [{{"sector":名称,"weight_percent":数字}}]。
3. 严禁把“十大持仓/主要持仓”表中的持仓比例按所属行业或地区重新分类，
   也不得将持仓表的比例原样复制到行业或地区列表。
4. Markdown 表格中已经能配对的名称与百分比必须完整读取，包括现金、其他和净流动资产。
5. 不从投资目标或正文观点推导；没有披露才返回 []。
6. 完整分布应重新核对合计是否约为 100；若原文明确是前十大、局部分布，
   即使合计不足 100 也必须保留已披露项目，不要编造差额。
7. 只输出合法 JSON，至少包含以下键：
   geographic_allocation、sector_allocation、field_evidence。{hint_block}
""".strip()


def _refinement_prompt(pdf_path: Path) -> str:
    return f"""
请重新核对这份权益类基金月报，只抽取基金规模、前十大/主要持仓、地区分布、行业分布、派息信息。

特别要求：
1. fund_name 固定为“{pdf_path.stem}”。
2. report_date 输出月报标题月份 YYYY-MM。若标题为“2026年6月（截至2026年5月29日）”，输出 2026-06；括号里的“截至/资料截至/As of”是数据截止日，不是月报月份。只有在没有标题月份、正文只披露“基金报告 | 2026年5月31日”或“截至 2026年5月31日”时，才输出 2026-05。不要取奖项表现截止日或脚注日期。
3. fund_size_original 必须抽取原始规模金额、币种、单位；例如 USD 779.95 Million 输出 amount=779.95,currency=USD,unit=Million。
4. top_holdings 每一项必须同时包含持仓名称和比例；没有比例的持仓不要输出。
5. geographic_allocation 只能从明确标题为“地区分布/地域分布/国家分布/市场分布/Geographical allocation/Market allocation/Country allocation”的图表或表格中抽取。不要从投资目标、投资范围、风险披露或可投资地区描述中推导；没有明确图表或无法精确配对“地区名称+比例”则填 []。
6. sector_allocation 只能从明确标题为“行业分布/产业分布/Sector allocation/Sector breakdown/Industry breakdown”的图表或表格中抽取。严禁根据“十大持仓/主要持仓”中各证券所属行业进行归类、汇总或推导，也不得把持仓比例原样复制为行业比例。不要从投资评论、投资目标、风险披露或行业观点段落中推导；无法精确配对“行业名称+比例”则填 []。
7. 严禁把同一个比例复用给多个持仓、地区或行业。每个项目的比例必须来自该项目旁边、同一行、同一表格单元或同一图表标签；图表文字顺序混乱时，只输出能确定配对的项目。
8. geographic_allocation 和 sector_allocation 如果是完整分布，应重新检查合计是否约为 100、是否漏了现金、其他或净流动资产。若原文明确是前十大或局部分布，即使合计不足 100 也必须保留；不得只因合计异常而填 []。
9. 同一分布允许多个项目真实披露相同数字，尤其允许多个 0.0%；只要图表能精确配对就必须保留。
10. dividend 只有具体派息金额或派息率时才输出，否则为 null。
11. field_evidence 每个字段仅输出上下文 [PAGE n] 内的 {{"page_idx":n,"block_index":m}}；不得复述原文，未披露或无法精确配对时填 null。

只输出合法 JSON，结构必须符合：
{json.dumps(_load_schema(), ensure_ascii=False, indent=2)}
""".strip()


def _chat_file_json(
    client: QwenClient,
    *,
    model: str,
    file_id: str,
    prompt: str,
    attempts: int,
    enable_thinking: bool,
    thinking_budget: int | None,
    call_name: str,
    response_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response_text = retry_call(
        lambda: client.chat(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": STRUCTURED_SYSTEM_PROMPT + f"\n\nFILE_ID: {file_id}",
                },
                {"role": "user", "content": prompt},
            ],
            enable_thinking=enable_thinking,
            thinking_budget=thinking_budget,
            response_schema=response_schema or build_subschema(_load_schema()),
            max_tokens=max_tokens_for_call(call_name),
            call_name=call_name,
        ),
        attempts=attempts,
    )
    try:
        return _parse_json(response_text)
    except Exception:
        repaired_text = client.chat(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": STRUCTURED_SYSTEM_PROMPT + "\n请只修复 JSON 格式，不新增事实。",
                },
                {"role": "user", "content": f"请修复为合法 JSON，不新增事实：\n{response_text}"},
            ],
            enable_thinking=enable_thinking,
            thinking_budget=thinking_budget,
            response_schema=response_schema or build_subschema(_load_schema()),
            max_tokens=max_tokens_for_call(f"{call_name}_json_repair"),
            call_name=f"{call_name}_json_repair",
        )
        return _parse_json(repaired_text)


def _extract_allocations_from_images(
    client: QwenClient,
    *,
    image_paths: list[Path],
    vision_model: str,
    attempts: int,
    enable_thinking: bool,
    thinking_budget: int | None,
    fields: list[str] | None = None,
    alias_hint: str = "",
) -> dict[str, Any] | None:
    if not image_paths:
        return None
    requested = (
        ["geographic_allocation", "sector_allocation"]
        if fields is None
        else fields
    )
    selected = [
        field
        for field in requested
        if field in {"geographic_allocation", "sector_allocation"}
    ]
    if not selected:
        return None
    sections = []
    schemas = []
    evidence = []
    if "geographic_allocation" in selected:
        sections.append(
            "地区分布：只读取标题明确为“地区/地域/国家/市场分布”的图表。"
        )
        schemas.append('"geographic_allocation": [{"region": string, "weight_percent": number}]')
        evidence.append('"geographic_allocation": string | null')
    if "sector_allocation" in selected:
        sections.append(
            "行业分布：只读取标题明确为“行业/产业分布”的图表。"
        )
        schemas.append('"sector_allocation": [{"sector": string, "weight_percent": number}]')
        evidence.append('"sector_allocation": string | null')
    prompt = (
        "请只读取图片中标题明确的基金配置图表，不要从正文或投资目标推导配置。\n"
        + "\n".join(sections)
        + "\n每个项目必须准确配对名称和百分比。\n"
        "忽略“十大持仓/主要持仓”表；不得根据持仓所属行业或上市地点自行归类，\n"
        "也不得把持仓比例复制为行业或地区比例。\n"
        "逐一读取图表周围的每一条引线标签，不能遗漏长标签或换行标签。\n"
        "现金、浮动资金、净流动资金、其他、其他基金、交易所买卖基金也要保留。\n"
        "输出前分别计算列表合计；完整图表应约为 100%，否则重新检查遗漏标签。\n"
        "若图表明确只披露前十大或局部项目，即使合计不足 100% 也要保留已披露项目。\n"
        "看不清的图表或项目不要猜。百分比只输出数字，不带 %。\n"
        "输出合法 JSON：\n{\n  "
        + ",\n  ".join(schemas)
        + ',\n  "field_evidence": {\n    '
        + ",\n    ".join(evidence)
        + "\n  }\n}"
    )
    if alias_hint:
        prompt += f"\n\n{alias_hint}"
    try:
        urls = [image_data_url(path) for path in image_paths[:12]]
        result = _chat_image_json(
            client,
            model=vision_model,
            prompt=prompt,
            image_urls=urls,
            attempts=attempts,
            enable_thinking=False,
            thinking_budget=None,
            call_name="equity_allocation_vision_refinement",
        )
        geographic = (
            _clean_weighted_items(result.get("geographic_allocation"), name_key="region")
            if "geographic_allocation" in selected
            else []
        )
        sectors = (
            _clean_weighted_items(result.get("sector_allocation"), name_key="sector")
            if "sector_allocation" in selected
            else []
        )
        needs_retry = (
            (not geographic and not sectors)
            or (
                "geographic_allocation" in selected
                and _allocation_needs_vision_retry(geographic)
            )
            or (
                "sector_allocation" in selected
                and _allocation_needs_vision_retry(sectors)
            )
        )
        if not needs_retry:
            return {
                field: result.get(field)
                for field in selected
                if field in result
            } | (
                {
                    "field_evidence": {
                        field: value
                        for field, value in (result.get("field_evidence") or {}).items()
                        if field in selected
                    }
                }
                if isinstance(result.get("field_evidence"), dict)
                else {}
            )
        retry_prompt = (
            f"{prompt}\n\n"
            "上一次识别结果不完整或合计明显异常。请重新逐一查看所有引线标签，"
            "尤其检查浮动资金、其他、基金/ETF 等容易漏掉的项目。不要沿用错误结果。\n"
            f"上一次结果：{json.dumps(result, ensure_ascii=False)}"
        )
        retried = _chat_image_json(
            client,
            model=vision_model,
            prompt=retry_prompt,
            image_urls=urls,
            attempts=attempts,
            enable_thinking=False,
            thinking_budget=None,
            call_name="equity_allocation_vision_retry",
        )
        return {
            field: retried.get(field)
            for field in selected
            if field in retried
        } | (
            {
                "field_evidence": {
                    field: value
                    for field, value in (retried.get("field_evidence") or {}).items()
                    if field in selected
                }
            }
            if isinstance(retried.get("field_evidence"), dict)
            else {}
        )
    except Exception:
        return None


def _merge_raw(primary: dict[str, Any], refinement: dict[str, Any], pdf_text: str, pdf_path: Path) -> dict[str, Any]:
    merged = dict(primary)
    current = _finalize(merged, pdf_path, pdf_text)
    refined = _finalize(refinement, pdf_path, pdf_text)
    for field in ["fund_size_original", "top_holdings", "geographic_allocation", "sector_allocation", "dividend"]:
        if not current.get(field if field != "fund_size_original" else "fund_size_hkd_100m") and refinement.get(field) not in (None, "", []):
            merged[field] = refinement.get(field)
        elif field in {"geographic_allocation", "sector_allocation"}:
            if refinement.get(field) not in (None, "", []):
                refined_items = refined.get(field)
                current_mirrors_holdings = _allocation_mirrors_holdings(current, field)
                refined_with_context = dict(refined)
                if not refined_with_context.get("top_holdings"):
                    refined_with_context["top_holdings"] = current.get("top_holdings", [])
                refined_mirrors_holdings = _allocation_mirrors_holdings(refined_with_context, field)
                if current_mirrors_holdings and not refined_mirrors_holdings:
                    merged[field] = refinement.get(field)
                elif _prefer_refined_allocation(current.get(field), refined_items):
                    merged[field] = refinement.get(field)
    if refined.get("top_holdings_total_percent") and not current.get("top_holdings_total_percent"):
        merged["top_holdings_total_percent"] = refinement.get("top_holdings_total_percent")
    evidence = {}
    if isinstance(primary.get("field_evidence"), dict):
        evidence.update(primary["field_evidence"])
    if isinstance(refinement.get("field_evidence"), dict):
        evidence.update(refinement["field_evidence"])
    if evidence:
        merged["field_evidence"] = evidence
    return merged


def _evidence_reference_schema(fields: list[str]) -> dict[str, Any]:
    """构建仅重试证据引用的子 Schema；参数 fields 为待修复的字段名。"""

    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["field_evidence"],
        "properties": {"field_evidence": evidence_schema(fields)},
    }


def _resolve_field_evidence(raw: dict[str, Any], local_result: Any) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """校验并回填最终证据；参数 raw/local_result 为模型输出与本地证据索引。"""

    local_fields = getattr(local_result, "fields", {}) if local_result is not None else {}
    blocks = getattr(local_result, "blocks", []) if local_result is not None else []
    return resolve_evidence(
        raw.get("field_evidence"),
        blocks,
        fields=EVIDENCE_FIELDS,
        local_evidence=local_fields,
    )


def _validation_issues(record: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if not record.get("fund_name"):
        issues.append("缺少基金名称")
    if record.get("fund_size_hkd_100m") in (None, ""):
        issues.append("fund_size_hkd_100m 为空")
    for field in ["top_holdings", "geographic_allocation", "sector_allocation"]:
        if not record.get(field):
            issues.append(f"{field} 为空")
    for field, name_key in [("top_holdings", "name"), ("geographic_allocation", "region"), ("sector_allocation", "sector")]:
        for index, item in enumerate(record.get(field) or [], start=1):
            if not item.get(name_key) or item.get("weight_percent") in (None, ""):
                issues.append(f"{field}[{index}] 缺少名称或比例")
        if field in {"geographic_allocation", "sector_allocation"} and _allocation_is_suspicious(record.get(field)):
            issues.append(f"{field} 疑似错配或比例合计异常")
        if field in {"geographic_allocation", "sector_allocation"} and _allocation_mirrors_holdings(record, field):
            issues.append(f"{field} 疑似误用了持仓明细比例")
    return issues


def extract_pdf(
    *,
    pdf_path: Path,
    primary_model: str,
    fallback_model: str,
    attempts: int,
    prompt_path: Path,
    timeout: int,
    enable_thinking: bool = True,
    thinking_budget: int | None = None,
    vision_model: str = "qwen3.7-plus",
    source: str = "mineru",
    mineru_model: str = "vlm",
    mineru_outdir: Path = DEFAULT_MINERU_OUTDIR,
    mineru_force: bool = False,
    mineru_cache_only: bool = False,
    mineru_poll_timeout: int = 900,
) -> dict[str, Any]:
    client = QwenClient(timeout=timeout)
    client_provider = str(getattr(client, "provider", "deepseek"))
    prompt = _load_prompt(prompt_path, pdf_path) if prompt_path.exists() else _default_prompt(pdf_path)
    manager_profile = profile_for_pdf(pdf_path)
    pdf_text = _extract_pdf_text(pdf_path)
    failures: list[str] = []
    input_source = source
    mineru_manifest: dict[str, Any] | None = None
    mineru_bundle: dict[str, Any] | None = None

    if source in {"mineru", "auto"}:
        try:
            mineru_manifest = parse_local_pdf(
                pdf_path,
                outdir=mineru_outdir,
                mode="auto",
                model=mineru_model,
                force=mineru_force,
                cache_only=mineru_cache_only,
                request_timeout=timeout,
                poll_timeout=mineru_poll_timeout,
            )
            mineru_bundle = load_mineru_bundle(mineru_manifest)
            pdf_text = str(mineru_bundle["context"])
            input_source = "mineru"
        except Exception as exc:  # noqa: BLE001
            failures.append(f"mineru: {type(exc).__name__}: {exc}")
            input_source = "file-id" if source == "auto" else "mineru_failed"

    local_result = None
    local_meta: dict[str, Any] = {"available": False, "mode": configured_mode()}
    if configured_mode() != "off":
        local_result, local_meta = run_local_first(
            pdf_path=pdf_path,
            bundle=mineru_bundle,
            category="equity",
            manager_profile=manager_profile,
        )

    call_decisions: list[dict[str, Any]] = []
    for model in dict.fromkeys([primary_model, fallback_model]):
        if input_source == "mineru_failed":
            break
        try:
            file_id: str | None = None
            if input_source == "mineru" and mineru_bundle:
                raw = _chat_context_json(
                    client,
                    model=model,
                    context=str(mineru_bundle["context"]),
                    prompt=prompt,
                    attempts=attempts,
                    enable_thinking=enable_thinking,
                    thinking_budget=thinking_budget,
                    response_schema=build_subschema(_load_schema()),
                    call_name="equity_mineru_primary_extraction",
                )
            else:
                file_id = retry_call(lambda: client.upload_file(pdf_path), attempts=attempts)
                raw = _chat_file_json(
                    client,
                    model=model,
                    file_id=file_id,
                    prompt=prompt,
                    attempts=attempts,
                    enable_thinking=enable_thinking,
                    thinking_budget=thinking_budget,
                    call_name="equity_primary_extraction",
                )
            raw, local_apply_meta = apply_local_first(
                raw,
                local_result,
                fields=LOCAL_FIRST_FIELDS,
            )
            local_meta["apply"] = local_apply_meta
            local_text_skip = _local_text_skip_fields(local_meta)
            final = _finalize(raw, pdf_path, pdf_text)
            refinement = None
            text_refinement_fields = _missing_text_refinement_fields(
                final,
                manager_profile,
                pdf_text,
                skip_text_fields=local_text_skip if input_source == "mineru" else set(),
            )
            if text_refinement_fields and not manager_profile.skips("field"):
                call_decisions.append({
                    "call": "combined_text_refinement",
                    "status": "called",
                    "fields": text_refinement_fields,
                })
                if input_source == "mineru" and mineru_bundle:
                    refinement = _chat_context_json(
                        client,
                        model=model,
                        context=str(mineru_bundle["context"]),
                        prompt=(
                            _refinement_prompt(pdf_path)
                            + "\n\n本次仅补提这些字段："
                            + "、".join(text_refinement_fields)
                        ),
                        attempts=attempts,
                        enable_thinking=enable_thinking,
                        thinking_budget=thinking_budget,
                        response_schema=build_subschema(_load_schema()),
                        call_name="equity_mineru_combined_text_refinement",
                    )
                else:
                    refinement = _chat_file_json(
                        client,
                        model=model,
                        file_id=str(file_id),
                        prompt=(
                            _refinement_prompt(pdf_path)
                            + "\n\n本次仅补提这些字段："
                            + "、".join(text_refinement_fields)
                        ),
                        attempts=attempts,
                        enable_thinking=enable_thinking,
                        thinking_budget=thinking_budget,
                        call_name="equity_combined_text_refinement",
                    )
                raw = _merge_raw(raw, refinement, pdf_text, pdf_path)
                final = _finalize(raw, pdf_path, pdf_text)
            else:
                call_decisions.append({
                    "call": "combined_text_refinement",
                    "status": "skipped",
                    "fields": text_refinement_fields,
                    "skip_reason": (
                        "manager_profile_skip" if manager_profile.skips("field")
                        else "no_text_recoverable_fields"
                    ),
                })
            matrix_text_refinement = None
            vision_refinement = None
            vision_context = (
                str(mineru_bundle["context"]) if mineru_bundle else pdf_text
            )
            vision_enabled = (
                manager_profile.vision_review
                and not manager_profile.skips("vision")
                and client_provider != "local"
            )
            remaining_matrix_fields = _missing_matrix_fields_for_profile(
                final,
                manager_profile,
                vision_context,
            )
            if vision_enabled and mineru_bundle and remaining_matrix_fields:
                call_decisions.append({
                    "call": "allocation_vision_refinement",
                    "status": "called",
                    "fields": remaining_matrix_fields,
                })
                vision_refinement = _extract_allocations_from_images(
                    client,
                    image_paths=list(mineru_bundle.get("image_paths") or []),
                    vision_model=vision_model,
                    attempts=attempts,
                    enable_thinking=enable_thinking,
                    thinking_budget=thinking_budget,
                    fields=remaining_matrix_fields,
                    alias_hint=manager_profile.alias_hint(remaining_matrix_fields),
                )
                if vision_refinement:
                    raw = _merge_raw(raw, vision_refinement, pdf_text, pdf_path)
                    final = _finalize(raw, pdf_path, pdf_text)
            else:
                call_decisions.append({
                    "call": "allocation_vision_refinement",
                    "status": "skipped",
                    "skip_reason": (
                        "local_text_model_no_vision" if client_provider == "local"
                        else "manager_profile_skip" if not vision_enabled
                        else "no_missing_visual_fields"
                    ),
                })
            ticker_resolution = None
            ticker_resolution_error = None
            try:
                final["top_holdings"], ticker_resolution = resolve_holding_tickers(
                    client,
                    list(final.get("top_holdings") or []),
                    model=model,
                    attempts=attempts,
                    asset_class="权益类",
                )
                if ticker_resolution and ticker_resolution.get("_error"):
                    ticker_resolution_error = str(ticker_resolution["_error"])
                    failures.append(f"{model}/ticker_resolution: {ticker_resolution_error}")
            except Exception as exc:  # noqa: BLE001
                ticker_resolution_error = f"{type(exc).__name__}: {exc}"
                failures.append(f"{model}/ticker_resolution: {ticker_resolution_error}")
                final["top_holdings"] = [
                    {**item, "ticker": None}
                    for item in final.get("top_holdings") or []
                    if isinstance(item, dict)
                ]
            field_evidence, evidence_audit = _resolve_field_evidence(raw, local_result)
            retry_fields = evidence_retry_fields(evidence_audit)
            can_retry_evidence = bool(
                retry_fields
                and input_source == "mineru"
                and mineru_bundle
                and "[BLOCK " in str(mineru_bundle.get("context") or "")
            )
            if can_retry_evidence:
                call_decisions.append({"call": "evidence_ref_retry", "status": "called", "fields": retry_fields})
                retried_evidence = _chat_context_json(
                    client,
                    model=model,
                    context=str(mineru_bundle["context"]),
                    prompt=(
                        "仅修复下列字段的 field_evidence。每项必须引用上下文中实际存在的 "
                        "[PAGE n] / [BLOCK m]，输出 {\"field_evidence\": {...}}，不得输出原文或字段值："
                        + "、".join(retry_fields)
                    ),
                    attempts=1,
                    enable_thinking=False,
                    thinking_budget=None,
                    call_name="equity_evidence_ref_retry",
                    response_schema=_evidence_reference_schema(retry_fields),
                )
                if isinstance(retried_evidence.get("field_evidence"), dict):
                    raw["field_evidence"] = {
                        **(raw.get("field_evidence") if isinstance(raw.get("field_evidence"), dict) else {}),
                        **retried_evidence["field_evidence"],
                    }
                    field_evidence, evidence_audit = _resolve_field_evidence(raw, local_result)
            else:
                call_decisions.append({
                    "call": "evidence_ref_retry",
                    "status": "skipped",
                    "fields": retry_fields,
                    "skip_reason": "no_invalid_references" if not retry_fields else "block_context_unavailable",
                })
            final["field_evidence"] = field_evidence
            issues = _validation_issues(final)
            if ticker_resolution_error:
                issues.append("ticker API 复核失败")
            return {
                "status": "ok" if not issues else "review",
                "data": final,
                "_meta": {
                    "source_file": str(pdf_path),
                    "extracted_at": datetime.now(timezone.utc).isoformat(),
                    "model": model,
                    "input_source": input_source,
                    "manager_profile": manager_profile.to_meta(),
                    "file_id": file_id,
                    "mineru": {
                        "manifest_path": mineru_manifest.get("manifest_path"),
                        "batch_id": mineru_manifest.get("batch_id"),
                        "model_version": mineru_manifest.get("model_version"),
                        "markdown_path": str(mineru_bundle.get("markdown_path")) if mineru_bundle else None,
                        "content_list_path": str(mineru_bundle.get("content_list_path")) if mineru_bundle and mineru_bundle.get("content_list_path") else None,
                        "image_count": len(mineru_bundle.get("image_paths") or []) if mineru_bundle else 0,
                        "report_month_hint": mineru_bundle.get("report_month_hint") if mineru_bundle else None,
                        "context_mode": mineru_bundle.get("context_mode") if mineru_bundle else None,
                        "context_chars": mineru_bundle.get("context_chars") if mineru_bundle else None,
                        "context_full_chars": mineru_bundle.get("context_full_chars") if mineru_bundle else None,
                        "context_sha256": mineru_bundle.get("context_sha256") if mineru_bundle else None,
                        "context_full_sha256": mineru_bundle.get("context_full_sha256") if mineru_bundle else None,
                    } if mineru_manifest else None,
                    "report_date_validation": _report_date_validation(
                        pdf_text,
                        raw.get("report_date"),
                        final.get("report_date"),
                    ),
                    "raw_model_output": raw,
                    "refinement_output": refinement,
                    "matrix_text_refinement_output": matrix_text_refinement,
                    "call_decisions": call_decisions,
                    "vision_refinement_output": vision_refinement,
                    "ticker_resolution_output": ticker_resolution,
                    "ticker_resolution_error": ticker_resolution_error,
                    "local_first": local_meta,
                    "pipeline": local_meta.get("pipeline"),
                    "pipeline_version": (local_meta.get("pipeline") or {}).get("pipeline_version"),
                    "field_provenance": local_meta.get("field_provenance", {}),
                    "evidence_contract": evidence_audit,
                    "output_contract": {
                        "schema_mode": "json_schema_strict" if client_provider == "qwen" else "json_object_subschema",
                        "schema_sha256": schema_sha256(build_subschema(_load_schema())),
                    },
                    "fund_size_conversion": _fund_size_conversion(
                        _effective_fund_size_original(raw.get("fund_size_original"), pdf_text)
                    ),
                    "enable_thinking": enable_thinking,
                    "thinking_budget": thinking_budget,
                    "token_usage": client.token_usage(),
                    "validation_issues": issues,
                },
            }
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{model}/{input_source}: {type(exc).__name__}: {exc}")

    return {
        "status": "failed",
        "data": _to_simple({
            "fund_name": pdf_path.stem,
            "report_date": None,
            "fund_size_hkd_100m": None,
            "top_holdings": [],
            "top_holdings_total_percent": None,
            "geographic_allocation": [],
            "sector_allocation": [],
            "dividend": None,
        }),
        "_meta": {
            "source_file": str(pdf_path),
            "extracted_at": datetime.now(timezone.utc).isoformat(),
            "failures": failures,
            "manager_profile": manager_profile.to_meta(),
            "enable_thinking": enable_thinking,
            "thinking_budget": thinking_budget,
            "token_usage": client.token_usage(),
            "input_source": input_source,
            "local_first": local_meta,
        },
    }


def write_result(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result["data"], ensure_ascii=False, indent=2), encoding="utf-8")
    path.with_suffix(".meta.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract equity fund structured data from one PDF.")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR)
    parser.add_argument("--primary-model", default="deepseek-flash")
    parser.add_argument("--fallback-model", default="deepseek-v4-pro")
    parser.add_argument("--vision-model", default="deepseek-flash")
    parser.add_argument("--source", choices=("mineru", "auto", "file-id"), default="mineru", help="auto falls back to the legacy file-id path")
    parser.add_argument("--mineru-model", choices=("vlm", "pipeline"), default="vlm")
    parser.add_argument("--mineru-outdir", type=Path, default=DEFAULT_MINERU_OUTDIR)
    parser.add_argument("--mineru-force", action="store_true", help="Ignore a completed MinerU cache")
    parser.add_argument("--mineru-cache-only", action="store_true", help="只读 MinerU 缓存，缺失时失败，不调用 MinerU API")
    parser.add_argument("--mineru-poll-timeout", type=int, default=900)
    parser.add_argument("--no-thinking", action="store_true", help="Disable model thinking mode")
    parser.add_argument("--thinking-budget", type=int, default=None, help="Optional thinking budget")
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--prompt", type=Path, default=DEFAULT_PROMPT)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()

    pdf_path = args.pdf.resolve()
    result = extract_pdf(
        pdf_path=pdf_path,
        primary_model=args.primary_model,
        fallback_model=args.fallback_model,
        attempts=args.attempts,
        prompt_path=args.prompt,
        timeout=args.timeout,
        enable_thinking=not args.no_thinking,
        thinking_budget=args.thinking_budget,
        vision_model=args.vision_model,
        source=args.source,
        mineru_model=args.mineru_model,
        mineru_outdir=args.mineru_outdir,
        mineru_force=args.mineru_force,
        mineru_cache_only=args.mineru_cache_only,
        mineru_poll_timeout=args.mineru_poll_timeout,
    )
    output = args.outdir / f"{source_stem(str(pdf_path))}.equity_fund.json"
    write_result(output, result)
    print(json.dumps({"status": result["status"], "output": str(output), "fund_count": 1 if result["status"] != "failed" else 0}, ensure_ascii=False))
    return 0 if result["status"] != "failed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
