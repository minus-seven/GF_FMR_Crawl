#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从骏利亨德森香港 Documents 分页下载最新英文 Factsheet。"""

from __future__ import annotations

import html
import os
import re
import sys
import tempfile
from datetime import date, datetime
from urllib.parse import urljoin

import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common.fund_link_backfill import normalize_name  # noqa: E402
from common_paths import configure_session, extract_report_month_from_pdf, finalize_pdf_to_monthly_path  # noqa: E402


COMPANY = "janushenderson"
DOCUMENTS_URL = "https://www.janushenderson.com/en-hk/investor/documents/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8",
}
SESSION = configure_session(requests.Session())


def _key(value: str) -> str:
    """归一化文档/工作簿英文名；参数 value 为产品或文档标题。"""

    text = normalize_name(str(value or "")).casefold()
    # normalize_name 已去掉英文空格，因此这里按连续词清理品牌前缀。
    text = text.replace("janushenderson", "")
    text = text.replace("horizon", "")
    text = text.replace("fund", "")
    # 文档标题会带 A2 USD/A5m HSGD 等份额后缀；后缀不参与产品匹配。
    text = re.sub(r"\b(?:a|h)\d+[a-z]*\b", "", text)
    return re.sub(r"[^a-z0-9]+", "", text)


def _pages() -> list[str]:
    """生成官方文件分页 URL；无参数，返回第 1 至 8 页。"""

    return [DOCUMENTS_URL] + [f"{DOCUMENTS_URL}page/{page}/" for page in range(2, 9)]


def _factsheet_rows(page_url: str, content: str) -> list[dict[str, str]]:
    """解析一页 Factsheet 文档；参数 page_url 为页面地址、content 为 HTML。"""

    rows: list[dict[str, str]] = []
    for match in re.finditer(r"<tr[^>]*>(.*?)</tr>", content, re.I | re.S):
        row = match.group(1)
        anchor = re.search(
            r'<a[^>]+class=["\'][^"\']*document-download[^"\']*["\'][^>]*>',
            row,
            re.I | re.S,
        )
        if not anchor:
            continue
        tag = anchor.group(0)
        name_match = re.search(r'data-name=["\']([^"\']+)', tag, re.I)
        href_match = re.search(r'href=["\']([^"\']+)', tag, re.I)
        date_match = re.search(r'document-date[^>]*>(.*?)</', row, re.I | re.S)
        type_match = re.search(r'document-type[^>]*>(.*?)</', row, re.I | re.S)
        language_match = re.search(r'document-language[^>]*>(.*?)</', row, re.I | re.S)
        if not name_match or not href_match:
            continue
        name = html.unescape(name_match.group(1)).strip()
        doc_type = re.sub(r"\s+", " ", html.unescape(type_match.group(1))).strip() if type_match else ""
        language = re.sub(r"\s+", " ", html.unescape(language_match.group(1))).strip() if language_match else ""
        date_text = re.sub(r"\s+", " ", html.unescape(date_match.group(1))).strip() if date_match else ""
        try:
            report_date = datetime.strptime(date_text, "%d/%m/%Y").date()
        except ValueError:
            continue
        if "factsheet" not in f"{name} {doc_type}".casefold() or language.upper() != "EN":
            continue
        rows.append(
            {
                "name": name,
                "url": urljoin(page_url, html.unescape(href_match.group(1))),
                "date": report_date.isoformat(),
                "currency_rank": str(_currency_rank(name)),
            }
        )
    return rows


def _currency_rank(name: str) -> int:
    """返回份额币种优先级；参数 name 为 Factsheet 标题。"""

    upper = name.upper()
    if re.search(r"\bHKD\b|HSGD", upper):
        return 0
    if re.search(r"\bUSD\b|HUSD", upper):
        return 1
    if re.search(r"\bEUR\b|HEUR|JPY|HJPY|AUD|HAUD", upper):
        return 2
    return 3


def _find_latest_factsheet(english: str) -> dict[str, str]:
    """在官方分页中选最新同名英文 Factsheet；参数 english 为工作簿英文基金名。"""

    target_key = _key(english)
    candidates: list[dict[str, str]] = []
    for page_url in _pages():
        response = SESSION.get(page_url, headers=HEADERS, timeout=90)
        response.raise_for_status()
        for row in _factsheet_rows(page_url, response.text):
            row_key = _key(row["name"])
            # 文档标题附带份额后缀，产品主体应精确一致；仅允许前缀包含以兼容品牌词差异。
            if row_key == target_key or row_key.startswith(target_key) or target_key.startswith(row_key):
                candidates.append(row)
    if not candidates:
        raise RuntimeError(f"骏利亨德森香港 Documents 未找到同名英文 Factsheet：{english}")
    return sorted(candidates, key=lambda row: (row["date"], -int(row["currency_rank"])), reverse=True)[0]


def _download(url: str, target: str) -> None:
    """下载并校验 PDF；参数 url 为文档直链、target 为临时文件路径。"""

    with SESSION.get(url, headers=HEADERS, timeout=120, stream=True) as response:
        response.raise_for_status()
        first = True
        with open(target, "wb") as handle:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                if first and not chunk.startswith(b"%PDF-"):
                    raise RuntimeError(f"骏利亨德森下载内容不是 PDF：{response.url}")
                first = False
                handle.write(chunk)
        if first:
            raise RuntimeError(f"骏利亨德森 PDF 为空：{response.url}")


def main() -> int:
    """解析命令行并归档 Factsheet；参数从 sys.argv 读取。"""

    page_url = str(sys.argv[1] if len(sys.argv) > 1 else "").strip()
    fund_name = str(sys.argv[2] if len(sys.argv) > 2 else "").strip()
    if not page_url or not fund_name:
        raise SystemExit("用法：python download_janushenderson_factsheet.py <官方产品页> <英文基金名>")
    fd, temp_path = tempfile.mkstemp(prefix="janushenderson_", suffix=".pdf")
    os.close(fd)
    try:
        selected = _find_latest_factsheet(fund_name)
        _download(selected["url"], temp_path)
        fallback = date.today().strftime("%Y-%m-%d")
        try:
            report_month = extract_report_month_from_pdf(temp_path, fallback_date=fallback)
        except Exception:
            report_month = fallback
        target, _, created = finalize_pdf_to_monthly_path(
            temp_path, COMPANY, fund_name, fallback_date=report_month
        )
        print(f"[*] 产品页: {page_url}")
        print(f"[*] Factsheet: {selected['name']} ({selected['date']})")
        print(f"[*] {'完成' if created else '已存在'} -> {target}")
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
