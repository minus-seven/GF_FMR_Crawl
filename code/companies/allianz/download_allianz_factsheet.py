#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""下载安联环球基金详情页当前最新 Factsheet（月报）。

详情页由 ``allianz_fund_link.py`` 写入工作簿 I 列。安联文件 URL 带有
Sitecore hash/版本路径，不能长期固化；本脚本每次运行先读取详情页上的
``data-url=...pdf``，再下载并校验 PDF 文件头，最后归档到项目标准月报目录。

用法：
  python download_allianz_factsheet.py "https://hk.allianzgi.com/en-hk/retail/products-solutions/retail-funds/allianz-hong-kong-equity-a-hkd"
"""

from __future__ import annotations

import html
import os
import re
import sys
import tempfile
from datetime import date
from urllib.parse import urljoin, urlparse

import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common_paths import (  # noqa: E402
    configure_session,
    extract_report_month_from_pdf,
    finalize_pdf_to_monthly_path,
)


BASE_URL = "https://hk.allianzgi.com"
DEFAULT_URL = BASE_URL + "/en-hk/retail/products-solutions/retail-funds/allianz-hong-kong-equity-a-hkd"
COMPANY = "allianz"
TIMEOUT = 90
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/pdf,*/*;q=0.8",
}
SESSION = configure_session(requests.Session())
PDF_RE = re.compile(r'data-url=["\']([^"\']+\.pdf[^"\']*)', re.I)
ALT_PDF_RE = re.compile(r'(?:href|data-file-value)=["\']([^"\']+\.pdf[^"\']*)', re.I)


def _safe_name(url: str) -> str:
    """生成归档名；参数 url 为基金详情页或 PDF 地址。"""
    slug = urlparse(url).path.rstrip("/").split("/")[-1] or "allianz_fund"
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", slug)
    return slug[:120]


def _pdf_date(url: str) -> tuple[int, int]:
    """提取 URL 年月；参数 url 为候选 PDF 地址，缺失时返回零值。"""
    text = urlparse(url).path + " " + url
    match = re.search(r"20(\d{2})(0[1-9]|1[0-2])(?:[^0-9]|$)", text)
    return (int("20" + match.group(1)), int(match.group(2))) if match else (0, 0)


def _find_pdf(page_url: str) -> str:
    """从详情页发现 PDF；参数 page_url 为安联基金详情页地址。"""
    response = SESSION.get(page_url, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    text = response.text
    links = [html.unescape(link) for link in PDF_RE.findall(text)]
    if not links:
        links = [html.unescape(link) for link in ALT_PDF_RE.findall(text)]
    links = list(dict.fromkeys(urljoin(page_url, link) for link in links))
    if not links:
        raise RuntimeError("安联基金详情页未找到 Factsheet PDF 链接")
    # 详情页通常只含当前文件；若同时存在多份，按 URL 中的年月选最新。
    links.sort(key=lambda link: (_pdf_date(link), "factsheet" in link.lower()), reverse=True)
    return links[0]


def _download_pdf(pdf_url: str, page_url: str) -> str:
    """下载并校验 PDF；参数 pdf_url 为文件地址，page_url 为来源详情页地址。"""
    response = SESSION.get(pdf_url, headers={**HEADERS, "Referer": page_url}, timeout=TIMEOUT, stream=True)
    response.raise_for_status()
    fd, temp_path = tempfile.mkstemp(prefix="allianz_", suffix=".pdf")
    os.close(fd)
    try:
        with open(temp_path, "wb") as handle:
            first = True
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                if first and not chunk.startswith(b"%PDF-"):
                    raise RuntimeError(f"安联下载内容不是 PDF：{response.url}")
                first = False
                handle.write(chunk)
        return temp_path
    except Exception:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise
    finally:
        response.close()


def main() -> int:
    page_url = str(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL).strip()
    pdf_url = page_url if page_url.lower().split("?", 1)[0].endswith(".pdf") else _find_pdf(page_url)
    temp_path = _download_pdf(pdf_url, page_url)
    fallback = date.today().strftime("%Y-%m-%d")
    try:
        report_month = extract_report_month_from_pdf(temp_path, fallback_date=fallback)
    except Exception:
        report_month = fallback
    target, _, created = finalize_pdf_to_monthly_path(
        temp_path,
        COMPANY,
        _safe_name(page_url),
        fallback_date=report_month,
    )
    print(f"[*] 详情页: {page_url}")
    print(f"[*] Factsheet: {pdf_url}")
    print(f"[*] {'完成' if created else '已存在'} -> {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
