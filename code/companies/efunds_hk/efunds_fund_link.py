#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
易方达香港（E Fund HK）基金月报爬虫补全脚本。

目录来源：官网产品列表页（服务端渲染，纯 requests 可用）
  https://www.efunds.com.hk/sc/products/
  每个产品链接形如 /sc/products/<id>/info，锚文本含类别前缀（如“权益: 易方达（香港）…”）。
月报判定：产品文档页 /sc/products/<id>/documents/ 的 <select> 中是否存在“月报”类文档
          （复用下载脚本的 fetch_page / parse_documents）。

匹配：中文核心名（去“易方达（香港）/香港/基金”与符号）→ 英文核心名兜底 →
      包含兜底（同公司目录内 first-wins）。
I = https://www.efunds.com.hk/sc/products/<id>/documents/
J = python download_hk_documents.py <id> --type MonthlyFact

用法：
  python efunds_fund_link.py 易方达(香港)精选债券基金
  python efunds_fund_link.py --probe --excel /path/to/爬虫list.xlsx --sheet 易方达
  python efunds_fund_link.py --backfill --excel /path/to/爬虫list.xlsx --sheet 易方达
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "efunds_cn")))
sys.path.insert(0, HERE)

from common.fund_link_backfill import cli_main, normalize_name  # noqa: E402
from download_hk_documents import fetch_page, parse_documents  # noqa: E402
from download_monthly_report import (  # noqa: E402
    fetch_page as fetch_mainland_page,
    parse_monthly_reports as parse_mainland_monthly,
)
from download_periodic_report import parse_periodic_reports  # noqa: E402


SHEET_NAME = "易方达"
LIST_URL = "https://www.efunds.com.hk/sc/products/"
DOCS_URL = "https://www.efunds.com.hk/sc/products/{pid}/documents/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
LINK_RE = re.compile(r'<a[^>]+href="(/sc/products/(\d+)/[^"]*)"[^>]*>(.*?)</a>', re.S)
LINKS_PATH = os.path.join(HERE, "efunds_links.json")
MAINLAND_PAGE = "https://www.efunds.com.cn/fund/{code}.shtml"


def _load_mainland_codes() -> dict[str, str]:
    """读取内地站基金代码配置（基金名 → 代码），用于港站未收录/无月报时的兜底。"""
    if not os.path.exists(LINKS_PATH):
        return {}
    try:
        with open(LINKS_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle) or {}
    except (OSError, json.JSONDecodeError):
        return {}
    return {str(k).strip(): str(v).strip() for k, v in (data.get("mainland_codes") or {}).items()}


def _cn(value: str) -> str:
    """中文核心名：简繁统一、去“易方达/香港/基金”与符号。"""
    text = normalize_name(value)
    for token in ("易方達", "易方达", "香港", "基金", "系列"):
        text = text.replace(token, "")
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", text)


def _en(value: str) -> str:
    """英文核心名：去 E Fund/HK/Fund 等词，仅保留字母数字。"""
    text = str(value or "").strip().lower()
    for token in ("e fund", "efund", "hk", "fund", "funds"):
        text = text.replace(token, "")
    return re.sub(r"[^a-z0-9]", "", text)


def _fetch_catalog() -> list[dict[str, Any]]:
    """返回官网产品目录（产品 id + 名称）。"""
    import requests

    response = requests.get(LIST_URL, headers=HEADERS, timeout=30)
    response.raise_for_status()
    catalog: dict[str, str] = {}
    for _href, pid, inner in LINK_RE.findall(response.text):
        name = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", inner)).split())
        name = re.sub(r"^[^:：]{1,6}[:：]\s*", "", name)  # 去掉“权益:”/“固定收益:”前缀
        if pid and name and pid not in catalog:
            catalog[pid] = name
    if not catalog:
        raise RuntimeError("易方达产品列表页未解析到产品 id")
    return [{"fund_id": pid, "fund_name": name} for pid, name in catalog.items()]


def _build_index(catalog: list[dict[str, Any]]) -> dict[str, Any]:
    """建立中文/英文核心名索引并保留全量目录。"""
    index: dict[str, Any] = {"cn": {}, "en": {}, "all": catalog}
    for record in catalog:
        index["cn"].setdefault(_cn(record["fund_name"]), record)
    return index


def _match_record(row: dict[str, Any], index: dict[str, Any]) -> dict[str, Any] | None:
    """匹配基金：伞名+基金名 → 中文核心名 → 英文名 → 包含兜底。"""
    fund = str(row.get("fund") or "").strip()
    umbrella = str(row.get("umbrella") or "").strip()
    keys = [_cn(fund)]
    if umbrella:
        keys.append(_cn(f"{umbrella}{fund}"))
    for key in keys:
        if key and key in index["cn"]:
            return index["cn"][key]
    english = _en(str(row.get("english") or ""))
    if english:
        for record in index["all"]:
            if english and english in _en(record["fund_name"]):
                return record
    for key in keys:
        if len(key) < 4:
            continue
        for candidate_key, record in index["cn"].items():
            if candidate_key and (key in candidate_key or candidate_key in record["fund_name"]):
                return record
    return None


_DOC_CACHE: dict[str, bool] = {}


def _has_monthly(pid: str) -> bool:
    """产品文档页是否存在“月报”类文档。"""
    if pid not in _DOC_CACHE:
        try:
            documents = parse_documents(fetch_page(pid))
            monthly = documents.get("MonthlyFact") or {}
            _DOC_CACHE[pid] = bool(monthly.get("versions")) and (
                "月报" in str(monthly.get("title") or "") or "月報" in str(monthly.get("title") or "")
            )
        except Exception:  # noqa: BLE001 - 页面异常按无月报处理
            _DOC_CACHE[pid] = False
    return _DOC_CACHE[pid]


def _resolve_single(row: dict[str, Any], index: dict[str, Any]) -> tuple[str, str, str | None]:
    """解析一支基金，返回 (I月报链接, J运行方式, 跳过原因)；不认领时原因为 None。"""
    record = _match_record(row, index)
    if record and _has_monthly(record["fund_id"]):
        return (
            DOCS_URL.format(pid=record["fund_id"]),
            # 显式指定文档类型，避免官网新增“产品资料概要/说明书”后
            # 默认选择器发生漂移，任务误下载到非月报文件。
            f"python download_hk_documents.py {record['fund_id']} --type MonthlyFact",
            "",
        )
    # 港站未收录或无月报：尝试内地站（互认基金/内地公募基金的“基金月度资料”）
    mainland_code = _load_mainland_codes().get(str(row.get("fund") or "").strip())
    if mainland_code:
        try:
            page_html = fetch_mainland_page(mainland_code)
        except Exception:  # noqa: BLE001 - 内地页异常按不可用处理
            page_html = ""
        reports = parse_mainland_monthly(page_html) if page_html else []
        if reports:
            return (
                MAINLAND_PAGE.format(code=mainland_code),
                f"python download_monthly_report.py {mainland_code}",
                "",
            )
        periodic = parse_periodic_reports(page_html) if page_html else []
        if periodic:
            return (
                MAINLAND_PAGE.format(code=mainland_code),
                f"python download_periodic_report.py {mainland_code}",
                "",
            )
        return "", "", f"内地站（基金代码 {mainland_code}）无月报，也无定期报告"
    if record:
        return "", "", "香港站该产品文档页无“月报”类文档（内地站亦未配置代码）"
    return "", "", "香港站/内地站均未收录该基金（可在此脚本的 efunds_links.json 补充代码）"


def main(argv: list[str] | None = None) -> int:
    """命令行入口：单基金查询 / --probe / --backfill。"""
    return cli_main(
        argv,
        description="按爬虫list.xlsx易方达表 B 列基金名查询官网月报链接（含批量补全）",
        sheet_name=SHEET_NAME,
        catalog_loader=_fetch_catalog,
        index_builder=_build_index,
        resolver=_resolve_single,
        single_print_run_command=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
