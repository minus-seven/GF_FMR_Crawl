#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""下载联博香港基金单只官方 Fact Sheet（月报）。"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date
from typing import Any
from urllib.parse import urljoin

import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common_paths import (  # noqa: E402
    configure_session,
    extract_report_month_from_pdf,
    finalize_pdf_to_monthly_path,
)


BASE_URL = "https://www.abfunds.com.hk"
PRODUCT_DOCS_API = "https://webapi.alliancebernstein.com/v2/funds/hk/en/investor/product-docs"
COMPANY = "alliancebernstein"
TIMEOUT = 120
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, application/pdf,*/*;q=0.8",
    "Referer": "https://www.abfunds.com.hk/hk/en/investor/funds.html",
}
SESSION = configure_session(requests.Session())


def _download(url: str, referer: str) -> str:
    """下载并校验 PDF；参数 url/referer 为文档地址和基金页地址。"""

    response = SESSION.get(url, headers={**HEADERS, "Referer": referer}, timeout=TIMEOUT, stream=True)
    response.raise_for_status()
    fd, path = tempfile.mkstemp(prefix="alliancebernstein_", suffix=".pdf")
    os.close(fd)
    first = True
    try:
        with open(path, "wb") as handle:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                if first and not chunk.startswith(b"%PDF-"):
                    raise RuntimeError(f"联博下载内容不是 PDF：{response.url}")
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


def _find_current_doc(fund_number: str) -> dict[str, Any] | None:
    """直链失效时从官方 product-docs 目录重新找当前 Factsheet。"""

    response = SESSION.post(PRODUCT_DOCS_API, json={}, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    candidates: list[dict[str, Any]] = []
    for item in payload if isinstance(payload, list) else []:
        info = item.get("fundInfo") if isinstance(item, dict) else None
        if not isinstance(info, dict) or str(info.get("fundNumber") or "") != fund_number:
            continue
        candidates.extend(
            doc
            for doc in item.get("productDocs") or []
            if isinstance(doc, dict)
            and doc.get("docType") == "productdocdetails-doctype-fact-sheet"
            and str(doc.get("docURL") or "").strip()
        )
    if not candidates:
        return None
    candidates.sort(key=lambda doc: str(doc.get("publishDate") or ""), reverse=True)
    return candidates[0]


def main() -> int:
    """执行单只联博 Fact Sheet 下载。"""

    source = str(sys.argv[1] if len(sys.argv) > 1 else "").strip()
    name = str(sys.argv[2] if len(sys.argv) > 2 else "").strip()
    fund_number = str(sys.argv[3] if len(sys.argv) > 3 else "").strip()
    document_url = str(sys.argv[4] if len(sys.argv) > 4 else "").strip()
    if not source or not name or not fund_number:
        raise SystemExit(
            "用法：python download_alliancebernstein_factsheet.py <基金页> <英文基金名> <fundNumber> [Factsheet URL]"
        )
    document_url = urljoin(BASE_URL, document_url)
    try:
        temp_path = _download(document_url, source)
    except Exception as direct_error:
        document = _find_current_doc(fund_number)
        if not document:
            raise RuntimeError(f"联博 Fact Sheet 下载失败，且官方目录没有当前文档：{direct_error}") from direct_error
        document_url = urljoin(BASE_URL, str(document["docURL"]))
        temp_path = _download(document_url, source)
    try:
        report_month = extract_report_month_from_pdf(temp_path, fallback_date=date.today().strftime("%Y-%m-%d"))
    except Exception:
        report_month = date.today().strftime("%Y-%m-%d")
    target, _, created = finalize_pdf_to_monthly_path(
        temp_path,
        COMPANY,
        name,
        fallback_date=report_month,
    )
    print(f"[*] 基金页: {source}")
    print(f"[*] fundNumber: {fund_number}")
    print(f"[*] Fact Sheet: {document_url}")
    print(f"[*] {'完成' if created else '已存在'} -> {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
