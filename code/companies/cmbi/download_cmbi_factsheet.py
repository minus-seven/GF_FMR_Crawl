# -*- coding: utf-8 -*-
"""
CMB International latest fund monthly sheet downloader.

Target page example:
  https://www.cmbi.com.hk/zh-HK/mutualfund?id=5#anchor7

The fund documents are server-rendered in the "基金文件" section. This script
re-reads the product page each run and downloads the matching document from the
"基金月報" block, so it follows updated CMBI download keys.

Usage:
  python download_cmbi_factsheet.py
  python download_cmbi_factsheet.py "https://www.cmbi.com.hk/zh-HK/mutualfund?id=5#anchor7"
  python download_cmbi_factsheet.py "https://www.cmbi.com.hk/zh-HK/mutualfund?id=5#anchor7" "基金月報" "中文"
"""

import html
import os
import re
import shutil
import sys
from html.parser import HTMLParser
from urllib.parse import parse_qs, urljoin, urlparse

import requests
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common_paths import configure_session, company_data_dir, dated_output_path


DEFAULT_PAGE_URL = "https://www.cmbi.com.hk/zh-HK/mutualfund?id=5#anchor7"
DEFAULT_CATEGORY = "基金月報"
DEFAULT_KEYWORD = "中文"
BASE_URL = "https://www.cmbi.com.hk"
COMPANY = "cmbi"
SAVE_DIR = company_data_dir(COMPANY)
TIMEOUT = 30
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

SESSION = requests.Session()
configure_session(SESSION)


class CmbiDocumentParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.docs = []
        self.current_category = ""
        self._tag_stack = []
        self._capture_title = False
        self._capture_doc_name = False
        self._doc_name = ""
        self._doc_href = ""
        self._in_li = False

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs_map = {key.lower(): value or "" for key, value in attrs}
        classes = set(attrs_map.get("class", "").split())
        self._tag_stack.append((tag, classes))

        if tag == "div" and "list-title" in classes:
            self.current_category = ""
            self._capture_title = True
            return

        if tag == "li":
            self._in_li = True
            self._doc_name = ""
            self._doc_href = ""
            return

        if self._in_li and tag == "a":
            href = attrs_map.get("href", "")
            if "/front/mfunddownload/file" in href:
                self._doc_href = html.unescape(href)

        if self._in_li and tag == "div":
            self._capture_doc_name = True

    def handle_data(self, data):
        if self._capture_title:
            self.current_category += data
        elif self._capture_doc_name:
            self._doc_name += data

    def handle_entityref(self, name):
        value = f"&{name};"
        if self._capture_title:
            self.current_category += value
        elif self._capture_doc_name:
            self._doc_name += value

    def handle_charref(self, name):
        value = f"&#{name};"
        if self._capture_title:
            self.current_category += value
        elif self._capture_doc_name:
            self._doc_name += value

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "div" and self._capture_title:
            self.current_category = clean_text(self.current_category)
            self._capture_title = False
        elif tag == "div" and self._capture_doc_name:
            self._capture_doc_name = False
        elif tag == "li" and self._in_li:
            name = clean_text(self._doc_name)
            name = re.sub(r"下載$", "", name).strip()
            if name and self._doc_href:
                self.docs.append(
                    {
                        "category": self.current_category,
                        "name": name,
                        "url": urljoin(BASE_URL, self._doc_href),
                    }
                )
            self._in_li = False
            self._doc_name = ""
            self._doc_href = ""

        if self._tag_stack:
            self._tag_stack.pop()


def clean_text(value):
    value = html.unescape(value or "")
    return re.sub(r"\s+", " ", value).strip()


def safe_filename(value):
    value = re.sub(r"\s+", "_", value.strip())
    return re.sub(r'[\\/:*?"<>|]', "_", value)


def fetch_page(page_url):
    resp = SESSION.get(page_url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or resp.encoding
    return resp.text


def parse_documents(page_html):
    parser = CmbiDocumentParser()
    parser.feed(page_html)
    return parser.docs


def select_document(docs, category, keyword):
    matches = []
    for doc in docs:
        if category and category not in doc["category"]:
            continue
        if keyword and keyword not in doc["name"]:
            continue
        matches.append(doc)

    if not matches:
        raise RuntimeError(f"No CMBI document matched category='{category}', keyword='{keyword}'.")
    if len(matches) > 1:
        names = "; ".join(doc["name"] for doc in matches)
        raise RuntimeError(f"Multiple CMBI documents matched. Narrow keyword. Matches: {names}")
    return matches[0]


def archive_tag(download_url):
    query = parse_qs(urlparse(download_url).query)
    key = query.get("key", [""])[0]
    if key:
        return safe_filename(key)[:24]
    return "latest"


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
    page_url = DEFAULT_PAGE_URL
    category = DEFAULT_CATEGORY
    keyword = DEFAULT_KEYWORD

    if not args:
        return page_url, category, keyword

    first = args[0].strip()
    if first.startswith("http://") or first.startswith("https://"):
        page_url = first
        if len(args) > 1:
            category = args[1].strip()
        if len(args) > 2:
            keyword = args[2].strip()
    else:
        keyword = first
        if len(args) > 1:
            category = args[1].strip()

    return page_url, category, keyword


def main():
    page_url, category, keyword = resolve_args(sys.argv[1:])
    os.makedirs(SAVE_DIR, exist_ok=True)

    print(f"[*] Page: {page_url}")
    print(f"[*] Category: {category}")
    print(f"[*] Keyword: {keyword}")

    doc = select_document(parse_documents(fetch_page(page_url)), category, keyword)
    output_path = dated_output_path(COMPANY, doc["name"], archive_tag(doc["url"]))

    print(f"[*] Selected: {doc['category']} / {doc['name']}")
    print(f"[*] PDF: {doc['url']}")

    size, final_url = download_pdf(doc["url"], output_path)

    print(f"[*] Final URL: {final_url}")
    print(f"[*] Updated ({size / 1024:.1f} KB) -> {output_path}")


if __name__ == "__main__":
    main()
