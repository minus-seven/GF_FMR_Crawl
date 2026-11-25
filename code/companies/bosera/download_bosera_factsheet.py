# -*- coding: utf-8 -*-
"""
Bosera latest factsheet downloader.

Target page example:
  https://www.bosera.com.hk/zh-HK/products/fund/detail/SLIEO

The fund detail page is an Angular app. It reads factsheets from the public
fundinfo API. This script calls the same API each run, selects the requested
language, and downloads the current attachment URL.

Usage:
  python download_bosera_factsheet.py
  python download_bosera_factsheet.py SLIEO
  python download_bosera_factsheet.py "https://www.bosera.com.hk/zh-HK/products/fund/detail/SLIEO" "中文"
"""

import os
import re
import shutil
import sys
from datetime import datetime
from urllib.parse import unquote, urljoin, urlparse

import requests
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common_paths import configure_session, company_data_dir, dated_output_path


DEFAULT_PAGE_URL = "https://www.bosera.com.hk/zh-HK/products/fund/detail/SLIEO"
DEFAULT_FUND_CODE = "SLIEO"
DEFAULT_LANGUAGE = "中文"
BASE_URL = "https://www.bosera.com.hk"
FACTSHEET_API = f"{BASE_URL}/api/fundinfo/factsheet.json"
COMPANY = "bosera"
SAVE_DIR = company_data_dir(COMPANY)
TIMEOUT = 30
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}

SESSION = requests.Session()
configure_session(SESSION)


def safe_filename(value):
    value = unquote(value or "").strip()
    value = re.sub(r"\s+", "_", value)
    return re.sub(r'[\\/:*?"<>|]', "_", value)


def parse_date(value):
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(value, fmt)
        except (TypeError, ValueError):
            pass
    return datetime.min


def date_tag(value):
    parsed = parse_date(value)
    if parsed == datetime.min:
        return safe_filename(value or "latest")
    return parsed.strftime("%Y-%m-%d")


def fund_code_from_url(page_url):
    path = urlparse(page_url).path.rstrip("/")
    if not path:
        return ""
    return path.rsplit("/", 1)[-1]


def fetch_factsheets(fund_code, page_url):
    headers = dict(HEADERS)
    headers["Referer"] = page_url
    headers["Origin"] = BASE_URL
    headers["Content-Type"] = "application/json"
    resp = SESSION.post(
        FACTSHEET_API,
        headers=headers,
        json={"fundCode": fund_code},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("retCode") != "0000":
        raise RuntimeError(f"Bosera API returned {data.get('retCode')}: {data.get('retMsg')}")
    return data.get("data", {}).get("returnList", []) or []


def select_factsheet(items, language):
    language_aliases = {
        "中文": ("中文", "繁體", "繁体", "简体", "CHN", "CHI", "中文 "),
        "english": ("English", "ENG", "英文"),
    }
    aliases = language_aliases.get(language.lower(), (language,))

    matches = []
    for item in items:
        haystack = " ".join(
            str(item.get(key) or "")
            for key in ("title", "content", "keyWords")
        )
        if any(alias.lower() in haystack.lower() for alias in aliases):
            matches.append(item)

    if not matches:
        # In the Traditional Chinese page, console encoding can make "中文"
        # unreadable, but the Chinese item is the non-English factsheet.
        if language in ("中文", "繁體", "繁体", "简体"):
            non_english = [
                item for item in items
                if "english" not in str(item.get("title") or "").lower()
                and "eng" != str(item.get("content") or "").strip().lower()
            ]
            matches = non_english

    if not matches:
        available = "; ".join(str(item.get("title") or item.get("content") or item.get("id")) for item in items)
        raise RuntimeError(f"No Bosera factsheet matched language='{language}'. Available: {available}")

    matches.sort(key=lambda item: parse_date(item.get("originalTime")), reverse=True)
    return matches[0]


def download_pdf(url, dest, page_url):
    tmp = dest + ".part"
    headers = dict(HEADERS)
    headers["Referer"] = page_url
    with SESSION.get(url, headers=headers, timeout=TIMEOUT, stream=True, allow_redirects=True) as resp:
        resp.raise_for_status()
        final_url = resp.url
        with open(tmp, "wb") as fh:
            first = True
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                if first and not chunk.startswith(b"%PDF-"):
                    raise RuntimeError(f"Downloaded content is not a PDF: {final_url}")
                first = False
                fh.write(chunk)
    os.replace(tmp, dest)
    return os.path.getsize(dest), final_url


def resolve_args(args):
    page_url = DEFAULT_PAGE_URL
    fund_code = DEFAULT_FUND_CODE
    language = DEFAULT_LANGUAGE

    if not args:
        return page_url, fund_code, language

    first = args[0].strip()
    if first.startswith("http://") or first.startswith("https://"):
        page_url = first
        fund_code = fund_code_from_url(first) or DEFAULT_FUND_CODE
        if len(args) > 1:
            language = args[1].strip()
    else:
        fund_code = first
        page_url = f"https://www.bosera.com.hk/zh-HK/products/fund/detail/{fund_code}"
        if len(args) > 1:
            language = args[1].strip()

    return page_url, fund_code, language


def main():
    page_url, fund_code, language = resolve_args(sys.argv[1:])
    os.makedirs(SAVE_DIR, exist_ok=True)

    print(f"[*] Page: {page_url}")
    print(f"[*] Fund code: {fund_code}")
    print(f"[*] Language: {language}")

    item = select_factsheet(fetch_factsheets(fund_code, page_url), language)
    pdf_url = urljoin(BASE_URL, item["url"])
    title = str(item.get("title") or language)
    original_time = str(item.get("originalTime") or "")
    output_path = dated_output_path(COMPANY, f"{fund_code}_{title}", original_time)

    print(f"[*] Selected: {title} / {original_time}")
    print(f"[*] PDF: {pdf_url}")

    size, final_url = download_pdf(pdf_url, output_path, page_url)

    print(f"[*] Final URL: {final_url}")
    print(f"[*] Updated ({size / 1024:.1f} KB) -> {output_path}")


if __name__ == "__main__":
    main()
