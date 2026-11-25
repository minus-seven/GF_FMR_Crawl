"""投资组合结构标签的跨语言关键词归并。"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from typing import Any


# 顺序很重要：现金及其他先于“其他”，债券先于存款证，避免一个标签
# 同时命中多个宽泛关键词时被归入过于笼统的类别。
_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "现金及其他",
        (
            "现金",
            "現金",
            "cash",
            "cash equivalent",
            "cash equivalents",
            "cash and other",
            "cash & other",
            "cash and cash",
            "流动资金",
            "流動資金",
        ),
    ),
    (
        "债券/固定收益",
        (
            "债券",
            "債券",
            "债务证券",
            "債務證券",
            "debt securities",
            "fixed income",
            "fixed-income",
            "bond",
            "bonds",
        ),
    ),
    (
        "存款",
        (
            "存款",
            "存款证",
            "存款憑證",
            "存單",
            "定期",
            "银行存款",
            "銀行存款",
            "deposit",
            "deposits",
            "time deposit",
            "time deposits",
            "certificate of deposit",
            "certificates of deposit",
        ),
    ),
    (
        "货币市场工具",
        (
            "货币市场",
            "貨幣市場",
            "money market",
        ),
    ),
    (
        "股票/权益",
        (
            "股票",
            "股份",
            "权益",
            "權益",
            "equity",
            "equities",
            "stock",
            "stocks",
            "share",
            "shares",
        ),
    ),
    (
        "其他净资产",
        (
            "其他净资产",
            "其他淨資產",
            "other net assets",
        ),
    ),
)


def _compact(value: Any) -> str:
    """归一化大小写、全半角、空白和分隔符，保留中英文关键词。"""

    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[\s_\-/&+,()（）【】\[\]：:]+", "", text)


def canonical_allocation_label(value: Any) -> str:
    """把资产类别映射到稳定的跨语言展示标签。"""

    original = re.sub(r"\s+", " ", str(value or "")).strip()
    compact = _compact(original)
    if not compact:
        return ""
    for canonical, keywords in _GROUPS:
        if any(_compact(keyword) in compact for keyword in keywords):
            return canonical
    return original


def merge_allocation_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """按关键词归并资产类别并累加权重；保留首次出现顺序。"""

    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        label = canonical_allocation_label(row.get("label") or row.get("category"))
        if not label:
            continue
        try:
            value = float(row.get("value") or row.get("weight") or 0)
        except (TypeError, ValueError):
            continue
        current = merged.setdefault(label, {"label": label, "value": 0.0})
        current["value"] += value
    return [
        {"label": label, "value": round(float(item["value"]), 6)}
        for label, item in merged.items()
    ]
