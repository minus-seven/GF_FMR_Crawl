#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""中银保诚香港公开基金目录 / Factsheet（月报）适配器。

官网 ``/en/api/UTFund/utListing`` 是中银保诚官网基金页使用的公开 JSON
接口。一个基金会按 share class 返回多行，但 ``pdf.fs`` 是基金级 Factsheet；
因此本适配器只按基础基金代码建立索引，并选择港元、美元优先的份额作为
可追溯入口。没有 ``pdf.fs`` 的记录不回退到 KFS、年报或通用报告，避免将
非月报文件写入月报列。
"""

from __future__ import annotations

import html
import os
import re
import sys
from typing import Any
from urllib.parse import urljoin

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))

from common.fund_link_backfill import (  # noqa: E402
    cli_main,
    normalize_name,
    pick_share_class,
)
from common_paths import configure_session  # noqa: E402


SHEET_NAME = "中银保诚"
BASE_URL = "https://www.boci-pru.com.hk"
LISTING_PAGE = BASE_URL + "/en/ut-prices"
LISTING_API = BASE_URL + "/en/api/UTFund/utListing"
RUN_COMMAND = 'python download_bocip_factsheet.py "{url}" "{english}" "{fund_code}" "{doc_url}"'
TIMEOUT = 90
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": LISTING_PAGE,
}
SESSION = configure_session(requests.Session())


def _strip_html(value: str) -> str:
    """去除 HTML 标记；参数 value 为官网返回的带标记文本。"""

    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", str(value or ""))).split())


def _en_key(value: str) -> str:
    """去掉份额类别、品牌和标记；参数 value 为页表或官网英文基金名。"""

    text = _strip_html(value).casefold()
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"[ℽ♦△♯§*^Ω]+", " ", text)
    text = text.replace("&", " and ")
    # 页表中的历史品牌名与当前目录不同，但产品词相同。
    for token in (
        "boc-prudential",
        "boci-prudential",
        "bochk",
        "bocip",
        "fund",
        "funds",
        "series",
    ):
        text = re.sub(rf"\b{re.escape(token)}\b", " ", text)
    return re.sub(r"[^a-z0-9]+", "", text)


# 页表中的历史/伞名产品与当前官网品牌的可验证映射。仅处理确定的
# 品牌迁移；无法证明是同一产品的名称（例如 Asia Equity 与 Asia Pacific
# Equity）不做相似猜测。
ALIASES = {
    _en_key("BOC-Prudential Hong Kong Equity Fund"): "BOC-HKEQ",
    _en_key("BOC-Prudential Global Bond Fund"): "BOC-GBD",
    _en_key("BOC-Prudential China Equity Fund"): "BOC-CEQ",
    _en_key("BOC-Prudential Global Equity Fund"): "BOC-GEQ",
    _en_key("BOC-Prudential Japan Equity Fund"): "BOC-JEQ",
    _en_key("BOCHK Asia Pacific Equity Fund"): "BOC-APEQ",
    _en_key("NCB China Equity Fund"): "NCB-CEQ",
    _en_key("NCB HK Dollar Money Market Fund"): "NCB-HKDMMF",
}


def _fetch_catalog() -> list[dict[str, Any]]:
    """读取官网当前目录；本函数无参数，返回含基金页和文档字段的记录。"""

    response = SESSION.post(
        LISTING_API,
        json={"query": "", "currency": [], "dist": [], "investment": [], "region": [], "riskLv": [], "Lang": "en"},
        headers=HEADERS,
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    records = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(records, list) or not records:
        raise RuntimeError("中银保诚官网 utListing 接口为空或格式异常")
    return [item for item in records if isinstance(item, dict)]


def _build_index(catalog: list[dict[str, Any]]) -> dict[str, Any]:
    """按 fund code 和英文核心名建立索引；不把 share class 当成独立基金。"""

    by_code: dict[str, list[dict[str, Any]]] = {}
    by_name: dict[str, list[dict[str, Any]]] = {}
    for item in catalog:
        code = str(item.get("utFundCode") or "").strip()
        name = _strip_html((item.get("fundName") or {}).get("nameEn", ""))
        if code:
            # 基础代码是去掉份额后缀的最稳健索引；具体基金名映射优先使用
            # 明确别名，否则取 name key 的全部候选。
            base = re.sub(r"-(?:RHKD|RUSD|RRMB|IHKD|IUSD|MHKD|B|M|USD|RMB|HKD|HR|ACC|DIS)$", "", code)
            by_code.setdefault(base, []).append(item)
        if name:
            by_name.setdefault(_en_key(name), []).append(item)
    return {"by_code": by_code, "by_name": by_name, "catalog": catalog}


def _find_record(row: dict[str, Any], index: dict[str, Any]) -> dict[str, Any] | None:
    """匹配一行基金；参数 row 为页表行、index 为官网索引。"""

    english = str(row.get("english") or "").strip()
    key = _en_key(english)
    code = ALIASES.get(key)
    candidates: list[dict[str, Any]] = []
    if code:
        candidates = index["by_code"].get(code, [])
    if not candidates:
        candidates = index["by_name"].get(key, [])
    if not candidates and key:
        # 当前目录有时在品牌前缀/文字标记上与页表不同，允许严格的
        # 核心词包含匹配；不使用中文近似名，避免跨基金误配。
        for candidate_key, records in index["by_name"].items():
            if key == candidate_key or (len(key) >= 12 and (key in candidate_key or candidate_key in key)):
                candidates.extend(records)
    if not candidates:
        return None
    return pick_share_class(candidates)


def _resolve_single(row: dict[str, Any], index: dict[str, Any]) -> tuple[str, str, str | None]:
    """解析单行链接；参数 row 为页表行、index 为官网索引。"""

    record = _find_record(row, index)
    if not record:
        return "", "", "NO_CURRENT_FUND：中银保诚官网当前目录未找到可验证基金页"
    pdf = record.get("pdf") or {}
    doc_path = str(pdf.get("fs") or "").strip().replace("\\", "/")
    if not doc_path:
        return "", "", "NO_FACTSHEET：官网基金页没有单只 Factsheet（月报），仅有价格/监管资料"
    page_path = str((record.get("fundName") or {}).get("link") or record.get("priceLink") or "").strip()
    if not page_path:
        return "", "", "NO_CURRENT_FUND：官网目录未返回基金详情页"
    page_url = urljoin(BASE_URL, page_path)
    doc_url = urljoin(BASE_URL, doc_path)
    fund_code = str(record.get("utFundCode") or "").strip()
    english = str(row.get("english") or row.get("fund") or "").strip()
    command = RUN_COMMAND.format(
        url=page_url,
        english=english,
        fund_code=fund_code,
        doc_url=doc_url,
    )
    return page_url, command, ""


def main(argv: list[str] | None = None) -> int:
    return cli_main(
        argv,
        description="按中银保诚官网 utListing 和单只 Factsheet 补全月报入口",
        sheet_name=SHEET_NAME,
        catalog_loader=_fetch_catalog,
        index_builder=_build_index,
        resolver=_resolve_single,
        single_print_run_command=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
