#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""下载景顺香港基金详情页中的当前 Factsheet（月报）。"""

from __future__ import annotations

import html
import json
import os
import re
import sys
import tempfile
from datetime import date
from urllib.parse import urljoin

import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common_paths import (  # noqa: E402
    configure_session,
    extract_report_month_from_pdf,
    finalize_pdf_to_monthly_path,
)


BASE_URL = "https://www.invesco.com"
COMPANY = "invesco"
TIMEOUT = 120
HEADERS = {
    "User-Agent": "invesco-client/1.0",
    "Referer": "https://www.invesco.com/",
    "Accept": "text/html,application/pdf,*/*;q=0.8",
}
SESSION = configure_session(requests.Session())


def _find_factsheet(page_url: str) -> dict:
    """从详情页 JSON 中提取 Factsheet；参数 page_url 为景顺详情页。"""

    response = SESSION.get(page_url, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    match = re.search(r'<div id="spa-root"[^>]*data-model-json="(.*?)"', response.text, flags=re.S)
    if not match:
        raise RuntimeError(f"景顺详情页没有 spa-root 数据：{page_url}")
    try:
        model = json.loads(html.unescape(match.group(1)))
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"景顺详情页 JSON 解析失败：{page_url}") from exc
    found: dict | None = None

    def walk(value):
        """递归查找 Factsheet 文档；参数 value 为页面模型节点。"""

        nonlocal found
        if found is not None:
            return
        if isinstance(value, dict):
            sections = value.get("documentsSections")
            if isinstance(sections, list):
                for section in sections:
                    docs = section.get("documents") if isinstance(section, dict) else []
                    for document in docs or []:
                        if str(document.get("displayName") or "").casefold() == "factsheet" and document.get("documentPath"):
                            found = document
                            return
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(model)
    if not found:
        raise RuntimeError(f"景顺详情页没有可验证 Factsheet：{page_url}")
    return {**found, "documentPath": urljoin(page_url, str(found["documentPath"]))}


def _download(url: str, referer: str) -> str:
    """下载并校验 PDF；参数 url/referer 为文档地址和基金详情页地址。"""

    response = SESSION.get(url, headers={**HEADERS, "Referer": referer}, timeout=TIMEOUT, stream=True)
    response.raise_for_status()
    fd, path = tempfile.mkstemp(prefix="invesco_", suffix=".pdf")
    os.close(fd)
    first = True
    try:
        with open(path, "wb") as handle:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                if first and not chunk.startswith(b"%PDF-"):
                    raise RuntimeError(f"景顺下载内容不是 PDF：{response.url}")
                first = False
                handle.write(chunk)
        return path
    except Exception:
        try:
            os.remove(path)
        except OSError:
            pass
        raise
    finally:
        response.close()


def main() -> int:
    """执行单只景顺 Factsheet 下载；参数来自命令行。"""

    source = str(sys.argv[1] if len(sys.argv) > 1 else "").strip()
    fund_name = str(sys.argv[2] if len(sys.argv) > 2 else "").strip()
    isin = str(sys.argv[3] if len(sys.argv) > 3 else "").strip()
    account_type = str(sys.argv[4] if len(sys.argv) > 4 else "MUTUAL-FUND").strip()
    if not source or not fund_name or not isin:
        raise SystemExit(
            "用法：python download_invesco_factsheet.py <官方基金页> <英文基金名> <ISIN> [MUTUAL-FUND|ORSO]"
        )
    document = _find_factsheet(source)
    temp_path = _download(str(document["documentPath"]), source)
    try:
        report_month = extract_report_month_from_pdf(temp_path, fallback_date=date.today().strftime("%Y-%m-%d"))
    except Exception:
        report_month = date.today().strftime("%Y-%m-%d")
    target, _, created = finalize_pdf_to_monthly_path(
        temp_path, COMPANY, fund_name, fallback_date=report_month
    )
    print(f"[*] 基金页: {source}")
    print(f"[*] 目录类别: {account_type} / ISIN: {isin}")
    print(f"[*] Factsheet: {document.get('documentPath')}")
    print(f"[*] {'完成' if created else '已存在'} -> {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
