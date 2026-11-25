#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""施罗德香港基金月报入口补全适配器。

施罗德 Fund Centre 的页面由前端渲染，但其官方前端 API 提供了稳定的
份额目录和文档分类接口。本脚本只把基金详情页写入 I 列，下载时再用
shareClassExternalId 读取最新 Factsheet，避免固化带版本时间戳的 PDF 地址。
"""

from __future__ import annotations

import os
import re
import sys
from typing import Any
from urllib.parse import quote

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))

from common.fund_link_backfill import cli_main, currency_rank, normalize_name  # noqa: E402
from common_paths import configure_session  # noqa: E402


SHEET_NAME = "施罗德"
API_HOST = "https://fev2-vite.schdr.eu-central-1.isgdigital.com"
SHARE_CLASS_API = API_HOST + "/api/v1/getShareClassList"
DOCUMENT_CATEGORY_API = API_HOST + "/api/v1/getDocumentCategories"
FUND_CENTRE_URL = "https://www.schroders.com/en-hk/hk/individual/fund-centre/"
RUN_COMMAND = 'python download_schroder_factsheet.py "{url}" "{english}" "{external_id}"'
TIMEOUT = 60
PAGE_SIZE = 20
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
}
SESSION = configure_session(requests.Session())


def _key(value: str) -> str:
    """归一化英文核心名，去掉系列前缀、类别和币种尾缀。"""

    text = str(value or "").strip().casefold()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", "", text)
    for prefix in (
        "schroderinternationalselectionfund",
        "schroderfund",
        "schroder",
    ):
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break
    # A/C/A1 等份额类别后面的 Accumulation/Distribution、币种和份额后缀。
    text = re.sub(
        r"(?:a1|a2|a|c|i|e|d|b)(?:accumulation|distribution).*$",
        "",
        text,
    )
    # 例如 ``...FundClassAUSDAcc``，类别前面可能已有 Fund。
    text = re.sub(r"class[a-z0-9]+$", "", text)
    text = re.sub(r"fund$", "", text)
    return text


def _currency(item: dict[str, Any]) -> str:
    values = item.get("values") if isinstance(item.get("values"), dict) else {}
    return str(values.get("baseCurrency") or values.get("baseCurrencyRaw") or "").strip()


def _fetch_catalog() -> list[dict[str, Any]]:
    """分页读取施罗德香港 Fund Centre 当前份额目录。"""

    result: list[dict[str, Any]] = []
    page_index = 0
    while True:
        response = SESSION.get(
            SHARE_CLASS_API,
            params={
                "location": "hk",
                "channel": "individual",
                "language": "en",
                "pageIndex": page_index,
                "pageSize": PAGE_SIZE,
            },
            headers=HEADERS,
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
        items = payload.get("items") or []
        if not isinstance(items, list):
            raise RuntimeError("施罗德官方目录格式异常：items 不是数组")
        result.extend(item for item in items if isinstance(item, dict))
        pagination = payload.get("pagination") or {}
        page_count = int(pagination.get("pageCount") or 0)
        if not items or page_count <= page_index + 1:
            break
        page_index += 1
        if page_index > 50:
            raise RuntimeError("施罗德官方目录分页异常")
    if not result:
        raise RuntimeError("施罗德官方基金目录为空")
    return result


def _latest_factsheet(external_id: str) -> dict[str, Any] | None:
    """从官方文档分类接口取一份基金的最新 Factsheet。"""

    response = SESSION.get(
        DOCUMENT_CATEGORY_API,
        params={
            "location": "hk",
            "channel": "individual",
            "language": "en",
            # API 的历史命名大小写为 shareclassExternalId。
            "shareclassExternalId": external_id,
        },
        headers=HEADERS,
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    groups = ((payload.get("data") or {}).get("documentGroups") or [])
    for group in groups:
        for document in group.get("data") or []:
            title = str(document.get("title") or "").casefold()
            if "factsheet" in title and str(document.get("url") or "").strip():
                return document
    return None


def _build_index(catalog: list[dict[str, Any]]) -> dict[str, Any]:
    index: dict[str, list[dict[str, Any]]] = {}
    for item in catalog:
        values = item.get("values") if isinstance(item.get("values"), dict) else {}
        name = str(values.get("name") or item.get("name") or "").strip()
        item = {**item, "fund_name": name, "currency": _currency(item)}
        index.setdefault(_key(name), []).append(item)
    return {"index": index, "catalog": catalog}


def _pick(records: list[dict[str, Any]]) -> dict[str, Any]:
    # 与公共规则一致：港元优先，其次美元，其余币种；目录顺序作为同币种稳定顺序。
    return sorted(records, key=lambda item: currency_rank(item, field="currency"))[0]


def _match(row: dict[str, Any], index: dict[str, Any]) -> list[dict[str, Any]]:
    key = _key(row.get("english") or "")
    if not key:
        return []
    # 只接受归一化后的核心名精确相等。不能用 ``startswith``：
    # “Emerging Markets Multi-Asset” 会被误配成 “Emerging Markets”。
    return index["index"].get(key) or []


def _detail_url(record: dict[str, Any]) -> str:
    external_id = str(record.get("id") or "").strip()
    name = str(record.get("fund_name") or record.get("name") or "").strip()
    return (
        f"{FUND_CENTRE_URL}?language=en&location=hk&channel=individual"
        f"&clientId=schdr&clientVersion=v1&externalId={quote(external_id)}"
        f"&r=%2Ffund%2F{quote(external_id)}%2F&fundName={quote(name)}"
    )


def _resolve_single(row: dict[str, Any], index: dict[str, Any]) -> tuple[str, str, str | None]:
    records = _match(row, index)
    if not records:
        return "", "", "NO_CURRENT_FUND：官方当前目录未找到可验证份额"
    record = _pick(records)
    external_id = str(record.get("id") or "").strip()
    try:
        factsheet = _latest_factsheet(external_id)
    except requests.RequestException as exc:
        return "", "", f"FACTSHEET_PROBE_FAILED：官方文档接口请求失败（{type(exc).__name__}）"
    if not factsheet:
        return "", "", "NO_FACTSHEET：基金页未返回可验证 Factsheet（月报）"
    link = _detail_url(record)
    english = str(row.get("english") or row.get("fund") or "").strip()
    return link, RUN_COMMAND.format(url=link, english=english, external_id=external_id), ""


def main(argv: list[str] | None = None) -> int:
    return cli_main(
        argv,
        description="按施罗德香港 Fund Centre 补全月报入口",
        sheet_name=SHEET_NAME,
        catalog_loader=_fetch_catalog,
        index_builder=_build_index,
        resolver=_resolve_single,
        single_print_run_command=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
