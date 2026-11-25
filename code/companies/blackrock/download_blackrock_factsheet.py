# -*- coding: utf-8 -*-
"""
BlackRock Hong Kong latest literature downloader.

Target page example:
  https://www.blackrock.com/hk/zh/literature?productIdentifier=LU2720916845%7E334659

The literature page includes the current document rows in the returned HTML.
This script re-reads that page each run, selects the matching material type and
product identifier, then downloads the direct PDF URL.

Usage:
  python download_blackrock_factsheet.py
  python download_blackrock_factsheet.py "LU2720916845~334659"
  python download_blackrock_factsheet.py "https://www.blackrock.com/hk/zh/literature?productIdentifier=LU2720916845%7E334659"
  python download_blackrock_factsheet.py "LU2720916845~334659" "fact-sheet"
"""

import html
import os
import re
import shutil
import sys
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common_paths import configure_session, company_data_dir, dated_output_path


DEFAULT_PAGE_URL = "https://www.blackrock.com/hk/zh/literature?productIdentifier=LU2720916845%7E334659"
DEFAULT_PRODUCT_IDENTIFIER = "LU2720916845~334659"
DEFAULT_MATERIAL_TYPE = "fact-sheet"
BASE_URL = "https://www.blackrock.com"
COMPANY = "blackrock"
SAVE_DIR = company_data_dir(COMPANY)
TIMEOUT = 45
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

SESSION = requests.Session()
configure_session(SESSION)


class BlackRockLiteratureParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.records = []
        self._in_record = False
        self._record_depth = 0
        self._record_html = []

    def handle_starttag(self, tag, attrs):
        attrs_map = {key.lower(): value or "" for key, value in attrs}
        classes = set(attrs_map.get("class", "").split())
        if tag.lower() == "tr" and "record" in classes:
            self._in_record = True
            self._record_depth = 1
            self._record_html = [self.get_starttag_text() or ""]
            return

        if self._in_record:
            self._record_depth += 1
            self._record_html.append(self.get_starttag_text() or "")

    def handle_startendtag(self, tag, attrs):
        if self._in_record:
            self._record_html.append(self.get_starttag_text() or "")

    def handle_data(self, data):
        if self._in_record:
            self._record_html.append(data)

    def handle_entityref(self, name):
        if self._in_record:
            self._record_html.append(f"&{name};")

    def handle_charref(self, name):
        if self._in_record:
            self._record_html.append(f"&#{name};")

    def handle_endtag(self, tag):
        if not self._in_record:
            return

        self._record_html.append(f"</{tag}>")
        self._record_depth -= 1
        if self._record_depth <= 0:
            self.records.append("".join(self._record_html))
            self._in_record = False
            self._record_html = []


def clean_text(value):
    value = html.unescape(value or "")
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def safe_filename(value):
    value = unquote(value or "").strip()
    value = re.sub(r"\s+", "_", value)
    return re.sub(r'[\\/:*?"<>|]', "_", value)


def product_from_url(page_url):
    query = parse_qs(urlparse(page_url).query)
    return unquote((query.get("productIdentifier") or [""])[0])


def isin_from_product(product_identifier):
    return (product_identifier or "").split("~", 1)[0]


def page_url_for_product(product_identifier):
    encoded = product_identifier.replace("~", "%7E")
    return f"https://www.blackrock.com/hk/zh/literature?productIdentifier={encoded}"


def fetch_page(page_url):
    resp = SESSION.get(page_url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or resp.encoding
    return resp.text


def parse_chinese_date(value):
    value = clean_text(value)
    match = re.search(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})", value)
    if match:
        year, month, day = map(int, match.groups())
        return datetime(year, month, day)
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass
    return datetime.min


def date_tag(value):
    parsed = parse_chinese_date(value)
    if parsed == datetime.min:
        return safe_filename(value or "latest")
    return parsed.strftime("%Y-%m-%d")


def direct_pdf_url(href):
    href = html.unescape(href or "")
    href = href.replace("/literature/download/", "/literature/")
    return urljoin(BASE_URL, href)


def parse_records(page_html):
    parser = BlackRockLiteratureParser()
    parser.feed(page_html)
    docs = []
    for record_html in parser.records:
        hrefs = re.findall(r'href="([^"]+\.pdf[^"]*)"', record_html, re.I)
        input_values = re.findall(r'<input[^>]+name="downloadCb"[^>]+value="([^"]+)"', record_html, re.I)
        candidates = input_values + hrefs
        candidates = [direct_pdf_url(value) for value in candidates]
        candidates = list(dict.fromkeys(candidates))
        if not candidates:
            continue

        title_match = re.search(r'title="([^"]+)"', record_html, re.I)
        title = clean_text(title_match.group(1) if title_match else "")
        if not title:
            h3_match = re.search(r'<td class="document-name".*?</td>', record_html, re.I | re.S)
            title = clean_text(h3_match.group(0) if h3_match else "")

        pub_match = re.search(r'<td class="document-publicationdate">\s*(.*?)\s*</td>', record_html, re.I | re.S)
        publication_date = clean_text(pub_match.group(1) if pub_match else "")

        info_name_match = re.search(
            r"<strong>\s*文件名稱:\s*</strong><br />\s*(.*?)\s*</p>",
            record_html,
            re.I | re.S,
        )
        info_type_match = re.search(
            r"<strong>\s*文件類別:\s*</strong><br />\s*(.*?)\s*</p>",
            record_html,
            re.I | re.S,
        )

        docs.append(
            {
                "title": title,
                "publication_date": publication_date,
                "info_name": clean_text(info_name_match.group(1) if info_name_match else ""),
                "info_type": clean_text(info_type_match.group(1) if info_type_match else ""),
                "urls": candidates,
            }
        )
    return docs


def select_document(docs, product_identifier, material_type):
    isin = isin_from_product(product_identifier).lower()
    material = (material_type or "").lower().strip()
    matches = []
    for doc in docs:
        urls = [url for url in doc["urls"] if isin in url.lower()]
        if not urls:
            continue
        if material and not any(f"/{material}/" in url.lower() for url in urls):
            continue
        selected = dict(doc)
        selected["url"] = urls[0]
        matches.append(selected)

    if not matches:
        available = "; ".join(doc.get("title") or doc["urls"][0] for doc in docs[:10])
        raise RuntimeError(
            f"No BlackRock document matched product='{product_identifier}', material='{material_type}'. "
            f"Available examples: {available}"
        )

    matches.sort(key=lambda item: parse_chinese_date(item.get("publication_date")), reverse=True)
    return matches[0]


def download_pdf(url, dest, referer):
    tmp = dest + ".part"
    headers = dict(HEADERS)
    headers["Referer"] = referer
    headers["Accept"] = "application/pdf,*/*"
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
    product_identifier = DEFAULT_PRODUCT_IDENTIFIER
    material_type = DEFAULT_MATERIAL_TYPE

    if not args:
        return page_url, product_identifier, material_type

    first = args[0].strip()
    if first.startswith("http://") or first.startswith("https://"):
        page_url = first
        product_identifier = product_from_url(first) or DEFAULT_PRODUCT_IDENTIFIER
        if len(args) > 1:
            material_type = args[1].strip()
    else:
        product_identifier = first
        page_url = page_url_for_product(product_identifier)
        if len(args) > 1:
            material_type = args[1].strip()

    return page_url, product_identifier, material_type


def main():
    page_url, product_identifier, material_type = resolve_args(sys.argv[1:])
    os.makedirs(SAVE_DIR, exist_ok=True)

    print(f"[*] Page: {page_url}")
    print(f"[*] Product: {product_identifier}")
    print(f"[*] Material type: {material_type}")

    docs = parse_records(fetch_page(page_url))
    doc = select_document(docs, product_identifier, material_type)
    title = doc.get("title") or isin_from_product(product_identifier)
    publication_date = doc.get("publication_date") or ""
    pdf_url = doc["url"]
    output_path = dated_output_path(COMPANY, f"{title}_{material_type}", publication_date)

    print(f"[*] Selected: {title} / {publication_date}")
    if doc.get("info_name") or doc.get("info_type"):
        print(f"[*] Info: {doc.get('info_name')} / {doc.get('info_type')}")
    print(f"[*] PDF: {pdf_url}")

    size, final_url = download_pdf(pdf_url, output_path, page_url)

    print(f"[*] Final URL: {final_url}")
    print(f"[*] Updated ({size / 1024:.1f} KB) -> {output_path}")


if __name__ == "__main__":
    main()
