#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
富达香港基金月报爬虫补全脚本（HK 站，伞优先匹配）。

匹配策略（按伞限定系列，避免跨伞同名误配）：
  1. 用表格 C 列“伞”映射到官网系列（FF / FF2 / FSF / FAP）；
  2. 先在同一系列内用中文全名匹配；
  3. 再在同一系列内用“英文核心名”匹配（去掉系列前缀，统一货币符号与 (MPF) 等后缀）。
  ILAS 伞（富达环球投资基金）官网无对应系列，本脚本不认领（留给 TW 或汇总为跳过）。

输出：
  I = https://www.fidelity.com.hk/zh/funds/factsheet/<fundId>/tab-documents
  J = python download_fidelity_factsheet.py <fundId>

用法：
  python fidelity_fund_link.py "富达基金 - 环球焦点基金"
  python fidelity_fund_link.py --probe --excel /path/to/爬虫list.xlsx --sheet 富达
  python fidelity_fund_link.py --backfill --excel /path/to/爬虫list.xlsx --sheet 富达
"""

from __future__ import annotations

import os
import re
import sys
from typing import Any
from urllib.parse import urlencode

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common.browser_fetch import (  # noqa: E402
    fetch_json,
    is_access_denied,
    launch_browser,
    new_context,
)
from common.fund_link_backfill import (  # noqa: E402
    cli_main,
    normalize_name,
    pick_share_class,
)
from playwright.sync_api import sync_playwright  # noqa: E402
from download_fidelity_ilas_factsheet import (  # noqa: E402
    download_report as _ilas_download_report,
    resolve_latest_report_url as _ilas_resolve_latest_url,
    resolve_section as _ilas_resolve_section,
)


SHEET_NAME = "富达"
BASE_URL = "https://www.fidelity.com.hk"
FUND_LIST_API = f"{BASE_URL}/api/ce/fdh/FundList.json"
LIST_PAGE = f"{BASE_URL}/zh/our-funds/mutual-funds"
BASE_PARAMS = {
    "countries": "hk",
    "country": "hk",
    "channels": "ce.private-investor",
    "channel": "ce.private-investor",
}
# 三个产品线：默认（互惠基金）、强积金（FRMT）、ORSO（FAP）
FUND_RANGES: tuple[str | None, ...] = (
    None,
    "Fidelity Retirement Master Trust",
    "Fidelity Advantage Portfolio Fund",
)
# 表格 C 列伞名 → 官网 fundRangeCode（ILAS 伞刻意不映射）
SHEET_UMBRELLA_TO_SERIES = {
    "富达基金": "FF",
    "富达基金2": "FF2",
    "富达精选基金": "FSF",
    "富达优越投资组合基金": "FAP",
}
# ILAS 伞：官网无零售产品页，改用 FGIF 伞级报告按子基金章节切分
ILAS_UMBRELLA = "富达环球投资基金"
_ILAS_REPORT_CACHE: dict[str, str] = {}


def _ilas_latest_url() -> str:
    """返回当前最新一期 FGIF 伞级报告 URL（进程内缓存）。"""
    if "url" not in _ILAS_REPORT_CACHE:
        _ILAS_REPORT_CACHE["url"] = _ilas_resolve_latest_url()[0]
    return _ILAS_REPORT_CACHE["url"]


def _ilas_section(fund_name: str):
    """在 FGIF 伞级报告目录中查子基金页码；首次调用下载并缓存报告。"""
    if "path" not in _ILAS_REPORT_CACHE:
        _ILAS_REPORT_CACHE["path"] = _ilas_download_report()
    return _ilas_resolve_section(_ILAS_REPORT_CACHE["path"], fund_name)
# 官网中文名里带的系列前缀（仅 FF/FF2/FSF 需要剥离；FRMT/FAP 为裸名）
CN_SERIES_PREFIX = {"FF": "富達基金", "FF2": "富達基金2", "FSF": "富達精選基金"}
# 英文系列前缀
EN_SERIES_PREFIX = (
    "Fidelity Retirement Master Trust",
    "Fidelity Advantage Portfolio Fund",
    "Fidelity Selected Funds",
    "Fidelity Funds 2",
    "Fidelity Funds",
    "Fidelity Global Investment Fund",
)
TIMEOUT_MS = 60000


def _cn_key(value: str, series_prefix: str = "") -> str:
    """中文名归一化：简→繁、去空格与分隔符，并剥离系列前缀。"""
    text = normalize_name(value).replace(" ", "")
    for token in ("-", "－", "–", "—", "−", "─", "〜", "~"):
        text = text.replace(token, "")
    prefix = normalize_name(series_prefix).replace(" ", "")
    for token in ("-", "－", "–", "—", "−", "─"):
        prefix = prefix.replace(token, "")
    if prefix and text.startswith(prefix):
        text = text[len(prefix):]
    return re.sub(r"\([^()]*\)$", "", text)


def _en_core(value: str) -> str:
    """英文名去掉系列前缀，返回核心名。"""
    text = str(value or "").strip()
    for prefix in EN_SERIES_PREFIX:
        if text.lower().startswith(prefix.lower()):
            rest = text[len(prefix):].lstrip(" -–—:")
            return rest or text
    return text


def _en_key(value: str) -> str:
    """英文核心名归一化：统一货币符号、去掉 (MPF)/(hedged) 等后缀。"""
    text = str(value or "").strip().lower()
    text = text.replace("hk$", "hkd").replace("us$", "usd")
    text = re.sub(r"\((?:mpf|hedged|hgd|acc|dist)\)", "", text)
    return re.sub(r"[^a-z0-9]", "", text)


def _fetch_catalog() -> list[dict[str, Any]]:
    """抓取 HK 目录（中文名 + 英文名 + 币种 + 是否有月报）。"""
    records: list[dict[str, Any]] = []
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        context = new_context(browser, locale="zh-HK")
        page = context.new_page()
        try:
            page.goto(LIST_PAGE, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
            if is_access_denied(page):
                raise RuntimeError("富达香港站点返回 Access Denied，请确认已安装 Chrome/Edge")
            for fund_range in FUND_RANGES:
                suffix = f"&fundrange={fund_range.replace(' ', '%20')}" if fund_range else ""
                params = urlencode(dict(BASE_PARAMS, languages="zh,en", language="zh"))
                zh_data = fetch_json(page, f"{FUND_LIST_API}?{params}{suffix}", timeout_ms=TIMEOUT_MS)
                params_en = urlencode(dict(BASE_PARAMS, languages="en,zh", language="en"))
                en_data = fetch_json(page, f"{FUND_LIST_API}?{params_en}{suffix}", timeout_ms=TIMEOUT_MS)
                if not isinstance(zh_data, dict) or zh_data.get("errorCode"):
                    continue
                for fund_id, item in zh_data.items():
                    if not isinstance(item, dict):
                        continue
                    en_item = en_data.get(fund_id) if isinstance(en_data, dict) else {}
                    head = item.get("headFundFacts") or {}
                    share = item.get("shareClassFacts") or {}
                    en_head = (en_item or {}).get("headFundFacts") or {}
                    records.append(
                        {
                            "fund_id": fund_id,
                            "series": str(item.get("fundRangeCode") or ""),
                            "cn_name": str(head.get("headFundName") or "").strip(),
                            "en_name": str(en_head.get("headFundName") or "").strip(),
                            "currency": str(share.get("currencyName") or "").strip(),
                            "has_factsheet": bool(item.get("hasFactsheet")),
                        }
                    )
        finally:
            browser.close()
    if not records:
        raise RuntimeError("富达香港基金目录为空，请检查接口或浏览器渠道")
    return records


def _build_index(catalog: list[dict[str, Any]]):
    """建立 (系列, 类型, 归一化名) → 记录列表 的索引。"""
    index: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for record in catalog:
        series = record["series"]
        cn_key = _cn_key(record["cn_name"], CN_SERIES_PREFIX.get(series, ""))
        if cn_key:
            index.setdefault((series, "cn", cn_key), []).append(record)
        en_key = _en_key(_en_core(record["en_name"]))
        if en_key:
            index.setdefault((series, "en", en_key), []).append(record)
    return index


def _resolve_single(row: dict[str, Any], index) -> tuple[str, str, str | None]:
    """解析一支基金，返回 (I, J, 跳过原因)；不认领时原因为 None。"""
    umbrella = str(row.get("umbrella") or "").strip()
    series = SHEET_UMBRELLA_TO_SERIES.get(umbrella)
    if not series:
        if umbrella == ILAS_UMBRELLA:
            # ILAS 伞：官网无零售产品页，改用 FGIF 伞级半年/年度报告，按子基金章节切分
            section = _ilas_section(str(row.get("fund") or ""))
            if not section:
                return "", "", "FGIF 伞级报告中未找到该子基金章节"
            return (
                _ilas_latest_url(),
                f'python download_fidelity_ilas_factsheet.py "{row.get("fund")}"',
                "",
            )
        # 其它官网无对应系列的伞：不认领，交给其它脚本/最终汇总
        return "", "", None
    cn_key = _cn_key(str(row.get("fund") or ""), umbrella)
    rows = index.get((series, "cn", cn_key))
    if not rows:
        en_key = _en_key(_en_core(str(row.get("english") or "")))
        rows = index.get((series, "en", en_key)) if en_key else None
    if not rows:
        return "", "", None
    with_factsheet = [item for item in rows if item.get("has_factsheet")]
    if not with_factsheet:
        return "", "", "官网暂未发布月报，跳过"
    chosen = pick_share_class(with_factsheet, field="currency")
    fund_id = str(chosen.get("fund_id") or "")
    if not fund_id:
        return "", "", None
    link = f"{BASE_URL}/zh/funds/factsheet/{fund_id}/tab-documents"
    return link, f"python download_fidelity_factsheet.py {fund_id}", ""


def main(argv: list[str] | None = None) -> int:
    """命令行入口：单基金查询 / --probe / --backfill。"""
    return cli_main(
        argv,
        description="按爬虫list.xlsx富达表 B 列基金名查询富达香港月报链接（伞优先匹配）",
        sheet_name=SHEET_NAME,
        catalog_loader=_fetch_catalog,
        index_builder=_build_index,
        resolver=_resolve_single,
        single_print_run_command=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
