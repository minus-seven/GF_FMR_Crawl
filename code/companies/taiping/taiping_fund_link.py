#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
太平（中国太平 / Taiping）香港基金月报爬虫补全脚本。

逻辑（2026-10 重构，替代“手工维护随机 PDF 直链”）：
  1. 目录：基金列表页 https://www.tpfh.cntaiping.com/sc/ywjs-jj/
     表格形如 `| 太平美元货币基金 TPMMFUA HK … | [查阅](…/ywjs-taipingmmfundinfo/) |`
     → 得到「基金名 ↔ 基本资料页」。
  2. 若 `taiping_links.json` 里配置了该基金的 `pages` 覆盖，则优先使用配置页；
     否则按 **伞名 → 中文核心名 → 英文核心名** 在目录里匹配（匹配规则同《新公司接入SOP》）。
  3. 抓该基本资料页，在「基金概览」板块里找形如
       `基金概览 (二零二六年九月)_TGCNMEB [下载](https://www.cntaiping.com/upload/cms/tpfh/…pdf)`
     的条目，按中文日期取最新一期，并把 PDF 直链探活后回填。

WAF 说明：`www.tpfh.cntaiping.com` 对本机返回 412，本脚本先直连，失败时自动改用
      `https://r.jina.ai/<url>` 阅读代理取同一页面（内容一致，仅去掉导航噪音）。

I = 最新一期「基金概览」PDF 直链
J = python download_taiping_factsheet.py "<I>"

用法：
  python taiping_fund_link.py 太平大中华新动力股票基金
  python taiping_fund_link.py --probe --excel /path/to/爬虫list.xlsx --sheet 太平
  python taiping_fund_link.py --backfill --excel /path/to/爬虫list.xlsx --sheet 太平
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

from common.fund_link_backfill import cli_main, normalize_name  # noqa: E402


SHEET_NAME = "太平"
BASE_URL = "https://www.tpfh.cntaiping.com"
FUND_LIST_URL = BASE_URL + "/sc/ywjs-jj/"
JINA_PREFIX = "https://r.jina.ai/"
LINKS_PATH = os.path.join(HERE, "taiping_links.json")
MONTHLY_TITLE = re.compile(r"基金概[览覽]\s*[\(（]\s*([^\)）]{2,30}?)\s*[\)）]", re.M)
PDF_LINK_RE = re.compile(r"\((https?://[^)\s]+?\.pdf)\)")
FUND_LINK_RE = re.compile(r"\((https://www\.tpfh\.cntaiping\.com/sc/ywjs-[^)\s]+/?)\)")
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "zh-CN,zh;q=0.9",
}
TIMEOUT = 60
CN_DIGITS = {"〇": 0, "零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _cn(value: str) -> str:
    """中文核心名：简繁统一、去“太平/基金/股票/货币市场”等通用词与符号。"""
    text = normalize_name(value)
    for token in ("太平", "基金", "股票", "市場", "市场", "系列", "投資", "投资"):
        text = text.replace(token, "")
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", text)


def _en(value: str) -> str:
    """英文核心名：去 Taiping/Fund 等词，仅保留字母数字。"""
    text = str(value or "").strip().lower()
    for token in ("taiping", "fund", "funds", "series"):
        text = text.replace(token, "")
    return re.sub(r"[^a-z0-9]", "", text)


def _fetch(url: str) -> str:
    """抓取页面：先直连，遇 412/异常改用 r.jina.ai 阅读代理。"""
    try:
        response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if response.status_code == 200 and len(response.text) > 2000:
            return response.text
    except Exception:  # noqa: BLE001 - 继续走代理兜底
        pass
    proxied = requests.get(JINA_PREFIX + url, headers=HEADERS, timeout=TIMEOUT + 30)
    proxied.raise_for_status()
    return proxied.text


def _cn_number(value: str) -> int | None:
    """把“二零二六”“九”这类中文数字转成整数（支持 0-99 足够用于年月）。"""
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if "十" in text:
        head, _, tail = text.partition("十")
        tens = int("".join(str(CN_DIGITS[c]) for c in head) or 1)
        ones = int("".join(str(CN_DIGITS[c]) for c in tail) or 0)
        return tens * 10 + ones
    try:
        return int("".join(str(CN_DIGITS[c]) for c in text))
    except (KeyError, ValueError):
        return None


def _parse_period(label: str) -> tuple[int, int]:
    """解析“二零二六年九月”“2026年9月”为 (年, 月)，失败返回 (0, 0)。"""
    text = str(label or "")
    match = re.search(r"(\d{4})\s*年?\s*(\d{1,2})?\s*月?", text)
    if match:
        return int(match.group(1)), int(match.group(2) or 0)
    match = re.search(r"([〇零一二三四五六七八九]{2,4})年\s*([一二三四五六七八九十]{1,3})月", text)
    if match:
        year = _cn_number(match.group(1)) or 0
        month = _cn_number(match.group(2)) or 0
        return year, month
    return 0, 0


def _load_page_overrides() -> dict[str, str]:
    """读取 taiping_links.json 的 pages 配置（基金名 → 基本资料页）。"""
    if not os.path.exists(LINKS_PATH):
        return {}
    try:
        with open(LINKS_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle) or {}
    except (OSError, json.JSONDecodeError):
        return {}
    pages = data.get("pages") or {}
    return {str(key).strip(): str(value).strip() for key, value in pages.items() if key and value}


def _parse_fund_list(page_text: str) -> list[dict[str, Any]]:
    """从基金列表页的 Markdown 表格解析「基金名 + 代码 + 资料页」。"""
    funds: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line in page_text.splitlines():
        if "|" not in line:
            continue
        links = FUND_LINK_RE.findall(line)
        if not links:
            continue
        page_url = links[0]
        if page_url in seen:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not cells:
            continue
        first = " ".join(cells[0].split())
        if not first or first.startswith("---"):
            continue
        codes = re.findall(r"\b([A-Z]{2,10}\d{0,10})\b", first)
        name = re.sub(r"\b[A-Z]{2,10}\d{0,10}(?:\s+HK)?\b", "", first)
        name = re.sub(r"\s+", " ", name).strip(" -")
        if not name:
            continue
        seen.add(page_url)
        funds.append({"fund_name": name, "page_url": page_url, "codes": codes, "source": "list"})
    return funds


def _fetch_catalog() -> list[dict[str, Any]]:
    """返回基金目录：列表页解析结果 + 配置文件里的资料页覆盖。"""
    catalog: list[dict[str, Any]] = []
    try:
        list_text = _fetch(FUND_LIST_URL)
        catalog.extend(_parse_fund_list(list_text))
    except Exception:  # noqa: BLE001 - 列表页失败时仍可用配置覆盖
        pass
    known_pages = {item["page_url"] for item in catalog}
    for name, page_url in _load_page_overrides().items():
        if page_url in known_pages:
            for item in catalog:
                if item["page_url"] == page_url:
                    item["fund_name"] = name
            continue
        catalog.append({"fund_name": name, "page_url": page_url, "codes": [], "source": "config"})
    if not catalog:
        raise RuntimeError("太平基金目录为空：基金列表页与配置均不可用")
    return catalog


def _build_index(catalog: list[dict[str, Any]]) -> dict[str, Any]:
    """建立中文/英文核心名索引，并保留原始列表用于伞名兜底。"""
    index: dict[str, Any] = {"cn": {}, "en": {}, "all": catalog}
    for record in catalog:
        index["cn"].setdefault(_cn(record["fund_name"]), record)
    return index


def _match_record(row: dict[str, Any], index: dict[str, Any]) -> dict[str, Any] | None:
    """匹配基金：中文核心名 → 英文核心名 → 伞名+基金名 → 包含兜底。"""
    fund = str(row.get("fund") or "").strip()
    umbrella = str(row.get("umbrella") or "").strip()
    for key in (_cn(fund), _cn(f"{umbrella}{fund}") if umbrella else ""):
        if key and key in index["cn"]:
            return index["cn"][key]
    english = _en(str(row.get("english") or ""))
    if english:
        for candidate in index["cn"].values():
            if english and english in _en(candidate["fund_name"]):
                return candidate
    for key in (_cn(fund), _cn(f"{umbrella}{fund}") if umbrella else ""):
        if len(key) < 4:
            continue
        for candidate_key, record in index["cn"].items():
            if candidate_key and (key in candidate_key or candidate_key in key):
                return record
    return None


_MONTHLY_CACHE: dict[str, dict[str, Any] | None] = {}


def _latest_monthly(page_url: str) -> dict[str, Any] | None:
    """抓基本资料页，返回最新一期「基金概览」（含 PDF 直链与期间）。"""
    if page_url in _MONTHLY_CACHE:
        return _MONTHLY_CACHE[page_url]
    result = None
    try:
        text = _fetch(page_url)
    except Exception:  # noqa: BLE001 - 页面不可用则视为无月报
        _MONTHLY_CACHE[page_url] = None
        return None
    entries = []
    # 标题后紧跟“下载”图标链接（Jina 的 Markdown 里是 [下载 ![Image](icon)](pdf)），
    # 直接取标题之后、下一个条目标题之前的第一个 PDF 链接，兼容有无图标两种写法。
    matches = list(MONTHLY_TITLE.finditer(text))
    for index, match in enumerate(matches):
        window_end = matches[index + 1].start() if index + 1 < len(matches) else match.end() + 600
        window = text[match.end(): min(window_end, match.end() + 600)]
        pdf_match = PDF_LINK_RE.search(window)
        if not pdf_match:
            continue
        label = match.group(1)
        year, month = _parse_period(label)
        entries.append({"label": label, "url": pdf_match.group(1), "year": year, "month": month})
    if entries:
        entries.sort(key=lambda item: (item["year"], item["month"]), reverse=True)
        result = entries[0]
    _MONTHLY_CACHE[page_url] = result
    return result


def _probe_pdf(url: str) -> bool:
    """探活 PDF 直链（200 且 content-type 含 pdf）。"""
    try:
        response = requests.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True)
        ok = response.status_code == 200 and "pdf" in (response.headers.get("content-type") or "").lower()
        response.close()
        return ok
    except Exception:  # noqa: BLE001
        return False


def _resolve_single(row: dict[str, Any], index: dict[str, Any]) -> tuple[str, str, str | None]:
    """解析一支基金，返回 (I月报链接, J运行方式, 跳过原因)；不认领时原因为 None。"""
    record = _match_record(row, index)
    if not record:
        return "", "", "基金列表页未找到该基金的基本资料页（名称/伞名不匹配）"
    monthly = _latest_monthly(record["page_url"])
    if not monthly:
        return "", "", "该基金基本资料页无「基金概览」月报条目"
    if not _probe_pdf(monthly["url"]):
        return "", "", f"基金概览直链不可下载：{monthly['url']}"
    return monthly["url"], f'python download_taiping_factsheet.py "{monthly["url"]}"', ""


def main(argv: list[str] | None = None) -> int:
    """命令行入口：单基金查询 / --probe / --backfill。"""
    return cli_main(
        argv,
        description="按爬虫list.xlsx太平表 B 列基金名查询官网最新基金概览（含批量补全）",
        sheet_name=SHEET_NAME,
        catalog_loader=_fetch_catalog,
        index_builder=_build_index,
        resolver=_resolve_single,
        single_print_run_command=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
