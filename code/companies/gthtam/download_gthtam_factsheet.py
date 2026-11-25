# -*- coding: utf-8 -*-
"""
GTHT Asset Management latest fund overview downloader.

Target page example:
  https://gthtam.com.hk/asm/zh-chs/Home/library/38?series=Funds

The library page renders each document as a label plus a select box of dated
PDF options. This script re-reads the page each run, finds the requested label,
and downloads the newest dated option.

Usage:
  python download_gthtam_factsheet.py
  python download_gthtam_factsheet.py "https://gthtam.com.hk/asm/zh-chs/Home/library/38?series=Funds"
  python download_gthtam_factsheet.py "https://gthtam.com.hk/asm/zh-chs/Home/library/38?series=Funds" "基金概览"
"""

import html
import os
import re
import shutil
import sys
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin

import requests
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common_paths import configure_session, company_data_dir, dated_output_path


DEFAULT_PAGE_URL = "https://gthtam.com.hk/asm/zh-chs/Home/library/38?series=Funds"
DEFAULT_LABEL = "基金概览"
BASE_URL = "https://gthtam.com.hk"
COMPANY = "gthtam"
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


class GthtamLibraryParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.docs = []
        self._in_item = False
        self._capture_label = False
        self._in_select = False
        self._in_option = False
        self._label = ""
        self._option_url = ""
        self._option_date = ""
        self._options = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs_map = {key.lower(): value or "" for key, value in attrs}
        classes = set(attrs_map.get("class", "").split())

        if tag == "div" and "doc-list-item" in classes:
            self._in_item = True
            self._label = ""
            self._options = []
            return

        if not self._in_item:
            return

        if tag == "div" and "label" in classes:
            self._capture_label = True
        elif tag == "select":
            self._in_select = True
        elif self._in_select and tag == "option":
            self._in_option = True
            self._option_url = html.unescape(attrs_map.get("value", ""))
            self._option_date = ""

    def handle_data(self, data):
        if self._capture_label:
            self._label += data
        elif self._in_option:
            self._option_date += data

    def handle_entityref(self, name):
        value = f"&{name};"
        if self._capture_label:
            self._label += value
        elif self._in_option:
            self._option_date += value

    def handle_charref(self, name):
        value = f"&#{name};"
        if self._capture_label:
            self._label += value
        elif self._in_option:
            self._option_date += value

    def handle_endtag(self, tag):
        tag = tag.lower()

        if self._capture_label and tag == "div":
            self._capture_label = False
            self._label = clean_text(self._label)
            return

        if self._in_option and tag == "option":
            date_text = clean_text(self._option_date)
            if self._option_url:
                self._options.append(
                    {
                        "date": date_text,
                        "url": urljoin(BASE_URL, self._option_url),
                    }
                )
            self._in_option = False
            self._option_url = ""
            self._option_date = ""
            return

        if self._in_select and tag == "select":
            self._in_select = False
            return

        if self._in_item and tag == "div":
            if self._label and self._options:
                self.docs.append({"label": self._label, "options": self._options})
            self._in_item = False
            self._label = ""
            self._options = []


def clean_text(value):
    value = html.unescape(value or "")
    return re.sub(r"\s+", " ", value).strip()


def safe_filename(value):
    value = unquote(value)
    value = os.path.basename(value)
    value = re.sub(r"\.pdf$", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+", "_", value.strip())
    return re.sub(r'[\\/:*?"<>|]', "_", value)


def parse_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return datetime.min


def fetch_page(page_url):
    resp = SESSION.get(page_url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    return resp.text


def parse_documents(page_html):
    parser = GthtamLibraryParser()
    parser.feed(page_html)
    return parser.docs


def select_document(docs, label):
    matches = [doc for doc in docs if label in doc["label"]]
    if not matches:
        available = "; ".join(doc["label"] for doc in docs)
        raise RuntimeError(f"No GTHTAM document matched label='{label}'. Available: {available}")
    if len(matches) > 1:
        names = "; ".join(doc["label"] for doc in matches)
        raise RuntimeError(f"Multiple GTHTAM documents matched. Narrow label. Matches: {names}")

    doc = matches[0]
    options = sorted(doc["options"], key=lambda item: parse_date(item["date"]), reverse=True)
    if not options:
        raise RuntimeError(f"GTHTAM document has no PDF options: {doc['label']}")
    return doc["label"], options[0]


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
    label = DEFAULT_LABEL

    if not args:
        return page_url, label

    first = args[0].strip()
    if first.startswith("http://") or first.startswith("https://"):
        page_url = first
        if len(args) > 1:
            label = args[1].strip()
    else:
        label = first

    return page_url, label


def main():
    page_url, label = resolve_args(sys.argv[1:])
    os.makedirs(SAVE_DIR, exist_ok=True)

    print(f"[*] Page: {page_url}")
    print(f"[*] Label: {label}")

    selected_label, option = select_document(parse_documents(fetch_page(page_url)), label)
    output_path = dated_output_path(COMPANY, selected_label, option["date"])

    print(f"[*] Selected: {selected_label} / {option['date']}")
    print(f"[*] PDF: {option['url']}")

    size, final_url = download_pdf(option["url"], output_path)

    print(f"[*] Final URL: {final_url}")
    print(f"[*] Updated ({size / 1024:.1f} KB) -> {output_path}")


if __name__ == "__main__":
    main()
