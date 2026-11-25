"""静态官方目录适配器共用函数。"""

from __future__ import annotations

import re
from typing import Any

from common.fund_link_backfill import normalize_name


def name_key(value: str, *noise: str) -> str:
    """生成基金名称匹配键；参数 value 为原始名称、noise 为需移除的品牌词。"""
    text = normalize_name(str(value or "")).casefold()
    for token in noise:
        text = text.replace(normalize_name(token).casefold(), "")
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", text)


def build_static_index(catalog: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """建立静态目录索引；参数 catalog 为含 fund_name 的记录列表。"""
    index: dict[str, list[dict[str, Any]]] = {}
    for item in catalog:
        index.setdefault(str(item.get("key") or name_key(str(item.get("fund_name") or ""))), []).append(item)
    return index


def resolve_static(
    row: dict[str, Any], index: dict[str, list[dict[str, Any]]], command_template: str
) -> tuple[str, str, str]:
    """按英文名解析静态官方入口；参数 row/index/template 分别为页表行、索引和运行命令模板。"""
    target = name_key(str(row.get("english") or row.get("fund") or ""), "fund", "investment funds")
    exact = index.get(target) or []
    if not exact:
        candidates = [
            (len(key), item)
            for key, items in index.items()
            if key.startswith(target) or target.startswith(key) or key in target or target in key
            for item in items
        ]
        candidates.sort(key=lambda pair: pair[0], reverse=True)
        exact = [item for _, item in candidates]
    if not exact:
        return "", "", "官方目录未核验到同名月报入口"
    item = exact[0]
    link = str(item.get("url") or "")
    command = command_template.format(url=link, english=str(row.get("english") or row.get("fund") or ""))
    return link, command, ""
