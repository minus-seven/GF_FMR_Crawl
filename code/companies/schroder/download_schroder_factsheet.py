#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""下载施罗德香港 Fund Centre 当前 Factsheet（月报）。"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date

import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common_paths import (  # noqa: E402
    configure_session,
    extract_report_month_from_pdf,
    finalize_pdf_to_monthly_path,
)


API_HOST = "https://fev2-vite.schdr.eu-central-1.isgdigital.com"
DOCUMENT_CATEGORY_API = API_HOST + "/api/v1/getDocumentCategories"
COMPANY = "schroder"
TIMEOUT = 120
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, application/pdf,*/*;q=0.8",
}
SESSION = configure_session(requests.Session())


def _find_factsheet(external_id: str) -> dict:
    response = SESSION.get(
        DOCUMENT_CATEGORY_API,
        params={
            "location": "hk",
            "channel": "individual",
            "language": "en",
            "shareclassExternalId": external_id,
        },
        headers={**HEADERS, "Content-Type": "application/json"},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    groups = ((payload.get("data") or {}).get("documentGroups") or [])
    for group in groups:
        for document in group.get("data") or []:
            if "factsheet" in str(document.get("title") or "").casefold() and document.get("url"):
                return document
    raise RuntimeError(f"施罗德官方文档目录没有 Factsheet：{external_id}")


def _download(url: str, referer: str) -> str:
    response = SESSION.get(url, headers={**HEADERS, "Referer": referer}, timeout=TIMEOUT, stream=True)
    response.raise_for_status()
    fd, path = tempfile.mkstemp(prefix="schroder_", suffix=".pdf")
    os.close(fd)
    first = True
    try:
        with open(path, "wb") as handle:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                if first and not chunk.startswith(b"%PDF-"):
                    raise RuntimeError(f"施罗德下载内容不是 PDF：{response.url}")
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
    source = str(sys.argv[1] if len(sys.argv) > 1 else "").strip()
    fund_name = str(sys.argv[2] if len(sys.argv) > 2 else "").strip()
    external_id = str(sys.argv[3] if len(sys.argv) > 3 else "").strip()
    if not source or not fund_name or not external_id:
        raise SystemExit(
            "用法：python download_schroder_factsheet.py <官方基金页> <英文基金名> <shareClassExternalId>"
        )
    document = _find_factsheet(external_id)
    temp_path = _download(str(document["url"]), source)
    fallback = str(document.get("date") or date.today().strftime("%Y-%m-%d"))
    try:
        report_month = extract_report_month_from_pdf(temp_path, fallback_date=fallback)
    except Exception:
        report_month = fallback
    target, _, created = finalize_pdf_to_monthly_path(
        temp_path, COMPANY, fund_name, fallback_date=report_month
    )
    print(f"[*] 基金页: {source}")
    print(f"[*] Factsheet: {document.get('title')} / {document.get('date')}")
    print(f"[*] {'完成' if created else '已存在'} -> {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
