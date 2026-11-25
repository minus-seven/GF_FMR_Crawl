#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""下载并归档中银保诚官网单只 Factsheet（月报）。"""

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


BASE_URL = "https://www.boci-pru.com.hk"
LISTING_API = BASE_URL + "/en/api/UTFund/utListing"
COMPANY = "bocip"
TIMEOUT = 120
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, application/pdf,*/*;q=0.8",
}
SESSION = configure_session(requests.Session())


def _download(url: str, referer: str) -> str:
    """下载并校验 PDF；参数 url 为文件地址、referer 为基金页地址。"""

    response = SESSION.get(url, headers={**HEADERS, "Referer": referer}, timeout=TIMEOUT, stream=True)
    response.raise_for_status()
    fd, path = tempfile.mkstemp(prefix="bocip_", suffix=".pdf")
    os.close(fd)
    first = True
    try:
        with open(path, "wb") as handle:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                if first and not chunk.startswith(b"%PDF-"):
                    raise RuntimeError(f"中银保诚下载内容不是 PDF：{response.url}")
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


def _find_current_doc(fund_code: str) -> dict[str, Any] | None:
    """直链失效时刷新目录；参数 fund_code 为官网份额代码。"""

    response = SESSION.post(
        LISTING_API,
        json={"query": "", "currency": [], "dist": [], "investment": [], "region": [], "riskLv": [], "Lang": "en"},
        headers=HEADERS,
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    for item in (payload.get("data") if isinstance(payload, dict) else []) or []:
        if str(item.get("utFundCode") or "").strip() != fund_code:
            continue
        pdf = item.get("pdf") or {}
        doc = str(pdf.get("fs") or "").strip().replace("\\", "/")
        if doc:
            return {"url": urljoin(BASE_URL, doc), "page": urljoin(BASE_URL, str((item.get("fundName") or {}).get("link") or ""))}
    return None


def main() -> int:
    """解析命令行并归档文件；本函数无参数，参数从 sys.argv 读取。"""

    source = str(sys.argv[1] if len(sys.argv) > 1 else "").strip()
    name = str(sys.argv[2] if len(sys.argv) > 2 else "").strip()
    fund_code = str(sys.argv[3] if len(sys.argv) > 3 else "").strip()
    document_url = str(sys.argv[4] if len(sys.argv) > 4 else "").strip()
    if not source or not name or not fund_code:
        raise SystemExit("用法：python download_bocip_factsheet.py <基金页> <英文基金名> <utFundCode> [Factsheet URL]")
    document_url = urljoin(BASE_URL, document_url)
    try:
        temp_path = _download(document_url, source)
    except Exception as direct_error:
        document = _find_current_doc(fund_code)
        if not document:
            raise RuntimeError(f"中银保诚 Factsheet 下载失败，且官方目录没有当前文档：{direct_error}") from direct_error
        document_url = document["url"]
        temp_path = _download(document_url, document.get("page") or source)
    try:
        report_month = extract_report_month_from_pdf(temp_path, fallback_date=date.today().strftime("%Y-%m-%d"))
    except Exception:
        report_month = date.today().strftime("%Y-%m-%d")
    target, _, created = finalize_pdf_to_monthly_path(temp_path, COMPANY, name, fallback_date=report_month)
    print(f"[*] 基金页: {source}")
    print(f"[*] 基金代码: {fund_code}")
    print(f"[*] Factsheet: {document_url}")
    print(f"[*] {'完成' if created else '已存在'} -> {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
