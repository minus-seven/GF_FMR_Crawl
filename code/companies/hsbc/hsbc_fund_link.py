#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
汇丰（HSBC 资产管理香港）基金月报爬虫补全脚本。

名称匹配来源：汇丰基金中心页面（SPA）的目录接口与搜索接口，均为
  POST /api/v1/nav/funds
搜索页 `?s=<名称或ISIN>` 会把输入拆成 `searchTerm: [...]`，接口直接返回
基金名及全部股份类别 ISIN。该接口需要 token；本脚本用真实浏览器捕获一次
（URL + 请求体 + 授权头），再用 requests 回放目录与名称搜索。

月报下载接口（纯 requests 可用，非 HK 注册的 ISIN 同样可下载）：
  /api/v1/download/document/<isin>/hk/zh/factsheet

I = https://www.assetmanagement.hsbc.com.hk/zh/intermediary/fund-centre/<isin>
J = python download_hsbc_factsheet.py <isin>

用法：
  python hsbc_fund_link.py 汇丰亚洲债券基金
  python hsbc_fund_link.py --probe --excel /path/to/爬虫list.xlsx --sheet 汇丰
  python hsbc_fund_link.py --backfill --excel /path/to/爬虫list.xlsx --sheet 汇丰
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
sys.path.insert(0, HERE)

from common.browser_fetch import launch_browser, new_context  # noqa: E402
from common.fund_link_backfill import (  # noqa: E402
    cli_main,
    normalize_name,
)
from download_hsbc_annual_factsheet import (  # noqa: E402
    ANNUAL_REPORT_URL,
    resolve_section as _annual_section,
)
from download_hsbc_it_factsheet import (  # noqa: E402
    resolve_report_section as _ifp_section,
)
from playwright.sync_api import sync_playwright  # noqa: E402


SHEET_NAME = "汇丰"
BASE_URL = "https://www.assetmanagement.hsbc.com.hk"
FUND_CENTRE_PATH = "/zh/intermediary/fund-centre"
UK_BASE_URL = "https://www.assetmanagement.hsbc.co.uk"
UK_FUND_CENTRE_PATH = "/en/individual-investor/funds"
NAV_FUNDS_PATH = "/api/v1/nav/funds"
FACTSHEET_API = BASE_URL + "/api/v1/download/document/{isin}/hk/zh/factsheet"
DETAIL_URL = BASE_URL + FUND_CENTRE_PATH + "/{isin}"
UK_FACTSHEET_API = UK_BASE_URL + "/api/v1/download/document/{isin}/gb/en/factsheet"
UK_DETAIL_URL = UK_BASE_URL + UK_FUND_CENTRE_PATH + "/{isin}"
PAGE_LOAD_TIMEOUT_MS = 90000
SETTLE_MS = 4000
TIMEOUT = 40
_NAV_CAPTURE_CACHE: dict[str, dict[str, Any]] = {}
_SEARCH_CACHE: dict[tuple[str, tuple[str, ...]], list[dict[str, Any]]] = {}


def _norm(value: str) -> str:
    """中文名归一化：简繁统一、异体字（滙/匯）统一、去空格符号与尾部标注。"""
    text = normalize_name(value)
    text = text.replace("滙", "匯").replace("汇", "匯").replace("証", "證")
    text = text.replace("*", "").replace("＊", "")
    while True:
        trimmed = re.sub(r"\([^()]*\)$", "", text)
        if trimmed == text:
            break
        text = trimmed
    for token in ("-", "－", "–", "—", "−", "─", "（", "）", "(", ")", "＋", "+", " ", "\u3000"):
        text = text.replace(token, "")
    return text


def _en(value: str) -> str:
    """英文名归一化：去 HSBC/汇丰 等品牌词，仅保留字母数字。"""
    text = str(value or "").strip().lower()
    for token in ("hsbc", "匯豐", "汇丰"):
        text = text.replace(token, "")
    return re.sub(r"[^a-z0-9]", "", text)


def _capture_nav_request(page_url: str) -> dict[str, Any]:
    """用真实浏览器打开基金中心页，捕获 nav/funds 的请求体与授权头。"""
    if page_url in _NAV_CAPTURE_CACHE:
        return _NAV_CAPTURE_CACHE[page_url]
    captured: dict[str, Any] = {}

    def _on_request(request):
        """记录首个 nav/funds POST 请求。"""
        if (
            not captured
            and request.method == "POST"
            and NAV_FUNDS_PATH in request.url
        ):
            captured["url"] = request.url
            captured["body"] = request.post_data
            captured["headers"] = dict(request.headers)

    with sync_playwright() as playwright:
        browser = launch_browser(playwright, headless=True)
        context = new_context(browser, locale="zh-HK")
        page = context.new_page()
        page.on("request", _on_request)
        page.goto(page_url, wait_until="networkidle", timeout=PAGE_LOAD_TIMEOUT_MS)
        page.wait_for_timeout(SETTLE_MS)
        browser.close()
    if not captured:
        raise RuntimeError("未捕获到汇丰 nav/funds 请求，站点结构或鉴权流程可能已变更")
    _NAV_CAPTURE_CACHE[page_url] = captured
    return captured


def _request_headers(captured: dict[str, Any]) -> dict[str, str]:
    """用捕获到的浏览器请求头构造回放请求头。"""
    origin_match = re.match(r"(https?://[^/]+)", captured["url"])
    origin = origin_match.group(1) if origin_match else BASE_URL
    headers = {
        "User-Agent": captured["headers"].get("user-agent", ""),
        "Content-Type": "application/json",
        "Origin": origin,
        "Referer": captured["headers"].get("referer", origin),
    }
    authorization = captured["headers"].get("authorization")
    if authorization:
        headers["Authorization"] = authorization
    return headers


def _fund_isins(fund: dict[str, Any]) -> list[str]:
    """取出该基金所有份额类别的 ISIN，HK 开头的排前面（下载脚本 HK 站优先）。"""
    isins: list[str] = []
    for share_class in fund.get("shareClasses") or []:
        for cell in share_class.get("data") or []:
            if cell.get("columnId") == "UniqueIdentifier" and cell.get("value"):
                isin = str(cell["value"]).strip()
                if isin and isin not in isins:
                    isins.append(isin)
    return sorted(isins, key=lambda item: (not item.lower().startswith("hk"), item))


def _normalize_fund(fund: dict[str, Any]) -> dict[str, Any]:
    """把汇丰接口的基金对象转成统一的名称与 ISIN 记录。"""
    return {
        "fund_id": str(fund.get("id") or ""),
        "fund_name": str(fund.get("name") or "").strip(),
        "isins": _fund_isins(fund),
    }


def _post_nav_page(
    captured: dict[str, Any], body: dict[str, Any], page_number: int
) -> dict[str, Any]:
    """用已捕获的授权会话回放一页 nav/funds 请求并返回 JSON。"""
    payload = json.loads(json.dumps(body))
    payload["paging"] = {"currentPage": page_number}
    payload["appliedFilters"] = []
    response = requests.post(
        captured["url"],
        headers=_request_headers(captured),
        json=payload,
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def _fetch_catalog_for(page_url: str) -> list[dict[str, Any]]:
    """抓取某一语言基金中心页的全量基金（名 + ISIN）。"""
    captured = _capture_nav_request(page_url)
    body = json.loads(captured["body"])
    funds: list[dict[str, Any]] = []
    total_pages = 1
    for page_number in range(1, 30):
        data = _post_nav_page(captured, body, page_number)
        funds.extend(data.get("funds") or [])
        total_pages = int((data.get("paging") or {}).get("totalPages") or 1)
        if page_number >= total_pages:
            break
    return [_normalize_fund(fund) for fund in funds]


def _search_funds(
    search_terms: list[str],
    page_url: str = BASE_URL + FUND_CENTRE_PATH,
) -> list[dict[str, Any]]:
    """调用指定汇丰基金中心站内搜索，返回匹配基金及 ISIN（进程内缓存）。"""
    terms = [str(term).strip() for term in search_terms if str(term).strip()]
    cache_key = (page_url, tuple(terms))
    if cache_key in _SEARCH_CACHE:
        return _SEARCH_CACHE[cache_key]
    if not terms:
        return []
    captured = _capture_nav_request(page_url)
    body = json.loads(captured["body"])
    body["searchTerm"] = terms
    funds: list[dict[str, Any]] = []
    total_pages = 1
    for page_number in range(1, 10):
        data = _post_nav_page(captured, body, page_number)
        funds.extend(_normalize_fund(fund) for fund in (data.get("funds") or []))
        total_pages = int((data.get("paging") or {}).get("totalPages") or 1)
        if page_number >= total_pages:
            break
    _SEARCH_CACHE[cache_key] = funds
    return funds


def _fetch_catalog() -> list[dict[str, Any]]:
    """返回官网基金中心目录（中文名 + 英文名 + ISIN，按 fund_id 合并）。"""
    catalogs = [
        ("cn", _fetch_catalog_for(BASE_URL + FUND_CENTRE_PATH)),
        ("en", _fetch_catalog_for(BASE_URL + "/en/intermediary/fund-centre")),
    ]
    merged: dict[str, dict[str, Any]] = {}
    for language, records in catalogs:
        for record in records:
            key = record["fund_id"] or record["fund_name"]
            item = merged.setdefault(key, {"fund_id": record["fund_id"], "isins": record["isins"]})
            item[language] = record["fund_name"]
            if record["isins"] and not item.get("isins"):
                item["isins"] = record["isins"]
    if not merged:
        raise RuntimeError("汇丰基金中心未返回任何基金，请检查接口是否变更")
    return list(merged.values())


def _build_index(catalog: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """建立 (语言, 归一化名) → 基金记录 的索引。"""
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for record in catalog:
        if record.get("cn"):
            index.setdefault(("cn", _norm(record["cn"])), record)
        if record.get("en"):
            index.setdefault(("en", _en(record["en"])), record)
    return index


def _has_factsheet(isin: str, market: str = "hk") -> bool:
    """用流式请求探测香港或英国月报接口是否可下载（只读响应头）。"""
    api = UK_FACTSHEET_API if market == "uk" else FACTSHEET_API
    response = requests.get(
        api.format(isin=isin.lower()),
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=TIMEOUT,
        stream=True,
    )
    try:
        return response.status_code == 200 and "pdf" in (
            response.headers.get("content-type") or ""
        ).lower()
    finally:
        response.close()


def _search_record(row: dict[str, Any]) -> dict[str, Any] | None:
    """用官网搜索接口按英文名找基金，并以中英文归一化名称严格复核。"""
    english = str(row.get("english") or "").strip()
    if not english:
        return None
    queries = [
        english,
        re.sub(r"^HSBC\s+Global\s+Investment\s+Funds\s*[-–—]\s*", "", english, flags=re.I),
        re.sub(r"^HSBC\s+Global\s+Funds\s+ICAV\s*[-–—]\s*", "", english, flags=re.I),
        re.sub(r"\s+Local(?=\s+Bond\b)", "", english, flags=re.I),
        re.sub(r"^HSBC\s+", "", english, flags=re.I),
    ]
    target_cn = _norm(str(row.get("fund") or ""))
    target_en = _en(english)
    target_cn_short = target_cn.replace("当地", "").replace("本地", "")
    target_en_short = target_en.replace("local", "")

    def _pick(records: list[dict[str, Any]]) -> dict[str, Any] | None:
        """从一批搜索结果中选唯一最佳名称匹配。"""
        scored: list[tuple[int, dict[str, Any]]] = []
        for record in records:
            candidate_cn = _norm(record.get("fund_name") or "")
            candidate_en = _en(record.get("fund_name") or "")
            candidate_cn_short = candidate_cn.replace("当地", "").replace("本地", "")
            candidate_en_short = candidate_en.replace("local", "")
            score = 0
            if target_cn and candidate_cn:
                if target_cn == candidate_cn:
                    score = max(score, 1000)
                elif target_cn in candidate_cn or candidate_cn in target_cn:
                    score = max(score, 800 + min(len(target_cn), len(candidate_cn)))
            if target_en and candidate_en:
                if target_en == candidate_en:
                    score = max(score, 900)
                elif target_en in candidate_en or candidate_en in target_en:
                    score = max(score, 700 + min(len(target_en), len(candidate_en)))
            if target_cn_short and candidate_cn_short:
                if target_cn_short == candidate_cn_short:
                    score = max(score, 990)
                elif (
                    target_cn_short in candidate_cn_short
                    or candidate_cn_short in target_cn_short
                ):
                    score = max(
                        score,
                        790 + min(len(target_cn_short), len(candidate_cn_short)),
                    )
            if target_en_short and candidate_en_short:
                if target_en_short == candidate_en_short:
                    score = max(score, 890)
                elif (
                    target_en_short in candidate_en_short
                    or candidate_en_short in target_en_short
                ):
                    score = max(
                        score,
                        690 + min(len(target_en_short), len(candidate_en_short)),
                    )
            if score:
                scored.append((score, record))
        if not scored:
            return None
        scored.sort(key=lambda item: item[0], reverse=True)
        if len(scored) > 1 and scored[0][0] == scored[1][0]:
            return None
        record = scored[0][1]
        return record if record.get("isins") else None

    for query in dict.fromkeys(item.strip() for item in queries if item.strip()):
        try:
            records = _search_funds(query.split())
        except requests.RequestException:
            continue
        match = _pick(records)
        if match:
            return match
    return None


def _search_uk_record(row: dict[str, Any]) -> dict[str, Any] | None:
    """对香港官网未匹配的英文基金，在英国官网搜索并按英文名严格复核。"""
    english = str(row.get("english") or "").strip()
    if not english or len(re.findall(r"[A-Za-z]", english)) < 3:
        return None
    short = re.sub(
        r"^HSBC\s+(?:Global\s+(?:Investment\s+Funds|Funds\s+ICAV)|"
        r"Index\s+Tracker\s+Investment\s+Funds|Managed\s+Funds)\s*[-–—]?\s*",
        "",
        english,
        flags=re.I,
    ).strip()
    for query in dict.fromkeys(item for item in (short, english) if item):
        try:
            records = _search_funds(query.split(), UK_BASE_URL + UK_FUND_CENTRE_PATH)
        except requests.RequestException:
            continue
        target = _en(english)
        scored: list[tuple[int, dict[str, Any]]] = []
        for record in records:
            candidate = _en(record.get("fund_name") or "")
            if target and candidate == target:
                scored.append((1000, record))
            elif target and candidate and (target in candidate or candidate in target):
                scored.append((800 + min(len(target), len(candidate)), record))
        if not scored:
            continue
        scored.sort(key=lambda item: item[0], reverse=True)
        if len(scored) > 1 and scored[0][0] == scored[1][0]:
            continue
        record = scored[0][1]
        if record.get("isins"):
            return record
    return None


def _resolve_single(
    row: dict[str, Any], index: dict[tuple[str, str], dict[str, Any]]
) -> tuple[str, str, str | None]:
    """解析一支基金，返回 (I月报链接, J运行方式, 跳过原因)；不认领时原因为 None。"""
    english = str(row.get("english") or "").strip()

    def _annual_fallback():
        """HSBC GIF 子基金：月报缺失时退回伞级年报章节。"""
        if not english.lower().startswith("hsbc global investment funds"):
            return None
        if not _annual_section(english):
            return None
        return (
            ANNUAL_REPORT_URL,
            f'python download_hsbc_annual_factsheet.py "{english}"',
            "",
        )

    def _uk_fallback():
        """香港目录未命中的英文基金，尝试英国官网页面及最新 Factsheet。"""
        record = _search_uk_record(row)
        if not record:
            return None
        for isin in record.get("isins") or []:
            if not _has_factsheet(isin, market="uk"):
                continue
            detail_url = UK_DETAIL_URL.format(isin=isin.lower())
            return (
                detail_url,
                f'python download_hsbc_factsheet.py "{detail_url}"',
                "",
            )
        return None

    def _index_tracker_fallback():
        """用港交所 IFP 官方搜索找基金编号，并切分最新可用财报章节。"""
        if not english:
            return None
        try:
            section = _ifp_section(english, chinese_name=str(row.get("fund") or ""))
        except (OSError, RuntimeError, ValueError, requests.RequestException):
            return None
        if not section:
            return None
        return (
            section["url"],
            f'python download_hsbc_it_factsheet.py "{english}"',
            "",
        )

    record = index.get(("cn", _norm(str(row.get("fund") or ""))))
    if not record:
        record = index.get(("en", _en(english)))
    if not record:
        record = _search_record(row)
    if not record:
        fallback = _uk_fallback() or _annual_fallback() or _index_tracker_fallback()
        if fallback:
            return fallback
        return "", "", "汇丰基金中心与港交所均未找到可用月报/财报"
    isins = record.get("isins") or []
    if not isins:
        return "", "", "官网未提供该基金 ISIN"
    for isin in isins:
        if _has_factsheet(isin):
            return (
                DETAIL_URL.format(isin=isin.lower()),
                f"python download_hsbc_factsheet.py {isin.lower()}",
                "",
            )
    search_record = _search_record(row)
    if search_record and search_record is not record:
        for isin in search_record.get("isins") or []:
            if _has_factsheet(isin):
                return (
                    DETAIL_URL.format(isin=isin.lower()),
                    f"python download_hsbc_factsheet.py {isin.lower()}",
                    "",
                )
    fallback = _uk_fallback() or _annual_fallback() or _index_tracker_fallback()
    if fallback:
        return fallback
    return "", "", f"官网无月报文件（ISIN {', '.join(isins)}）"


def main(argv: list[str] | None = None) -> int:
    """命令行入口：单基金查询 / --probe / --backfill。"""
    return cli_main(
        argv,
        description="按爬虫list.xlsx汇丰表 B 列基金名查询官网月报链接（含批量补全）",
        sheet_name=SHEET_NAME,
        catalog_loader=_fetch_catalog,
        index_builder=_build_index,
        resolver=_resolve_single,
        single_print_run_command=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
