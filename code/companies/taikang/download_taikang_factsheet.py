# -*- coding: utf-8 -*-
"""
Taikang Hong Kong latest monthly report downloader.

Target page:
  http://hk.taikangasset.cn/hk/product_service/productService.html?nav=1

The product page loads document links from the same JSON APIs used by the
website. The rendered link looks like:
  泰康開泰投資級債券基金月報 (2026年5月)

Usage:
  python download_taikang_factsheet.py
  python download_taikang_factsheet.py "泰康開泰投資級債券基金"
  python download_taikang_factsheet.py "http://hk.taikangasset.cn/hk/product_service/productService.html?nav=1" "泰康開泰投資級債券基金"
"""

import json
import os
import re
import shutil
import sys
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import requests
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common_paths import configure_session, company_data_dir, dated_output_path


PAGE_URL = "http://hk.taikangasset.cn/hk/product_service/productService.html?nav=1"
DEFAULT_FUND_KEYWORD = "泰康開泰投資級債券基金"
BASE_URL = "https://hk.taikangasset.cn"
COMPANY = "taikang"
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


class AnchorParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.links = []
        self._current = None

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a":
            return
        attr_map = {key.lower(): value or "" for key, value in attrs}
        self._current = {
            "href": attr_map.get("href", ""),
            "download": attr_map.get("download", ""),
            "title": attr_map.get("title", ""),
            "text": "",
        }

    def handle_data(self, data):
        if self._current is not None:
            self._current["text"] += data

    def handle_entityref(self, name):
        if self._current is not None:
            self._current["text"] += f"&{name};"

    def handle_charref(self, name):
        if self._current is not None:
            self._current["text"] += f"&#{name};"

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self._current is not None:
            self.links.append(self._current)
            self._current = None


def safe_filename(value):
    value = re.sub(r"\s+", "_", value.strip())
    return re.sub(r'[\\/:*?"<>|]', "_", value)


def clean_text(value):
    value = unescape(value or "")
    return re.sub(r"\s+", " ", value).strip()


def fetch_page(page_url):
    resp = SESSION.get(page_url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or resp.encoding
    return resp.text


def prime_session(page_url):
    resp = SESSION.get(page_url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()


def api_headers(page_url):
    headers = dict(HEADERS)
    headers.update(
        {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Referer": page_url,
        }
    )
    return headers


def post_json(path, payload, page_url):
    resp = SESSION.post(
        urljoin(BASE_URL, path),
        headers=api_headers(page_url),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Taikang API error: {data}")
    return data.get("data")


def get_json(path, page_url):
    headers = dict(HEADERS)
    headers.update({"Accept": "application/json, text/plain, */*", "Referer": page_url})
    resp = SESSION.get(urljoin(BASE_URL, path), headers=headers, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Taikang API error: {data}")
    return data.get("data")


def parse_report_month(label):
    match = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月", label or "")
    if not match:
        return (0, 0), "latest"
    year = int(match.group(1))
    month = int(match.group(2))
    return (year, month), f"{year:04d}-{month:02d}"


def parse_create_time(value):
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})", value or "")
    if not match:
        return (0, 0, 0)
    return tuple(int(part) for part in match.groups())


def fund_name_from_label(label, fallback):
    label = re.sub(r"\(\s*\d{4}\s*年\s*\d{1,2}\s*月\s*\)", "", label or "")
    label = re.sub(r"\d{4}\s*年\s*\d{1,2}\s*月", "", label)
    label = re.sub(r"月[報报].*$", "", label).strip()
    return label or fallback or "taikang_fund"


def parse_monthly_reports(page_html, page_url, fund_keyword):
    parser = AnchorParser()
    parser.feed(page_html)

    reports = []
    for link in parser.links:
        href = clean_text(link["href"])
        if not href:
            continue

        path = urlparse(href).path.lower()
        if not path.endswith(".pdf") or "productinformation" not in path:
            continue

        label = clean_text(link["download"]) or clean_text(link["title"]) or clean_text(link["text"])
        if "月報" not in label and "月报" not in label:
            continue
        if fund_keyword and fund_keyword not in label:
            continue

        month_key, month_label = parse_report_month(label)
        reports.append(
            {
                "label": label,
                "fund_name": fund_name_from_label(label, fund_keyword),
                "month_key": month_key,
                "month": month_label,
                "url": urljoin(page_url, href),
            }
        )

    reports.sort(key=lambda item: item["month_key"], reverse=True)
    return reports


def iter_child_funds(product_tree):
    for parent in product_tree or []:
        for child in parent.get("childProduct") or []:
            yield child


def resolve_fund_code(fund_keyword, page_url):
    if re.fullmatch(r"\d{6,}", fund_keyword or ""):
        return fund_keyword, fund_keyword

    product_tree = get_json("/api/product/fundListTree/hk", page_url)
    matches = []
    for child in iter_child_funds(product_tree):
        fund_name = child.get("fundName") or ""
        fund_code = child.get("fundCode") or ""
        if fund_keyword in fund_name or fund_name in fund_keyword:
            matches.append((fund_code, fund_name))

    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        names = ", ".join(f"{code}:{name}" for code, name in matches)
        raise RuntimeError(f"Multiple Taikang funds matched '{fund_keyword}': {names}")
    raise RuntimeError(f"Taikang fund not found in product tree: {fund_keyword}")


def parse_api_monthly_reports(records, page_url, fund_keyword):
    reports = []
    for item in records or []:
        label = clean_text(item.get("fundFileName"))
        if "月報" not in label and "月报" not in label:
            continue
        if fund_keyword and fund_keyword not in label and fund_keyword not in (item.get("fundName") or ""):
            continue

        file_name = item.get("fileName") or os.path.basename(item.get("fileAddress") or "")
        if not file_name.lower().endswith(".pdf"):
            continue

        month_key, month_label = parse_report_month(label)
        reports.append(
            {
                "label": label,
                "fund_name": item.get("fundName") or fund_name_from_label(label, fund_keyword),
                "month_key": month_key,
                "create_key": parse_create_time(item.get("createTime")),
                "month": month_label,
                "url": urljoin(page_url, f"/productinformation/{file_name}"),
            }
        )

    reports.sort(key=lambda item: (item["month_key"], item["create_key"]), reverse=True)
    return reports


def latest_monthly_report(page_url, fund_keyword):
    prime_session(page_url)
    fund_code, fund_name = resolve_fund_code(fund_keyword, page_url)
    data = post_json(
        "/api/tblProductInformation/getList",
        {"language": "hk", "fundCode": fund_code, "pageNum": 1, "pageSize": 100},
        page_url,
    )
    records = data.get("records") if isinstance(data, dict) else data
    reports = parse_api_monthly_reports(records, page_url, fund_name)
    if reports:
        return fund_code, reports[0]

    reports = parse_monthly_reports(fetch_page(page_url), page_url, fund_keyword)
    if reports:
        return fund_code, reports[0]

    raise RuntimeError("No matching Taikang monthly report PDF link found.")


def download_pdf(url, dest):
    tmp = dest + ".part"
    with SESSION.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True) as resp:
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
    page_url = PAGE_URL
    fund_keyword = DEFAULT_FUND_KEYWORD

    if not args:
        return page_url, fund_keyword

    first = args[0].strip()
    if first.startswith("http://") or first.startswith("https://"):
        page_url = first
        if len(args) > 1:
            fund_keyword = args[1].strip()
    else:
        fund_keyword = first

    return page_url, fund_keyword


def main():
    page_url, fund_keyword = resolve_args(sys.argv[1:])
    os.makedirs(SAVE_DIR, exist_ok=True)

    print(f"[*] Page: {page_url}")
    print(f"[*] Fund keyword: {fund_keyword}")

    fund_code, latest = latest_monthly_report(page_url, fund_keyword)
    output_path = dated_output_path(COMPANY, latest["fund_name"], latest["month"])

    print(f"[*] Fund code: {fund_code}")
    print(f"[*] Latest monthly report: {latest['label']}")
    print(f"[*] PDF: {latest['url']}")

    size, final_url = download_pdf(latest["url"], output_path)

    print(f"[*] Final URL: {final_url}")
    print(f"[*] Updated ({size / 1024:.1f} KB) -> {output_path}")


if __name__ == "__main__":
    main()
