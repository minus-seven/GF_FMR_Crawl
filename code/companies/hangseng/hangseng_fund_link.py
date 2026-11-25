#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""恒生投资管理香港基金 Factsheet（月报）入口补全适配器。

官方 Resources 页面使用同源资源目录接口返回当前基金与最新 Monthly
Factsheet。这里保留官方目录页作为稳定 I 列入口，J 列在运行时重新解析
Factsheet 地址，避免把临时 CDN 地址写死。
"""

from __future__ import annotations

import re
import sys
import os
from typing import Any
from urllib.parse import urljoin

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))

from common.fund_link_backfill import cli_main, normalize_name  # noqa: E402


SHEET_NAME = "恒生"
BASE_SITE = "https://www.hangsenginvestment.com"
RESOURCES_URL = BASE_SITE + "/en-hk/individual-investor/resources/"
API_URL = (
    "https://devcluster.api.p2g.netd2.hsbc.com.hk/"
    "pws-hk-hase-hsvm2-papi-sct-proxy/v1/hsvm/resource/offering_doc"
)
RUN_COMMAND = 'python download_hangseng_factsheet.py "{url}" "{english}" "{pdf_url}"'
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Accept": "application/json,text/plain,*/*",
    "Referer": RESOURCES_URL,
}


def _key(value: str) -> str:
    """归一化英文基金名；参数 value 为页表或官方目录名称。"""

    text = normalize_name(str(value or "")).casefold()
    return re.sub(r"[^a-z0-9]+", "", text)


def _fetch_catalog() -> list[dict[str, Any]]:
    """读取恒生官方资源目录；本函数无参数。"""

    response = requests.get(API_URL, headers=HEADERS, timeout=120)
    response.raise_for_status()
    payload = response.json()
    records = payload.get("Fund") if isinstance(payload, dict) else None
    if not isinstance(records, list) or not records:
        raise RuntimeError("恒生官方资源目录为空或格式异常")
    return [item for item in records if isinstance(item, dict)]


def _build_index(catalog: list[dict[str, Any]]) -> dict[str, Any]:
    """按官方英文基金名建立索引；参数 catalog 为官方基金目录。"""

    index: dict[str, list[dict[str, Any]]] = {}
    for record in catalog:
        name = str(record.get("Fund_name_eng") or "").strip()
        if name:
            index.setdefault(_key(name), []).append(record)
    return {"index": index, "catalog": catalog}


def _document_url(file_en: str) -> str:
    """把 API 的文档相对路径转成可下载 PDF；参数 file_en 为官方 JSON 路径。"""

    if file_en.startswith("http://") or file_en.startswith("https://"):
        return file_en
    path = "/" + file_en.lstrip("/")
    if not path.startswith("/cms/"):
        path = "/cms" + path
    return urljoin(BASE_SITE, path)


def _resolve_single(row: dict[str, Any], index: dict[str, Any]) -> tuple[str, str, str | None]:
    """解析一行恒生月报入口；参数 row 为页表行、index 为官方目录索引。"""

    english = str(row.get("english") or row.get("fund") or "").strip()
    records = index["index"].get(_key(english)) or []
    if not records:
        # ETF 在官方目录按上市/非上市份额拆成“基金名 - Listed/Unlisted Class”。
        # 只认领同名前缀，仍要求各份额存在可下载 Factsheet。
        prefix = _key(english)
        records = [
            record
            for name, values in index["index"].items()
            if name.startswith(prefix)
            for record in values
        ]
    if not records:
        return "", "", "NO_CURRENT_FUND：恒生官方资源目录未返回该基金"
    for record in records:
        factsheet = record.get("factsheet_doc")
        file_en = str((factsheet or {}).get("file_en") or "").strip() if isinstance(factsheet, dict) else ""
        if file_en:
            pdf_url = _document_url(file_en)
            return RESOURCES_URL, RUN_COMMAND.format(url=RESOURCES_URL, english=english, pdf_url=pdf_url), ""
    return "", "", "NO_FACTSHEET：官方目录存在但没有单只 Monthly Factsheet；不以公告/中期报告替代月报"


def main(argv: list[str] | None = None) -> int:
    """运行恒生 CLI；参数 argv 支持单基金、--probe、--backfill。"""

    return cli_main(
        argv,
        description="按恒生香港官方资源目录补全 Factsheet 月报入口",
        sheet_name=SHEET_NAME,
        catalog_loader=_fetch_catalog,
        index_builder=_build_index,
        resolver=_resolve_single,
        single_print_run_command=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
