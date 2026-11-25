#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从富兰克林邓普顿香港官方文档目录下载最新 Factsheet。"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from datetime import date
from urllib.parse import urljoin

import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common.fund_link_backfill import normalize_name  # noqa: E402
from common_paths import (  # noqa: E402
    configure_session,
    extract_report_month_from_pdf,
    finalize_pdf_to_monthly_path,
)

BASE_URL = "https://www.franklintempleton.com.hk"
DOCUMENTS_URL = BASE_URL + "/en-hk/our-funds/literature/fund-documents"
LITERATURE_API = BASE_URL + "/api/literature/v1/documents?channel=en-hk&cache=true&audience=investor"
COMPANY = "franklin"
TIMEOUT = 120
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, application/pdf,*/*;q=0.8",
}
SESSION = configure_session(requests.Session())


def _key(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"^fact\s*sheet\s*[-:：]?\s*", "", text)
    for prefix in (
        "franklin templeton investment funds -",
        "franklin templeton global funds plc -",
        "franklin templeton asia fund series -",
    ):
        text = text.replace(prefix, "")
    text = re.sub(r"\b(ftgf|franklin templeton investment funds)\b\s*[-:]?", "", text)
    return re.sub(r"[^a-z0-9]+", "", text)


def _date_key(item: dict) -> tuple[int, int, int]:
    text = str(item.get("frkReferenceDate") or item.get("fileUploadDate") or "")
    m = re.search(r"(20\d{2})[-/.](\d{1,2})(?:[-/.](\d{1,2}))?", text)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 1)) if m else (0, 0, 0)


def _documents() -> list[dict]:
    response = SESSION.get(LITERATURE_API, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    docs = payload.get("document") or []
    return [
        d for d in docs
        if isinstance(d, dict) and "factsheet" in str(d.get("dctermsType") or "").lower()
    ]


def _find_document(name: str) -> dict:
    key = _key(name)
    docs = _documents()
    candidates = []
    for item in docs:
        title = re.sub(r"^fact\s*sheet\s*[-:：]?\s*", "", str(item.get("dctermsTitle") or ""), flags=re.I)
        if _key(title) == key:
            candidates.append(item)
    if not candidates and len(key) >= 12:
        candidates = [
            item for item in docs
            if key in _key(item.get("dctermsTitle") or "")
            or _key(item.get("dctermsTitle") or "") in key
        ]
    if not candidates:
        raise RuntimeError(f"富兰克林官方文档目录没有匹配的 Factsheet（月报）：{name}")
    return sorted(candidates, key=_date_key, reverse=True)[0]


def _download(url: str, referer: str) -> str:
    response = SESSION.get(url, headers={**HEADERS, "Referer": referer}, timeout=TIMEOUT, stream=True)
    response.raise_for_status()
    fd, path = tempfile.mkstemp(prefix="franklin_", suffix=".pdf")
    os.close(fd)
    first = True
    try:
        with open(path, "wb") as handle:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                if first and not chunk.startswith(b"%PDF-"):
                    raise RuntimeError(f"富兰克林下载内容不是 PDF：{response.url}")
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
    source = str(sys.argv[1] if len(sys.argv) > 1 else DOCUMENTS_URL).strip()
    name = str(sys.argv[2] if len(sys.argv) > 2 else "").strip()
    if not name:
        raise SystemExit("用法：python download_franklin_factsheet.py <官方文档页> <英文基金名>")
    record = _find_document(name)
    download_url = str(record.get("downloadUrl") or "").strip()
    literature_href = str(record.get("literatureHref") or "").strip()
    candidates = [x for x in (download_url, urljoin(BASE_URL, literature_href)) if x]
    temp_path = None
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            temp_path = _download(candidate, source or DOCUMENTS_URL)
            break
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    if not temp_path:
        raise RuntimeError(f"富兰克林 Factsheet 下载失败：{last_error}")
    fallback = str(record.get("frkReferenceDate") or date.today().strftime("%Y-%m-%d"))
    try:
        report_month = extract_report_month_from_pdf(temp_path, fallback_date=fallback)
    except Exception:
        report_month = fallback
    target, _, created = finalize_pdf_to_monthly_path(
        temp_path,
        COMPANY,
        name,
        fallback_date=report_month,
    )
    print(f"[*] 文档目录: {source or DOCUMENTS_URL}")
    print(f"[*] Factsheet: {record.get('dctermsTitle')} / {record.get('frkReferenceDate')}")
    print(f"[*] {'完成' if created else '已存在'} -> {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
