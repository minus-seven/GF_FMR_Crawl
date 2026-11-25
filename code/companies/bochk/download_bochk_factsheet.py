# -*- coding: utf-8 -*-
"""
BOCHK Asset Management factsheet downloader.

Preferred source: Morningstar ODSHelper links used by BOCHK fund search:
  https://asiaapi.morningstar.com/ODSHelperWS/default.aspx?Id=<ID>&DocType=FS&ClientId=bochk&MarketId=CU$$$$$HKG&LanguageId=CH

Known IDs from the current workbook:
  0P0000ZEJA  BOCHK All Weather China High Yield Bond Fund
  0P00002C2T  BOCHK Global Equity Fund
  0P0000ZEJ8  BOCHK All Weather HK & China Equity Fund

Fallback source: BOCHK fund-detail pages that link directly to attachment PDFs.

Usage:
  python download_bochk_factsheet.py 0P0000ZEJA
  python download_bochk_factsheet.py "https://asiaapi.morningstar.com/ODSHelperWS/default.aspx?Id=0P0000ZEJA&DocType=FS&ClientId=bochk&MarketId=CU$$$$$HKG&LanguageId=CH"
  python download_bochk_factsheet.py "https://www.bochkam.com/cn/fund-detail-authorisedrmb.html"
  python download_bochk_factsheet.py "https://www.bochkam.com/attachment/FFS_BOCHK_AllWeatherRMBHighYieldBondFund_Chi.pdf"
"""

import html
import os
import re
import shutil
import sys
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import requests
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common_paths import configure_session, company_data_dir, dated_output_path


COMPANY = "bochk"
SAVE_DIR = company_data_dir(COMPANY)
TIMEOUT = 30
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}
ODS_BASE = "https://asiaapi.morningstar.com/ODSHelperWS/default.aspx"
DEFAULT_ID = "0P0000ZEJA"

SESSION = requests.Session()
configure_session(SESSION)

MSTAR_ID_RE = re.compile(r"^0P[0-9A-Z]+$", re.I)
DOC_HASH_RE = re.compile(r"/document/([^/?]+)\.msdoc", re.I)
PDF_LINK_RE = re.compile(r'<a\b[^>]*\bhref="(?P<href>[^"]+\.pdf(?:\?[^"]*)?)"[^>]*>', re.I)


def safe_filename(value):
    value = re.sub(r"\s+", "_", value.strip())
    return re.sub(r'[\\/:*?"<>|]', "_", value)


def ods_url(mstar_id):
    params = {
        "Id": mstar_id,
        "DocType": "FS",
        "ClientId": "bochk",
        "MarketId": "CU$$$$$HKG",
        "LanguageId": "CH",
    }
    return f"{ODS_BASE}?{urlencode(params)}"


def extract_id_from_url(url):
    query = parse_qs(urlparse(url).query)
    value = query.get("Id") or query.get("id")
    return value[0].strip() if value else None


def fetch_text(url):
    resp = SESSION.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or resp.encoding
    return resp.text


def first_direct_pdf_from_page(page_url):
    page_html = fetch_text(page_url)
    ffs_candidates = []
    other_candidates = []
    for match in PDF_LINK_RE.finditer(page_html):
        href = html.unescape(match.group("href"))
        absolute = urljoin(page_url, href)
        if "FFS_BOCHK" in href:
            ffs_candidates.append(absolute)
        elif "factsheet" in href.lower():
            other_candidates.append(absolute)
    candidates = ffs_candidates or other_candidates
    if not candidates:
        raise RuntimeError("No factsheet PDF link found on BOCHK detail page.")
    return candidates[0]


def infer_doc_tag(final_url, fallback):
    match = DOC_HASH_RE.search(final_url)
    if match:
        return safe_filename(match.group(1))
    name = os.path.basename(urlparse(final_url).path)
    return safe_filename(os.path.splitext(name)[0] or fallback)


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


def resolve_input(arg):
    arg = arg.strip()
    if not arg:
        arg = DEFAULT_ID

    if MSTAR_ID_RE.match(arg):
        return {"source": "ods", "id": arg.upper(), "url": ods_url(arg.upper())}

    parsed = urlparse(arg)
    lower = arg.lower()
    if "odshelperws/default.aspx" in lower:
        mstar_id = extract_id_from_url(arg)
        if not mstar_id:
            raise RuntimeError(f"Morningstar Id not found in URL: {arg}")
        return {"source": "ods", "id": mstar_id.upper(), "url": ods_url(mstar_id.upper())}

    if lower.endswith(".pdf"):
        slug = safe_filename(os.path.splitext(os.path.basename(parsed.path))[0])
        return {"source": "direct", "id": slug, "url": arg}

    if "bochkam.com" in lower:
        pdf_url = first_direct_pdf_from_page(arg)
        slug = safe_filename(os.path.splitext(os.path.basename(urlparse(pdf_url).path))[0])
        return {"source": "bochkam-page", "id": slug, "url": pdf_url}

    raise RuntimeError(f"Unsupported input. Use Morningstar ID, ODS URL, BOCHK page URL, or PDF URL: {arg}")


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ID
    os.makedirs(SAVE_DIR, exist_ok=True)

    target = resolve_input(arg)
    fund_name = safe_filename(target["id"])

    print(f"[*] Source: {target['source']}")
    print(f"[*] Download URL: {target['url']}")
    output_path = dated_output_path(COMPANY, fund_name, infer_doc_tag(target["url"], fund_name))
    size, final_url = download_pdf(target["url"], output_path)

    print(f"[*] Final URL: {final_url}")
    print(f"[*] Updated ({size / 1024:.1f} KB) -> {output_path}")


if __name__ == "__main__":
    main()
