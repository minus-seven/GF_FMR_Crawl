# -*- coding: utf-8 -*-
"""
China Taiping Financial Holdings latest monthly factsheet downloader.

Target page:
  https://www.tpfh.cntaiping.com/ywjs-taipingdzhinfo/

The page may expose either direct PDF links or intermediate detail/download
links. This script keeps only Taiping Greater China New Momentum Equity Fund
monthly factsheet rows, identified by TGCNMEB or 基金概覽/基金概览, selects the
latest dated row, resolves it to a PDF, and saves the result under
data/<YYYY-MM-DD>/.

Usage:
  python download_taiping_factsheet.py
  python download_taiping_factsheet.py "https://www.tpfh.cntaiping.com/ywjs-taipingdzhinfo/"
"""

import os
import re
import sys
from io import BytesIO
from urllib.parse import urljoin, urlparse

from pypdf import PdfReader
from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common_paths import company_data_dir, dated_output_path


for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(errors="replace")


COMPANY = "taiping"
DEFAULT_PAGE_URL = "https://www.tpfh.cntaiping.com/ywjs-taipingdzhinfo/"
DEFAULT_FUND_KEY = "tgcnmeb"
SAVE_DIR = company_data_dir(COMPANY)
TIMEOUT_MS = 90000
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

TARGET_RE = re.compile(r"(TGCNMEB|基金概覽|基金概览)", re.I)
EXCLUDE_RE = re.compile(
    r"(年度報告|年度报告|年報|年报|中期報告|中期报告|中期|通知|說明書|说明书|產品資料概要|产品资料概要|"
    r"prospectus|annual|interim|semi|notice|kfs)",
    re.I,
)
DATE_PATTERNS = (
    re.compile(r"(20\d{2})\s*[-./年]\s*(0?[1-9]|1[0-2])(?:\s*[-./月]\s*([0-3]?\d))?"),
    re.compile(r"(20\d{2})(0[1-9]|1[0-2])([0-3]\d)?"),
)
DATA_CUTOFF_RE = re.compile(r"(數據截止|数据截止|資料截至|资料截至|截至)[^\n。；;]{0,40}")
FACTSHEET_TITLE_DATE_RE = re.compile(r"(基金概覽|基金概览)[^\n。；;]{0,40}")

CHINESE_DIGIT = {
    "零": 0,
    "〇": 0,
    "○": 0,
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
CHINESE_DATE_RE = re.compile(r"([二兩两零〇○一三四五六七八九]{4})年([十十一二三四五六七八九]+)月")

KNOWN_TGCNMEB_REPORTS = [
    {
        "title": "基金概覽 (二零二六年六月)_TGCNMEB",
        "date": "2026-06-01",
        "url": "https://www.cntaiping.com/upload/cms/tpfh/202607/14141111q5a8.pdf",
    },
    {
        "title": "基金概覽 (二零二六年五月)_TGCNMEB",
        "date": "2026-05-01",
        "url": "https://www.cntaiping.com/upload/cms/tpfh/202606/05151609o7p2.pdf",
    },
    {
        "title": "基金概覽 (二零二六年四月)_TGCNMEB",
        "date": "2026-04-01",
        "url": "https://www.cntaiping.com/upload/cms/tpfh/202605/12145542rx3q.pdf",
    },
    {
        "title": "基金概覽 (二零二六年三月)_TGCNMEB",
        "date": "2026-03-01",
        "url": "https://www.cntaiping.com/upload/cms/tpfh/202604/131137420fns.pdf",
    },
    {
        "title": "基金概覽 (二零二六年二月)_TGCNMEB",
        "date": "2026-02-01",
        "url": "https://www.cntaiping.com/upload/cms/tpfh/202603/12143008cwyu.pdf",
    },
    {
        "title": "基金概覽 (二零二六年一月)_TGCNMEB",
        "date": "2026-01-01",
        "url": "https://www.cntaiping.com/upload/cms/tpfh/202602/12165227xci0.pdf",
    },
]


def safe_filename(value):
    value = re.sub(r"\s+", "_", str(value or "").strip())
    return re.sub(r'[\\/:*?"<>|]', "_", value)


def date_tag(value):
    text = str(value or "")
    match = CHINESE_DATE_RE.search(text)
    if match:
        year = chinese_year(match.group(1))
        month = chinese_month(match.group(2))
        if year and month:
            return f"{year}-{month:02d}-01"
    for pattern in DATE_PATTERNS:
        match = pattern.search(text)
        if match:
            year = match.group(1)
            month = int(match.group(2))
            return f"{year}-{month:02d}-01"
    return None


def chinese_year(value):
    digits = []
    for char in value:
        if char in ("兩", "两"):
            digits.append("2")
        elif char in CHINESE_DIGIT:
            digits.append(str(CHINESE_DIGIT[char]))
        else:
            return None
    return "".join(digits) if len(digits) == 4 else None


def chinese_month(value):
    text = value.replace("兩", "二").replace("两", "二")
    if text == "十":
        return 10
    if text.startswith("十"):
        suffix = text[1:]
        return 10 + (CHINESE_DIGIT.get(suffix, 0) if suffix else 0)
    if text.endswith("十"):
        prefix = text[:-1]
        return CHINESE_DIGIT.get(prefix, 0) * 10
    if "十" in text:
        prefix, suffix = text.split("十", 1)
        return CHINESE_DIGIT.get(prefix, 0) * 10 + CHINESE_DIGIT.get(suffix, 0)
    return CHINESE_DIGIT.get(text)


def is_target_row(blob):
    return bool(TARGET_RE.search(blob)) and not EXCLUDE_RE.search(blob)


def archive_tag(url, fallback):
    tag = date_tag(url)
    if tag:
        return tag
    stem = os.path.splitext(os.path.basename(urlparse(url).path))[0]
    return safe_filename(stem or fallback)


def pdf_date_tag(content):
    try:
        reader = PdfReader(BytesIO(content))
        text = "\n".join((page.extract_text() or "") for page in reader.pages[:2])
    except Exception:
        return None
    for pattern in (DATA_CUTOFF_RE, FACTSHEET_TITLE_DATE_RE):
        for match in pattern.finditer(text):
            tag = date_tag(match.group(0))
            if tag:
                return tag
    return date_tag(text)


def browser_context(playwright):
    browser = playwright.chromium.launch(
        headless=True,
        args=["--disable-blink-features=AutomationControlled"],
    )
    context = browser.new_context(
        locale="zh-CN",
        user_agent=USER_AGENT,
        ignore_https_errors=True,
        viewport={"width": 1365, "height": 900},
    )
    context.add_init_script('Object.defineProperty(navigator, "webdriver", {get: () => undefined});')
    return browser, context


def goto_and_wait(page, url):
    response = page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
    page.wait_for_timeout(5000)
    return response


def page_links(page):
    return page.evaluate(
        """() => Array.from(document.querySelectorAll('a')).map(a => ({
            href: a.href || a.getAttribute('href') || '',
            text: (a.innerText || a.textContent || '').trim(),
            rowText: ((a.closest('tr') || a.closest('li') || a.parentElement) &&
                (a.closest('tr') || a.closest('li') || a.parentElement).innerText || '').trim(),
            html: a.outerHTML || ''
        })).filter(x => x.href)"""
    )


def pdf_rows(page):
    return page.evaluate(
        """() => Array.from(document.querySelectorAll('.pdf-ul-text')).map(textEl => {
            const row = textEl.closest('li') || textEl.closest('.pdf-ul') || textEl.parentElement;
            const link = row && row.querySelector('a.pdf-ul-a[href], a[href$=".pdf"], a[href*=".pdf"]');
            return {
                title: (textEl.innerText || textEl.textContent || '').trim(),
                rowText: (row && row.innerText || '').trim(),
                href: link ? link.href : ''
            };
        }).filter(x => x.href)"""
    )


def collect_candidates(context, page_url):
    page = context.new_page()
    response = goto_and_wait(page, page_url)
    body_text = page.evaluate("() => (document.body && document.body.innerText || '').trim()")
    status = response.status if response else None

    if status and status >= 400 and not body_text:
        raise RuntimeError(
            f"Taiping page returned HTTP {status} with empty content. "
            "The site is blocking this browser/request context."
        )
    if not body_text and not page_links(page):
        raise RuntimeError("Taiping page loaded empty content; no links can be parsed.")

    candidates = []
    for row in pdf_rows(page):
        title = " ".join((row.get("title") or "").split())
        row_text = " ".join((row.get("rowText") or "").split())
        href = urljoin(page_url, row.get("href") or "")
        blob = f"{row_text} {title} {href}"
        if is_target_row(blob):
            candidates.append({"title": title or row_text or safe_filename(href), "url": href, "date": date_tag(blob)})

    for link in page_links(page):
        title = " ".join((link.get("text") or "").split())
        row_text = " ".join((link.get("rowText") or "").split())
        href = urljoin(page_url, link.get("href") or "")
        blob = f"{row_text} {title} {href} {link.get('html') or ''}"
        if is_target_row(blob):
            candidates.append({"title": row_text or title or safe_filename(href), "url": href, "date": date_tag(blob)})

    page.close()
    candidates.sort(key=lambda item: item.get("date") or "", reverse=True)
    return candidates


def known_candidates():
    candidates = [dict(item) for item in KNOWN_TGCNMEB_REPORTS]
    candidates.sort(key=lambda item: item["date"], reverse=True)
    return candidates


def resolve_pdf(context, candidate):
    url = candidate["url"]
    if url.lower().split("?", 1)[0].endswith(".pdf"):
        return url

    page = context.new_page()
    goto_and_wait(page, url)
    links = page_links(page)
    page.close()

    pdf_links = []
    for link in links:
        href = urljoin(url, link.get("href") or "")
        blob = f"{link.get('rowText') or ''} {link.get('text') or ''} {href} {link.get('html') or ''}"
        if is_target_row(blob) or href.lower().split("?", 1)[0].endswith(".pdf"):
            pdf_links.append(href)
    if not pdf_links:
        raise RuntimeError(f"No PDF/download link found from candidate: {url}")
    return pdf_links[0]


def download_pdf(context, url, dest):
    tmp = dest + ".part"
    response = context.request.get(url, timeout=TIMEOUT_MS, max_redirects=5)
    if not response.ok:
        raise RuntimeError(f"PDF request failed HTTP {response.status}: {url}")
    content = response.body()
    if not content.startswith(b"%PDF-"):
        raise RuntimeError(f"Downloaded content is not a PDF: {url}")
    content_date = pdf_date_tag(content)
    with open(tmp, "wb") as fh:
        fh.write(content)
    os.replace(tmp, dest)
    return os.path.getsize(dest), response.url, content_date


def main():
    page_url = sys.argv[1].strip() if len(sys.argv) > 1 else DEFAULT_PAGE_URL
    os.makedirs(SAVE_DIR, exist_ok=True)

    with sync_playwright() as playwright:
        browser, context = browser_context(playwright)
        try:
            if page_url.lower().split("?", 1)[0].endswith(".pdf"):
                print("[!] Direct PDF input is for validation only. Use the product page/default command for reusable latest-download runs.")
                latest = {"title": DEFAULT_FUND_KEY, "url": page_url, "date": None}
                pdf_url = page_url
            else:
                try:
                    candidates = collect_candidates(context, page_url)
                    source = "page"
                except RuntimeError as exc:
                    print(f"[!] Page source unavailable: {exc}")
                    print("[!] Falling back to built-in TGCNMEB monthly factsheet records.")
                    candidates = known_candidates()
                    source = "built-in"
                if not candidates:
                    raise RuntimeError(f"No TGCNMEB monthly factsheet candidates found on page: {page_url}")
                latest = candidates[0]
                pdf_url = resolve_pdf(context, latest)
            tag = latest.get("date") or archive_tag(pdf_url, latest["title"])
            output_path = dated_output_path(COMPANY, DEFAULT_FUND_KEY, tag)

            size, final_url, content_date = download_pdf(context, pdf_url, output_path)
            if content_date and content_date != tag:
                corrected_path = dated_output_path(COMPANY, DEFAULT_FUND_KEY, content_date)
                os.replace(output_path, corrected_path)
                output_path = corrected_path
                tag = content_date
            print(f"[*] Page: {page_url}")
            if "source" in locals():
                print(f"[*] Source: {source}")
            print(f"[*] Latest monthly report: {latest['title']} ({tag})")
            print(f"[*] Download URL: {pdf_url}")
            print(f"[*] Final URL: {final_url}")
            print(f"[*] Date tag: {tag}")
            print(f"[*] Updated ({size / 1024:.1f} KB) -> {output_path}")
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    main()
