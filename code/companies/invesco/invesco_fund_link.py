#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""景顺香港基金月报入口补全适配器。

普通互惠基金和景顺特选退休基金使用同一官方产品搜索 API 的两个
accountType；基金详情页中的 Factsheet 文档在运行时读取，避免固化
Morningstar 的临时文档地址。
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
import unicodedata
from typing import Any
from urllib.parse import urljoin

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))

from common.fund_link_backfill import cli_main, currency_rank  # noqa: E402
from common_paths import configure_session  # noqa: E402


SHEET_NAME = "景顺"
BASE_URL = "https://www.invesco.com"
SEARCH_API = "https://dng-api.invesco.com/product/search"
RUN_COMMAND = 'python download_invesco_factsheet.py "{url}" "{english}" "{isin}" "{account_type}"'
TIMEOUT = 30
HEADERS = {
    # Invesco's edge rejects generic browser UA for this JSON endpoint.
    "User-Agent": "invesco-client/1.0",
    "Referer": "https://www.invesco.com/",
    "Accept": "application/json, text/plain, */*",
}
PAGE_FIELDS = (
    "url,title,accountName,isin,shareClassStatus,accountStatus,baseCurrency,"
    "shareClassName,shareClassFullName,language,countryCode"
)
SESSION = configure_session(requests.Session())
_DOCUMENT_CACHE: dict[str, dict[str, Any] | None] = {}


def _key(value: str) -> str:
    """把英文基金名归一化为可比较的核心名；参数 value 为页表或官网名称。"""

    text = unicodedata.normalize("NFKC", str(value or "").casefold())
    text = text.replace("&", " and ").replace("$", "usd")
    # 先处理长词/缩写，再去掉标点，覆盖官网 accountName 的常见缩写。
    replacements = (
        ("invesco select retirement fund", ""),
        ("invesco great wall", "igw"),
        ("invesco", ""),
        ("investment", "invest"),
        ("invm", "invest"),
        ("grade", "grd"),
        ("corporate", "corp"),
        ("bond", "bd"),
        ("fund", "fd"),
        ("equity", "eq"),
        ("european", "eurp"),
        ("sustainable", "sust"),
        ("systematic", "systmtc"),
        ("estate", "estt"),
        ("securities", "secs"),
        ("small", "sm"),
        ("capital", "cp"),
        ("advantage", "adv"),
    )
    for source, target in replacements:
        text = re.sub(rf"\b{re.escape(source)}\b", target, text)
    text = re.sub(r"[^a-z0-9]+", "", text)
    # 官网 accountName 可能省略 Fund/Fd，页表英文名可能保留它。
    return re.sub(r"(?:fd|fund)$", "", text)


def _fetch_catalog(account_type: str) -> list[dict[str, Any]]:
    """读取一个官方目录；参数 account_type 为 MUTUAL-FUND 或 ORSO。"""

    params = [
        ("fq", 'countryCode:"HK"'),
        ("fq", 'language:"zh_hk"'),
        ("fq", f'accountType:"{account_type}"'),
        ("fq", 'contentType:"Product"'),
        ("fq", 'shareClassStatus:"open"'),
        ("q", "*:*"),
        ("fl", PAGE_FIELDS),
        ("rows", "1000"),
        ("start", "0"),
    ]
    response = SESSION.get(SEARCH_API, params=params, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    records = ((payload.get("response") or {}).get("docs") or [])
    if not isinstance(records, list):
        raise RuntimeError(f"景顺 {account_type} 目录格式异常：docs 不是数组")
    return [{**item, "account_type": account_type} for item in records if isinstance(item, dict)]


def _catalog_loader() -> list[dict[str, Any]]:
    """合并景顺普通互惠基金和 ORSO 目录；无参数。"""

    records = _fetch_catalog("MUTUAL-FUND") + _fetch_catalog("ORSO")
    if not records:
        raise RuntimeError("景顺官方目录为空")
    return records


def _build_index(catalog: list[dict[str, Any]]) -> dict[str, Any]:
    """按核心英文名建立索引；参数 catalog 为官方目录记录。"""

    index: dict[str, list[dict[str, Any]]] = {}
    for item in catalog:
        item = {**item, "fund_name": str(item.get("accountName") or "").strip()}
        index.setdefault(_key(item.get("fund_name", "")), []).append(item)
    return {"index": index, "catalog": catalog}


def _pick(records: list[dict[str, Any]], umbrella: str) -> dict[str, Any]:
    """按伞名和币种选择一条份额；参数 records/umbrella 为候选记录和页表伞名。"""

    umbrella_text = str(umbrella or "")
    if "退休" in umbrella_text:
        records = [x for x in records if x.get("account_type") == "ORSO"] or records
    elif "卢森堡" in umbrella_text or "信托" in umbrella_text:
        records = [x for x in records if x.get("account_type") == "MUTUAL-FUND"] or records
    return sorted(records, key=lambda item: currency_rank(item, field="baseCurrency"))[0]


def _detail_url(record: dict[str, Any]) -> str:
    """构造基金详情页 URL；参数 record 为目录记录。"""

    path = str(record.get("url") or "").strip()
    # Solr 返回 AEM 仓库路径 /content/invesco/...，公开页面实际从 /hk/... 提供。
    if path.startswith("/content/invesco/"):
        path = path[len("/content/invesco") :]
    return urljoin(BASE_URL, path)


def _extract_factsheet(page_url: str) -> dict[str, Any] | None:
    """从官方详情页 JSON 中取 Factsheet；参数 page_url 为景顺基金详情页。"""

    if page_url in _DOCUMENT_CACHE:
        return _DOCUMENT_CACHE[page_url]
    response = SESSION.get(page_url, headers={**HEADERS, "Accept": "text/html,*/*"}, timeout=TIMEOUT)
    response.raise_for_status()
    match = re.search(r'<div id="spa-root"[^>]*data-model-json="(.*?)"', response.text, flags=re.S)
    if not match:
        _DOCUMENT_CACHE[page_url] = None
        return None
    try:
        model = json.loads(html.unescape(match.group(1)))
    except (TypeError, ValueError):
        _DOCUMENT_CACHE[page_url] = None
        return None

    found: dict[str, Any] | None = None

    def walk(value: Any) -> None:
        """递归查找文档组件；参数 value 为页面模型节点。"""

        nonlocal found
        if found is not None:
            return
        if isinstance(value, dict):
            sections = value.get("documentsSections")
            if isinstance(sections, list):
                for section in sections:
                    for document in (section.get("documents") or []) if isinstance(section, dict) else []:
                        if str(document.get("displayName") or "").casefold() == "factsheet" and document.get("documentPath"):
                            found = document
                            return
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(model)
    if found is not None:
        found = {**found, "documentPath": urljoin(page_url, str(found["documentPath"]))}
    _DOCUMENT_CACHE[page_url] = found
    return found


def _resolve_single(row: dict[str, Any], index: dict[str, Any]) -> tuple[str, str, str | None]:
    """解析一行基金；参数 row 为页表行、index 为目录索引。"""

    key = _key(row.get("english") or "")
    records = index["index"].get(key) or []
    if not records:
        return "", "", "NO_CURRENT_FUND：景顺当前官方目录未找到可验证开放份额"
    record = _pick(records, str(row.get("umbrella") or ""))
    page_url = _detail_url(record)
    try:
        document = _extract_factsheet(page_url)
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        if status == 404:
            return "", "", "NO_PUBLIC_DETAIL：官方目录有开放份额，但公开基金详情页返回 404，未猜测资料地址"
        return "", "", f"FACTSHEET_PROBE_FAILED：基金详情页 HTTP 请求失败（HTTP {status}）"
    except requests.RequestException as exc:
        return "", "", f"FACTSHEET_PROBE_FAILED：基金详情页请求失败（{type(exc).__name__}）"
    if not document:
        return "", "", "NO_FACTSHEET：基金详情页未返回可验证 Factsheet（月报）"
    isin = str(record.get("isin") or "").strip()
    if not isin:
        return "", "", "NO_ISIN：官方目录没有份额代码，未写入猜测命令"
    english = str(row.get("english") or row.get("fund") or "").strip()
    return page_url, RUN_COMMAND.format(
        url=page_url,
        english=english,
        isin=isin,
        account_type=record.get("account_type") or "MUTUAL-FUND",
    ), ""


def main(argv: list[str] | None = None) -> int:
    """运行适配器 CLI；参数 argv 为命令行参数列表。"""

    return cli_main(
        argv,
        description="按景顺香港官方互惠基金/ORSO目录补全月报入口",
        sheet_name=SHEET_NAME,
        catalog_loader=_catalog_loader,
        index_builder=_build_index,
        resolver=_resolve_single,
        single_print_run_command=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
