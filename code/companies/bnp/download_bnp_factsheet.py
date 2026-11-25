#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""下载并归档法巴香港单只 Factsheet（月报）。"""

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


BASE_SITE = "https://www.bnpparibas-am.com/en-hk"
API_BASE = "https://api.bnpparibas-am.com"
DOC_API = API_BASE + "/push/doclib/IP_HK-FSE?language=ENG"
COMPANY = "bnp"
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
    """下载并校验 PDF；参数 url 为文档地址、referer 为基金详情页。"""

    response = SESSION.get(url, headers={**HEADERS, "Referer": referer}, timeout=TIMEOUT, stream=True)
    response.raise_for_status()
    fd, path = tempfile.mkstemp(prefix="bnp_", suffix=".pdf")
    os.close(fd)
    first = True
    try:
        with open(path, "wb") as handle:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                if first and not chunk.startswith(b"%PDF-"):
                    raise RuntimeError(f"法巴下载内容不是 PDF：{response.url}")
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


def _find_current_doc(compartment_id: str) -> dict[str, Any] | None:
    """直链失效时刷新 Doclib；参数 compartment_id 为法巴基金目录 ID。"""

    response = SESSION.get(DOC_API, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    candidates = [
        item
        for item in payload if isinstance(item, dict)
        and item.get("type") == "DOC_FACTSHEET"
        and str(item.get("compartment_id") or "").strip() == compartment_id
        and str(item.get("direct_url") or "").strip()
    ] if isinstance(payload, list) else []
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: str(item.get("date") or ""), reverse=True)[0]


def main() -> int:
    """解析命令行并归档文件；本函数无参数，参数从 sys.argv 读取。"""

    source = str(sys.argv[1] if len(sys.argv) > 1 else "").strip()
    name = str(sys.argv[2] if len(sys.argv) > 2 else "").strip()
    compartment_id = str(sys.argv[3] if len(sys.argv) > 3 else "").strip()
    document_url = str(sys.argv[4] if len(sys.argv) > 4 else "").strip()
    if not source or not name or not compartment_id:
        raise SystemExit("用法：python download_bnp_factsheet.py <基金页> <英文基金名> <compartment_id> [Factsheet URL]")
    document_url = urljoin(BASE_SITE, document_url)
    try:
        temp_path = _download(document_url, source)
    except Exception as direct_error:
        document = _find_current_doc(compartment_id)
        if not document:
            raise RuntimeError(f"法巴 Factsheet 下载失败，且 Doclib 没有当前文档：{direct_error}") from direct_error
        document_url = str(document["direct_url"]).strip()
        temp_path = _download(document_url, source)
    try:
        report_month = extract_report_month_from_pdf(temp_path, fallback_date=date.today().strftime("%Y-%m-%d"))
    except Exception:
        report_month = date.today().strftime("%Y-%m-%d")
    target, _, created = finalize_pdf_to_monthly_path(temp_path, COMPANY, name, fallback_date=report_month)
    print(f"[*] 基金页: {source}")
    print(f"[*] compartment_id: {compartment_id}")
    print(f"[*] Factsheet: {document_url}")
    print(f"[*] {'完成' if created else '已存在'} -> {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
