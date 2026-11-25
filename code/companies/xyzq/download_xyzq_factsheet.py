# -*- coding: utf-8 -*-
"""
XYZQ Hong Kong latest fund document downloader.

Target page example:
  https://www.xyzq.com.hk/detail/asset-manage/fund-detail/1711530264564/download

The frontend reads fund documents from /official-website/cms/list with the
path key jjlb:jjda:<fund_id>. This script calls that API each run, filters by
document title, and downloads the newest matching PDF by publish date.

Usage:
  python download_xyzq_factsheet.py
  python download_xyzq_factsheet.py 1711530264564
  python download_xyzq_factsheet.py 1711530264564 "基金月报"
  python download_xyzq_factsheet.py "https://www.xyzq.com.hk/detail/asset-manage/fund-detail/1711530264564/download"
"""

import json
import os
import re
import shutil
import sys
from datetime import datetime
from urllib.parse import unquote, urljoin, urlparse

import requests
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common_paths import configure_session, company_data_dir, dated_output_path


DEFAULT_PAGE_URL = "https://www.xyzq.com.hk/detail/asset-manage/fund-detail/1711530264564/download"
DEFAULT_FUND_ID = "1711530264564"
DEFAULT_KEYWORD = "基金月报"
API_URL = "https://www.xyzq.com.hk/official-website/cms/list"
COMPANY = "xyzq"
SAVE_DIR = company_data_dir(COMPANY)
TIMEOUT = 60
RETRIES = 3
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": DEFAULT_PAGE_URL,
}

SESSION = requests.Session()
configure_session(SESSION)


def safe_filename(value):
    value = unquote(value or "").strip()
    value = re.sub(r"\s+", "_", value)
    return re.sub(r'[\\/:*?"<>|]', "_", value)


def parse_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except (TypeError, ValueError):
        return datetime.min


def date_tag(value):
    parsed = parse_date(value)
    if parsed == datetime.min:
        return safe_filename(value or "latest")
    return parsed.strftime("%Y-%m-%d")


def fund_id_from_value(value):
    value = (value or "").strip()
    if not value:
        return DEFAULT_FUND_ID, DEFAULT_PAGE_URL
    if value.startswith(("http://", "https://")):
        match = re.search(r"/fund-detail/([^/]+)/", urlparse(value).path)
        if not match:
            raise RuntimeError(f"Cannot parse XYZQ fund id from URL: {value}")
        return match.group(1), value
    return value, f"https://www.xyzq.com.hk/detail/asset-manage/fund-detail/{value}/download"


def fetch_documents(fund_id, page_url, lang="1"):
    params = {
        "path_key": f"jjlb:jjda:{fund_id}",
        "pageSize": 1000,
        "pageIndex": 1,
        "lang": lang,
    }
    headers = dict(HEADERS)
    headers["Referer"] = page_url
    data = None
    for attempt in range(1, RETRIES + 1):
        try:
            resp = SESSION.get(API_URL, params=params, headers=headers, timeout=TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            break
        except requests.RequestException as exc:
            if attempt == RETRIES:
                raise
            print(f"[!] XYZQ API request failed; retrying {attempt}/{RETRIES}: {exc}")
    if not data.get("success"):
        raise RuntimeError(f"XYZQ API failed: {data}")
    return data.get("data") or []


def document_file_url(cols):
    file_list = cols.get("fileList") or []
    if file_list:
        return file_list[0].get("file_path") or ""
    return cols.get("link") or ""


def select_latest_document(items, keyword):
    docs = []
    for item in items:
        try:
            cols = json.loads(item.get("cols") or "{}")
        except json.JSONDecodeError:
            cols = {}

        title = cols.get("title") or item.get("title") or ""
        file_url = document_file_url(cols)
        if keyword and keyword not in title:
            continue
        if not file_url.lower().endswith(".pdf"):
            continue
        docs.append(
            {
                "title": title,
                "date": item.get("pushDate") or "",
                "url": file_url,
            }
        )

    if not docs:
        raise RuntimeError(f"No XYZQ PDF document matched keyword='{keyword}'.")

    docs.sort(key=lambda item: parse_date(item["date"]), reverse=True)
    # OSS 上的旧文件可能被删除但列表仍保留（2026-09 实测 404），逐条探活取最新可用一期
    for doc in docs:
        try:
            with SESSION.get(
                doc["url"], headers=HEADERS, timeout=TIMEOUT, stream=True, allow_redirects=True
            ) as resp:
                if resp.status_code == 200:
                    return doc
        except requests.RequestException:
            continue
    return docs[0]


def archive_tag(pdf_url):
    stem = os.path.splitext(os.path.basename(urlparse(pdf_url).path))[0]
    return safe_filename(stem or "latest")


def download_pdf(url, dest):
    tmp = dest + ".part"
    with SESSION.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True, allow_redirects=True) as resp:
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
    if not args:
        return DEFAULT_FUND_ID, DEFAULT_PAGE_URL, DEFAULT_KEYWORD
    fund_id, page_url = fund_id_from_value(args[0])
    keyword = args[1].strip() if len(args) > 1 else DEFAULT_KEYWORD
    return fund_id, page_url, keyword


def main():
    fund_id, page_url, keyword = resolve_args(sys.argv[1:])
    os.makedirs(SAVE_DIR, exist_ok=True)

    items = fetch_documents(fund_id, page_url)
    doc = select_latest_document(items, keyword)
    output_path = dated_output_path(COMPANY, f"{fund_id}_{doc['title']}", doc["date"])

    print(f"[*] Page: {page_url}")
    print(f"[*] Fund ID: {fund_id}")
    print(f"[*] Selected: {doc['title']} / {doc['date']}")
    print(f"[*] PDF: {doc['url']}")

    size, final_url = download_pdf(doc["url"], output_path)

    print(f"[*] Final URL: {final_url}")
    print(f"[*] Updated ({size / 1024:.1f} KB) -> {output_path}")


if __name__ == "__main__":
    main()
