#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""荷宝香港基金月报入口适配器。

荷宝香港基金目录公开列出子基金详情页，详情页的 Fund documents 区域提供
最新 Factsheet。适配器只保存官方详情页，下载器在运行时重新发现 PDF。
"""

from __future__ import annotations

import html
import os
import re
import sys
from copy import copy
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin

import openpyxl
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
from common.fund_link_backfill import cli_main, normalize_name  # noqa: E402

SHEET_NAME = "荷宝"
FUNDS_URL = "https://www.robeco.com/en-hk/products/funds"
DOWNLOADER = "download_robeco_factsheet.py"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}


class _LinkParser(HTMLParser):
    """收集 Robeco 基金详情链接；参数由 HTMLParser 负责传入。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[dict[str, str]] = []
        self._anchor: dict[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """开始收集锚点；参数 tag 为标签名、attrs 为 HTML 属性。"""
        if tag.lower() != "a":
            return
        values = {key.lower(): value or "" for key, value in attrs}
        href = values.get("href", "")
        if "/products/funds/isin-" in href:
            self._anchor = {"href": href, "text": ""}

    def handle_data(self, data: str) -> None:
        """累积锚点文本；参数 data 为 HTML 文本片段。"""
        if self._anchor is not None:
            self._anchor["text"] += data

    def handle_endtag(self, tag: str) -> None:
        """结束锚点；参数 tag 为标签名。"""
        if tag.lower() == "a" and self._anchor is not None:
            self.links.append(self._anchor)
            self._anchor = None


def _key(value: str) -> str:
    """归一化英文基金名；参数 value 为页表或官网基金名称。"""
    text = normalize_name(html.unescape(str(value or ""))).casefold()
    # normalize_name 已去掉空格，不能再依赖词边界；这些词只作为系列前缀/类别噪声。
    text = text.replace("robeco", "").replace("fund", "").replace("class", "")
    return re.sub(r"[^a-z0-9]+", "", text)


def _fetch_catalog() -> list[dict[str, Any]]:
    """读取荷宝香港官方基金目录并返回详情页目录；无参数。"""
    response = requests.get(FUNDS_URL, headers=HEADERS, timeout=90)
    response.raise_for_status()
    parser = _LinkParser()
    parser.feed(response.text)
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for item in parser.links:
        url = urljoin(FUNDS_URL, item["href"])
        text = " ".join(item["text"].split())
        if not text or url in seen:
            continue
        seen.add(url)
        result.append({"fund_name": text, "page_url": url, "currency": text.rsplit(" ", 1)[-1]})
    if not result:
        raise RuntimeError("荷宝香港官方基金目录未返回详情页")
    return result


def _build_index(catalog: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """按归一化名称建立详情页索引；参数 catalog 为官网目录记录。"""
    index: dict[str, list[dict[str, Any]]] = {}
    for item in catalog:
        index.setdefault(_key(str(item.get("fund_name") or "")), []).append(item)
    return index


def _resolve_single(row: dict[str, Any], index: dict[str, list[dict[str, Any]]]) -> tuple[str, str, str]:
    """解析基金详情页入口；参数 row 为页表行、index 为官网索引。"""
    target = _key(str(row.get("english") or row.get("fund") or ""))
    if not target:
        return "", "", "荷宝页表缺少英文基金名"
    candidates = [item for key, items in index.items() if target in key or key in target for item in items]
    if not candidates:
        return "", "", "荷宝香港官方目录未匹配同名基金详情页"
    candidates.sort(key=lambda item: (0 if str(item.get("currency", "")).upper() == "HKD" else 1, str(item.get("page_url"))))
    item = candidates[0]
    command = f'python {DOWNLOADER} "{item["page_url"]}" "{row.get("english") or row.get("fund")}"'
    return str(item["page_url"]), command, ""


def _ensure_sheet(excel_path: str) -> None:
    """从 Sheet2 创建荷宝页表；参数 excel_path 为正式或临时工作簿路径。"""
    workbook = openpyxl.load_workbook(excel_path)
    if SHEET_NAME in workbook.sheetnames:
        workbook.close()
        return
    source = workbook["Sheet2"]
    target = workbook.create_sheet(SHEET_NAME)
    headers = ["序号", "基金", "伞", "全称", "英文", "管理人", "大类", "小类", "是否派息", "月报链接", "运行方式"]
    source_header = {str(source.cell(1, c).value or "").strip(): c for c in range(1, source.max_column + 1)}
    for col, value in enumerate(headers, 1):
        target.cell(1, col, value)
        if source.cell(1, min(col, source.max_column)).has_style:
            target.cell(1, col)._style = copy(source.cell(1, min(col, source.max_column))._style)
    row_out = 2
    for row in range(2, source.max_row + 1):
        if str(source.cell(row, source_header.get("管理人", 6)).value or "").strip() != SHEET_NAME:
            continue
        for col in range(1, min(10, source.max_column) + 1):
            target.cell(row_out, col, source.cell(row, col).value)
        row_out += 1
    workbook.save(excel_path)
    workbook.close()


def main(argv: list[str] | None = None) -> int:
    """运行荷宝 CLI；参数 argv 支持 --probe/--backfill 及工作簿选项。"""
    args = list(sys.argv[1:] if argv is None else argv)
    if "--backfill" in args or "--probe" in args:
        excel = args[args.index("--excel") + 1] if "--excel" in args else os.path.join(HERE, "..", "..", "..", "爬虫list.xlsx")
        _ensure_sheet(excel)
    return cli_main(args, description="按 Robeco 香港官方详情页补全荷宝 Factsheet 月报入口", sheet_name=SHEET_NAME, catalog_loader=_fetch_catalog, index_builder=_build_index, resolver=_resolve_single, single_print_run_command=True)


if __name__ == "__main__":
    raise SystemExit(main())
