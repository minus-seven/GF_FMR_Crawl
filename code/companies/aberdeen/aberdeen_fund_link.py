#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""安本标准香港基金 Factsheet（月报）入口补全适配器。

安本基金总览页由 Next.js 渲染，但官方同源 gateway 提供稳定的
``funds/overview`` 目录接口。本适配器通过真实浏览器调用该接口，
取当前基金、份额和最新 Factsheet 直链，不把 KID、PHS、年报或宣传册当月报。
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))

from common.browser_fetch import launch_browser, new_context  # noqa: E402
from common.fund_link_backfill import cli_main, normalize_name  # noqa: E402


SHEET_NAME = "安本标准"
BASE_SITE = "https://www.aberdeeninvestments.com"
FUND_CENTRE_URL = BASE_SITE + "/en-hk/investor/funds/view-all-funds"
OVERVIEW_PATH = "/api/gateway/funds/overview"
RUN_COMMAND = 'python download_aberdeen_factsheet.py "{url}" "{english}" "{factsheet_url}"'
TIMEOUT_MS = 60000


def _key(value: str) -> str:
    """归一化英文基金名；参数 value 为页表或官方目录名称。"""

    text = normalize_name(str(value or "")).casefold()
    text = text.replace("–", "-").replace("—", "-")
    text = re.sub(r"[^a-z0-9]+", "", text)
    return text


def _fetch_catalog() -> list[dict[str, Any]]:
    """通过安本官方同源 gateway 读取当前基金目录；本函数无参数。"""

    body = {
        "countryInvestors": {
            "countryCode": "HKG",
            "investorType": "4,1",
            "jurisdiction": "Live",
            "literatureAuthorization": "1",
        },
        "searchQuery": {"name": ""},
        "language": "en-HK",
        "site": "Investor",
        "skip": 0,
        "take": 100,
        "filters": [
            {"name": "fund_product_type", "values": []},
            {"name": "asset_class", "values": []},
        ],
        "tab": "overview",
    }
    with sync_playwright() as playwright:
        browser = launch_browser(playwright, headless=True)
        try:
            context = new_context(browser, locale="en-HK")
            try:
                page = context.new_page()
                page.goto(
                    FUND_CENTRE_URL,
                    wait_until="domcontentloaded",
                    timeout=TIMEOUT_MS,
                )
                payload = page.evaluate(
                    """async ({path, body}) => {
                        const response = await fetch(path, {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            credentials: 'include',
                            body: JSON.stringify(body)
                        });
                        return {status: response.status, text: await response.text()};
                    }""",
                    {"path": OVERVIEW_PATH, "body": body},
                )
            finally:
                context.close()
        finally:
            browser.close()
    if int(payload.get("status") or 0) != 200:
        raise RuntimeError(
            f"安本官方目录 API 失败：HTTP {payload.get('status')}"
        )
    parsed = json.loads(str(payload.get("text") or "{}"))
    records = ((parsed.get("content") or {}).get("overview") or [])
    if not isinstance(records, list) or not records:
        raise RuntimeError("安本官方目录为空或格式异常")
    return [item for item in records if isinstance(item, dict)]


def _build_index(catalog: list[dict[str, Any]]) -> dict[str, Any]:
    """建立归一化英文名索引；参数 catalog 为安本官方基金目录。"""

    index: dict[str, list[dict[str, Any]]] = {}
    for record in catalog:
        name = str(record.get("name") or "").strip()
        if name:
            index.setdefault(_key(name), []).append(record)
    return {"index": index, "catalog": catalog}


def _pick_share_class(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    """选择有 Factsheet 的优先份额；参数 records 为同基金目录记录。"""

    candidates: list[dict[str, Any]] = []
    for record in records:
        for shareclass in record.get("shareclasses") or []:
            if not isinstance(shareclass, dict):
                continue
            factsheet = shareclass.get("factsheet")
            if isinstance(factsheet, dict) and str(factsheet.get("documentURI") or "").strip():
                shareclass_name = str(shareclass.get("shareclassName") or "")
                currency_rank_value = (
                    0 if "HKD" in shareclass_name or "HK$" in shareclass_name
                    else 1 if "USD" in shareclass_name or "US$" in shareclass_name
                    else 2
                )
                candidates.append({
                    "record": record,
                    "shareclass": shareclass,
                    "currency_rank": currency_rank_value,
                })
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: item["currency_rank"])[0]


def _resolve_single(row: dict[str, Any], index: dict[str, Any]) -> tuple[str, str, str | None]:
    """解析一行安本月报入口；参数 row 为页表行、index 为官方目录索引。"""

    english = str(row.get("english") or row.get("fund") or "").strip()
    records = index["index"].get(_key(english)) or []
    if not records:
        return "", "", "NO_CURRENT_FUND：安本香港当前官方目录未返回该基金"
    selected = _pick_share_class(records)
    if not selected:
        return "", "", "NO_FACTSHEET：官方基金页存在，但当前目录没有单只 Factsheet"
    factsheet = selected["shareclass"].get("factsheet") or {}
    factsheet_url = str(factsheet.get("documentURI") or "").strip()
    return (
        FUND_CENTRE_URL,
        RUN_COMMAND.format(url=FUND_CENTRE_URL, english=english, factsheet_url=factsheet_url),
        "",
    )


def main(argv: list[str] | None = None) -> int:
    """运行安本标准 CLI；参数 argv 支持单基金、--probe、--backfill。"""

    return cli_main(
        argv,
        description="按安本香港官方基金目录补全 Factsheet 月报入口",
        sheet_name=SHEET_NAME,
        catalog_loader=_fetch_catalog,
        index_builder=_build_index,
        resolver=_resolve_single,
        single_print_run_command=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
