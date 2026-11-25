#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""下载 Robeco 香港基金详情页中的最新 Factsheet。"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from datetime import date
from html.parser import HTMLParser
from urllib.parse import urljoin

import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common_paths import configure_session, extract_report_month_from_pdf, finalize_pdf_to_monthly_path  # noqa: E402

COMPANY = "robeco"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
SESSION = configure_session(requests.Session())


class _FactsheetParser(HTMLParser):
    """收集详情页中 Factsheet PDF 链接；参数由 HTMLParser 负责传入。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._anchor: dict[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """开始收集锚点；参数 tag 为标签名、attrs 为属性。"""
        if tag.lower() != "a":
            return
        values = {key.lower(): value or "" for key, value in attrs}
        if ".pdf" in values.get("href", "").lower():
            self._anchor = {"href": values["href"], "text": ""}

    def handle_data(self, data: str) -> None:
        """累积链接文本；参数 data 为 HTML 文本片段。"""
        if self._anchor is not None:
            self._anchor["text"] += data

    def handle_endtag(self, tag: str) -> None:
        """结束锚点；参数 tag 为标签名。"""
        if tag.lower() == "a" and self._anchor is not None:
            self.links.append((self._anchor["href"], " ".join(self._anchor["text"].split())))
            self._anchor = None


def _find_pdf(page_url: str) -> str:
    """从官方详情页选择 Factsheet PDF；参数 page_url 为基金详情页。"""
    response = SESSION.get(page_url, headers=HEADERS, timeout=90)
    response.raise_for_status()
    parser = _FactsheetParser()
    parser.feed(response.text)
    candidates = [(urljoin(page_url, href), text) for href, text in parser.links if "fact" in text.casefold() or "fact-" in href.casefold()]
    if not candidates:
        raise RuntimeError(f"Robeco 详情页未找到 Factsheet：{page_url}")
    return candidates[0][0]


def main(argv: list[str] | None = None) -> int:
    """下载并归档 Factsheet；参数 argv 为详情页 URL 和基金名称。"""
    args = list(sys.argv[1:] if argv is None else argv)
    page_url = args[0] if args else ""
    fund_name = args[1] if len(args) > 1 else "Robeco"
    if not page_url:
        raise SystemExit("用法：python download_robeco_factsheet.py <详情页URL> <基金英文名>")
    pdf_url = _find_pdf(page_url)
    fd, temp_path = tempfile.mkstemp(prefix="robeco_", suffix=".pdf")
    os.close(fd)
    try:
        with SESSION.get(pdf_url, headers=HEADERS, timeout=120, stream=True) as response:
            response.raise_for_status()
            with open(temp_path, "wb") as handle:
                first = True
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    if first and not chunk.startswith(b"%PDF-"):
                        raise RuntimeError(f"Robeco 下载内容不是 PDF：{response.url}")
                    first = False
                    handle.write(chunk)
        fallback = date.today().strftime("%Y-%m-%d")
        try:
            report_month = extract_report_month_from_pdf(temp_path, fallback_date=fallback)
        except Exception:
            report_month = fallback
        target, _, created = finalize_pdf_to_monthly_path(temp_path, COMPANY, re.sub(r"[^\w.-]+", "_", fund_name), fallback_date=report_month)
        print(f"[*] PDF: {pdf_url}")
        print(f"[*] {'完成' if created else '已存在'} -> {target}")
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
