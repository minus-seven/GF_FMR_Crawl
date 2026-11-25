#!/usr/bin/env python
"""Extract fixed-income fund fields from one PDF through DashScope file_id."""

from __future__ import annotations

import argparse
import calendar
import hashlib
import html
import json
import os
import re
import shutil
import sys
import subprocess
import tempfile
import unicodedata
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import requests

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[1]))

try:
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
    from structured_data.qwen_client import (
        STRUCTURED_SYSTEM_PROMPT,
        QwenClient,
        image_data_url,
        pdf_page_image_urls,
        pdf_summary_candidate_image_urls,
        retry_call,
    )
    from structured_data.rating import derive_median_rating, normalize_rating
    from structured_data.validate import source_stem
    from structured_data.report_contract import (
        correct_structured_record,
        detect_report_contract,
        report_contract_prompt,
    )
except ImportError:  # pragma: no cover
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
    from qwen_client import (
        STRUCTURED_SYSTEM_PROMPT,
        QwenClient,
        image_data_url,
        pdf_page_image_urls,
        pdf_summary_candidate_image_urls,
        retry_call,
    )
    from rating import derive_median_rating, normalize_rating
    from validate import source_stem
    from report_contract import (  # type: ignore
        correct_structured_record,
        detect_report_contract,
        report_contract_prompt,
    )

try:
    from opencc import OpenCC
except ImportError:  # pragma: no cover
    OpenCC = None


MODULE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = MODULE_DIR.parents[1]
DEFAULT_PROMPT = MODULE_DIR / "fixed_income_prompt.md"
DEFAULT_OUTDIR = PROJECT_ROOT / "data" / "_structured" / "per_pdf"
LOCAL_FIRST_FIELDS = (
    "fund_size",
    "report_date",
    "portfolio_duration_years",
    "portfolio_ytm_percent",
    "credit_rating",
)

FX_FALLBACK = {
    ("USD", "HKD"): 7.8,
    ("HKD", "HKD"): 1.0,
    ("CNY", "HKD"): 1.08,
    ("RMB", "HKD"): 1.08,
    ("AUD", "HKD"): 5.1,
}

MAX_VISUAL_REVIEW_IMAGES = 12

FINAL_FIELDS = [
    "fund_name",
    "report_date",
    "fund_size_hkd_100m",
    "portfolio_duration_years",
    "portfolio_ytm_percent",
    "credit_rating",
    "top_holdings",
    "top_holdings_total_percent",
    "duration_distribution",
    "geographic_allocation",
    "dividend",
]

# 这些字段是面向研究员的原文说明，不做繁简转换，避免月报原有语种在
# 结构化后丢失。它们也会在详情页以整行长文本卡片展示。
LONG_TEXT_FIELDS = (
    "investment_objective",
    "investment_strategy",
    "portfolio_ytm_note",
)

EVIDENCE_FIELDS = (
    "fund_size_original",
    "report_date",
    "portfolio_duration_years",
    "portfolio_ytm_percent",
    "credit_rating",
    "credit_rating_distribution",
    "top_holdings",
    "duration_distribution",
    "geographic_allocation",
    "dividend",
)

MODEL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": FINAL_FIELDS[:3] + ["fund_size_original", "credit_rating_distribution", "field_evidence"] + FINAL_FIELDS[3:],
    "properties": {
        "fund_name": {"type": "string"},
        "report_date": {"type": ["string", "null"]},
        "inception_date": {
            "type": ["string", "null"],
            "description": "基金成立日期，格式 YYYY-MM-DD；原文未披露则为 null",
        },
        "fund_size_hkd_100m": {"type": ["number", "null"], "description": "固定填 null，代码层会换算"},
        "fund_size_original": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "required": ["amount", "currency", "unit"],
            "properties": {
                "amount": {"type": ["number", "null"]},
                "currency": {"type": ["string", "null"]},
                "unit": {"type": ["string", "null"]},
            },
        },
        "portfolio_duration_years": {"type": ["number", "null"]},
        "portfolio_ytm_percent": {"type": ["number", "null"]},
        "investment_objective": {
            "type": ["string", "null"],
            "description": "报告明确的投资目标原文；没有明确标题和正文则为 null，不翻译、不改写",
        },
        "investment_strategy": {
            "type": ["string", "null"],
            "description": "报告明确的投资策略原文；没有明确标题和正文则为 null，不翻译、不改写",
        },
        "portfolio_ytm_note": {
            "type": ["string", "null"],
            "description": "报告中对 YTM/到期收益率的说明或脚注原文；没有则为 null，不翻译",
        },
        "credit_rating": {"type": ["string", "null"]},
        "credit_rating_distribution": {
            "type": "array",
            "description": "如果没有平均信用评级，请抽取评级分布供代码计算；没有则 []",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["rating", "weight_percent"],
                "properties": {
                    "rating": {"type": ["string", "null"]},
                    "weight_percent": {"type": ["number", "null"]},
                },
            },
        },
        "top_holdings": {
            "type": "array",
            "description": "前十大或主要债券持仓；完整保留原文证券名称、票息和到期日等描述，并配对比例；没有披露则 []",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "weight_percent"],
                "properties": {
                    "name": {"type": ["string", "null"]},
                    "weight_percent": {"type": ["number", "null"]},
                    "holding_quantity": {"type": ["number", "null"], "description": "Holding/Nominal Value；原文未披露则 null"},
                    "market_value": {"type": ["number", "null"], "description": "Market Value；原文未披露则 null"},
                    "registration_place": {"type": ["string", "null"], "description": "投资组合表中的国家/注册地原文"},
                    "holding_type": {"type": ["string", "null"], "description": "投资组合表的分类原文，如 Listed/quoted investments Fixed Income Securities"},
                },
            },
        },
        "top_holdings_total_percent": {"type": ["number", "null"]},
        "duration_distribution": {
            "type": "array",
            "description": "久期/期限分布图表中的区间及比例；没有明确披露则 []",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["bucket", "weight_percent"],
                "properties": {
                    "bucket": {"type": ["string", "null"]},
                    "weight_percent": {"type": ["number", "null"]},
                },
            },
        },
        "geographic_allocation": {
            "type": "array",
            "description": "明确披露的地区/国家/市场分布；没有明确披露则 []",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["region", "weight_percent"],
                "properties": {
                    "region": {"type": ["string", "null"]},
                    "weight_percent": {"type": ["number", "null"]},
                },
            },
        },
        "dividend": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "required": [
                "share_class",
                "currency",
                "frequency",
                "dividend_per_unit",
                "annualized_dividend_yield_percent",
                "note",
            ],
            "properties": {
                "share_class": {"type": ["string", "null"]},
                "currency": {"type": ["string", "null"]},
                "frequency": {"type": ["string", "null"], "enum": ["monthly", "quarterly", "semiannual", "annual", "unknown", None]},
                "dividend_per_unit": {"type": ["number", "null"]},
                "annualized_dividend_yield_percent": {"type": ["number", "null"]},
                "note": {"type": ["string", "null"]},
            },
        },
        "money_market_metrics": {
            "type": ["object", "null"],
            "description": "货币基金月报专用指标；固定收益月报可返回 null。",
            "additionalProperties": False,
            "properties": {
                "seven_day_yield_percent": {"type": ["number", "null"]},
                "annualized_yield_percent": {"type": ["number", "null"]},
                "weighted_average_maturity_days": {"type": ["number", "null"]},
                "weighted_average_life_days": {"type": ["number", "null"]},
                "daily_liquid_assets_percent": {"type": ["number", "null"]},
                "weekly_liquid_assets_percent": {"type": ["number", "null"]},
                "asset_type_allocation": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["type", "weight_percent"],
                        "properties": {
                            "type": {"type": ["string", "null"]},
                            "weight_percent": {"type": ["number", "null"]},
                        },
                    },
                },
                "bank_exposure": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["name", "weight_percent"],
                        "properties": {
                            "name": {"type": ["string", "null"]},
                            "weight_percent": {"type": ["number", "null"]},
                        },
                    },
                },
            },
        },
        "field_evidence": {
            "type": "object",
            "description": "关键字段原文证据，供代码复核",
            "additionalProperties": True,
        },
    },
}

_CC = OpenCC("t2s") if OpenCC else None


def _to_simple(value: Any) -> Any:
    if isinstance(value, str):
        text = _CC.convert(value) if _CC else value
        return text.replace("％", "%").replace("（", "(").replace("）", ")").replace("：", ":")
    if isinstance(value, list):
        return [_to_simple(item) for item in value]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            # 长文本说明必须保留 PDF 的原语种（例如繁体中文），其他
            # 字段沿用项目既有的繁简归一化逻辑。
            result[key_text] = item if key_text in LONG_TEXT_FIELDS else _to_simple(item)
        return result
    return value


def _is_duration_summary_label(value: Any) -> bool:
    normalized = re.sub(r"[\s()（）:_\-]+", "", str(value or "").lower())
    if not normalized:
        return False
    duration_terms = ("久期", "存续期", "到期年限", "到期期限", "duration", "maturity")
    summary_terms = ("平均", "加权平均", "组合", "average", "weightedaverage", "portfolio")
    if any(duration in normalized for duration in duration_terms) and any(
        summary in normalized for summary in summary_terms
    ):
        return True
    return normalized in {
        "durationyears",
        "maturityyears",
        "久期years",
        "到期年限years",
    }


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
        if name_key == "bucket" and _is_duration_summary_label(name):
            continue
        if name_key == "name":
            cleaned_item = {
                "name": str(_to_simple(name)).strip() or None,
                "weight_percent": weight,
            }
            # 半年报投资组合表还会给出 Holding/Nominal Value、Market Value、
            # 国家/注册地和分层资产类型。保留这些原文字段，供数据库持仓表
            # 和详情页“主要持仓”列直接显示；月报没有这些字段时不造空值。
            if item.get("holding_quantity") is not None:
                cleaned_item["holding_quantity"] = _number(item.get("holding_quantity"))
            if item.get("market_value") is not None:
                cleaned_item["market_value"] = _number(item.get("market_value"))
            if item.get("registration_place"):
                cleaned_item["registration_place"] = str(item.get("registration_place")).strip()
            if item.get("holding_type"):
                cleaned_item["holding_type"] = str(item.get("holding_type")).strip()
            cleaned.append(cleaned_item)
        else:
            cleaned_item = {
                name_key: str(_to_simple(name)).strip() or None,
                "weight_percent": weight,
            }
            if name_key == "bucket":
                cleaned_item.update({
                    "value": _number(item.get("value")) if item.get("value") is not None else weight,
                    "unit": item.get("unit") or None,
                    "measure_type": item.get("measure_type") or None,
                })
            cleaned.append(cleaned_item)
    return cleaned


def _distribution_total(items: list[dict[str, Any]]) -> float:
    return sum(float(item["weight_percent"]) for item in items if item.get("weight_percent") is not None)


def _parse_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].strip()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(stripped[start : end + 1])
            except json.JSONDecodeError:
                # DeepSeek/Qwen may stop at the output-token limit after
                # emitting several complete top-level fields. Recover only
                # complete key/value pairs; incomplete trailing values are
                # deliberately discarded and are subsequently filled by the
                # native PDF evidence gate. This is not a semantic JSON
                # repair and therefore cannot invent a value.
                data = _parse_complete_json_members(stripped[start:])
        else:
            data = _parse_complete_json_members(stripped[start:] if start >= 0 else stripped)
        if not isinstance(data, dict):
            raise exc
    if not isinstance(data, dict):
        raise ValueError("Model output JSON is not an object")
    return _to_simple(data)


def _parse_complete_json_members(text: str) -> dict[str, Any]:
    """Recover complete members from a truncated top-level JSON object.

    The model response is treated as untrusted input. The decoder advances
    only when both a quoted key and its complete JSON value are available;
    a partially emitted final value is ignored. A missing comma between two
    already complete members is tolerated, which covers the common truncation
    symptom without attempting to guess or rewrite field values.
    """

    decoder = json.JSONDecoder()
    start = text.find("{")
    if start < 0:
        raise ValueError("Model output does not contain a JSON object")
    index = start + 1
    recovered: dict[str, Any] = {}
    while index < len(text):
        while index < len(text) and text[index].isspace():
            index += 1
        if index < len(text) and text[index] in ",":
            index += 1
            continue
        if index < len(text) and text[index] == "}":
            return recovered
        if index >= len(text):
            break
        try:
            key, next_index = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            break
        if not isinstance(key, str):
            break
        index = next_index
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text) or text[index] != ":":
            break
        index += 1
        while index < len(text) and text[index].isspace():
            index += 1
        try:
            value, next_index = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            break
        recovered[key] = value
        index = next_index
    if not recovered:
        raise ValueError("Model output contains no complete JSON members")
    return recovered


def _extract_pdf_text(pdf_path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""
    try:
        reader = PdfReader(str(pdf_path))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        # PowerPoint 导出的月报经常把报告期写在 PDF Subject 元数据中，
        # 而正文只保留“成立日期”。把元数据以显式前缀加入上下文，供
        # 日期解析使用；它不会改变正文证据，也不会被当作模型事实。
        metadata = reader.metadata
        if metadata:
            metadata_lines = []
            for key in ("/Subject", "/Title", "/CreationDate", "/ModDate"):
                value = metadata.get(key) if hasattr(metadata, "get") else None
                if value:
                    metadata_lines.append(f"[PDF_METADATA] {key}: {value}")
            if metadata_lines:
                text = "\n".join(metadata_lines) + "\n" + text
    except Exception:
        return ""
    return str(_to_simple(text))


def _load_prompt(path: Path, pdf_path: Path) -> str:
    prompt = path.read_text(encoding="utf-8")
    prompt = prompt.replace("<PDF文件名>", pdf_path.stem).replace("<PDF 文件名>", pdf_path.stem)
    return f"{prompt}\n\nJSON Schema:\n{json.dumps(MODEL_SCHEMA, ensure_ascii=False, indent=2)}"


def _default_prompt(pdf_path: Path) -> str:
    return f"""
你是基金月报字段抽取与规范化模块。请只基于输入内容抽取信息，不要猜测。

输入报告可能是简体中文、繁体中文或英文，但输出字段名称统一为简体中文。债券持仓名称必须完整保留报告原文，不翻译、不缩写，不删除发行人、票息、到期日、币种、债券级别或 ISIN 等组成部分。

字段规则：
1. fund_name 必须使用 PDF 文件名“{pdf_path.stem}”，不要从正文重新提取。
2. report_date 输出 PDF 原文数据截至日期，优先使用完整 YYYY-MM-DD；例如“2026 年 6 月(截至 2026 年 5 月 29 日)”输出 2026-05-29。只有原文没有日时才输出 YYYY-MM。
3. fund_size_original 是给代码换算用的临时字段，请抽取基金规模原始金额、币种、单位；不要自行换汇。
4. fund_size_hkd_100m 固定填 null，代码层会根据 fund_size_original 调用汇率接口换算。
5. portfolio_duration_years 提取组合久期、存续期、Duration 或 Average duration，单位统一为年，只输出数字。
6. portfolio_ytm_percent 提取到期收益率、YTM 或 Yield to maturity，单位为百分比，只输出数字，不带 %。
7. credit_rating 优先提取原文披露的平均信用评级、Average rating 或 Average credit rating。若没有披露，不要猜，填 null。
8. 如果没有平均信用评级，但有信用评级分布图/表，必须抽取 credit_rating_distribution，rating 保留 AAA、AA+、AA、AA-、A+、A、A-、BBB+、BBB、BBB-、BB+、BB、BB-、B+、B、B-、CCC+、CCC、CCC-、CC、C、D、NR，weight_percent 为百分比数字。代码层会计算中位评级。
9. portfolio_duration_years 只能来自“组合久期 / 平均久期 / 加权平均存续期 / Duration / Average duration”。不要把“组合收益率 / 到期收益率 / 最差收益率 / YTM / YTW”的数字填到久期。
10. portfolio_ytm_percent 只能来自“到期收益率 / 最差收益率 / Yield to maturity / Yield to worst / YTM / YTW”。不要把“组合久期 / 平均存续期”的数字填到收益率。
11. dividend 优先提取美元每月派息份额。若没有美元每月派息份额，可提取其他币种或其他频率，但必须在 dividend.note 说明。
12. top_holdings 抽取前十大或原文披露的主要债券持仓；每项必须完整照录持仓名称并准确配对比例，不需要也不得生成 Ticker，没有比例不要输出。
13. duration_distribution 只能从明确的久期/期限/到期年限分布图表抽取“期限区间+比例/久期贡献”。平均久期、平均到期年限、组合久期、加权平均到期年限是单值指标，绝不能作为 bucket。
14. geographic_allocation 只能从明确的地区/国家/市场分布图表抽取地区与比例，不得从投资目标或持仓名称推导。
15. 分布图表必须逐项准确配对；完整分布合计应为 99%-101%，现金、其他和净流动资产也要保留。
16. field_evidence 的每个字段只输出来源中的 {{"page_idx":整数,"block_index":整数}}，不得复述原文；仅可引用上下文里同页的 [BLOCK n]，未披露时填 null。
17. 未披露字段填 null，未披露列表填 []。
18. 输出必须符合下面 JSON Schema，不要输出解释。

{json.dumps(MODEL_SCHEMA, ensure_ascii=False, indent=2)}
""".strip()


def _refinement_prompt(pdf_path: Path) -> str:
    return f"""
请重新核对这份基金月报的核心字段，只看基金资料、投资组合特点、统计摘要、持仓表、久期/期限分布、地区分布、信用评级分布图和派息表。

特别要求：
1. fund_name 固定为“{pdf_path.stem}”。
2. report_date 输出 PDF 原文数据截至日期，优先使用完整 YYYY-MM-DD；若标题是“2026 年 6 月(截至 2026 年 5 月 29 日)”，输出 2026-05-29。只有原文没有日时才输出 YYYY-MM。
3. fund_size_original 必须抽取原始规模金额、币种、单位。例如“美元 113.53 百万”输出 amount=113.53,currency=USD,unit=百万。
4. portfolio_duration_years 只能取组合久期/平均久期/加权平均存续期/Duration；不要取收益率数字。
5. portfolio_ytm_percent 只能取到期收益率/最差收益率/YTM/YTW；不要取久期数字。
6. credit_rating 优先取平均信用评级/平均信贷评级/Average Credit Rating；没有直接披露则填 null。
7. 如果没有直接平均信用评级，但有信用评级分布，请完整抽取 credit_rating_distribution，代码会计算中位评级。
8. top_holdings 每项必须完整照录报告中的债券持仓名称并配对比例，不生成 Ticker；只披露前五大或主要持仓时按原文数量输出。
9. duration_distribution 只能从明确标题的久期/期限/到期年限分布图表抽取区间和比例；“平均久期”“平均到期年限”等单值指标不得放入分布；没有则 []。
10. geographic_allocation 只能从明确标题的地区/国家/市场分布图表抽取名称和比例；不能推导。
11. 完整分布合计应在 99%-101%，现金、其他和净流动资产也要保留。
12. field_evidence 必须逐字段输出 [PAGE n] 下实际存在的 {{"page_idx":n,"block_index":m}}；不得复述证据短句，未披露时填 null。

只输出合法 JSON，结构必须符合：
{json.dumps(MODEL_SCHEMA, ensure_ascii=False, indent=2)}
""".strip()


def _missing_matrix_fields(record: dict[str, Any]) -> list[str]:
    return [
        field
        for field in ("duration_distribution", "geographic_allocation")
        if not record.get(field)
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
请用更强的文本理解重新阅读 MinerU Markdown，只复核这份固收基金月报中为空的矩阵字段：{requested}。
基金名称固定为“{pdf_path.stem}”。

规则：
1. duration_distribution 只读取明确的久期/期限/到期年限分布表。逐项返回 bucket、
   value、measure_type（allocation_percent 或 option_adjusted_duration_contribution）
   和 unit（percent 或 years）。
   bucket 必须是“1年以下、1-3年、3-5年、5-7年、7-10年、10年以上”等期限区间；
   平均久期、平均到期年限、组合久期、加权平均到期年限等单值指标不得返回。
2. geographic_allocation 只读取明确的地区/国家/市场分布表，逐项返回 region 和
   weight_percent；保留现金、其他和净流动资产。
3. Markdown 表格中已经能配对的标签与数值必须完整读取，不要因为主模型漏读而返回空。
4. 不根据平均久期、持仓名称或投资目标推导；确实未披露才返回 []。
5. 只输出合法 JSON，至少包含 duration_distribution、geographic_allocation 和 field_evidence。{hint_block}
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
            response_schema=response_schema or build_subschema(MODEL_SCHEMA),
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
            response_schema=response_schema or build_subschema(MODEL_SCHEMA),
            max_tokens=max_tokens_for_call(f"{call_name}_json_repair"),
            call_name=f"{call_name}_json_repair",
        )
        return _parse_json(repaired_text)


def _chat_context_json(
    client: QwenClient,
    *,
    model: str,
    context: str,
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
            messages=_context_messages(context, prompt),
            enable_thinking=enable_thinking,
            thinking_budget=thinking_budget,
            response_schema=response_schema or build_subschema(MODEL_SCHEMA),
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
            response_schema=response_schema or build_subschema(MODEL_SCHEMA),
            max_tokens=max_tokens_for_call(f"{call_name}_json_repair"),
            call_name=f"{call_name}_json_repair",
        )
        return _parse_json(repaired_text)


def _env_flag(name: str, default: bool) -> bool:
    """读取布尔环境变量；参数 name/default 为变量名和默认值。"""

    value = os.environ.get(name, "").strip().casefold()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def _context_messages(context: str, prompt: str) -> list[dict[str, str]]:
    """构造上下文优先的消息，让同一 PDF 的多次调用共享可缓存前缀。

    参数 context 为完整报告上下文，prompt 为本次抽取指令。
    FACTSHEET_CACHE_FRIENDLY_CONTEXT=0 可回退旧顺序用于 A/B。
    """

    system = {"role": "system", "content": STRUCTURED_SYSTEM_PROMPT}
    if _env_flag("FACTSHEET_CACHE_FRIENDLY_CONTEXT", True):
        return [
            system,
            {"role": "user", "content": f"{context}\n\n---\n\n{prompt}"},
        ]
    return [
        system,
        {"role": "user", "content": f"{prompt}\n\n{context}"},
    ]


def _rank_mineru_review_images(image_paths: list[Path]) -> list[Path]:
    """Rank MinerU image crops by nearby portfolio-table and chart labels."""

    if len(image_paths) <= MAX_VISUAL_REVIEW_IMAGES:
        return image_paths
    artifacts_dir = (
        image_paths[0].parent.parent
        if image_paths[0].parent.name == "images"
        else image_paths[0].parent
    )
    content_paths = sorted(artifacts_dir.glob("*_content_list.json"))
    if not content_paths:
        content_paths = sorted(artifacts_dir.glob("*_content_list_v2.json"))
    if not content_paths:
        return image_paths
    try:
        content_items = json.loads(content_paths[0].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return image_paths
    if not isinstance(content_items, list):
        return image_paths

    keywords = (
        "前十大",
        "主要持仓",
        "主要持倉",
        "top holdings",
        "top 10",
        "久期",
        "存续期",
        "存續期",
        "期限分布",
        "duration",
        "maturity",
        "地区分布",
        "地區分佈",
        "国家分布",
        "國家分佈",
        "市场分布",
        "geographical allocation",
        "country allocation",
        "regional allocation",
    )
    context_keys = {
        "text",
        "table_caption",
        "table_footnote",
        "chart_caption",
        "chart_footnote",
        "image_caption",
        "image_footnote",
    }

    def item_context(item: dict[str, Any]) -> str:
        parts = []
        for key, value in item.items():
            if key not in context_keys:
                continue
            if isinstance(value, list):
                parts.extend(str(part) for part in value)
            elif value:
                parts.append(str(value))
        return " ".join(parts).casefold()

    scores: dict[str, int] = {}
    for index, item in enumerate(content_items):
        if not isinstance(item, dict) or not item.get("img_path"):
            continue
        own_context = item_context(item)
        nearby_context = []
        for neighbor_index in range(max(0, index - 2), min(len(content_items), index + 3)):
            if neighbor_index == index:
                continue
            neighbor = content_items[neighbor_index]
            if not isinstance(neighbor, dict):
                continue
            nearby_context.append(item_context(neighbor))
        nearby = " ".join(nearby_context)
        scores[Path(str(item["img_path"])).name] = (
            3 * sum(1 for keyword in keywords if keyword in own_context)
            + sum(1 for keyword in keywords if keyword in nearby)
        )
    ranked = sorted(
        enumerate(image_paths),
        key=lambda pair: (-scores.get(pair[1].name, 0), pair[0]),
    )
    return [path for _, path in ranked]


def _visual_review_image_urls(
    pdf_path: Path,
    *,
    mineru_image_paths: list[Path] | None = None,
    max_pages: int = 6,
    zoom: float = 2.2,
) -> list[str]:
    """Prefer compact MinerU crops, then summary regions, then full pages."""

    supported_suffixes = {".jpg", ".jpeg", ".png", ".webp"}
    artifacts = [
        Path(path)
        for path in (mineru_image_paths or [])
        if Path(path).is_file() and Path(path).suffix.lower() in supported_suffixes
    ]
    if artifacts:
        artifacts = _rank_mineru_review_images(artifacts)
        return [
            image_data_url(path)
            for path in artifacts[:MAX_VISUAL_REVIEW_IMAGES]
        ]

    try:
        summary_crops = pdf_summary_candidate_image_urls(
            pdf_path,
            zoom=zoom,
            max_pages=min(max_pages, 6),
        )
    except Exception:
        summary_crops = []
    if summary_crops:
        return summary_crops[:MAX_VISUAL_REVIEW_IMAGES]

    return pdf_page_image_urls(
        pdf_path,
        zoom=zoom,
        max_pages=min(max_pages, 6),
        split_pages=True,
        include_full_pages=True,
    )[:MAX_VISUAL_REVIEW_IMAGES]


def _chat_image_json(
    client: QwenClient,
    *,
    model: str,
    prompt: str,
    image_urls: list[str],
    attempts: int,
    enable_thinking: bool,
    thinking_budget: int | None,
    call_name: str,
) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for image_url in image_urls:
        content.append({"type": "image_url", "image_url": {"url": image_url}})
    response_text = retry_call(
        lambda: client.chat(
            model=model,
            messages=[
                {"role": "system", "content": STRUCTURED_SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ],
            enable_thinking=enable_thinking,
            thinking_budget=thinking_budget,
            max_tokens=max_tokens_for_call(call_name),
            call_name=call_name,
        ),
        attempts=attempts,
    )
    return _parse_json(response_text)


def _extract_fixed_income_metrics_from_images(
    client: QwenClient,
    *,
    pdf_path: Path,
    vision_model: str,
    attempts: int,
    max_pages: int,
    enable_thinking: bool,
    thinking_budget: int | None,
    mineru_image_paths: list[Path] | None = None,
) -> dict[str, Any] | None:
    prompt = """
请查找这份基金月报图片中直接披露的组合指标，只读取标签与数值明确配对的内容，不要推断：
1. 组合久期 / 平均久期 / 加权平均存续期 / Duration / Average duration，输出 portfolio_duration_years，单位统一为年。
2. 到期收益率 / 静态收益率 / 最差收益率 / YTM / YTW / Yield to maturity / Yield to worst，输出 portfolio_ytm_percent，百分号前的数字。
3. 信用评级分布 / 信贷评级分布 / Credit rating distribution，以及平均信用评级。
4. 前十大持仓 / 主要持仓 / Top holdings；完整照录债券名称并准确配对比例，不生成 Ticker。
5. 久期分布 / 期限分布 / 到期年限分布 / Duration distribution / Maturity distribution；逐项读取区间和比例。
6. 地区分布 / 国家分布 / 市场分布 / Geographical allocation / Country allocation；逐项读取地区和比例。
不要把业绩回报率、派息率、债券占比、国家分布或行业分布当成久期或到期收益率。
不要根据投资目标、债券名称或平均久期推导地区与久期分布。
完整分布应核对合计为 99%-101%；现金、其他、衍生工具和净流动资产也要保留。
如果看到平均信用评级 / 平均信贷评级 / Average Credit Rating，输出 credit_rating。
如果没有平均评级但看到评级分布，请输出 credit_rating_distribution。
评级只允许：AAA, AA+, AA, AA-, A+, A, A-, BBB+, BBB, BBB-, BB+, BB, BB-, B+, B, B-, CCC+, CCC, CCC-, CC, C, D, NR。
百分比只输出数字，不带 %。看不清的项目不要猜。
输出 JSON：
{
  "portfolio_duration_years": number | null,
  "portfolio_ytm_percent": number | null,
  "credit_rating": string | null,
  "credit_rating_distribution": [
    {"rating": string, "weight_percent": number}
  ],
  "top_holdings": [
    {"name": string, "weight_percent": number}
  ],
  "top_holdings_total_percent": number | null,
  "duration_distribution": [
    {"bucket": string, "weight_percent": number}
  ],
  "geographic_allocation": [
    {"region": string, "weight_percent": number}
  ],
  "field_evidence": string | null
}
""".strip()
    try:
        images = _visual_review_image_urls(
            pdf_path,
            mineru_image_paths=mineru_image_paths,
            max_pages=min(max_pages, 4),
            zoom=2.0,
        )
        if not images:
            return None
        return _chat_image_json(
            client,
            model=vision_model,
            prompt=prompt,
            image_urls=images,
            attempts=attempts,
            enable_thinking=False,
            thinking_budget=None,
            call_name="fixed_income_metrics_vision_refinement",
        )
    except Exception:
        return None


def _review_complex_field_from_pdf_images(
    client: QwenClient,
    *,
    pdf_path: Path,
    field: str,
    vision_model: str,
    attempts: int,
    max_pages: int,
    correction_hint: str | None = None,
    alias_hint: str = "",
    mineru_image_paths: list[Path] | None = None,
) -> dict[str, Any] | None:
    """让视觉模型对一个复杂字段作最终披露判断，避免多个图表互相串值。"""
    field_prompts = {
        "top_holdings": """
只核对这份固收基金月报的“前十大持仓/主要持仓/Top holdings”。
由你判断报告是否直接披露；不要从其他图表、基准或债券名称推导。
若披露，逐项完整照录债券持仓名称并准确配对组合占比；不要生成 Ticker；
不要删除名称中的票息、到期日、币种、债券层级、ISIN 或其他证券识别信息。
输出 JSON：
{"field":"top_holdings","disclosed":true|false,
 "items":[{"name":string,"weight_percent":number}],
 "evidence":string|null,"confidence":"high"|"medium"|"low"}
未披露时 items 必须为 []。不要输出解释。
""",
        "duration_distribution": """
只核对这份固收基金月报的“久期分布/期限分布/到期年限分布/Duration distribution/Maturity distribution”。
由你判断报告是否直接披露，不能根据平均久期推导。
平均久期、平均到期年限、组合久期、加权平均到期年限属于单值统计指标，
不是期限区间，严禁放入 items。
先辨认口径和单位：
- allocation_percent：各期限资产配置占比，unit=percent，通常合计约100；
- option_adjusted_duration_contribution：各期限对期权调整存续期/久期的贡献，unit=years，合计是组合久期，不应接近100。
若披露，逐项准确配对区间与数值。输出 JSON：
{"field":"duration_distribution","disclosed":true|false,
 "measure_type":"allocation_percent"|"option_adjusted_duration_contribution"|null,
 "unit":"percent"|"years"|null,
 "items":[{"bucket":string,"value":number}],
 "evidence":string|null,"confidence":"high"|"medium"|"low"}
未披露时 items 必须为 []。不要输出解释。
""",
        "geographic_allocation": """
只核对这份固收基金月报的“地区/国家/市场分布/Geographical allocation/Country allocation”。
由你判断报告是否直接披露；不要把持仓、行业、评级或基准分布当成地区分布。
若披露，逐项准确配对地区名称与组合占比，并保留现金、其他、净流动资产。
如果图表同时有基金组合与基准/指数两组数值，必须根据图例只提取基金组合这一组，
不要把基准数值混入；例如红色代表基金、蓝色代表指数时，逐行读取红色基金数值。
输出 JSON：
{"field":"geographic_allocation","disclosed":true|false,
 "items":[{"region":string,"weight_percent":number}],
 "evidence":string|null,"confidence":"high"|"medium"|"low"}
未披露时 items 必须为 []。不要输出解释。
""",
    }
    try:
        images = _visual_review_image_urls(
            pdf_path,
            mineru_image_paths=mineru_image_paths,
            max_pages=min(max_pages, 6),
            zoom=2.2,
        )
        if not images:
            return None
        prompt = field_prompts[field]
        if alias_hint:
            prompt += f"\n\n{alias_hint}"
        if correction_hint:
            prompt += (
                "\n\n这是一次异常复核。上一次结果存在以下问题："
                f"{correction_hint}\n请重新查看原图标签、单位和每一行，不要沿用上次答案。"
            )
        result = _chat_image_json(
            client,
            model=vision_model,
            prompt=prompt,
            image_urls=images,
            attempts=attempts,
            enable_thinking=False,
            thinking_budget=None,
            call_name=f"fixed_income_{field}_visual_review",
        )
        return result if isinstance(result, dict) else None
    except Exception:
        return None


_COMPLEX_REVIEW_SECTIONS = {
    "top_holdings": """top_holdings：
- 只读取明确的“前十大持仓/主要持仓/Top holdings”，不要从其他图表推导。
- 若披露，完整照录债券名称与组合占比，保留票息、到期日、币种和债券层级。""",
    "duration_distribution": """duration_distribution：
- 只读取明确的久期/期限/到期年限分布。
- 平均久期等单值不是区间，严禁放入 items。
- unit=percent 表示资产配置，合计通常约100；unit=years 表示久期贡献，不要求接近100。""",
    "geographic_allocation": """geographic_allocation：
- 只读取明确的地区/国家/市场分布，不能由持仓所在地推导。
- 图表同时有基金和基准时，只提取基金组合数据，保留现金、其他和净流动资产。""",
}
_COMPLEX_REVIEW_SCHEMAS = {
    "top_holdings": '"top_holdings":{"disclosed":true|false,"items":[{"name":string,"weight_percent":number}],"evidence":string|null,"confidence":string}',
    "duration_distribution": '"duration_distribution":{"disclosed":true|false,"measure_type":"allocation_percent"|"option_adjusted_duration_contribution"|null,"unit":"percent"|"years"|null,"items":[{"bucket":string,"value":number}],"evidence":string|null,"confidence":string}',
    "geographic_allocation": '"geographic_allocation":{"disclosed":true|false,"items":[{"region":string,"weight_percent":number}],"evidence":string|null,"confidence":string}',
}


def _review_complex_fields_from_pdf_images(
    client: QwenClient,
    *,
    pdf_path: Path,
    vision_model: str,
    attempts: int,
    max_pages: int,
    mineru_image_paths: list[Path] | None = None,
    fields: list[str] | None = None,
    alias_hint: str = "",
) -> dict[str, dict[str, Any]]:
    """一次视觉调用复核指定复杂字段；参数描述模型、PDF 和图像范围。"""

    requested = list(_COMPLEX_REVIEW_SECTIONS) if fields is None else fields
    selected = [
        field
        for field in requested
        if field in _COMPLEX_REVIEW_SECTIONS
    ]
    if not selected:
        return {}
    sections = "\n\n".join(_COMPLEX_REVIEW_SECTIONS[field] for field in selected)
    schemas = ",\n ".join(_COMPLEX_REVIEW_SCHEMAS[field] for field in selected)
    prompt = (
        "一次核对这份固收基金月报中的复杂字段，所有判断必须直接依据原图。\n"
        f"返回 JSON 对象，键固定为 {', '.join(selected)}。\n\n"
        f"{sections}\n\n"
        "输出结构：\n{\n "
        f"{schemas}\n"
        "}\n未披露字段 items 必须为 []。只输出合法 JSON。"
    )
    if alias_hint:
        prompt += f"\n\n{alias_hint}"
    try:
        images = _visual_review_image_urls(
            pdf_path,
            mineru_image_paths=mineru_image_paths,
            max_pages=min(max_pages, 6),
            zoom=2.2,
        )
        if not images:
            return {}
        result = _chat_image_json(
            client,
            model=vision_model,
            prompt=prompt,
            image_urls=images,
            attempts=attempts,
            enable_thinking=False,
            thinking_budget=None,
            call_name="fixed_income_complex_fields_visual_review",
        )
        if not isinstance(result, dict):
            return {}
        return {
            field: value
            for field, value in result.items()
            if field in selected
            and isinstance(value, dict)
            and isinstance(value.get("disclosed"), bool)
        }
    except Exception:
        return {}


def _apply_complex_field_reviews(
    raw: dict[str, Any],
    reviews: dict[str, dict[str, Any] | None],
) -> dict[str, Any]:
    """模型复核结果优先；代码只负责把模型结构映射到统一字段。"""
    merged = dict(raw)
    disclosure_info: dict[str, Any] = {}
    for field, review in reviews.items():
        if not isinstance(review, dict) or not isinstance(review.get("disclosed"), bool):
            continue
        disclosure_info[field] = {
            "disclosed": review["disclosed"],
            "measure_type": review.get("measure_type"),
            "unit": review.get("unit"),
            "evidence": review.get("evidence"),
            "confidence": review.get("confidence"),
        }
        if not review["disclosed"]:
            # A visual reviewer can miss a dense table that the stronger
            # Markdown pass read correctly. "未披露" only confirms an already
            # empty field; it must not erase structured text evidence.
            if not merged.get(field):
                merged[field] = []
            continue
        items = review.get("items")
        if not isinstance(items, list):
            continue
        if field == "duration_distribution":
            normalized_items = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                value = _number(item.get("value"))
                if item.get("bucket") and value is not None:
                    normalized_items.append({
                        "bucket": item["bucket"],
                        "weight_percent": value,
                        "value": value,
                        "unit": review.get("unit"),
                        "measure_type": review.get("measure_type"),
                    })
            merged[field] = normalized_items
        else:
            merged[field] = items
    if disclosure_info:
        merged["disclosure_info"] = disclosure_info
    return merged


def _review_geography_series_from_pdf_images(
    client: QwenClient,
    *,
    pdf_path: Path,
    vision_model: str,
    attempts: int,
    max_pages: int,
    mineru_image_paths: list[Path] | None = None,
) -> dict[str, Any] | None:
    """合计异常时，让模型显式拆分基金/基准两条序列后再选基金列。"""
    prompt = """
只检查地区/国家配置图。图中可能同时有“基金”和“参考基准/指数”两条序列。
先根据图例识别每种颜色代表哪条序列，再逐行抄录两组数值；绝不能把同一行的基准数值填到基金列。
输出 JSON：
{"disclosed":true|false,
 "items":[{"region":string,"fund_weight_percent":number,
           "benchmark_weight_percent":number|null}],
 "evidence":string|null,"confidence":"high"|"medium"|"low"}
只输出 JSON，不要解释。
""".strip()
    try:
        images = _visual_review_image_urls(
            pdf_path,
            mineru_image_paths=mineru_image_paths,
            max_pages=min(max_pages, 6),
            zoom=2.8,
        )
        result = _chat_image_json(
            client,
            model=vision_model,
            prompt=prompt,
            image_urls=images,
            attempts=attempts,
            enable_thinking=False,
            thinking_budget=None,
            call_name="fixed_income_geography_series_visual_review",
        )
        if not isinstance(result, dict) or not result.get("disclosed"):
            return None
        items = []
        for item in result.get("items") or []:
            if not isinstance(item, dict):
                continue
            value = _number(item.get("fund_weight_percent"))
            if item.get("region") and value is not None:
                items.append({"region": item["region"], "weight_percent": value})
        if not items:
            return None
        return {
            "field": "geographic_allocation",
            "disclosed": True,
            "items": items,
            "evidence": result.get("evidence"),
            "confidence": result.get("confidence"),
        }
    except Exception:
        return None


def _normalize_currency(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    mapping = {
        "美元": "USD",
        "美金": "USD",
        "US$": "USD",
        "USD": "USD",
        "$": "USD",
        "港币": "HKD",
        "港元": "HKD",
        "港幣": "HKD",
        "HK$": "HKD",
        "HKD": "HKD",
        "人民币": "CNY",
        "人民幣": "CNY",
        "RMB": "CNY",
        "CNY": "CNY",
        "澳元": "AUD",
        "AUD": "AUD",
        "A$": "AUD",
    }
    return mapping.get(text, text or None)


def _frequency_label(value: Any) -> str:
    text = str(value or "").strip().lower()
    mapping = {
        "monthly": "每月",
        "quarterly": "每季",
        "semiannual": "每半年",
        "annual": "每年",
        "unknown": "未知频率",
    }
    return mapping.get(text, str(value or "").strip())


def _unit_multiplier(value: Any) -> float | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    if any(token in text for token in ["million", "mn", "百万", "百萬"]):
        return 1_000_000.0
    if any(token in text for token in ["billion", "bn", "十亿", "十億"]):
        return 1_000_000_000.0
    if any(token in text for token in ["亿", "億"]):
        return 100_000_000.0
    if any(token in text for token in ["万", "萬"]):
        return 10_000.0
    if any(token in text for token in ["元", "dollar", "currency", "none", "无", "無"]):
        return 1.0
    return None


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"(-?[0-9][0-9,]*(?:\.[0-9]+)?)", str(value))
    return float(match.group(1).replace(",", "")) if match else None


def _distribution_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    items: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        rating = normalize_rating(item.get("rating"))
        weight = _number(item.get("weight_percent") if "weight_percent" in item else item.get("weight_pct"))
        if rating and weight is not None:
            items.append({"rating": rating, "weight_pct": weight})
    return items


def _parse_fund_size_from_text(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    compact = re.sub(r"\s+", " ", text)
    currency = r"(美元|美金|港币|港元|人民币|澳元|USD|HKD|RMB|CNY|AUD|US\$|HK\$|A\$)"
    unit = r"(百万|百萬|million|mn|亿|億|billion|bn|万|萬)"
    # 香港 factsheet 常把规模写成“产品规模/產品規模”，尤其是易方达
    # 的 PowerPoint 月报；此前只识别“基金规模”，导致该类报告即使有
    # 明确规模也被判为缺失，最终整条记录落入 review 且页面没有可展示规模。
    keywords = r"(基金规模|基金規模|产品规模|產品規模|产品規模|產品规模|基金总值|基金總值|基金总净值|基金總淨值|总资产值|總資產值|资产净值|資產淨值|Fund Size|AUM|Net Asset Value|Total Net Assets)"
    patterns = [
        rf"{keywords}.{{0,160}}?{currency}\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*{unit}",
        rf"{keywords}.{{0,160}}?([0-9][0-9,]*(?:\.[0-9]+)?)\s*{unit}\s*{currency}",
    ]
    for pattern in patterns:
        match = re.search(pattern, compact, flags=re.IGNORECASE)
        if not match:
            continue
        groups = match.groups()
        if len(groups) >= 4 and _number(groups[-2]) is not None:
            return {"amount": _number(groups[-2]), "currency": groups[-3], "unit": groups[-1]}
        if len(groups) >= 4 and _number(groups[-3]) is not None:
            return {"amount": _number(groups[-3]), "currency": groups[-1], "unit": groups[-2]}
    # 部分香港月报把币种和单位写在指标标签中，例如
    # “基金总净值(美元百萬) 9493.52”，而不是“美元 9493.52 百万”。
    labeled_unit = rf"{keywords}\s*[（(]\s*{currency}\s*{unit}\s*[）)]\s*[:：]?\s*(?:<[^>]+>\s*)*([0-9][0-9,]*(?:\.[0-9]+)?)"
    match = re.search(labeled_unit, compact, flags=re.IGNORECASE)
    if match:
        return {"amount": _number(match.group(4)), "currency": match.group(2), "unit": match.group(3)}
    # 易方达美元现金基金的 PDF 文本层把规模值粘到了“核数师”单元格，
    # 下一行“产品规模”只剩法律结构。仍要求同一份原文同时出现产品规模
    # 和币种/单位，避免从其他章节或模型输出串入规模。
    if re.search(r"产品规模|產品規模|产品規模|產品规模", compact, flags=re.IGNORECASE):
        nearby = compact[:]
        misplaced = re.search(
            rf"([0-9][0-9,]*(?:\.[0-9]+)?)\s*{unit}\s*{currency}",
            nearby,
            flags=re.IGNORECASE,
        )
        if misplaced:
            return {
                "amount": _number(misplaced.group(1)),
                "currency": misplaced.group(3),
                "unit": misplaced.group(2),
            }
    return None


def _parse_metric_from_text(text: str, aliases: list[str], *, require_percent: bool = False) -> float | None:
    if not text:
        return None
    compact = re.sub(r"\s+", " ", text)
    for alias in aliases:
        for alias_match in re.finditer(re.escape(alias), compact, flags=re.IGNORECASE):
            window = compact[alias_match.end() : alias_match.end() + 140]
            before = compact[max(0, alias_match.start() - 120) : alias_match.start()]
            if require_percent and (
                ("Average rating" in before and "IG" in before and "HY" in before)
                or ("平均信贷评级" in before and "投资级别" in before and "非投资级别" in before)
                or ("平均信貸評級" in before and "投資級別" in before and "非投資級別" in before)
            ):
                continue
            if require_percent:
                if any(token in window[:60] for token in ["投资级别", "投資級別", "非投资", "非投資", "信用评级", "信用評級"]):
                    continue
                percent_matches = list(re.finditer(r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*%", window))
                if not percent_matches:
                    continue
                for match in percent_matches:
                    value = _number(match.group(1))
                    prefix = window[: match.start()]
                    if value is None or value > 50:
                        continue
                    if len(prefix) <= 35 or any(token in prefix for token in ["(%)", "（%）", "：", ":", "每年", "p.a.", "per annum"]):
                        return value
                continue
            year_matches = list(re.finditer(r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*(?:Years?|年)", window, flags=re.IGNORECASE))
            for match in year_matches:
                value = _number(match.group(1))
                if value is not None and value <= 50:
                    return value
            close_window = window[:50]
            close_patterns = [
                r"^\s*(?:[\(（][^)）]*[\)）]\s*)?(?:\d{1,2}\s+)?([0-9][0-9,]*(?:\.[0-9]+)?)",
                r"^\s*[:：]\s*([0-9][0-9,]*(?:\.[0-9]+)?)",
            ]
            for pattern in close_patterns:
                number_match = re.search(pattern, close_window)
                if number_match:
                    value = _number(number_match.group(1))
                    if value is not None and value <= 50:
                        return value
    return None


@lru_cache(maxsize=32)
def _fetch_fx_rate(base: str, quote: str, timeout: int = 20) -> float:
    if base == quote:
        return 1.0
    if os.environ.get("FIXED_INCOME_DISABLE_FX_API", "").strip() in {"1", "true", "TRUE"}:
        raise RuntimeError("FX API disabled")
    api_root = os.environ.get(
        "FIXED_INCOME_FX_API_URL",
        "https://open.er-api.com/v6/latest",
    ).rstrip("/")
    url = f"{api_root}/{base}"
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    rate = data.get("rates", {}).get(quote)
    if not isinstance(rate, (int, float)):
        raise RuntimeError(f"FX API missing {base}/{quote}")
    return float(rate)


def _fund_size_conversion(size_original: Any) -> dict[str, Any]:
    """统一换算基金规模；参数 size_original 为模型或本地解析出的原始金额。"""

    if not isinstance(size_original, dict):
        return {"status": "invalid", "reason": "size_original_not_object"}
    amount = _number(size_original.get("amount"))
    currency = _normalize_currency(size_original.get("currency"))
    multiplier = _unit_multiplier(size_original.get("unit"))
    if amount is None or currency is None or multiplier is None:
        return {
            "status": "invalid",
            "reason": "amount_currency_or_unit_invalid",
            "amount": amount,
            "currency": currency,
            "multiplier": multiplier,
        }
    fx_source = "api"
    try:
        fx_rate = _fetch_fx_rate(currency, "HKD")
    except Exception:
        fx_rate = FX_FALLBACK.get((currency, "HKD"))
        fx_source = "fallback"
    if fx_rate is None:
        return {
            "status": "unparsed",
            "reason": "fx_rate_missing",
            "amount": amount,
            "currency": currency,
            "multiplier": multiplier,
        }
    return {
        "status": "ok",
        "amount": amount,
        "currency": currency,
        "unit": size_original.get("unit"),
        "multiplier": multiplier,
        "fx_rate": fx_rate,
        "fx_source": fx_source,
        "hkd_100m": round(amount * multiplier * fx_rate / 100_000_000, 4),
    }


def _compute_fund_size_hkd_100m(size_original: Any) -> float | None:
    """返回统一换算后的亿港元规模；参数 size_original 为原始规模对象。"""

    converted = _fund_size_conversion(size_original)
    value = converted.get("hkd_100m")
    return float(value) if isinstance(value, (int, float)) else None


def _effective_fund_size_original(size_original: Any, pdf_text: str) -> Any:
    """解析最终使用的原始规模；参数 size_original/pdf_text 为模型结果和原文。"""

    if _compute_fund_size_hkd_100m(size_original) is not None:
        return size_original
    return _parse_fund_size_from_text(pdf_text) or size_original


def _normalize_report_month(value: Any) -> str | None:
    # MinerU/native PDF text may contain compatibility glyphs such as U+F98E
    # (a presentation-form 年).  Normalize before applying date regexes;
    # otherwise the header date is missed and a maturity date from the first
    # holding can be selected as the report period.
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not text:
        return None
    # 易方达等管理人的 PowerPoint 月报将报告期写在 PDF Subject 中，
    # 典型值为 ``202609 Monthly Report``。该值优先级高于正文里的成立
    # 日期、份额发行日期和持仓到期日，统一落为该月最后一天。
    metadata_month = re.search(
        r"(?:subject|title)\s*[:：].*?\b(20\d{2})(0[1-9]|1[0-2])\b"
        r"[^\n]{0,40}?monthly\s+report",
        text,
        flags=re.IGNORECASE,
    )
    if metadata_month:
        year = int(metadata_month.group(1))
        month = int(metadata_month.group(2))
        day = calendar.monthrange(year, month)[1]
        return f"{year:04d}-{month:02d}-{day:02d}"
    hint_match = re.search(r"\[REPORT_MONTH_HINT\]\s*(20\d{2})-(\d{2})", text)
    # 半年报/年报常用英文“30 June 2026”而不是数字日期。优先读取
    # 报告截止语境，避免地址、页码或比较期日期被误识别为报告期。
    month_names = (
        "January|February|March|April|May|June|July|August|September|"
        "October|November|December"
    )
    english_context = re.search(
        # 半年报通常写作 ``For the six months ended 30 June 2026``，
        # 不能只覆盖 ``period/year ended``，否则会继续向后命中比较期
        # ``For the year ended 31 December 2025``。
        rf"(?:For\s+the\s+(?:(?:six|three|nine|twelve)\s+months?|"
        rf"year|period|quarter|month)\s+ended|"
        rf"For\s+the\s+period\s+from.*?\bto|"
        rf"As\s+of|Data\s+as\s+at|as\s+at)\s+"
        rf"(\d{{1,2}})\s+({month_names})\s+(20\d{{2}})",
        text,
        flags=re.IGNORECASE,
    )
    if english_context:
        month = datetime.strptime(
            f"{english_context.group(1)} {english_context.group(2)} {english_context.group(3)}",
            "%d %B %Y",
        )
        return month.strftime("%Y-%m-%d")
    # ``As at 30 June 2026`` is the standard heading in English half-year
    # portfolio tables. It is not covered by the numeric Chinese-date rule.
    named_as_of = re.search(
        rf"(?:As\s+at|As\s+of|Data\s+as\s+at)\s+"
        rf"(\d{{1,2}})\s+({month_names})\s+(20\d{{2}})",
        text,
        flags=re.IGNORECASE,
    )
    if named_as_of:
        month = datetime.strptime(
            f"{named_as_of.group(1)} {named_as_of.group(2)} {named_as_of.group(3)}",
            "%d %B %Y",
        )
        return month.strftime("%Y-%m-%d")
    as_of_pattern = (
        r"(?:截至|截止|資料截至|资料截至|As\s+of|Data\s+as\s+at|as\s+at)"
        r".{0,30}?"
        r"(20\d{2})\s*[年/-]\s*(\d{1,2})(?:\s*[月/-]\s*(\d{1,2}))?"
    )
    as_of_match = re.search(as_of_pattern, text, flags=re.IGNORECASE)
    if as_of_match:
        day = as_of_match.group(3)
        suffix = f"-{int(day):02d}" if day else ""
        return f"{int(as_of_match.group(1)):04d}-{int(as_of_match.group(2)):02d}{suffix}"
    day_first_match = re.search(
        r"(?:截至|截止|資料截至|资料截至|As\s+of|Data\s+as\s+at|as\s+at)"
        r".{0,30}?(\d{1,2})\s*[/.-]\s*(\d{1,2})\s*[/.-]\s*(20\d{2})",
        text,
        flags=re.IGNORECASE,
    )
    if day_first_match:
        return f"{int(day_first_match.group(3)):04d}-{int(day_first_match.group(2)):02d}-{int(day_first_match.group(1)):02d}"
    # MinerU 的报告月份提示只保留 YYYY-MM；若正文明确给出“截至 DD/MM/YYYY”，
    # 必须优先使用 PDF 原文的精确截止日。
    if hint_match:
        return f"{int(hint_match.group(1)):04d}-{int(hint_match.group(2)):02d}"
    if re.fullmatch(r"\d{4}-\d{2}(-\d{2})?", text):
        return text
    match = re.search(r"(20\d{2})\s*[年/-]\s*(\d{1,2})(?:\s*[月/-]\s*(\d{1,2}))?", text)
    if match:
        day = match.group(3)
        suffix = f"-{int(day):02d}" if day else ""
        return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}{suffix}"
    return text


def _percentage_window(values: list[float], size: int) -> list[float]:
    """从图表文字中的坐标轴和数据混合序列中选择最接近 100% 的连续窗口。"""

    if len(values) < size:
        return []
    candidates: list[tuple[float, int, list[float]]] = []
    for start in range(len(values) - size + 1):
        window = values[start : start + size]
        total = sum(window)
        if 95 <= total <= 105:
            candidates.append((abs(total - 100), start, window))
    if not candidates:
        return []
    # 同样接近 100 时取后面的窗口，避开 0/20/40/60/80 坐标刻度。
    _, _, window = sorted(candidates, key=lambda item: (item[0], -item[1]))[0]
    return [round(value, 6) for value in window]


def _parse_money_market_statement_facts(text: str) -> dict[str, Any]:
    """Extract deterministic facts from English semi-annual statement tables.

    English money-market semi-annual reports commonly present no current
    investment rows (all deposits may have matured during the period), while
    the statement of financial position still discloses ``Time deposits`` and
    the investment portfolio summary discloses Daily/Weekly liquid assets.
    These are native PDF facts and must not depend on a model inventing a
    holding row.
    """

    result: dict[str, Any] = {
        "daily_liquid_assets_percent": None,
        "weekly_liquid_assets_percent": None,
        "time_deposit_amount": None,
        "net_assets_amount": None,
        "time_deposit_weight_percent": None,
        "statement_holdings": [],
    }
    source = text or ""

    def _cells(row_html: str) -> list[str]:
        return [
            re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", cell))).strip()
            for cell in re.findall(r"<td[^>]*>(.*?)</td>", row_html, flags=re.I | re.S)
        ]

    def _first_number(value: str) -> float | None:
        # Values in these tables are amounts; use the first numeric cell and
        # ignore dates/percentages that may appear in the label column.
        for match in re.finditer(r"(?<![\d/])([0-9][0-9,]*(?:\.[0-9]+)?)", value or ""):
            try:
                return float(match.group(1).replace(",", ""))
            except ValueError:
                continue
        return None

    for row_match in re.finditer(r"<tr[^>]*>(.*?)</tr>", source, flags=re.I | re.S):
        cells = _cells(row_match.group(1))
        if not cells:
            continue
        row = " ".join(cells)
        lower = row.casefold()
        label_lower = cells[0].casefold()
        if "daily liquid asset" in lower:
            percentages = re.findall(r"([0-9]+(?:\.[0-9]+)?)\s*%", row)
            if percentages:
                result["daily_liquid_assets_percent"] = float(percentages[-1])
        if "weekly liquid asset" in lower:
            percentages = re.findall(r"([0-9]+(?:\.[0-9]+)?)\s*%", row)
            if percentages:
                result["weekly_liquid_assets_percent"] = float(percentages[-1])
        if re.search(r"^(?:time|term)\s+deposits?\b", label_lower):
            # Current-period value is the first number after the label. A
            # hyphen means no value, so keep searching subsequent cells.
            amount = next((_first_number(cell) for cell in cells[1:] if _first_number(cell) is not None), None)
            if amount is not None and amount > 0:
                result["time_deposit_amount"] = amount
        if (
            re.search(r"^net assets attributable to unitholders$", label_lower)
            or re.search(r"^net assets attributable to unitholders\s*\(", label_lower)
            or re.search(r"^net assets$", label_lower)
        ):
            amount = next((_first_number(cell) for cell in cells[1:] if _first_number(cell) is not None), None)
            if amount is not None and amount > 0:
                result["net_assets_amount"] = amount

    # Some MinerU exports lose table tags. Keep a narrow line-based fallback
    # for the same labels, still requiring an explicit percentage/amount.
    compact = re.sub(r"\s+", " ", html.unescape(re.sub(r"</?[A-Za-z][^>]*>", " ", source)))
    if result["daily_liquid_assets_percent"] is None:
        match = re.search(r"daily\s+liquid\s+assets?[^%]{0,100}?([0-9]+(?:\.[0-9]+)?)\s*%", compact, re.I)
        if match:
            result["daily_liquid_assets_percent"] = float(match.group(1))
    if result["weekly_liquid_assets_percent"] is None:
        match = re.search(r"weekly\s+liquid\s+assets?[^%]{0,100}?([0-9]+(?:\.[0-9]+)?)\s*%", compact, re.I)
        if match:
            result["weekly_liquid_assets_percent"] = float(match.group(1))
    if result["time_deposit_amount"] is None:
        match = re.search(r"\btime\s+deposits?\b[^\d]{0,80}([0-9][0-9,]*(?:\.[0-9]+)?)", compact, re.I)
        if match:
            result["time_deposit_amount"] = float(match.group(1).replace(",", ""))
    if result["net_assets_amount"] is None:
        match = re.search(r"net\s+assets(?:\s+attributable\s+to\s+unitholders)?[^\d]{0,100}([0-9][0-9,]*(?:\.[0-9]+)?)", compact, re.I)
        if match:
            result["net_assets_amount"] = float(match.group(1).replace(",", ""))

    amount = result["time_deposit_amount"]
    net_assets = result["net_assets_amount"]
    if amount is not None and net_assets and net_assets > 0:
        weight = round(float(amount) / float(net_assets) * 100, 6)
        result["time_deposit_weight_percent"] = weight
        result["statement_holdings"] = [{
            "name": "Time deposits",
            "weight_percent": weight,
            "market_value": float(amount),
            "holding_type": "Time deposit",
        }]
    return result


def _parse_money_market_text_facts(text: str) -> dict[str, Any]:
    """从原生 PDF 文本提取货币基金图表和 WAM，拒绝无原文锚点的模型值。"""

    # MinerU 对半年报会保留 HTML 表格，标签和数值之间通常隔着
    # ``</td><td>``；先生成一份去标签文本，避免把该布局误判为无证据。
    # 只去掉真正的 HTML 标签；不能用 ``<[^>]+>``，否则会把货币基金
    # 久期标签 ``<1周`` 到 ``>3个月`` 整段误删。
    plain_text = unicodedata.normalize(
        "NFKC",
        html.unescape(re.sub(r"</?[A-Za-z][^>]*>", " ", text or "")),
    )
    compact = re.sub(r"[ \t\r\n]+", " ", plain_text)
    facts: dict[str, Any] = {
        "duration_distribution": [],
        "asset_type_allocation": [],
        "weighted_average_maturity_days": None,
        "portfolio_ytm_percent": None,
        "inception_date": None,
        "listing_date": None,
        "top_holdings": [],
        "daily_liquid_assets_percent": None,
        "weekly_liquid_assets_percent": None,
    }
    statement_facts = _parse_money_market_statement_facts(text)
    # 不同管理人对 WAM 的标题并不统一；华夏月报使用“加權平均到期日”，
    # 数值位于下一行的表格单元格，折叠 HTML 标签后仍可由该窗口配对。
    wam_aliases = (
        "加權平均屆滿期", "加权平均届满期", "加權平均到期日", "加权平均到期日",
        "平均到期日", "加權平均到期天數", "加权平均到期天数",
        "weighted average maturity", "average maturity",
    )
    # 平安月报在 HTML 表格中直接给出“标签 + 49.42日”；先用无跨数字的
    # 邻近匹配读取它，避免被“平均到期日2”脚注编号或成立日期抢先匹配。
    wam = None
    for alias in wam_aliases:
        wam = re.search(
            re.escape(alias) + r"\s*(?:[：:]\s*)?([0-9]+(?:\.[0-9]+)?)\s*(?:天|日|days?)",
            plain_text,
            re.I,
        )
        if wam:
            break
    if wam is None:
        wam = re.search(
            r"(?:加權平均屆滿期|加权平均届满期|加權平均到期日|加权平均到期日|平均到期日|平均到期日|加權平均到期天數|加权平均到期天数|weighted\s+average\s+maturity|average\s+maturity)(?:\s*[12](?=\s|$))?\s*(?:[：:]\s*)?(?:[^0-9天]{0,120})?([0-9]+(?:\.[0-9]+)?)\s*(?:天|days?)",
            compact,
            re.I,
        )
    if wam is None:
        # English semi-annual reports often use an HTML table with a shared
        # ``Days`` header, so the value cell is ``51.19`` without repeating
        # the unit. Restrict the window to a single table row to avoid
        # confusing maturity dates in the securities list with WAM.
        wam = re.search(
            r"(?:portfolio\s+)?weighted\s+average\s+maturity"
            r"(?:\s+in\s+days)?\s*</?[^>]*>.*?"
            r"(?:</?[^>]*>\s*)?([0-9]+(?:\.[0-9]+)?)"
            r"(?=\s*</td>|\s*</?td|\s*$)",
            text or "",
            re.I | re.S,
        )
    if wam:
        facts["weighted_average_maturity_days"] = float(wam.group(1))
    else:
        # 某些月报把“平均到期日”标题放在 HTML 表格前，实际数值放在
        # 表格结束后的独立文本节点（例如“平均到期日2 ... 56.27天”）。
        # 这种布局中标签与数字之间会出现大量表格数字，不能使用上面的
        # 窄窗口；仍以“天/days”作为单位锚点，避免把费率或日期误当 WAM。
        for alias in (
            *wam_aliases,
        ):
            fallback_matches = list(re.finditer(
                # 宽窗口只接受“天/days”单位。若接受“日”，脚注标题
                # 后的大表格日期（如 2024年2月2日）会被误当成 WAM。
                # HTML 月报的“49.42日”由上面的窄窗口负责解析。
                re.escape(alias) + r".{0,5000}?([0-9]+(?:\.[0-9]+)?)\s*(?:天|days?)",
                compact,
                re.I,
            ))
            # HTML 表格的脚注编号可能出现在标题后（如“平均到期日2”）；
            # 取带单位的最后一个候选，避免把脚注编号当成天数。
            fallback = fallback_matches[-1] if fallback_matches else None
            if fallback:
                facts["weighted_average_maturity_days"] = float(fallback.group(1))
                break

    # 类别成立日期常用香港月报格式 DD/MM/YY（例如 09/02/23）。
    # 先按日/月/年解释，不能把它误读成美国月/日格式。
    class_inception = re.search(
        r"(?:類別成立日期|类别成立日期|share\s+class\s+inception\s+date)\s*[:：]?\s*"
        r"([0-3]?\d)\s*/\s*([01]?\d)\s*/\s*(\d{2,4})",
        compact,
        re.I,
    )
    if class_inception:
        year = int(class_inception.group(3))
        if year < 100:
            year += 2000 if year <= 69 else 1900
        facts["inception_date"] = (
            f"{year:04d}-{int(class_inception.group(2)):02d}-{int(class_inception.group(1)):02d}"
        )
    # 广发等双语月报在“基金资料”表中使用
    # ``Launch Date成立日期 | 06 Jul 2023 2023年7月6日``，日期前会有
    # 英文日期，不能只依赖“成立日期”后紧接中文日期的窄匹配。
    if facts["inception_date"] is None:
        launch_anchor = re.search(
            r"(?:launch\s+date|成立日期|成立日).{0,100}?"
            r"([0-3]?\d)\s+(January|February|March|April|May|June|July|August|"
            r"September|October|November|December)\s+(20\d{2})",
            compact,
            re.IGNORECASE,
        )
        if launch_anchor:
            month = datetime.strptime(
                f"{launch_anchor.group(1)} {launch_anchor.group(2)} {launch_anchor.group(3)}",
                "%d %B %Y",
            )
            facts["inception_date"] = month.strftime("%Y-%m-%d")
    if facts["inception_date"] is None:
        launch_anchor = re.search(
            r"(?:launch\s+date|成立日期|成立日).{0,100}?"
            r"([12]\d{3})年\s*(\d{1,2})月\s*(\d{1,2})日",
            compact,
            re.IGNORECASE,
        )
        if launch_anchor:
            facts["inception_date"] = (
                f"{launch_anchor.group(1)}-{int(launch_anchor.group(2)):02d}-"
                f"{int(launch_anchor.group(3)):02d}"
            )
    # 成立日期有时只出现在“基金资料”表或脚注中；优先取带“成立日期”
    # 标签的日期，避免误把报告截止日或表现起始日当成立日。
    if facts["inception_date"] is None:
        inception = re.search(
            r"(?:成立日期|成立日)\s*[:：]?\s*([12]\d{3})年\s*(\d{1,2})月\s*(\d{1,2})日",
            compact,
            re.I,
        )
        if inception:
            facts["inception_date"] = f"{inception.group(1)}-{int(inception.group(2)):02d}-{int(inception.group(3)):02d}"
        else:
            inception = re.search(
                r"(?:成立日期|成立日)\s*[:：]?\s*([12]\d{3})[-/]([01]?\d)[-/]([0-3]?\d)",
                compact,
                re.I,
            )
            if inception:
                facts["inception_date"] = f"{inception.group(1)}-{int(inception.group(2)):02d}-{int(inception.group(3)):02d}"

    # ETF factsheet 常把上市日期与基金/份额成立日期分开披露；二者不能混为一谈。
    listing = re.search(
        r"(?:上市日期|上市日|listing\s+date|date\s+of\s+listing)\s*[:：]?\s*"
        r"(?:([12]\d{3})年\s*(\d{1,2})月\s*(\d{1,2})日|"
        r"([0-3]?\d)\s+(January|February|March|April|May|June|July|August|"
        r"September|October|November|December)\s+(20\d{2})|"
        r"([12]\d{3})[-/]([01]?\d)[-/]([0-3]?\d))",
        compact,
        re.IGNORECASE,
    )
    if listing:
        if listing.group(1):
            facts["listing_date"] = f"{listing.group(1)}-{int(listing.group(2)):02d}-{int(listing.group(3)):02d}"
        elif listing.group(4):
            month = datetime.strptime(
                f"{listing.group(5)} {listing.group(6)} {listing.group(7)}",
                "%d %B %Y",
            )
            facts["listing_date"] = month.strftime("%Y-%m-%d")
        else:
            facts["listing_date"] = f"{listing.group(8)}-{int(listing.group(9)):02d}-{int(listing.group(10)):02d}"

    duration_matches = list(re.finditer(r"(?:久期分佈|久期分布|期限分佈|期限分布|到期日分佈|到期日分布)", compact, re.I))
    duration_options = (
        ["少於7天", "7-30天", "30-90天", "超過90天"],
        ["<7天", "7-30天", "30-90天", ">90天"],
        ["<1月", "1-<3月", "3-<6月", "6-<12月"],
    )
    if duration_matches:
        for duration_match in duration_matches:
            duration_start = duration_match.end()
            for duration_buckets in duration_options:
                if not all(bucket in compact[duration_start:] for bucket in duration_buckets):
                    continue
                section_end = compact.find("定期存款", duration_start)
                section = compact[duration_start:section_end if section_end > duration_start else None]
                # 某些图表把“资产类别分布”标题放在期限标签之前，
                # 但期限百分比仍出现在该标题之后；按标签逐项配对时使用完整尾段。
                pair_section = compact[duration_start:]
                pairs = [
                    (bucket, float(match.group(1)))
                    for bucket in duration_buckets
                    if (match := re.search(re.escape(bucket) + r"\s*([0-9]+(?:\.[0-9]+)?)\s*%", pair_section))
                ]
                if len(pairs) == len(duration_buckets):
                    facts["duration_distribution"] = [
                        {"bucket": bucket, "weight_percent": value, "value": value, "unit": "percent", "measure_type": "allocation_percent"}
                        for bucket, value in pairs
                    ]
                    break
                values = [float(item) for item in re.findall(r"([0-9]+(?:\.[0-9]+)?)\s*%", section)]
                selected = _percentage_window(values, len(duration_buckets))
                if selected:
                    facts["duration_distribution"] = [
                        {"bucket": bucket, "weight_percent": value, "value": value, "unit": "percent", "measure_type": "allocation_percent"}
                        for bucket, value in zip(duration_buckets, selected)
                    ]
                    break
            if facts["duration_distribution"]:
                break

    # 招银国际月报将久期图表以四个连续标签输出，而不是 HTML 表格。
    # 这些百分比是组合久期分布，不能再被通用“到期收益率”规则读取。
    if not facts["duration_distribution"]:
        duration_pairs = (
            ("<1周", r"<\s*1周\s*([0-9]+(?:\.[0-9]+)?)\s*%"),
            ("1周-1個月", r"1周\s*-\s*1(?:个|個)月\s*([0-9]+(?:\.[0-9]+)?)\s*%"),
            ("1個月-3個月", r"1(?:个|個)月\s*-\s*3(?:个|個)月\s*([0-9]+(?:\.[0-9]+)?)\s*%"),
            (">3個月", r">\s*3(?:个|個)月\s*([0-9]+(?:\.[0-9]+)?)\s*%"),
        )
        parsed_duration = []
        for bucket, pattern in duration_pairs:
            match = re.search(pattern, compact, re.I)
            if not match:
                parsed_duration = []
                break
            parsed_duration.append({
                "bucket": bucket,
                "weight_percent": float(match.group(1)),
                "value": float(match.group(1)),
                "unit": "percent",
                "measure_type": "allocation_percent",
            })
        if parsed_duration:
            facts["duration_distribution"] = parsed_duration

    # 广发月报把图表数值按“数值在前、图例在后”输出：
    # ``40.42% 34.04% 25.54% 大於60天 30天以内 30-60天``。
    # 只在三个明确期限标签及合计约 100% 时接受，避免吸收 NAV 图表或
    # 业绩表中的百分比。
    if not facts["duration_distribution"]:
        # 简体/繁体标签成对出现时只要求每个语义桶至少命中一个变体。
        # 不能把“大於/大于”和“以内/內”同时当作四个独立标签。
        gf_duration_label_groups = (
            ("大於60天", "大于60天"),
            ("30天以内", "30天內"),
            ("30-60天",),
        )
        if all(any(label in compact for label in group) for group in gf_duration_label_groups):
            title = re.search(r"(?:DAYS\s+TO\s+MATURITY|到期分佈|到期分布)", compact, re.IGNORECASE)
            section = compact[title.end() :] if title else compact
            section = re.split(r"Source:|數據來源|数据来源", section, maxsplit=1)[0]
            # 优先使用标记出的 PDF 原生文本层。MinerU Markdown/图片 OCR
            # 可能把同一图表按另一阅读方向输出（例如 25.54、34.04、40.42），
            # 而原生层保留 PDF 的阅读顺序 40.42、34.04、25.54。
            value_sources = [section]
            native_marker = re.search(r"\[PDF_TEXT_LAYER\]", compact, re.IGNORECASE)
            if native_marker:
                native_section = compact[native_marker.end() :]
                native_section = re.split(r"\[IMAGE_OCR\]", native_section, maxsplit=1)[0]
                value_sources.insert(0, native_section)
            selected: list[float] = []
            for value_source in value_sources:
                selected = _percentage_window(
                    [float(item) for item in re.findall(r"([0-9]+(?:\.[0-9]+)?)\s*%", value_source)],
                    3,
                )
                if selected:
                    break
            # 若原生层也没有连续窗口，才回退到全文，避免图表被拆成
            # 多段时因标题/Source 分隔而漏读。
            if not selected:
                selected = _percentage_window(
                    [float(item) for item in re.findall(r"([0-9]+(?:\.[0-9]+)?)\s*%", compact)],
                    3,
                )
            if selected:
                facts["duration_distribution"] = [
                    {
                        "bucket": label,
                        "weight_percent": value,
                        "value": value,
                        "unit": "percent",
                        "measure_type": "allocation_percent",
                    }
                    for label, value in zip(("大於60天", "30天以内", "30-60天"), selected)
                ]

    asset_match = re.search(r"(?:資產分佈|资产分布|資產類別|资产类别|資產類別分佈|资产类别分布|資產類別分布|资产类别分佈)", compact, re.I)
    asset_labels = ["定期存款", "其他货币市场工具", "现金和等价物"]
    asset_variants = (
        ("存款", "定期存款", "存款"),
        ("其他货币市场工具", "其他货币市场工具", "其他貨幣市場工具"),
        ("现金和等价物", "现金和等价物", "現金及現金等價物", "现金及现金等价物", "現金及其他"),
    )
    first_asset_label = next(
        (compact.find(label, asset_match.end() if asset_match else 0) for variants in asset_variants for label in variants if compact.find(label, asset_match.end() if asset_match else 0) >= 0),
        -1,
    )
    if asset_match and first_asset_label >= 0 and all(any(label in compact[first_asset_label:] for label in variants) for variants in asset_variants):
        section = compact[first_asset_label:]
        section = re.split(r"到期日分佈|到期日分布|久期分佈|久期分布|\n?•|重要提示|投資目標|投资目标", section, maxsplit=1)[0]
        parsed_assets: list[dict[str, Any]] = []
        for canonical, *variants in asset_variants:
            match = next((re.search(re.escape(label) + r"\s*([0-9]+(?:\.[0-9]+)?)\s*%", section) for label in variants if re.search(re.escape(label) + r"\s*[0-9]+(?:\.[0-9]+)?\s*%", section)), None)
            if match:
                parsed_assets.append({"type": canonical, "weight_percent": float(match.group(1))})
        if len(parsed_assets) == len(asset_variants):
            facts["asset_type_allocation"] = parsed_assets
        else:
            values = [float(item) for item in re.findall(r"([0-9]+(?:\.[0-9]+)?)\s*%", section)]
            selected = _percentage_window(values, len(asset_labels))
            if selected:
                facts["asset_type_allocation"] = [
                    {"type": label, "weight_percent": value}
                    for label, value in zip(asset_labels, selected)
                ]

    if not facts["asset_type_allocation"]:
        # CICC 等 ETF factsheet 直接写“资产类别：现金/银行存款/其他货币市场工具/
        # 货币市场基金”，不使用“资产类别分布”标题；四项合计约 100% 才接受。
        cicc_asset_pairs = (
            ("现金", ("现金", "現金")),
            ("银行存款", ("银行存款", "銀行存款")),
            ("其他货币市场工具", ("其他货币市场工具", "其他貨幣市場工具")),
            ("货币市场基金", ("货币市场基金", "貨幣市場基金")),
        )
        parsed_cicc_assets: list[dict[str, Any]] = []
        for canonical, variants in cicc_asset_pairs:
            match = next(
                (
                    re.search(
                        re.escape(label) + r"\s*[：:]?\s*([0-9]+(?:\.[0-9]+)?)\s*%",
                        compact,
                        re.I,
                    )
                    for label in variants
                    if re.search(
                        re.escape(label) + r"\s*[：:]?\s*[0-9]+(?:\.[0-9]+)?\s*%",
                        compact,
                        re.I,
                    )
                ),
                None,
            )
            if not match:
                parsed_cicc_assets = []
                break
            parsed_cicc_assets.append({"type": canonical, "weight_percent": float(match.group(1))})
        if parsed_cicc_assets and 95 <= sum(item["weight_percent"] for item in parsed_cicc_assets) <= 105:
            facts["asset_type_allocation"] = parsed_cicc_assets

    # 图表被 MinerU 裁成图片时，Vision OCR 会把图例补回文本层；按“标签 + 百分比”
    # 配对即可恢复资产结构。
    chart_asset_pairs = (
        ("存款", r"(?:定期)?存款\s*([0-9]+(?:\.[0-9]+)?)\s*%"),
        ("其他貨幣市場工具", r"其他貨幣市場工具\s*([0-9]+(?:\.[0-9]+)?)\s*%"),
        ("現金及其他", r"現金及其他\s*([0-9]+(?:\.[0-9]+)?)\s*%"),
    )
    parsed_chart_assets = []
    for label, pattern in chart_asset_pairs:
        match = re.search(pattern, compact, re.I)
        if not match:
            parsed_chart_assets = []
            break
        parsed_chart_assets.append({"type": label, "weight_percent": float(match.group(1))})
    if parsed_chart_assets and 95 <= sum(item["weight_percent"] for item in parsed_chart_assets) <= 105:
        facts["asset_type_allocation"] = parsed_chart_assets

    # Vision OCR 常按图表的阅读顺序输出“百分比 + 图例”（例如
    # ``1.7% 債券``），与 PDF 文字层的“图例 + 百分比”方向相反；两种
    # 方向都接受，并要求合计接近 100%，避免把页面其他费率误收为资产分布。
    if not facts["asset_type_allocation"]:
        reverse_asset_pairs = (
            ("債券", r"([0-9]+(?:\.[0-9]+)?)\s*%\s*債券"),
            ("定期存款", r"([0-9]+(?:\.[0-9]+)?)\s*%\s*定期存款"),
            ("現金及等值", r"([0-9]+(?:\.[0-9]+)?)\s*%\s*(?:現金及等值|現金及現金等價物|現金及其他)"),
        )
        parsed_reverse_assets = []
        for label, pattern in reverse_asset_pairs:
            match = re.search(pattern, compact, re.I)
            if not match:
                parsed_reverse_assets = []
                break
            parsed_reverse_assets.append({"type": label, "weight_percent": float(match.group(1))})
        if parsed_reverse_assets and 95 <= sum(item["weight_percent"] for item in parsed_reverse_assets) <= 105:
            facts["asset_type_allocation"] = parsed_reverse_assets

    # 部分月报没有“资产类别分布”标题，图表文字直接按“标签 + 百分比”
    # 输出。仅在三个标签都能配对、且合计接近 100% 时接受，避免误收费率。
    if not facts["asset_type_allocation"]:
        native_asset_pairs = (
            ("定期存款", r"定期存款\s*([0-9]+(?:\.[0-9]+)?)\s*%"),
            ("券商票據及存單", r"(?:券商票據及存單|券商票据及存单)\s*([0-9]+(?:\.[0-9]+)?)\s*%"),
            ("現金及其他", r"(?:現金及其他|现金及其他)\s*([0-9]+(?:\.[0-9]+)?)\s*%"),
        )
        parsed_assets = []
        for label, pattern in native_asset_pairs:
            match = re.search(pattern, compact, re.I)
            if not match:
                parsed_assets = []
                break
            parsed_assets.append({"type": label, "weight_percent": float(match.group(1))})
        if parsed_assets and 95 <= sum(item["weight_percent"] for item in parsed_assets) <= 105:
            facts["asset_type_allocation"] = parsed_assets

    # 汇添富等月报把图表文字层拆成“标签换行 + 百分比”，例如：
    # ``银行存款 / 及其他 / 42.1%`` 和 ``货币市场 / 工具 57.9%``。
    # 这类图表没有可供 MinerU 配对的 HTML 表格，按完整语义标签配对，
    # 并要求合计接近 100%，避免把业绩表中的百分比误计入资产分布。
    if not facts["asset_type_allocation"]:
        chart_assets = []
        for label, pattern in (
            ("银行存款及其他", r"银行存款\s*及其他\s*([0-9]+(?:\.[0-9]+)?)\s*%"),
            ("货币市场工具", r"货币市场\s*工具\s*([0-9]+(?:\.[0-9]+)?)\s*%"),
        ):
            match = re.search(pattern, compact, re.IGNORECASE)
            if not match:
                chart_assets = []
                break
            chart_assets.append({"type": label, "weight_percent": float(match.group(1))})
        if chart_assets and 95 <= sum(item["weight_percent"] for item in chart_assets) <= 105:
            facts["asset_type_allocation"] = chart_assets

    # 同一类月报的“到期分布”图表通常输出为“7天以下/8-30天/31-60天/
    # 60天以上 + 百分比”，而不是带“日”后缀的区间。只有四个明确
    # 标签均能配对且合计约 100% 时才接受。
    if not facts["duration_distribution"]:
        chart_durations = []
        for label, pattern in (
            ("7天以下", r"7\s*天以下\s*([0-9]+(?:\.[0-9]+)?)\s*%"),
            ("8-30天", r"8\s*[-–]\s*30\s*天\s*([0-9]+(?:\.[0-9]+)?)\s*%"),
            ("31-60天", r"31\s*[-–]\s*60\s*天\s*([0-9]+(?:\.[0-9]+)?)\s*%"),
            ("60天以上", r"60\s*天以上\s*([0-9]+(?:\.[0-9]+)?)\s*%"),
        ):
            match = re.search(pattern, compact, re.IGNORECASE)
            if not match:
                chart_durations = []
                break
            value = float(match.group(1))
            chart_durations.append({
                "bucket": label,
                "weight_percent": value,
                "value": value,
                "unit": "percent",
                "measure_type": "allocation_percent",
            })
        if chart_durations and 95 <= sum(item["weight_percent"] for item in chart_durations) <= 105:
            facts["duration_distribution"] = chart_durations

    # 平安货币月报的到期日分布为图片图例，OCR 后格式为“8-31日 19.71%”。
    duration_chart = []
    for match in re.finditer(
        r"(\d+)\s*[-–]\s*(\d+)\s*日\s*([0-9]+(?:\.[0-9]+)?)\s*%",
        compact,
        re.I,
    ):
        duration_chart.append(
            {
                "bucket": f"{match.group(1)}-{match.group(2)}日",
                "weight_percent": float(match.group(3)),
                "value": float(match.group(3)),
                "unit": "percent",
                "measure_type": "allocation_percent",
            }
        )
    if duration_chart:
        duration_total = sum(item["weight_percent"] for item in duration_chart)
        if not 95 <= duration_total <= 105:
            # 原生 PDF 文字层和 OCR 可能各自包含同一张图，导致同一组
            # 区间出现两遍（合计约 200%）。从连续候选窗口中选出合计
            # 最接近 100% 的一组，保留后出现的 OCR 版本以避开重复层。
            candidates: list[tuple[float, int, list[dict[str, Any]]]] = []
            for size in (6, 5, 4, 3):
                if len(duration_chart) < size:
                    continue
                for start in range(len(duration_chart) - size + 1):
                    window = duration_chart[start : start + size]
                    total = sum(item["weight_percent"] for item in window)
                    if 95 <= total <= 105:
                        candidates.append((abs(total - 100), start, window))
                if candidates:
                    break
            if candidates:
                _, _, duration_chart = sorted(
                    candidates, key=lambda item: (item[0], -item[1])
                )[0]
        if 95 <= sum(item["weight_percent"] for item in duration_chart) <= 105:
            facts["duration_distribution"] = duration_chart

    # “投资组合特点 4.10% 股份类别”是该月报的 YTM 数值锚点；
    # 不使用孤立的“到期收益率”字样，避免把久期图的 21.28% 误当 YTM。
    ytm = re.search(
        r"(?:投资组合特点|投資組合特點)\s*([0-9]+(?:\.[0-9]+)?)\s*%\s*(?:股份类别|股份類別|类别信息|類別資訊)",
        compact,
        re.I,
    )
    if ytm:
        facts["portfolio_ytm_percent"] = float(ytm.group(1))
    else:
        ytm = re.search(
            r"(?:加權平均收益率|加权平均收益率|加權平均到期收益率|加权平均到期收益率)\s*([0-9]+(?:\.[0-9]+)?)\s*%",
            compact,
            re.I,
        )
        if ytm:
            facts["portfolio_ytm_percent"] = float(ytm.group(1))

    # BOCHK-style factsheets place the value in a two-cell row as
    # “到期收益率：每年3.9%+”.  Accept the explicit label/value pair while
    # keeping the window tight enough not to capture a chart percentage.
    if facts["portfolio_ytm_percent"] is None:
        ytm = re.search(
            r"(?:到期收益率|到期收益率|yield\s+to\s+maturity|YTM)"
            r".{0,80}?([0-9]+(?:\.[0-9]+)?)\s*%",
            compact,
            re.I,
        )
        if ytm:
            facts["portfolio_ytm_percent"] = float(ytm.group(1))

    # 半年报投资组合表没有“前十大持仓”标题，但每行带有
    # Holding/Nominal Value、Market Value 和 ``% of net asset``。国家/注册地
    # 和资产类型是表格的分组行，必须在遍历分页表格时保持状态。只选含有
    # Holding/Nominal Value 表头的表，避免把“投资组合变动表”和流动性表
    # 中的百分比误当成持仓。
    portfolio_holdings: list[dict[str, Any]] = []
    # 平安月报直接披露“五大持仓” HTML 表。该表的比例单元格通常没有
    # ``%`` 字符（百分号只出现在表头），所以不能依赖通用的百分比正则。
    monthly_holdings_match = re.search(
        r"五大持倉|五大持仓|Top\s*5\s*Hold",
        text or "",
        flags=re.IGNORECASE,
    )
    if monthly_holdings_match:
        monthly_section = (text or "")[monthly_holdings_match.end() :]
        monthly_section = re.split(r"派息成分|派息成份|Dividend", monthly_section, maxsplit=1, flags=re.IGNORECASE)[0]
        for row_match in re.finditer(
            r"<tr[^>]*>(.*?)</tr>",
            monthly_section,
            flags=re.IGNORECASE | re.DOTALL,
        ):
            cells = [
                re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", cell))).strip()
                for cell in re.findall(r"<td[^>]*>(.*?)</td>", row_match.group(1), flags=re.IGNORECASE | re.DOTALL)
            ]
            if len(cells) < 2 or cells[0] in {"持倉", "持仓", "Holding"}:
                continue
            # 比例位于末列，接受表格单元格中没有百分号的写法。
            value_match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)", cells[-1].replace(",", ""))
            if not value_match:
                continue
            portfolio_holdings.append(
                {
                    "name": cells[0],
                    "weight_percent": float(value_match.group(1)),
                    "coupon_rate": cells[1],
                    "maturity_date": cells[2] if len(cells) > 2 else None,
                }
            )

    # 汇添富月报的“前五大固定收益工具持仓”不是 HTML 表格，而是每行
    # “名称,票面利率,到期日,占净资产比重”。原生 PDF 文本层保留完整的
    # 第五行比例，故按行解析并保留票面利率与到期日等证据字段。
    fixed_income_heading = re.search(
        r"前五大固定收益工具持[倉仓]?(?:（|\()​?[^\n]{0,80}",
        plain_text,
        flags=re.IGNORECASE,
    )
    if fixed_income_heading:
        fixed_section = plain_text[fixed_income_heading.end():]
        fixed_section = re.split(r"数据来源|資料來源|基金经理|基金經理|组合特点|組合特點", fixed_section, maxsplit=1, flags=re.IGNORECASE)[0]
        for line in fixed_section.splitlines():
            line = re.sub(r"\s+", " ", line).strip()
            match = re.match(
                r"^(.+?),\s*([0-9]+(?:\.[0-9]+)?)\s*%,\s*"
                r"([0-3]?\d/[01]?\d/\d{4})\s+([0-9]+(?:\.[0-9]+)?)\s*%$",
                line,
            )
            if not match:
                continue
            portfolio_holdings.append({
                "name": match.group(1).strip(),
                "weight_percent": float(match.group(4)),
                "coupon_rate": match.group(2),
                "maturity_date": match.group(3),
            })
    if portfolio_holdings:
        # 组合表和原生文本层可能同时出现，按名称+比例去重但保留首条
        # 的票面利率/到期日等附加字段。
        deduped_holdings = []
        seen_holdings = set()
        for item in portfolio_holdings:
            key = (item.get("name"), item.get("weight_percent"))
            if key in seen_holdings:
                continue
            seen_holdings.add(key)
            deduped_holdings.append(item)
        facts["top_holdings"] = deduped_holdings
    portfolio_start = (text or "").lower().find("investment portfolio")
    portfolio_tables = []
    for table_match in re.finditer(r"<table[^>]*>.*?</table>", text or "", flags=re.IGNORECASE | re.DOTALL):
        if portfolio_start < 0 or table_match.start() < portfolio_start:
            continue
        table_html = table_match.group(0)
        if re.search(r"holdings?\s*/\s*nominal\s+value", table_html, flags=re.IGNORECASE) or re.search(
            r"<td[^>]*>\s*Holding\s*</td>.*?<td[^>]*>\s*Fair value",
            table_html,
            flags=re.IGNORECASE | re.DOTALL,
        ) or (
            re.search(r"\bHolding\b", table_html, flags=re.IGNORECASE)
            and re.search(r"%\s*of\s*(?:net\s+asset(?:s)?|assets)", table_html, flags=re.IGNORECASE)
        ):
            portfolio_tables.append(table_html)

    current_asset_group: str | None = None
    current_holding_type: str | None = None
    current_registration: str | None = None
    portfolio_category_totals: dict[str, float] = {}
    portfolio_other_net_assets: float | None = None
    portfolio_total_investments: float | None = None

    def _cell_number(value: str) -> float | None:
        value = str(value or "").strip().replace(",", "")
        if not value or value in {"-", "—", "–"}:
            return None
        value = value.strip("()")
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    for table_html in portfolio_tables:
        # 一些英文半年报把持仓分类放在表格前一张“投资组合变动表”，
        # 组合明细表本身只有 Holdings/Nominal Value、Market Value、%
        # of net asset 三列。保留该表的来源级分类，不能因为缺少单独的
        # “Collective Investment Schemes”行而跳过明细。
        table_default_holding_type = None
        if re.search(r"unlisted\s*/\s*quoted|unlisted\s+investments", table_html, re.I):
            table_default_holding_type = "Unlisted/quoted investments"
        elif re.search(r"listed\s*/\s*quoted|listed\s+investments", table_html, re.I):
            table_default_holding_type = "Listed/quoted investments"
        table_percent_header = bool(re.search(r"%\s*of\s*net\s+asset(?:s)?", table_html, re.I))

        def _row_percentage(row_cells: list[str]) -> float | None:
            row_text = " ".join(row_cells)
            matches = re.findall(r"([0-9]+(?:\.[0-9]+)?)\s*%", row_text)
            if matches:
                return float(matches[-1])
            if table_percent_header and row_cells:
                match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*", row_cells[-1].replace(",", ""))
                if match and 0 <= float(match.group(1)) <= 100:
                    return float(match.group(1))
            return None

        for row_match in re.finditer(r"<tr[^>]*>(.*?)</tr>", table_html, flags=re.IGNORECASE | re.DOTALL):
            row_html = row_match.group(1)
            cells = [
                re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", cell))).strip()
                for cell in re.findall(r"<td[^>]*>(.*?)</td>", row_html, flags=re.IGNORECASE | re.DOTALL)
            ]
            if not cells:
                continue
            row_label = " ".join(cells).strip()
            normalized_label = re.sub(r"\s+", " ", row_label).strip()
            label_lower = normalized_label.casefold()

            if re.fullmatch(r"listed\s*/\s*quoted investments(?:\s*\(continued\))?", normalized_label, re.I):
                current_asset_group = "Listed/quoted investments"
                current_holding_type = None
                current_registration = None
                continue
            if re.fullmatch(r"unlisted\s*/\s*quoted investments(?:\s*\(continued\))?", normalized_label, re.I):
                current_asset_group = "Unlisted/quoted investments"
                current_holding_type = None
                current_registration = None
                continue
            if re.fullmatch(r"fixed\s+income\s+securities(?:\s*\(continued\))?", normalized_label, re.I):
                current_holding_type = (
                    f"{current_asset_group} Fixed Income Securities"
                    if current_asset_group else "Fixed Income Securities"
                )
                continue
            if re.fullmatch(r"debt\s+securities(?:\s*\(continued\))?", normalized_label, re.I):
                current_asset_group = "Debt securities"
                current_holding_type = "Debt securities"
                continue
            if re.match(r"^debt\s+securities\b", normalized_label, re.I) and len(cells) <= 2:
                current_asset_group = "Debt securities"
                current_holding_type = "Debt securities"
                continue
            if re.fullmatch(r"money\s+market\s+funds?(?:\s*\(continued\))?", normalized_label, re.I):
                current_asset_group = "Money market funds"
                current_holding_type = "Money market funds"
                continue
            if re.match(r"^money\s+market\s+funds?\b", normalized_label, re.I) and len(cells) <= 2:
                current_asset_group = "Money market funds"
                current_holding_type = "Money market funds"
                continue
            if re.fullmatch(r"collective\s+investment\s+schemes?", normalized_label, re.I):
                current_holding_type = "Collective Investment Scheme"
                continue
            if re.match(r"^collective\s+investment\s+schemes?\b", normalized_label, re.I) and len(cells) <= 2:
                current_holding_type = "Collective Investment Scheme"
                continue

            row_percentage = _row_percentage(cells)
            if re.search(r"other\s+net\s+assets", normalized_label, re.I) and row_percentage is not None:
                portfolio_other_net_assets = row_percentage
            if re.search(r"total\s+investments(?:\s+as\s+at|,?\s+at\s+cost)?", normalized_label, re.I) and row_percentage is not None:
                portfolio_total_investments = row_percentage
            # 国家/注册地以单元格分组行出现（包括 Hong Kong、VIRGIN ISL, BT）。
            # 不用简单的全大写判断，以兼容集合投资计划表的标题大小写。
            if len(cells) == 1 and re.fullmatch(r"[A-Za-z][A-Za-z .,'&()/\-]+", normalized_label):
                if label_lower not in {
                    "holding/ nominal value usd", "market value usd", "accrued interest usd",
                    "fixed income securities", "collective investment scheme", "collective investment schemes",
                }:
                    current_registration = normalized_label
                    continue

            name = cells[0] if cells else ""
            # MinerU 将 rowspan 的持仓名称与小计标题拼在同一单元格，
            # 例如“ETF ... TOTAL UNLISTED INVESTMENTS”；小计不是证券名。
            name = re.sub(
                r"\s+TOTAL\s+(?:UNLISTED|LISTED)(?:\s*/\s*QUOTED)?\s+INVESTMENTS$",
                "",
                name,
                flags=re.I,
            ).strip()

            # 兼容旧式三列表（Holding / Fair value / % of net assets），
            # 该格式没有国家和类型，但仍是原文持仓证据。
            if len(cells) >= 3 and len(cells) < 5 and not current_holding_type:
                fallback_percentage = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*%", cells[-1])
                # HTML 表格常把百分号只放在列标题，行值为裸数字；
                # 此时只接受最后一个数值单元格，避免把市值误当比例。
                if fallback_percentage is None and table_percent_header:
                    fallback_percentage = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*", cells[-1])
                if (
                    fallback_percentage
                    and name
                    and not re.fullmatch(r"\d{1,2}\s+\w+\s+\d{4}", name)
                    and not re.search(
                        r"total investments|total (?:unlisted|listed)(?:\s*/\s*quoted)?\s+investment(?:s)?|net assets|other net assets|"
                        r"daily liquid assets|weekly liquid assets|total investments,? at cost",
                        name,
                        re.I,
                    )
                ):
                    holding_type = table_default_holding_type
                    portfolio_holdings.append({
                        "name": name,
                        "weight_percent": float(fallback_percentage.group(1)),
                        # Holdings/Nominal Value、Market Value、% of net asset
                        # 三个数值列的顺序不能按旧式两列表处理。
                        "holding_quantity": _cell_number(cells[1]),
                        "market_value": _cell_number(cells[2]) if len(cells) >= 4 else _cell_number(cells[1]),
                        "registration_place": None,
                        "holding_type": holding_type,
                    })
                    if holding_type:
                        portfolio_category_totals[holding_type] = (
                            portfolio_category_totals.get(holding_type, 0.0)
                            + float(fallback_percentage.group(1))
                        )
                continue
            if len(cells) < 5 or not current_holding_type:
                continue
            percentage_match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*%", cells[-1])
            if not percentage_match:
                continue
            # 小计行的第一列为空，分页重复行也可能只有数值列；这两类不
            # 是可展示的证券持仓。
            if not name or re.search(
                r"^(?:Holding|Fair value|Accrued interest|Total investments|Other net assets|Net assets|Daily liquid assets|Weekly liquid assets)$",
                name,
                re.I,
            ):
                continue
            if re.search(r"(?:Net assets|Total investments|Total investment portfolio|Other net assets|Daily liquid assets|Weekly liquid assets)", name, re.I):
                continue
            if re.fullmatch(r"\d{1,2}\s+\w+\s+\d{4}", name):
                continue
            holding_quantity = _cell_number(cells[1])
            market_value = _cell_number(cells[2])
            if holding_quantity is None and market_value is None:
                continue
            portfolio_holdings.append({
                "name": name,
                "weight_percent": float(percentage_match.group(1)),
                "holding_quantity": holding_quantity,
                "market_value": market_value,
                "registration_place": current_registration,
                "holding_type": current_holding_type,
            })
            if re.search(r"(?:fixed\s+income|debt|bond)", current_holding_type or "", re.I):
                category = "Debt securities"
            elif re.search(r"(?:money\s+market|collective investment)", current_holding_type or "", re.I):
                category = "Money market funds"
            else:
                category = None
            if category:
                portfolio_category_totals[category] = portfolio_category_totals.get(category, 0.0) + float(percentage_match.group(1))

    # 部分管理人另有“Statement of movements in portfolio holdings”汇总表，
    # 其表头为“% of Net Assets”，没有持仓金额列。该表是 BNW892 半年报的
    # 资产结构证据，按 2026 列（第一列数值）累计债券/货币基金比例。
    summary_category_totals: dict[str, float] = {}
    for table_match in re.finditer(r"<table[^>]*>.*?</table>", text or "", flags=re.IGNORECASE | re.DOTALL):
        if portfolio_start < 0 or table_match.start() < portfolio_start:
            continue
        table_html = table_match.group(0)
        if not re.search(r"%\s*of\s+Net\s+Assets", table_html, flags=re.IGNORECASE):
            continue
        current_summary_category: str | None = None
        for row_match in re.finditer(r"<tr[^>]*>(.*?)</tr>", table_html, flags=re.IGNORECASE | re.DOTALL):
            cells = [
                re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", cell))).strip()
                for cell in re.findall(r"<td[^>]*>(.*?)</td>", row_match.group(1), flags=re.IGNORECASE | re.DOTALL)
            ]
            if not cells:
                continue
            label = " ".join(cells).strip()
            if re.match(r"^debt\s+securities\b", label, re.I):
                current_summary_category = "Debt securities"
                continue
            if re.match(r"^money\s+market\s*funds?\b", label, re.I):
                current_summary_category = "Money market funds"
                continue
            if re.search(r"total\s+investments", label, re.I):
                percentages = re.findall(r"([0-9]+(?:\.[0-9]+)?)\s*%", label)
                if percentages:
                    portfolio_total_investments = float(percentages[0])
                current_summary_category = None
                continue
            if re.search(r"other\s+net\s+assets", label, re.I):
                percentages = re.findall(r"([0-9]+(?:\.[0-9]+)?)\s*%", label)
                if percentages:
                    portfolio_other_net_assets = float(percentages[0])
                current_summary_category = None
                continue
            if current_summary_category is None or len(cells) < 2:
                continue
            # 第一列是 2026 年数据；若该单元格为“■”等非数字符号，
            # 第二列才是 2025 年数据，不能误计入本期结构。
            current_value = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*%", cells[1])
            if current_value:
                summary_category_totals[current_summary_category] = (
                    summary_category_totals.get(current_summary_category, 0.0)
                    + float(current_value.group(1))
                )

    if summary_category_totals:
        portfolio_category_totals = summary_category_totals

    # MinerU 对部分英文半年报会把一条持仓拆成多行，导致上面的严格五列
    # 解析拿不到足够的前十大。保留一个仅用于名称/比例的兼容回退，仍以
    # 原文金额列和百分比作为证据；能从分组状态识别出的注册地/类型继续
    # 使用主解析结果。
    if len(portfolio_holdings) < 10:
        for row_match in re.finditer(r"<tr[^>]*>(.*?)</tr>", text or "", flags=re.IGNORECASE | re.DOTALL):
            if portfolio_start < 0 or row_match.start() < portfolio_start:
                continue
            row_html = row_match.group(1)
            cells = [
                re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", cell))).strip()
                for cell in re.findall(r"<td[^>]*>(.*?)</td>", row_html, flags=re.IGNORECASE | re.DOTALL)
            ]
            if not cells:
                continue
            percentage_match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*%", " ".join(cells[-2:]))
            if not percentage_match:
                continue
            name = next((cell for cell in cells[:-1] if re.search(r"[A-Za-z]", cell)), "")
            if not name or re.fullmatch(r"\d{1,2}\s+\w+\s+\d{4}", name):
                continue
            if re.search(r"(?:Holding|Fair value|Accrued interest|Total investments|Total investment portfolio|Other net assets|Net assets|Daily liquid assets|Weekly liquid assets)", name, re.I):
                continue
            if sum(bool(re.search(r"\d", cell)) for cell in cells[:-1]) < 2:
                continue
            if re.fullmatch(r"[0-9,.() HKD]+", name, re.I):
                continue
            portfolio_holdings.append({"name": name, "weight_percent": float(percentage_match.group(1))})

    # 同一证券可能因分页/合计行出现两次；按名称保留比例更高的明细，
    # 同时保留与该比例对应的份额、注册地和类型。先用完整明细参与资产
    # 类别汇总，再按产品页面契约只向 top_holdings 暴露前十大。
    merged_holdings: dict[str, dict[str, Any]] = {}
    for item in portfolio_holdings:
        key = str(item["name"]).strip()
        previous = merged_holdings.get(key)
        if previous is None or float(item["weight_percent"]) > float(previous["weight_percent"]):
            merged_holdings[key] = item
    if merged_holdings:
        facts["top_holdings"] = [
            item
            for item in sorted(merged_holdings.values(), key=lambda item: float(item["weight_percent"]), reverse=True)[:10]
        ]
    # 半年报投资组合表通常没有“资产类别分布”图，而是用分类标题和
    # ``% of net assets`` 明细列表达。将逐笔持仓比例按固定收益/货币基金
    # 分类汇总，并补上 Other net assets，供详情页饼图及股债比使用。
    if portfolio_total_investments is not None and portfolio_category_totals:
        non_debt = sum(
            value for label, value in portfolio_category_totals.items()
            if label != "Debt securities"
        )
        if non_debt > 0 and portfolio_total_investments >= non_debt:
            portfolio_category_totals["Debt securities"] = round(
                portfolio_total_investments - non_debt, 6
            )
    if portfolio_category_totals:
        allocation = [
            {"type": label, "weight_percent": round(weight, 6)}
            for label, weight in portfolio_category_totals.items()
            if weight > 0
        ]
        if portfolio_other_net_assets is not None:
            allocation.append({"type": "Other net assets", "weight_percent": portfolio_other_net_assets})
        facts["asset_type_allocation"] = allocation

    # 半年报投资组合表常用英文分类标题，而不是月报里的“资产类别分布”
    # 图表。按第一张目标组合表的分类行累计比例，补出债务证券、货币市场
    # 基金和其他净资产，供详情页的投资组合结构饼图使用。该解析只读取
    # 组合表的首个基金段，避免把同一份多基金半年报后续子基金混入目标基金。
    portfolio_heading = re.search(r"##\s+INVESTMENT PORTFOLIO", text, flags=re.IGNORECASE)
    if portfolio_heading:
        portfolio_start = portfolio_heading.start()
        portfolio_chunks = re.split(
            r"(?=##\s+INVESTMENT PORTFOLIO)",
            text[portfolio_start:],
            flags=re.IGNORECASE,
        )
        first_chunk = next((chunk for chunk in portfolio_chunks if chunk.strip()), "")
        fund_marker_match = re.search(
            r"([A-Za-z]+\s+Money Market Fund)",
            first_chunk,
            flags=re.IGNORECASE,
        )
        fund_marker = fund_marker_match.group(1).casefold() if fund_marker_match else None
        selected_chunks = [first_chunk]
        first_index = portfolio_chunks.index(first_chunk) if first_chunk else -1
        for chunk in portfolio_chunks[first_index + 1 :]:
            prefix = chunk[:900].casefold()
            if "continued" not in prefix:
                break
            if fund_marker and fund_marker in prefix:
                selected_chunks.append(chunk)
            else:
                break
        portfolio_section = "".join(selected_chunks)
        asset_totals: dict[str, float] = {}
        current_bucket: str | None = None
        for row_html in re.finditer(
            r"<tr[^>]*>(.*?)</tr>",
            portfolio_section,
            flags=re.IGNORECASE | re.DOTALL,
        ):
            cells = [
                re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", cell))).strip()
                for cell in re.findall(r"<td[^>]*>(.*?)</td>", row_html.group(1), flags=re.IGNORECASE | re.DOTALL)
            ]
            if not cells:
                continue
            row_label = " ".join(cells).strip()
            row_lower = row_label.casefold()
            if len(cells) == 1 and re.fullmatch(
                r"debt securities(?:\s*\(continued\))?",
                cells[0],
                flags=re.IGNORECASE,
            ):
                current_bucket = "Debt securities"
                continue
            if len(cells) == 1 and re.fullmatch(
                r"mutual fund(?:s)?(?:\s*\(continued\))?",
                cells[0],
                flags=re.IGNORECASE,
            ):
                current_bucket = "Money market funds"
                continue
            other_match = re.search(r"other net assets.*?([0-9]+(?:\.[0-9]+)?)\s*%", row_label, re.I)
            if other_match:
                asset_totals["Other net assets"] = float(other_match.group(1))
                current_bucket = None
                continue
            if re.search(r"total investments|total net assets", row_lower):
                current_bucket = None
                continue
            if current_bucket is None:
                continue
            percentage_matches = re.findall(r"([0-9]+(?:\.[0-9]+)?)\s*%", row_label)
            if not percentage_matches:
                continue
            name = cells[0] if cells else ""
            if not name or re.search(r"holdings|fair value|nominal value|as at", name, re.I):
                continue
            # 证券名称通常也含票面利率，最后一个百分比才是“% of Net
            # Assets”列，不能取行内第一个百分比。
            asset_totals[current_bucket] = asset_totals.get(current_bucket, 0.0) + float(percentage_matches[-1])
        if asset_totals and len(asset_totals) >= 2:
            facts["asset_type_allocation"] = [
                {"type": label, "weight_percent": round(weight, 6)}
                for label, weight in asset_totals.items()
                if weight > 0
            ]

    # MinerU 对部分月报会保留 HTML 表格；按“标签单元格 + 数值单元格”
    # 配对，比在整篇正文中寻找最近数字更可靠，可避免把历史表现误当成 YTM。
    rows: list[tuple[list[str], int]] = []
    holdings_heading = re.search(
        r"(?:五大主要投資|五大主要投资|前五大持倉|前五大持仓|前十大持倉|前十大持仓|top\s*(?:5|five)?\s*holdings|portfolio\s+holdings)",
        text or "",
        flags=re.IGNORECASE,
    )
    # “portfolio holdings” 也会出现在半年报目录/组合表标题中；两列表
    # 的通用月报回退只能在明确的“五大/前十大/Top holdings”区段启用，
    # 否则会把半年报中的普通财务表行误收为持仓。
    monthly_holdings_heading = re.search(
        r"(?:五大主要投資|五大主要投资|前五大持倉|前五大持仓|前十大持倉|前十大持仓|top\s*(?:5|five)?\s*holdings)",
        text or "",
        flags=re.IGNORECASE,
    )
    holdings_end = re.search(
        r"(?:投資組合特點|投资组合特点|基金表現圖|基金表现图|基金表現|基金表现)",
        text[holdings_heading.end() :] if holdings_heading else "",
        flags=re.IGNORECASE,
    )
    holdings_start = holdings_heading.end() if holdings_heading else -1
    holdings_stop = (
        holdings_start + holdings_end.start()
        if holdings_heading and holdings_end
        else len(text or "")
    )
    for row_match in re.finditer(
        r"<tr[^>]*>(.*?)</tr>",
        text or "",
        flags=re.IGNORECASE | re.DOTALL,
    ):
        row_html = row_match.group(1)
        cells = [re.sub(r"<[^>]+>", " ", cell) for cell in re.findall(r"<td[^>]*>(.*?)</td>", row_html, flags=re.IGNORECASE | re.DOTALL)]
        cells = [re.sub(r"\s+", " ", html.unescape(cell)).strip() for cell in cells]
        # 货币基金的“久期分布”在部分 MinerU 输出中被压成单个单元格，
        # 例如 ``<td>隔夜 17.81%</td>``，不能只依赖两列标签/数值配对。
        if len(cells) >= 2:
            rows.append((cells, row_match.start()))
        elif len(cells) == 1:
            rows.append((cells, row_match.start()))
    duration_labels = (
        "隔夜", "兩日至一星期", "两日至一星期", "一星期至一個月", "一星期至一个月",
        "一至三個月", "一至三个月", "三至六個月", "三至六个月", "六個月以上", "六个月以上",
    )
    asset_labels_native = (
        "其他貨幣市場工具", "其他货币市场工具", "存款憑證", "存款凭证", "定期存款", "现金及其他", "現金及其他",
    )
    for cells, row_position in rows:
        label = cells[0]
        # 单元格内同时包含标签和百分比时，优先从整行提取百分比。
        inline_match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*%", label)
        if len(cells) == 1 and inline_match:
            for token in duration_labels:
                if label.startswith(token):
                    facts["duration_distribution"].append({"bucket": token, "weight_percent": float(inline_match.group(1)), "value": float(inline_match.group(1)), "unit": "percent", "measure_type": "allocation_percent"})
                    break
            # BOCHK monthly reports render the “五大主要投资” table as one
            # cell per row: name + percentage.  Preserve the full security
            # label and pair it with the row percentage.
            if holdings_start >= 0 and holdings_start <= row_position <= holdings_stop:
                percent_matches = list(re.finditer(r"([0-9]+(?:\.[0-9]+)?)\s*%", label))
                weight_match = percent_matches[-1] if percent_matches else inline_match
                holding_name = label[: weight_match.start()].strip(" ：:，,;；")
                if holding_name and not re.search(
                    r"^(?:資料來源|资料来源|投資組合特點|投资组合特点)$",
                    holding_name,
                    flags=re.IGNORECASE,
                ):
                    facts["top_holdings"].append({
                        "name": holding_name,
                        "weight_percent": float(weight_match.group(1)),
                    })
            continue
        # 广发等月报的前五大表为三列：英文名称、中文名称/到期日、比例。
        # 旧逻辑只读取第二列，遇到日期后会把整行丢弃。
        if (
            len(cells) >= 3
            and monthly_holdings_heading is not None
            and holdings_start >= 0
            and holdings_start <= row_position <= holdings_stop
        ):
            weight_match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*%", cells[-1])
            label_text = " ".join(cells[:-1]).strip()
            if weight_match and label_text and not re.search(
                r"^(?:TOTAL|總計|总计|前五大持倉|前五大持仓)\b",
                label_text,
                flags=re.IGNORECASE,
            ):
                facts["top_holdings"].append({
                    "name": label_text,
                    "weight_percent": float(weight_match.group(1)),
                })
            continue
        if len(cells) < 2:
            continue
        number_match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*%?", cells[1])
        value = float(number_match.group(1)) if number_match else None
        if value is None:
            continue
        # BOCHK 等月报的“主要投资”表是“证券描述 + 比例”两列，
        # 证券描述未必包含 deposit/bond 等英文关键词；只要行位于明确
        # 的“五大主要投资”区段，就按原文保存，避免整张表被丢弃。
        if (
            monthly_holdings_heading is not None
            and holdings_start >= 0
            and holdings_start <= row_position <= holdings_stop
            and not re.search(
                r"^(?:五大主要投資|五大主要投资|持倉|持仓|Holding|資料來源|资料来源)$",
                label,
                flags=re.IGNORECASE,
            )
        ):
            facts["top_holdings"].append({"name": label, "weight_percent": value})
        elif any(token in label for token in ("加權平均到期天數", "加权平均到期天数", "weighted average maturity")):
            facts["weighted_average_maturity_days"] = value
        elif any(token in label for token in ("加權平均到期收益率", "加权平均到期收益率", "YTM", "yield to maturity")):
            facts["portfolio_ytm_percent"] = value
        elif any(label == token for token in duration_labels) and "%" in cells[0] + cells[1]:
            facts["duration_distribution"].append({"bucket": label, "weight_percent": value, "value": value, "unit": "percent", "measure_type": "allocation_percent"})
        elif any(label == token for token in asset_labels_native) and "%" in cells[0] + cells[1]:
            facts["asset_type_allocation"].append({"type": label, "weight_percent": value})
        elif re.search(r"(?:deposit|存款|存款憑證|存款凭证|treasury|commercial paper|repo)", label, re.IGNORECASE) and "%" in cells[0] + cells[1]:
            facts["top_holdings"].append({"name": label, "weight_percent": value})

    # MinerU 不可用或命中 native-pdf-text 缓存时，广发月报会只剩 PDF
    # 原生文本层；该层按阅读顺序把前五大定期存款输出为独立行，未保留
    # HTML 表格。按“名称 + 到期日 + 行比例”识别，仍以原文为准。
    if not facts["top_holdings"]:
        native_text = plain_text
        native_marker = re.search(r"\[PDF_TEXT_LAYER\]", native_text, re.IGNORECASE)
        if native_marker:
            native_text = native_text[native_marker.end() :]
            native_text = re.split(r"\[IMAGE_OCR\]", native_text, maxsplit=1)[0]
        for match in re.finditer(
            r"(?P<name>(?:Time\s+deposit|定期\s*存款)[^\n]{0,120}?"
            r"20\d{2}[/-]\d{1,2}[/-]\d{1,2})\s+"
            r"(?P<weight>[0-9]+(?:\.[0-9]+)?)\s*%",
            native_text,
            re.IGNORECASE,
        ):
            name = re.sub(r"\s+", " ", match.group("name")).strip(" ：:，,;；")
            facts["top_holdings"].append({
                "name": name,
                "weight_percent": float(match.group("weight")),
            })
            if len(facts["top_holdings"]) >= 5:
                break

    # 上面的组合表解析可能覆盖月报专用的持仓列表；在所有通用路径完成
    # 后再用“前五大固定收益工具持仓”锚点回填，确保汇添富这类五行文本
    # 表完整保留（尤其是第五行在 Markdown 图片投影中常缺失比例）。
    fixed_income_heading = re.search(
        r"前五大固定收益工具持[倉仓]?(?:（|\()[^\n]{0,80}",
        plain_text,
        flags=re.IGNORECASE,
    )
    if fixed_income_heading:
        fixed_section = plain_text[fixed_income_heading.end():]
        fixed_section = re.split(
            r"数据来源|資料來源|基金经理|基金經理|组合特点|組合特點",
            fixed_section,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
        fixed_holdings: list[dict[str, Any]] = []
        for line in fixed_section.splitlines():
            line = re.sub(r"\s+", " ", line).strip()
            match = re.match(
                r"^(.+?),\s*([0-9]+(?:\.[0-9]+)?)\s*%,\s*"
                r"([0-3]?\d/[01]?\d/\d{4})\s+([0-9]+(?:\.[0-9]+)?)\s*%$",
                line,
            )
            if match:
                fixed_holdings.append({
                    "name": match.group(1).strip(),
                    "weight_percent": float(match.group(4)),
                    "coupon_rate": match.group(2),
                    "maturity_date": match.group(3),
                })
        if fixed_holdings:
            facts["top_holdings"] = fixed_holdings[:10]

    # English semi-annual statements can have no current investment rows because
    # all instruments matured during the period. Preserve disclosed time
    # deposits as a holding and carry the native liquidity percentages forward.
    facts["daily_liquid_assets_percent"] = statement_facts.get("daily_liquid_assets_percent")
    facts["weekly_liquid_assets_percent"] = statement_facts.get("weekly_liquid_assets_percent")
    if statement_facts.get("statement_holdings") and not facts["top_holdings"]:
        # Use statement-level deposits only when the report has no native
        # current portfolio rows. Reports such as BRG187/BNW892 already have
        # a genuine investment-portfolio table; appending balance-sheet cash
        # or deposits there would double count the composition.
        facts["top_holdings"].extend(statement_facts["statement_holdings"])
        deposit_weight = statement_facts.get("time_deposit_weight_percent")
        if deposit_weight is not None and not facts["asset_type_allocation"] and not any(
            str(item.get("type", "")).casefold() == "time deposits"
            for item in facts["asset_type_allocation"]
            if isinstance(item, dict)
        ):
            facts["asset_type_allocation"].append({
                "type": "Time deposits",
                "weight_percent": float(deposit_weight),
            })

    # full MinerU 上下文同时包含 Markdown 和 content_list 投影时，同一张表
    # 可能出现两次；按“标签 + 数值”去重，避免合并结果把前五大持仓、期限
    # 分布和资产类别重复计入。
    for field, key_name in (
        ("duration_distribution", "bucket"),
        ("asset_type_allocation", "type"),
        ("top_holdings", "name"),
    ):
        unique: list[dict[str, Any]] = []
        seen: set[tuple[str, float | None]] = set()
        for item in facts[field]:
            if not isinstance(item, dict):
                continue
            label = re.sub(r"\s+", " ", str(item.get(key_name) or "")).strip()
            number = item.get("weight_percent")
            try:
                numeric = float(number) if number is not None else None
            except (TypeError, ValueError):
                numeric = None
            # 同一发行人/名称可能在前五大表中出现多次（不同到期日或
            # 票面利率），不能仅按名称+比例去重；图表分布项则保持原有
            # 的名称+比例去重策略。
            if field == "top_holdings":
                marker = (
                    label.casefold(),
                    numeric,
                    str(item.get("coupon_rate") or "").strip(),
                    str(item.get("maturity_date") or "").strip(),
                )
            else:
                marker = (label.casefold(), numeric)
            if marker in seen:
                continue
            seen.add(marker)
            unique.append(item)
        facts[field] = unique
    return facts


def _apply_verified_image_chart_overrides(
    facts: dict[str, Any],
    pdf_path: Path | None,
) -> dict[str, Any]:
    """回填已人工核验、但当前 PDF 仅以图片呈现的图表。

    BTM337 的 2026-09 月报把“資產分佈/存續期分佈”作为矢量图嵌入，
    MinerU 没有输出图像资源，系统 Vision OCR 在无中文模型的机器上也可能
    返回 nilError。这里按 PDF SHA 做窄范围兜底，避免把另一只基金的图表
    数字串入当前记录；后续 OCR 可用时，正常解析结果会优先保留。
    """

    if not pdf_path or not Path(pdf_path).is_file():
        return facts
    try:
        digest = hashlib.sha256(Path(pdf_path).read_bytes()).hexdigest()
    except OSError:
        return facts
    chart_overrides = {
        # BOG055 / 中金美元货币市场 ETF 2026-09 上市类别。图表为 PDF
        # 内嵌图片，MinerU 只保留图片而没有文字层；数值按原图核验并以
        # PDF SHA 限定，避免同类基金图表串值。
        "2fd8f6384a6ee89efeff52fcce72621b17a9ced6fd9153b8de1603eff61f9f42": {
            "duration_distribution": [
                {"bucket": "0-7 天", "weight_percent": 18.86, "value": 18.86, "unit": "percent", "measure_type": "allocation_percent"},
                {"bucket": "8-14 天", "weight_percent": 6.36, "value": 6.36, "unit": "percent", "measure_type": "allocation_percent"},
                {"bucket": "15-30 天", "weight_percent": 10.79, "value": 10.79, "unit": "percent", "measure_type": "allocation_percent"},
                {"bucket": "31-60 天", "weight_percent": 10.29, "value": 10.29, "unit": "percent", "measure_type": "allocation_percent"},
                {"bucket": "61-90 天", "weight_percent": 16.49, "value": 16.49, "unit": "percent", "measure_type": "allocation_percent"},
                {"bucket": "91-180 天", "weight_percent": 18.46, "value": 18.46, "unit": "percent", "measure_type": "allocation_percent"},
                {"bucket": "181-360 天", "weight_percent": 18.76, "value": 18.76, "unit": "percent", "measure_type": "allocation_percent"},
            ],
        },
        # BTM337 / 中银香港全天候美元货币市场基金，2026-09 月报。
        "a08073fac79fe09cd6fcaa5e389d7684ae5ea24de50a36391b8c99a72a4adea6": {
            "asset_type_allocation": [
                {"type": "債券", "weight_percent": 1.7},
                {"type": "定期存款", "weight_percent": 77.2},
                {"type": "現金及等值", "weight_percent": 21.1},
            ],
            "duration_distribution": [
                {"bucket": "1個月內", "weight_percent": 17.1, "value": 17.1, "unit": "percent", "measure_type": "allocation_percent"},
                {"bucket": "1-2個月", "weight_percent": 20.1, "value": 20.1, "unit": "percent", "measure_type": "allocation_percent"},
                {"bucket": "2-3個月", "weight_percent": 15.0, "value": 15.0, "unit": "percent", "measure_type": "allocation_percent"},
                {"bucket": "3-6個月", "weight_percent": 23.9, "value": 23.9, "unit": "percent", "measure_type": "allocation_percent"},
                {"bucket": "超過6個月", "weight_percent": 2.8, "value": 2.8, "unit": "percent", "measure_type": "allocation_percent"},
                {"bucket": "現金及其他流動資產", "weight_percent": 21.1, "value": 21.1, "unit": "percent", "measure_type": "allocation_percent"},
            ],
        },
        # BSV019 / 中银香港全天候港元货币市场基金，2026-09 月报。
        # 图表为 PDF 内嵌图片，MinerU 当前没有输出图像文字层；数值已按
        # 原图人工核验，并通过 SHA 限定到该文件，避免跨基金串值。
        "9fca18f76e4c69ce47e66ad081243aaca47ce255e5676481d215de8a2494a8d7": {
            "asset_type_allocation": [
                {"type": "定期存款", "weight_percent": 84.3},
                {"type": "現金及等值", "weight_percent": 15.7},
            ],
            "duration_distribution": [
                {"bucket": "1個月內", "weight_percent": 27.7, "value": 27.7, "unit": "percent", "measure_type": "allocation_percent"},
                {"bucket": "1-2個月", "weight_percent": 25.6, "value": 25.6, "unit": "percent", "measure_type": "allocation_percent"},
                {"bucket": "2-3個月", "weight_percent": 24.4, "value": 24.4, "unit": "percent", "measure_type": "allocation_percent"},
                {"bucket": "3-6個月", "weight_percent": 0.7, "value": 0.7, "unit": "percent", "measure_type": "allocation_percent"},
                {"bucket": "超過6個月", "weight_percent": 6.0, "value": 6.0, "unit": "percent", "measure_type": "allocation_percent"},
                {"bucket": "現金及其他流動資產", "weight_percent": 15.6, "value": 15.6, "unit": "percent", "measure_type": "allocation_percent"},
            ],
        },
    }
    override = chart_overrides.get(digest)
    if not override:
        return facts
    for field, value in override.items():
        if not facts.get(field):
            facts[field] = value
    return facts


def _extract_money_market_long_text(text: str) -> dict[str, str | None]:
    """抽取月报中有明确标题的长文本说明。

    MinerU 文本通常保留 ``## 投資目標``/``## 投資策略`` 标题，直接按标题
    截取可避免把风险披露或投资范围描述误当成策略。YTM 说明则从带有
    ``YTM`` 的脚注中截取到“不能代表本子基金的业绩”为止。没有明确原文
    锚点时返回 null，绝不由模型或常识补写。
    """

    source = str(text or "")
    # MinerU 的上下文模式用 [BLOCK n] 标记替代换行；恢复为段落边界后，
    # “投资策略”不会把后续基金详情、表现和派息说明串成一张长卡片。
    source = re.sub(r"\s*\[(?:BLOCK|PAGE)\s+\d+\]\s*", "\n", source)
    result: dict[str, str | None] = {
        "investment_objective": None,
        "investment_strategy": None,
        "portfolio_ytm_note": None,
    }

    def section(headings: tuple[str, ...]) -> str | None:
        stop_headings = (
            "基金基本信息", "基金基本资讯", "基金资料", "基金資料", "基金详情", "基金詳情", "投资策略", "投資策略", "投资组合特点", "投資組合特點",
            "组合统计数据", "組合統計數據", "基金表现", "基金表現", "单位类别信息", "單位類別信息", "單位類別資訊",
            "累积表现", "累積表現", "年度表现", "年度表現", "类别资料", "類別資料",
            "主要资料", "主要資料", "主要信息", "主要資訊", "类别信息", "類別資訊",
            "组合特点", "組合特點", "组合配置", "組合配置", "风险", "風險", "免责声明", "免責聲明",
            "fund information", "fund documents", "performance", "portfolio characteristics",
            "investment objective", "investment strategy", "top 5 holdings", "top 10 holdings",
        )
        lines = source.splitlines()
        start = None
        for index, line in enumerate(lines):
            cleaned = re.sub(r"^[#\s]+", "", line).strip().rstrip(":：")
            is_heading = bool(re.match(r"^\s*#{1,6}\s+", line)) or (
                len(cleaned) <= 120
                and not re.search(r"[。.!！?？]", cleaned)
                and any(heading.casefold() in cleaned.casefold() for heading in headings + stop_headings)
            )
            normalized_heading = re.sub(r"\s+", " ", cleaned).casefold()
            if is_heading and any(heading.casefold() in normalized_heading for heading in headings):
                start = index + 1
                break
        if start is None:
            return None
        collected: list[str] = []
        for line in lines[start:]:
            # 下一个 Markdown 标题表示该章节结束；MinerU 的纯文本章节
            # 也会在下一标题处换行。
            if re.match(r"^\s*#{1,6}\s+", line):
                break
            stripped = line.strip()
            normalized_heading = re.sub(r"^[#\s]+", "", stripped).rstrip(":：").casefold()
            is_heading = bool(re.match(r"^\s*#{1,6}\s+", line)) or (
                len(normalized_heading) <= 120
                and not re.search(r"[。.!！?？]", normalized_heading)
            )
            if is_heading and any(
                heading.casefold() in normalized_heading for heading in stop_headings
            ):
                break
            if not stripped or stripped.startswith("<!--") or stripped.endswith("-->"):
                continue
            if re.match(r"^(?:數據來源|数据来源)\s*[:：]", stripped):
                continue
            # 只保留正文，不把图像占位符、表格行带入说明卡片。
            if stripped.startswith("<table") or stripped.startswith("</table") or stripped.startswith("<tr"):
                continue
            if stripped.startswith("|"):
                continue
            collected.append(stripped)
        value = re.sub(r"\s+", " ", " ".join(collected)).strip()
        return value or None

    result["investment_objective"] = section(("投资目标", "投資目標", "Investment objective"))
    result["investment_strategy"] = section(("投资策略", "投資策略", "Investment strategy"))
    compact_for_sections = re.sub(r"\s+", " ", source).strip()
    # 某些 MinerU/native 文本把双语标题放到正文后面，或把标题识别成
    # 页眉，导致按章节截取拿不到正文。此时用“正文句式 + 下一个基金资料
    # 标题”作为安全回退，仍要求报告中明确出现 objective/strategy 语义。
    objective_needs_fallback = (
        not result["investment_objective"]
        or len(result["investment_objective"]) < 30
        or bool(re.search(r"GF\s+Open[- ]ended|Important\s+Information|Tel\s+电话|FUND\s+INFORMATION", result["investment_objective"], re.I))
    )
    if objective_needs_fallback:
        objective_inline = re.search(
            r"((?:The\s+investment\s+objective\s+of\s+the\s+Sub-Fund\s+is|"
            r"子基金的投资目标是|子基金的投資目標是).{20,1200}?)(?=\s*(?:GF\s+Open[- ]ended|Important\s+Information|重要\s+資訊|FUND\s+INFORMATION|基金资料|基金資料|##))",
            compact_for_sections,
            re.IGNORECASE,
        )
        if objective_inline:
            result["investment_objective"] = objective_inline.group(1).strip()
    strategy_needs_fallback = (
        not result["investment_strategy"]
        or len(result["investment_strategy"]) < 30
        or bool(re.search(r"GF\s+Open[- ]ended|Important\s+Information|Tel\s+电话|FUND\s+INFORMATION", result["investment_strategy"], re.I))
    )
    if strategy_needs_fallback:
        strategy_inline = re.search(
            r"((?:The\s+investment\s+strategy\s+of\s+the\s+Sub-Fund\s+is|"
            r"子基金的投资策略是|子基金的投資策略是).{20,1600}?)(?=\s*(?:FUND\s+INFORMATION|基金资料|基金資料|##))",
            compact_for_sections,
            re.IGNORECASE,
        )
        if strategy_inline:
            result["investment_strategy"] = strategy_inline.group(1).strip()
    if result["investment_objective"] is None and result["investment_strategy"] is None:
        combined = section(("投资目标及策略", "投資目標及策略", "Investment objective and strategy"))
        if combined:
            # 招银国际月报采用一个合并标题；按句号拆分为两个可读卡片，
            # 保留原文语种和原始措辞，不进行翻译或改写。
            sentences = [item.strip() for item in re.split(r"(?<=[。.!！?？])\s*", combined) if item.strip()]
            result["investment_objective"] = sentences[0] if sentences else combined
            result["investment_strategy"] = " ".join(sentences[1:]) or combined

    # 先把换行折叠，适配原生 PDF 文本将脚注拆成多行的情况。保留
    # 原文标点和语言，只去掉脚注前的星号。
    compact = re.sub(r"\s+", " ", source).strip()
    ytm = re.search(
        r"(?:[*＊]\s*)?"
        r"(到期收益率\s*[（(]\s*YTM\s*[）)]\s*是.*?"
        r"YTM\s*不能代表[^。.!！?？]*[。.!！?？])",
        compact,
        re.IGNORECASE,
    )
    if ytm:
        result["portfolio_ytm_note"] = ytm.group(1).strip()
    # 华夏月报使用编号脚注而不是“YTM 是……不能代表业绩”的长说明。
    # 只有在正文同时出现加权平均到期收益率/YTM 锚点时才接受，避免误收其他脚注。
    if result["portfolio_ytm_note"] is None and re.search(
        r"(?:加權平均到期收益率|加权平均到期收益率|YTM|yield\s+to\s+maturity)",
        compact,
        re.IGNORECASE,
    ):
        footnote = re.search(
            r"(?:^|\s)(?:1\s+)?(使用基金中[^。.!！?？]*[。.!！?？])",
            compact,
        )
        if footnote:
            result["portfolio_ytm_note"] = footnote.group(1).strip()
    # 广发双语月报的脚注有时把“加權”OCR 成“加模”，且不出现 YTM 缩写；
    # 仍以“加权平均到期收益率/不代表基金回报”这两个边界锚点截取原文，
    # 避免把整段风险披露误放入说明卡片。
    if result["portfolio_ytm_note"] is None:
        weighted_alias = (
            r"(?:加\s*[權权模]\s*平均\s*到期\s*收益率|"
            r"weighted\s+average\s+yield\s+to\s+m\s*a\s*turity)"
        )
        weighted_starts = list(re.finditer(weighted_alias, compact, re.IGNORECASE))
        for weighted_start in reversed(weighted_starts):
            tail = compact[weighted_start.start() : weighted_start.start() + 1800]
            weighted_end = re.search(
                r"(?:並不\s*代表|并不\s*代表|does\s+not\s+represent)[^。.!！?？\n]*[。.!！?？]",
                tail,
                re.IGNORECASE,
            )
            if weighted_end:
                result["portfolio_ytm_note"] = tail[: weighted_end.end()].strip()
                break
    if result["portfolio_ytm_note"] is None:
        footnote = re.search(
            r"(?:^|\s)1\s*到期收益率是.*?(?:並不代表基金的回報|并不代表基金的回报)[。.!！?？]",
            compact,
            re.IGNORECASE,
        )
        if footnote:
            result["portfolio_ytm_note"] = footnote.group(0).strip()
    return result


def _has_text_anchor(text: str, aliases: tuple[str, ...]) -> bool:
    normalized = re.sub(r"\s+", "", text or "").casefold()
    return any(re.sub(r"\s+", "", alias).casefold() in normalized for alias in aliases)


def _parse_money_percent(text: str, aliases: tuple[str, ...]) -> float | None:
    """读取货币基金标签后紧邻的百分比，避免跨段取数。"""

    # 半年报投资组合表通常只在列标题写“% of net asset”，行内的比例
    # 是裸数字（例如 Daily liquid assets | 3,347,928,233.01 | 20.28）。
    # 先按 HTML 行取最后一个数值单元格，避免把金额的小数尾部（190.31）
    # 当成比例。
    for row_match in re.finditer(r"<tr[^>]*>(.*?)</tr>", text or "", flags=re.IGNORECASE | re.DOTALL):
        cells = [
            re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", cell))).strip()
            for cell in re.findall(r"<td[^>]*>(.*?)</td>", row_match.group(1), flags=re.IGNORECASE | re.DOTALL)
        ]
        if len(cells) < 2:
            continue
        label = cells[0].casefold()
        if not any(str(alias).casefold() in label for alias in aliases):
            continue
        # 取末列；若同一单元格附带百分号也兼容。标题中可能有逗号，
        # 比例本身不能大于 100，作为最后一道防串值保护。
        value = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*%?\s*", cells[-1].replace(",", ""))
        if value:
            parsed = float(value.group(1))
            if 0 <= parsed <= 100:
                return parsed

    compact = re.sub(r"\s+", " ", text or "")
    for alias in aliases:
        match = re.search(re.escape(alias) + r".{0,100}?([0-9]+(?:\.[0-9]+)?)\s*%", compact, re.I)
        if match:
            parsed = float(match.group(1))
            if 0 <= parsed <= 100:
                return parsed
    return None


def _parse_money_days(text: str, aliases: tuple[str, ...]) -> float | None:
    """读取货币基金标签后紧邻的天数。"""

    compact = re.sub(r"\s+", " ", text or "")
    for alias in aliases:
        # 标题常见“加权平均有效期 (WAL) 5”脚注，实际数值随后才在
        # “加权平均有效期: 94.22天”出现；取该别名的最后一个带单位匹配，
        # 避免把 WAM 的 47.68 天误分配给 WAL。
        matches = list(re.finditer(
            re.escape(alias) + r".{0,120}?([0-9]+(?:\.[0-9]+)?)\s*(?:天|日|days?)",
            compact,
            re.I,
        ))
        if matches:
            return float(matches[-1].group(1))
    return None


def _parse_credit_rating_from_text(text: str) -> str | None:
    """只接受评级标签附近的评级 token，不接受模型自由填写的评级。"""

    compact = re.sub(r"\s+", " ", text or "")
    token = r"([A-Z]{2,4}(?:MF)?)"
    patterns = (
        rf"{token}\s*[“”\"']?\s*(?:貨幣市場基金|货币市场基金)?\s*(?:評級|评级)",
        rf"(?:平均信用評級|平均信用评级|Average credit rating|Average rating).{{0,30}}?{token}",
    )
    for pattern in patterns:
        match = re.search(pattern, compact, re.I)
        if match:
            value = normalize_rating(match.group(1))
            if value:
                return value
    return None


def _filter_items_by_native_text(items: Any, text: str, name_key: str) -> list[dict[str, Any]]:
    """只保留名称在当前 PDF 原文中出现过的模型列表项。"""

    if not isinstance(items, list):
        return []
    normalized = re.sub(r"\s+", "", text or "").casefold()
    kept: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = re.sub(r"\s+", "", str(item.get(name_key) or item.get("name") or item.get("region") or "")).casefold()
        if name and (name in normalized or len(name) >= 8 and name[:8] in normalized):
            kept.append(item)
    return kept


def _has_dividend_value_anchor(text: str) -> bool:
    """“派息”说明本身不算数值证据，必须紧邻金额或百分比。"""

    compact = re.sub(r"\s+", " ", text or "")
    return bool(re.search(
        r"(?:每单位派息|每單位派息|派息金额|派息金額|dividend per unit|dividend rate|dividend yield).{0,80}?[0-9]+(?:\.[0-9]+)?\s*%?",
        compact,
        re.I,
    ))


def _apply_money_market_text_facts(
    raw: dict[str, Any],
    pdf_text: str,
    category: str,
    pdf_path: Path | None = None,
) -> dict[str, Any]:
    """用原生 PDF 证据覆盖货币基金模型结果，防止模板复用值进入最终结果。"""

    if category not in {"money", "货币"}:
        return raw
    merged = dict(raw)
    evidence = dict(raw.get("field_evidence") or {}) if isinstance(raw.get("field_evidence"), dict) else {}
    # 原模型常把别的基金的引用一起带过来；这些字段后续只能由本地证据回填。
    for field in ("report_date", "portfolio_ytm_percent", "top_holdings", "duration_distribution", "geographic_allocation", "dividend"):
        evidence[field] = []
    merged["field_evidence"] = evidence
    facts = _parse_money_market_text_facts(pdf_text)
    facts = _apply_verified_image_chart_overrides(facts, pdf_path)
    long_text = _extract_money_market_long_text(pdf_text)
    for field, value in long_text.items():
        # 原文有明确锚点时覆盖模型输出；没有原文时清空潜在的跨模板值。
        merged[field] = value
    merged["inception_date"] = facts.get("inception_date")
    merged["listing_date"] = facts.get("listing_date")
    native_size = _parse_fund_size_from_text(pdf_text)
    # 规模也必须有当前报告的原文锚点。不能在易方达/其他管理人的
    # PowerPoint 月报中沿用 Qwen 上一份 HSBC 模板的规模，否则下载成功
    # 后会把错误规模带入数据库；没有原文规模时宁可留空进入 review。
    merged["fund_size_original"] = native_size
    native_report_date = _normalize_report_month(pdf_text)
    if native_report_date:
        merged["report_date"] = native_report_date
    native_ytm = facts.get("portfolio_ytm_percent") or _parse_metric_from_text(
        pdf_text,
        ["平均最低孳息率", "最低孳息率", "组合到期收益率", "組合到期收益率", "投资组合到期收益率", "投資組合到期收益率", "YTM", "Yield to maturity"],
        require_percent=True,
    )
    merged["portfolio_ytm_percent"] = native_ytm
    native_duration = _parse_metric_from_text(
        pdf_text,
        ["组合久期", "組合久期", "平均久期", "平均久期", "Duration", "Average duration"],
    )
    merged["portfolio_duration_years"] = native_duration
    native_rating = _parse_credit_rating_from_text(pdf_text)
    merged["credit_rating"] = native_rating
    if not native_rating:
        # 货币基金没有原文评级时，禁止沿用模型带入的评级分布，否则
        # 模型串台会在最终 median 推导阶段重新生成一个虚假评级。
        merged["credit_rating_distribution"] = []
    # 有图表标题但没有可配对数字时也必须清空模型候选，不能因为标题存在
    # 就把另一只基金的久期分布写入当前基金。
    merged["duration_distribution"] = facts["duration_distribution"]

    money = dict(raw.get("money_market_metrics") or {}) if isinstance(raw.get("money_market_metrics"), dict) else {}
    money["weighted_average_maturity_days"] = facts["weighted_average_maturity_days"]
    # 这些值只有在 PDF 存在明确标签时才允许模型填入；无标签值是典型跨模板污染。
    anchor_map = {
        "seven_day_yield_percent": ("七日年化收益率", "7-day yield", "seven day annualized yield"),
        "annualized_yield_percent": ("年化收益率", "annualized yield"),
        "weighted_average_life_days": (
            "加权平均有效期", "加權平均有效期", "平均有效期", "平均有效期",
            "加权平均存续期", "加權平均存續期", "平均存续期", "平均存續期",
            "weighted average life", "WAL",
        ),
        "daily_liquid_assets_percent": ("每日流动资产", "每日流動資產", "daily liquid assets", "daily liquid asset"),
        "weekly_liquid_assets_percent": ("每周流动资产", "每週流動資產", "weekly liquid assets", "weekly liquid asset"),
    }
    for field, aliases in anchor_map.items():
        if not _has_text_anchor(pdf_text, aliases):
            money[field] = None
    # 有明确标签时也优先采用原生文本的数字，不沿用 Qwen 的候选数字。
    money["seven_day_yield_percent"] = _parse_money_percent(pdf_text, ("七日年化收益率", "7-day yield", "seven day annualized yield"))
    money["annualized_yield_percent"] = _parse_money_percent(pdf_text, ("年化收益率", "annualized yield"))
    money["weighted_average_life_days"] = _parse_money_days(
        pdf_text,
        (
            "加权平均有效期", "加權平均有效期", "平均有效期", "平均有效期",
            "加权平均存续期", "加權平均存續期", "平均存续期", "平均存續期",
            "weighted average life", "WAL",
        ),
    )
    money["daily_liquid_assets_percent"] = (
        _parse_money_percent(pdf_text, ("每日流动资产", "每日流動資產", "daily liquid assets", "daily liquid asset"))
        or facts.get("daily_liquid_assets_percent")
    )
    money["weekly_liquid_assets_percent"] = (
        _parse_money_percent(pdf_text, ("每周流动资产", "每週流動資產", "weekly liquid assets", "weekly liquid asset"))
        or facts.get("weekly_liquid_assets_percent")
    )
    money["asset_type_allocation"] = facts["asset_type_allocation"]
    if facts.get("top_holdings"):
        merged["top_holdings"] = facts["top_holdings"]
        merged["top_holdings_total_percent"] = round(_distribution_total(facts["top_holdings"]), 4)
    if not _has_text_anchor(pdf_text, ("银行敞口", "銀行敞口", "bank exposure", "银行存款敞口")):
        money["bank_exposure"] = []
    merged["money_market_metrics"] = money
    # 部分月报不写“前十大持仓”标题，而是直接输出 Deposit/存款明细表；
    # 只要明细行本身带有名称和比例，就视为原生持仓证据，不能再被标题门控清空。
    has_native_holdings = bool(facts.get("top_holdings"))
    if has_native_holdings or _has_text_anchor(pdf_text, ("前十大持仓", "前十大持倉", "十大投资项目", "十大投資項目", "top holdings", "portfolio holdings")):
        merged["top_holdings"] = _filter_items_by_native_text(merged.get("top_holdings"), pdf_text, "name")
        merged["top_holdings_total_percent"] = round(_distribution_total(merged["top_holdings"]), 4) if merged["top_holdings"] else None
    else:
        merged["top_holdings"] = []
        merged["top_holdings_total_percent"] = None
    # 货币基金地域图表的原生文本经常把标签和值拆成两列；在没有专用
    # 配对解析器前不接受 Qwen 的单项“98.5%”模板值。
    merged["geographic_allocation"] = []
    if _has_text_anchor(pdf_text, ("银行敞口", "銀行敞口", "bank exposure", "银行存款敞口")):
        money["bank_exposure"] = _filter_items_by_native_text(money.get("bank_exposure"), pdf_text, "name")
    else:
        money["bank_exposure"] = []
    # 仅有“F 类-派息”并不等于报告披露派息金额/频率。
    if not _has_dividend_value_anchor(pdf_text):
        merged["dividend"] = None
    return merged


def _finalize(
    raw: dict[str, Any],
    pdf_path: Path,
    pdf_text: str = "",
    *,
    category: str | None = None,
) -> dict[str, Any]:
    # 货币基金的模型输出必须经过原生 PDF 证据门控；否则缺少图表配对时
    # Qwen 可能复用同类基金模板中的持仓、收益率或流动性数字。
    if category in {"money", "货币"} or isinstance(raw.get("money_market_metrics"), dict):
        raw = _apply_money_market_text_facts(raw, pdf_text, "money", pdf_path=pdf_path)
    report_contract = detect_report_contract(
        pdf_text,
        filename=pdf_path.name,
        category=category,
    )
    raw, correction_audit = correct_structured_record(
        raw,
        pdf_text,
        report_contract,
        category=category,
    )
    size_original = _effective_fund_size_original(raw.get("fund_size_original"), pdf_text)
    fund_size_hkd_100m = _compute_fund_size_hkd_100m(size_original)
    credit_rating = normalize_rating(raw.get("credit_rating")) or None
    if not credit_rating:
        credit_rating = derive_median_rating(_distribution_items(raw.get("credit_rating_distribution"))) or None
    holdings = _clean_weighted_items(raw.get("top_holdings"), name_key="name")
    holdings_total = round(_distribution_total(holdings), 4) if holdings else None
    duration_distribution = _clean_weighted_items(raw.get("duration_distribution"), name_key="bucket")
    geographic_allocation = _clean_weighted_items(raw.get("geographic_allocation"), name_key="region")
    dividend = raw.get("dividend")
    if not isinstance(dividend, dict):
        dividend = None
    else:
        currency = _normalize_currency(dividend.get("currency")) or dividend.get("currency") or None
        frequency = dividend.get("frequency") or None
        note = dividend.get("note") or None
        if note and any(token in str(note) for token in ["优先提取", "若没有", "字段规则", "必须在"]):
            note = None
        if currency == "USD" and frequency == "monthly":
            note = None
        elif currency == "USD" and frequency:
            note = note or f"未找到美元每月派息份额，改用美元{_frequency_label(frequency)}派息份额"
        elif currency:
            note = note or f"未找到美元每月派息份额，改用{currency}派息份额"
        dividend = {
            "share_class": dividend.get("share_class") or None,
            "currency": currency,
            "frequency": frequency,
            "dividend_per_unit": _number(dividend.get("dividend_per_unit")),
            "annualized_dividend_yield_percent": _number(dividend.get("annualized_dividend_yield_percent")),
            "note": note,
        }
    money_metrics = raw.get("money_market_metrics")
    if isinstance(money_metrics, dict):
        money_metrics = {
            "seven_day_yield_percent": _number(money_metrics.get("seven_day_yield_percent")),
            "annualized_yield_percent": _number(money_metrics.get("annualized_yield_percent")),
            "weighted_average_maturity_days": _number(money_metrics.get("weighted_average_maturity_days")),
            "weighted_average_life_days": _number(money_metrics.get("weighted_average_life_days")),
            "daily_liquid_assets_percent": _number(money_metrics.get("daily_liquid_assets_percent")),
            "weekly_liquid_assets_percent": _number(money_metrics.get("weekly_liquid_assets_percent")),
            "asset_type_allocation": _clean_weighted_items(money_metrics.get("asset_type_allocation"), name_key="type"),
            "bank_exposure": _clean_weighted_items(money_metrics.get("bank_exposure"), name_key="name"),
        }
    else:
        money_metrics = None
    disclosure_info = dict(raw.get("disclosure_info") or {}) if isinstance(raw.get("disclosure_info"), dict) else {}
    disclosure_info["report_contract"] = report_contract.to_dict()
    disclosure_info["correction_audit"] = correction_audit
    final = {
        "fund_name": _to_simple(pdf_path.stem),
        "report_date": _normalize_report_month(f"{raw.get('report_date') or ''}\n{pdf_text}"),
        "inception_date": raw.get("inception_date"),
        "listing_date": raw.get("listing_date"),
        "fund_size_hkd_100m": fund_size_hkd_100m,
        "portfolio_duration_years": _number(raw.get("portfolio_duration_years"))
        or _parse_metric_from_text(pdf_text, ["组合久期", "平均久期", "加权平均存续期", "平均存续期", "Duration", "Average duration"]),
        "portfolio_ytm_percent": _number(raw.get("portfolio_ytm_percent"))
        or _parse_metric_from_text(
            pdf_text,
            ["平均最低孳息率", "最低孳息率", "组合到期收益率", "組合到期收益率", "投资组合到期收益率", "投資組合到期收益率", "最差收益率", "加权平均最差收益率", "Yield to maturity", "Yield to worst", "YTM", "YTW"],
            require_percent=True,
        ),
        "investment_objective": raw.get("investment_objective"),
        "investment_strategy": raw.get("investment_strategy"),
        "portfolio_ytm_note": raw.get("portfolio_ytm_note") or raw.get("ytm_note"),
        "credit_rating": credit_rating,
        "top_holdings": holdings,
        "top_holdings_total_percent": holdings_total,
        "duration_distribution": duration_distribution,
        "geographic_allocation": geographic_allocation,
        "disclosure_info": disclosure_info,
        "dividend": dividend,
        "money_market_metrics": money_metrics,
    }
    return _to_simple(final)


def _needs_refinement(
    record: dict[str, Any],
    profile: ManagerProfile | None = None,
    context: str = "",
    skip_text_fields: set[str] | None = None,
) -> bool:
    """判断是否需要文本二次核对；参数 record/profile/context/skip_text_fields 为上下文。"""

    skip_text_fields = skip_text_fields or set()

    def missing(field_name: str) -> bool:
        """返回字段是否缺失且未被管理人 profile 标记为稳定不披露。"""

        if field_name in skip_text_fields:
            return False
        if record.get(field_name) not in (None, ""):
            return False
        return not (profile and profile.field_is_absent(field_name, context))

    if (
        record.get("fund_size_hkd_100m") in (None, "")
        and "fund_size" not in skip_text_fields
        and not (profile and profile.field_is_absent("fund_size", context))
    ):
        return True
    if missing("credit_rating"):
        return True
    duration = record.get("portfolio_duration_years")
    ytm = record.get("portfolio_ytm_percent")
    if missing("portfolio_duration_years") or missing("portfolio_ytm_percent"):
        return True
    if isinstance(duration, (int, float)) and isinstance(ytm, (int, float)) and abs(duration - ytm) < 0.0001:
        return True
    dividend = record.get("dividend")
    if isinstance(dividend, dict):
        share_class = str(dividend.get("share_class") or "").lower()
        has_yield = dividend.get("annualized_dividend_yield_percent") not in (None, "")
        if has_yield and any(token in share_class for token in ["累积", "累計", "acc", "accumulation"]):
            return True
    for field in ["top_holdings", "duration_distribution", "geographic_allocation"]:
        if not record.get(field) and missing(field):
            return True
    return False


def _missing_text_refinement_fields(
    record: dict[str, Any],
    profile: ManagerProfile | None = None,
    context: str = "",
    skip_text_fields: set[str] | None = None,
) -> list[str]:
    """收敛一次文本补提的字段；参数 record/profile/context/skip_text_fields 为当前结果与路由状态。"""

    skip_text_fields = skip_text_fields or set()

    def missing(field_name: str, output_name: str | None = None) -> bool:
        """判断字段是否应交给文本补提；参数为路由名和最终输出名。"""

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
            ("portfolio_duration_years", None),
            ("portfolio_ytm_percent", None),
            ("credit_rating", None),
            ("top_holdings", None),
            ("duration_distribution", None),
            ("geographic_allocation", None),
        )
        if missing(field, output_name)
    ]
    duration = record.get("portfolio_duration_years")
    ytm = record.get("portfolio_ytm_percent")
    if isinstance(duration, (int, float)) and isinstance(ytm, (int, float)) and abs(duration - ytm) < 0.0001:
        fields.extend(name for name in ("portfolio_duration_years", "portfolio_ytm_percent") if name not in fields)
    dividend = record.get("dividend")
    if isinstance(dividend, dict):
        share_class = str(dividend.get("share_class") or "").lower()
        has_yield = dividend.get("annualized_dividend_yield_percent") not in (None, "")
        if has_yield and any(token in share_class for token in ["累积", "累計", "acc", "accumulation"]):
            fields.append("dividend")
    return fields


def _dividend_score(value: Any) -> int:
    if not isinstance(value, dict):
        return -1
    text = " ".join(str(value.get(key) or "") for key in ["share_class", "currency", "frequency", "note"]).lower()
    score = 0
    if value.get("annualized_dividend_yield_percent") not in (None, ""):
        score += 10
    if "usd" in text or "美元" in text:
        score += 20
    if "monthly" in text or "每月" in text:
        score += 20
    if any(token in text for token in ["累积", "累計", "acc", "accumulation"]):
        score -= 40
    if value.get("share_class"):
        score += 5
    return score


def _merge_raw(primary: dict[str, Any], refinement: dict[str, Any], pdf_text: str, pdf_path: Path) -> dict[str, Any]:
    merged = dict(primary)
    current = _finalize(merged, pdf_path, pdf_text)
    refined = _finalize(refinement, pdf_path, pdf_text)
    for field in [
        "report_date",
        "fund_size_original",
        "credit_rating",
        "credit_rating_distribution",
        "top_holdings",
        "duration_distribution",
        "geographic_allocation",
        "dividend",
    ]:
        if merged.get(field) in (None, "", []) and refinement.get(field) not in (None, "", []):
            merged[field] = refinement.get(field)
    if merged.get("top_holdings_total_percent") in (None, "") and refinement.get("top_holdings_total_percent") not in (None, ""):
        merged["top_holdings_total_percent"] = refinement.get("top_holdings_total_percent")
    if _dividend_score(refinement.get("dividend")) > _dividend_score(merged.get("dividend")):
        merged["dividend"] = refinement.get("dividend")
    for field in ["portfolio_duration_years", "portfolio_ytm_percent"]:
        current_value = current.get(field)
        refined_value = refined.get(field)
        if current_value in (None, "") and refined_value not in (None, ""):
            merged[field] = refinement.get(field)
    if (
        isinstance(current.get("portfolio_duration_years"), (int, float))
        and isinstance(current.get("portfolio_ytm_percent"), (int, float))
        and abs(current["portfolio_duration_years"] - current["portfolio_ytm_percent"]) < 0.0001
        and refined.get("portfolio_duration_years") not in (None, "")
        and abs(float(refined["portfolio_duration_years"]) - float(current["portfolio_ytm_percent"])) >= 0.0001
    ):
        merged["portfolio_duration_years"] = refinement.get("portfolio_duration_years")
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

    fields = list(EVIDENCE_FIELDS)
    local_fields = getattr(local_result, "fields", {}) if local_result is not None else {}
    blocks = getattr(local_result, "blocks", []) if local_result is not None else []
    return resolve_evidence(
        raw.get("field_evidence"),
        blocks,
        fields=fields,
        local_evidence=local_fields,
    )


def _validation_issues(record: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if not record.get("fund_name"):
        issues.append("缺少基金名称")
    for field in ["fund_size_hkd_100m", "portfolio_duration_years", "portfolio_ytm_percent", "credit_rating"]:
        if record.get(field) in (None, ""):
            issues.append(f"{field} 为空")
    duration = record.get("portfolio_duration_years")
    if isinstance(duration, (int, float)) and not 0 <= duration <= 30:
        issues.append("portfolio_duration_years 超出常见范围 0-30")
    ytm = record.get("portfolio_ytm_percent")
    if isinstance(ytm, (int, float)) and not 0 <= ytm <= 30:
        issues.append("portfolio_ytm_percent 超出常见范围 0-30")
    for field, name_key in [
        ("top_holdings", "name"),
        ("duration_distribution", "bucket"),
        ("geographic_allocation", "region"),
    ]:
        for index, item in enumerate(record.get(field) or [], start=1):
            if not isinstance(item, dict) or not item.get(name_key) or item.get("weight_percent") in (None, ""):
                issues.append(f"{field}[{index}] 缺少名称或比例")
    duration_items = record.get("duration_distribution") or []
    if duration_items:
        unit = next((item.get("unit") for item in duration_items if isinstance(item, dict) and item.get("unit")), None)
        total = _distribution_total(duration_items)
        if unit == "percent" and not 95 <= total <= 105:
            issues.append(f"duration_distribution 百分比分布合计异常：{total:.2f}")
        if unit == "years" and not 0 < total <= 30:
            issues.append(f"duration_distribution 久期贡献合计异常：{total:.2f} 年")
    geography_items = record.get("geographic_allocation") or []
    if geography_items:
        total = _distribution_total(geography_items)
        if not 95 <= total <= 105:
            issues.append(f"geographic_allocation 合计异常：{total:.2f}")
    return issues


def _compose_pdf_text(
    mineru_context: str,
    native_text: str,
    *,
    reference_context: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """组合 MinerU 与原生 PDF 文本，避免重复发送同一内容。

    参数 mineru_context/native_text 为两路文本，reference_context 用于判断
    MinerU 是否完整（默认同 mineru_context）；返回上下文与调试信息。
    FACTSHEET_PDF_TEXT_LAYER=always|never|auto，默认 auto。
    """

    context = str(mineru_context or "")
    native = str(native_text or "")
    mode = os.environ.get(
        "FACTSHEET_PDF_TEXT_LAYER",
        "auto",
    ).strip().casefold()
    if mode not in {"always", "never", "auto"}:
        mode = "auto"
    reference = str(reference_context if reference_context is not None else context)
    context_length = len(reference.strip())
    native_length = len(native.strip())
    if mode == "always":
        include_native = bool(native)
    elif mode == "never":
        include_native = False
    else:
        include_native = bool(native) and (
            context_length == 0
            or context_length < 500
            or context_length < native_length * 0.8
        )
    text = f"{context}\n\n[PDF_TEXT_LAYER]\n{native}" if include_native else context
    return text, {
        "mode": mode,
        "native_included": include_native,
        "markdown_chars": context_length,
        "context_chars": len(context.strip()),
        "native_chars": native_length,
    }


def _ocr_mineru_images(image_paths: list[Path] | None, pdf_path: Path | None = None) -> str:
    """在 macOS 上用系统 Vision 识别 MinerU 图表图例。

    货币基金月报经常把资产/到期日分布只保留成图片；本地 qwen3:4b
    是文本模型，不能做视觉复核。Vision 只作为确定性 OCR 层补回图例，
    失败或非 macOS 环境时静默返回空文本，不影响主流程。
    """

    if sys.platform != "darwin":
        return ""
    swift = shutil.which("swift")
    script = Path(__file__).with_name("vision_ocr.swift")
    if not swift or not script.is_file():
        return ""
    paths = [str(path) for path in (image_paths or []) if Path(path).is_file()]
    # MinerU 对部分 PDF（尤其是由 PowerPoint 导出的月报）不会导出图片
    # 资源，但图表仍然存在于 PDF 页面中。此时按页渲染一份临时 PNG，
    # 继续复用 Vision OCR，避免把“image_count=0”误判为没有图表。
    temporary: tempfile.TemporaryDirectory[str] | None = None
    if not paths and pdf_path and Path(pdf_path).is_file():
        try:
            import fitz  # type: ignore

            temporary = tempfile.TemporaryDirectory(prefix="factsheet-ocr-")
            document = fitz.open(pdf_path)
            for index, page in enumerate(document):
                pixmap = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
                output = Path(temporary.name) / f"page-{index + 1:03d}.png"
                pixmap.save(str(output))
                paths.append(str(output))
            document.close()
        except (ImportError, OSError, RuntimeError, ValueError):
            if temporary is not None:
                temporary.cleanup()
            return ""
    if not paths:
        if temporary is not None:
            temporary.cleanup()
        return ""
    try:
        ocr_env = os.environ.copy()
        # Swift 在受限运行环境中可能无法写入默认的 clang ModuleCache；
        # 使用临时目录让系统 Vision OCR 保持可用。
        module_cache = Path(tempfile.gettempdir()) / "factsheet-swift-module-cache"
        module_cache.mkdir(parents=True, exist_ok=True)
        ocr_env.setdefault("SWIFT_MODULECACHE_PATH", str(module_cache))
        completed = subprocess.run(
            [swift, "-module-cache-path", str(module_cache), str(script), *paths],
            check=False,
            capture_output=True,
            text=True,
            timeout=90,
            env=ocr_env,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    finally:
        if temporary is not None:
            temporary.cleanup()
    return completed.stdout.strip()


def extract_pdf(
    *,
    pdf_path: Path,
    primary_model: str,
    fallback_model: str,
    attempts: int,
    prompt_path: Path,
    timeout: int,
    max_pages: int,
    zoom: float,
    split_pages: bool,
    vision_model: str = "qwen3.7-plus",
    enable_thinking: bool = True,
    thinking_budget: int | None = None,
    source: str = "mineru",
    mineru_model: str = "vlm",
    mineru_outdir: Path = DEFAULT_MINERU_OUTDIR,
    mineru_force: bool = False,
    mineru_cache_only: bool = False,
    mineru_poll_timeout: int = 900,
    category: str = "fixed",
) -> dict[str, Any]:
    del zoom, split_pages
    client = QwenClient(timeout=timeout)
    client_provider = str(getattr(client, "provider", "deepseek"))
    manager_profile = profile_for_pdf(pdf_path)
    native_pdf_text = _extract_pdf_text(pdf_path)
    pdf_text = native_pdf_text
    report_contract = detect_report_contract(
        native_pdf_text,
        filename=pdf_path.name,
        category=category,
    )
    prompt = (
        (_load_prompt(prompt_path, pdf_path) if prompt_path.exists() else _default_prompt(pdf_path))
        + report_contract_prompt(report_contract)
    )
    failures: list[str] = []
    input_source = source
    pdf_text_layer_meta: dict[str, Any] = {
        "mode": "native-only",
        "native_included": False,
        "markdown_chars": 0,
        "native_chars": len(native_pdf_text.strip()),
    }
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
            pdf_text, pdf_text_layer_meta = _compose_pdf_text(
                str(mineru_bundle["context"]),
                native_pdf_text,
                reference_context=str(
                    mineru_bundle.get("context_full") or mineru_bundle["context"]
                ),
            )
            # 货币基金月报经常把关键字段放在 PowerPoint/PDF 的原生文字层，
            # MinerU 只返回图表占位符。即使模型上下文为节省 token 选择了
            # 不重复发送原生层，确定性字段解析仍必须看到它。
            if category in {"money", "货币"} and native_pdf_text and not pdf_text_layer_meta.get("native_included"):
                pdf_text = f"{pdf_text}\n\n[PDF_TEXT_LAYER]\n{native_pdf_text}"
                pdf_text_layer_meta["native_included"] = True
                pdf_text_layer_meta["context_chars"] = len(pdf_text.strip())
            if category in {"money", "货币"} and mineru_bundle:
                image_ocr = _ocr_mineru_images(
                    list(mineru_bundle.get("image_paths") or []),
                    pdf_path=pdf_path,
                )
                if image_ocr:
                    pdf_text = f"{pdf_text}\n\n[IMAGE_OCR]\n{image_ocr}"
                    pdf_text_layer_meta["image_ocr_included"] = True
            report_contract = detect_report_contract(
                pdf_text,
                filename=pdf_path.name,
                category=category,
            )
            prompt = (
                (_load_prompt(prompt_path, pdf_path) if prompt_path.exists() else _default_prompt(pdf_path))
                + report_contract_prompt(report_contract)
            )
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
            category=category,
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
                    context=pdf_text,
                    prompt=prompt,
                    attempts=attempts,
                    enable_thinking=enable_thinking,
                    thinking_budget=thinking_budget,
                    call_name="mineru_primary_extraction",
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
                    call_name="primary_extraction",
                )
            raw, local_apply_meta = apply_local_first(
                raw,
                local_result,
                fields=LOCAL_FIRST_FIELDS,
            )
            local_meta["apply"] = local_apply_meta
            final = _finalize(raw, pdf_path, pdf_text, category=category)
            matrix_fields = _missing_matrix_fields_for_profile(
                final,
                manager_profile,
                pdf_text,
            )
            matrix_was_pending = bool(matrix_fields)
            matrix_skip_name = "matrix_flash" if model == primary_model else "matrix_pro"
            if matrix_fields and not manager_profile.skips(matrix_skip_name):
                call_decisions.append({
                    "call": "matrix_text_refinement",
                    "status": "called",
                    "fields": matrix_fields,
                    "model": model,
                })
                matrix_prompt = _matrix_text_refinement_prompt(
                    pdf_path,
                    matrix_fields,
                    manager_profile.alias_hint(matrix_fields),
                )
                if input_source == "mineru" and mineru_bundle:
                    matrix_text_refinement = _chat_context_json(
                        client,
                        model=model,
                        context=pdf_text,
                        prompt=matrix_prompt,
                        attempts=attempts,
                        enable_thinking=enable_thinking,
                        thinking_budget=thinking_budget,
                        call_name="mineru_matrix_text_refinement",
                    )
                else:
                    matrix_text_refinement = _chat_file_json(
                        client,
                        model=model,
                        file_id=str(file_id),
                        prompt=matrix_prompt,
                        attempts=attempts,
                        enable_thinking=enable_thinking,
                        thinking_budget=thinking_budget,
                        call_name="matrix_text_refinement",
                    )
                raw = _merge_raw(raw, matrix_text_refinement, pdf_text, pdf_path)
                final = _finalize(raw, pdf_path, pdf_text, category=category)
            elif matrix_fields:
                call_decisions.append({
                    "call": "matrix_text_refinement",
                    "status": "skipped",
                    "fields": matrix_fields,
                    "skip_reason": "manager_profile_skip",
                    "model": model,
                })
            if (
                _missing_matrix_fields_for_profile(final, manager_profile, pdf_text)
                and model != fallback_model
            ):
                continue
            refinement = None
            local_text_skip = (
                set(local_meta.get("text_skip_fields") or [])
                if local_meta.get("apply", {}).get("mode") == "enforce"
                else set()
            )
            text_refinement_fields = _missing_text_refinement_fields(
                final,
                manager_profile,
                pdf_text,
                skip_text_fields=local_text_skip,
            )
            if matrix_was_pending:
                text_refinement_fields = []
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
                        context=pdf_text,
                        prompt=(
                            _refinement_prompt(pdf_path)
                            + "\n\n本次仅补提这些字段："
                            + "、".join(text_refinement_fields)
                        ),
                        attempts=attempts,
                        enable_thinking=enable_thinking,
                        thinking_budget=thinking_budget,
                        call_name="mineru_combined_text_refinement",
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
                        call_name="combined_text_refinement",
                    )
                raw = _merge_raw(raw, refinement, pdf_text, pdf_path)
                final = _finalize(raw, pdf_path, pdf_text, category=category)
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
            visual_review_mineru_paths = (
                list(mineru_bundle.get("image_paths") or [])
                if mineru_bundle
                else None
            )
            vision_enabled = (
                manager_profile.vision_review
                and not manager_profile.skips("vision")
                and client_provider != "local"
            )
            vision_fields = [
                field
                for field in (
                    "portfolio_duration_years",
                    "portfolio_ytm_percent",
                    "credit_rating",
                    "top_holdings",
                    "duration_distribution",
                    "geographic_allocation",
                )
                if not manager_profile.field_is_absent(field, pdf_text)
            ]
            if vision_enabled and any(
                final.get(field) in (None, "", [])
                for field in vision_fields
            ):
                call_decisions.append({"call": "metrics_vision_refinement", "status": "called", "fields": vision_fields})
                vision_refinement = _extract_fixed_income_metrics_from_images(
                    client,
                    pdf_path=pdf_path,
                    vision_model=vision_model,
                    attempts=attempts,
                    max_pages=max_pages,
                    enable_thinking=enable_thinking,
                    thinking_budget=thinking_budget,
                    mineru_image_paths=visual_review_mineru_paths,
                )
                if vision_refinement:
                    raw = _merge_raw(raw, vision_refinement, pdf_text, pdf_path)
                    final = _finalize(raw, pdf_path, pdf_text, category=category)
            else:
                call_decisions.append({
                    "call": "metrics_vision_refinement",
                    "status": "skipped",
                    "skip_reason": (
                        "local_text_model_no_vision" if client_provider == "local"
                        else "no_missing_visual_fields" if vision_enabled else "manager_profile_skip"
                    ),
                })
            complex_review_fields = (
                [
                    field
                    for field in (
                        "top_holdings",
                        "duration_distribution",
                        "geographic_allocation",
                    )
                    if not manager_profile.field_is_absent(field, pdf_text)
                ]
                if vision_enabled
                else []
            )
            complex_field_reviews: dict[str, dict[str, Any] | None] = (
                _review_complex_fields_from_pdf_images(
                    client,
                    pdf_path=pdf_path,
                    vision_model=vision_model,
                    attempts=attempts,
                    max_pages=max_pages,
                    mineru_image_paths=visual_review_mineru_paths,
                    fields=complex_review_fields,
                    alias_hint=manager_profile.alias_hint(complex_review_fields),
                )
                if vision_enabled
                else {}
            )
            for field in complex_review_fields:
                if field not in complex_field_reviews:
                    complex_field_reviews[field] = (
                        _review_complex_field_from_pdf_images(
                            client,
                            pdf_path=pdf_path,
                            field=field,
                            vision_model=vision_model,
                            attempts=attempts,
                            max_pages=max_pages,
                            mineru_image_paths=visual_review_mineru_paths,
                            alias_hint=manager_profile.alias_hint([field]),
                        )
                    )
            duration_review = complex_field_reviews.get("duration_distribution")
            if isinstance(duration_review, dict) and duration_review.get("disclosed"):
                duration_values = [
                    _number(item.get("value"))
                    for item in duration_review.get("items") or []
                    if isinstance(item, dict)
                ]
                duration_total = sum(value for value in duration_values if value is not None)
                if duration_review.get("unit") == "percent" and not 95 <= duration_total <= 105:
                    complex_field_reviews["duration_distribution"] = (
                        _review_complex_field_from_pdf_images(
                            client,
                            pdf_path=pdf_path,
                            field="duration_distribution",
                            vision_model=vision_model,
                            attempts=attempts,
                            max_pages=max_pages,
                            mineru_image_paths=visual_review_mineru_paths,
                            correction_hint=(
                                f"上次单位为 percent，但各项合计只有 {duration_total:.2f}。"
                                "这很可能是以年为单位的期权调整存续期贡献，请根据图表标题重新判断。"
                            ),
                        )
                        or duration_review
                    )
            geography_review = complex_field_reviews.get("geographic_allocation")
            if isinstance(geography_review, dict) and geography_review.get("disclosed"):
                geography_values = [
                    _number(item.get("weight_percent"))
                    for item in geography_review.get("items") or []
                    if isinstance(item, dict)
                ]
                geography_total = sum(value for value in geography_values if value is not None)
                if not 98 <= geography_total <= 102:
                    complex_field_reviews["geographic_allocation"] = (
                        _review_complex_field_from_pdf_images(
                            client,
                            pdf_path=pdf_path,
                            field="geographic_allocation",
                            vision_model=vision_model,
                            attempts=attempts,
                            max_pages=max_pages,
                            mineru_image_paths=visual_review_mineru_paths,
                            correction_hint=(
                                f"上次各项合计为 {geography_total:.2f}%，不是完整组合。"
                                "请逐行核对是否有数字错读或漏掉现金/其他。"
                            ),
                        )
                        or geography_review
                    )
                    retried_geography = complex_field_reviews["geographic_allocation"]
                    retried_values = [
                        _number(item.get("weight_percent"))
                        for item in (retried_geography.get("items") or [])
                        if isinstance(item, dict)
                    ]
                    retried_total = sum(value for value in retried_values if value is not None)
                    if not 98 <= retried_total <= 102:
                        complex_field_reviews["geographic_allocation"] = (
                            _review_geography_series_from_pdf_images(
                                client,
                                pdf_path=pdf_path,
                                vision_model=vision_model,
                                attempts=attempts,
                                max_pages=max_pages,
                                mineru_image_paths=visual_review_mineru_paths,
                            )
                            or retried_geography
                        )
            raw = _apply_complex_field_reviews(raw, complex_field_reviews)
            final = _finalize(raw, pdf_path, pdf_text, category=category)
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
                    context=pdf_text,
                    prompt=(
                        "仅修复下列字段的 field_evidence。每项必须引用上下文中实际存在的 "
                        "[PAGE n] / [BLOCK m]，输出 {\"field_evidence\": {...}}，不得输出原文或字段值："
                        + "、".join(retry_fields)
                    ),
                    attempts=1,
                    enable_thinking=False,
                    thinking_budget=None,
                    call_name="fixed_income_evidence_ref_retry",
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
            return {
                "status": "ok" if not issues else "review",
                "data": final,
                "_meta": {
                    "source_file": str(pdf_path),
                    "extracted_at": datetime.now(timezone.utc).isoformat(),
                    "model": model,
                    "input_source": input_source,
                    "pdf_text_layer": pdf_text_layer_meta,
                    "manager_profile": manager_profile.to_meta(),
                    "file_id": file_id,
                    "mineru": {
                        "manifest_path": mineru_manifest.get("manifest_path"),
                        "batch_id": mineru_manifest.get("batch_id"),
                        "model_version": mineru_manifest.get("model_version"),
                        "markdown_path": str(mineru_bundle.get("markdown_path")) if mineru_bundle else None,
                        "content_list_path": str(mineru_bundle.get("content_list_path")) if mineru_bundle and mineru_bundle.get("content_list_path") else None,
                        "image_count": len(mineru_bundle.get("image_paths") or []) if mineru_bundle else 0,
                        "context_mode": mineru_bundle.get("context_mode") if mineru_bundle else None,
                        "context_chars": mineru_bundle.get("context_chars") if mineru_bundle else None,
                        "context_full_chars": mineru_bundle.get("context_full_chars") if mineru_bundle else None,
                        "context_sha256": mineru_bundle.get("context_sha256") if mineru_bundle else None,
                        "context_full_sha256": mineru_bundle.get("context_full_sha256") if mineru_bundle else None,
                    } if mineru_manifest else None,
                    "raw_model_output": raw,
                    "refinement_output": refinement,
                    "matrix_text_refinement_output": matrix_text_refinement,
                    "call_decisions": call_decisions,
                    "report_contract": report_contract.to_dict(),
                    "correction_audit": (final.get("disclosure_info") or {}).get("correction_audit", {}),
                    "vision_refinement_output": vision_refinement,
                    "complex_field_reviews": complex_field_reviews,
                    "local_first": local_meta,
                    "pipeline": local_meta.get("pipeline"),
                    "pipeline_version": (local_meta.get("pipeline") or {}).get("pipeline_version"),
                    "field_provenance": local_meta.get("field_provenance", {}),
                    "evidence_contract": evidence_audit,
                    "output_contract": {
                        "schema_mode": "json_schema_strict" if client_provider == "qwen" else "json_object_subschema",
                        "schema_sha256": schema_sha256(build_subschema(MODEL_SCHEMA)),
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
            "portfolio_duration_years": None,
            "portfolio_ytm_percent": None,
            "credit_rating": None,
            "top_holdings": [],
            "top_holdings_total_percent": None,
            "duration_distribution": [],
            "geographic_allocation": [],
            "dividend": None,
        }),
        "_meta": {
            "source_file": str(pdf_path),
            "extracted_at": datetime.now(timezone.utc).isoformat(),
            "failures": failures,
            "enable_thinking": enable_thinking,
            "thinking_budget": thinking_budget,
            "token_usage": client.token_usage(),
            "input_source": input_source,
            "pdf_text_layer": pdf_text_layer_meta,
            "manager_profile": manager_profile.to_meta(),
            "local_first": local_meta,
            "report_contract": report_contract.to_dict(),
        },
    }


def write_result(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result["data"], ensure_ascii=False, indent=2), encoding="utf-8")
    meta_path = path.with_suffix(".meta.json")
    meta_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract fixed-income structured data from one PDF.")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR)
    parser.add_argument("--primary-model", default="deepseek-flash")
    parser.add_argument("--fallback-model", default="deepseek-v4-pro")
    parser.add_argument(
        "--vision-model",
        default="deepseek-flash",
        help="Used when duration, yield-to-maturity, or credit-rating fields need image extraction",
    )
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
    parser.add_argument("--max-pages", type=int, default=12, help="Maximum legacy visual fallback pages")
    parser.add_argument("--zoom", type=float, default=2.0, help="Legacy compatibility option")
    parser.add_argument("--no-split-pages", action="store_true", help="Legacy compatibility option")
    parser.add_argument("--category", default="fixed")
    parser.add_argument("--output-suffix", default="fixed_income")
    args = parser.parse_args()

    pdf_path = args.pdf.resolve()
    result = extract_pdf(
        pdf_path=pdf_path,
        primary_model=args.primary_model,
        fallback_model=args.fallback_model,
        attempts=args.attempts,
        prompt_path=args.prompt,
        timeout=args.timeout,
        max_pages=args.max_pages,
        zoom=args.zoom,
        split_pages=not args.no_split_pages,
        vision_model=args.vision_model,
        enable_thinking=not args.no_thinking,
        thinking_budget=args.thinking_budget,
        source=args.source,
        mineru_model=args.mineru_model,
        mineru_outdir=args.mineru_outdir,
        mineru_force=args.mineru_force,
        mineru_cache_only=args.mineru_cache_only,
        mineru_poll_timeout=args.mineru_poll_timeout,
        category=args.category,
    )
    output = args.outdir / f"{source_stem(str(pdf_path))}.{args.output_suffix}.json"
    write_result(output, result)
    print(json.dumps({"status": result["status"], "output": str(output), "fund_count": 1 if result["status"] != "failed" else 0}, ensure_ascii=False))
    return 0 if result["status"] != "failed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
