# -*- coding: utf-8 -*-
"""中国平安资产管理（香港）基金报告下载器。"""

from __future__ import annotations

import html
import os
import re
import sys
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common_paths import configure_session, company_data_dir, finalize_pdf_to_monthly_path

COMPANY = "pinganhk"
SAVE_DIR = company_data_dir(COMPANY)
TIMEOUT = 60
SESSION = requests.Session()
configure_session(SESSION)
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
MONTHLY_MARKERS = ("monthly fund factsheet", "monthly factsheet", "月報", "月报")
SEMIANNUAL_MARKERS = ("interim report", "semi-annual report", "semiannual report", "中期報告", "中期报告")
ANNUAL_MARKERS = ("annual report", "年度報告", "年度报告")
NOTICE_MARKERS = ("notice", "announcement", "dividend", "通告", "公告", "通知")


class AnchorParser(HTMLParser):
    """收集平安官网页面中的 PDF 下载链接。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[dict[str, str]] = []
        self.current: dict[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """开始收集锚点；参数 tag 为标签，attrs 为属性。"""
        if tag.lower() != "a":
            return
        values = {key.lower(): value or "" for key, value in attrs}
        self.current = {"href": values.get("href", ""), "title": values.get("title", ""), "text": ""}

    def handle_data(self, data: str) -> None:
        """累积锚点文本；参数 data 为文本。"""
        if self.current is not None:
            self.current["text"] += data

    def handle_endtag(self, tag: str) -> None:
        """结束锚点；参数 tag 为标签。"""
        if tag.lower() == "a" and self.current is not None:
            self.links.append(self.current)
            self.current = None


def clean(value: str) -> str:
    """压缩网页文本；参数 value 为原始文本。"""
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def _parse_date(text: str) -> datetime:
    """从报告标题/链接解析日期；参数 text 为候选文本。"""
    for pattern in (r"(20\d{2})[-_/](\d{1,2})(?:[-_/](\d{1,2}))?", r"(20\d{2})[年./_-](\d{1,2})[月./_-]?(\d{1,2})?"):
        match = re.search(pattern, text or "")
        if match:
            try:
                return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3) or 1))
            except ValueError:
                pass
    return datetime.min


def _is_notice(label: str) -> bool:
    """判断候选是否为公告；参数 label 为标题。"""
    lowered = clean(label).casefold()
    return any(marker.casefold() in lowered for marker in NOTICE_MARKERS)


def list_documents(page_url: str) -> list[dict[str, str]]:
    """读取平安基金产品页；参数 page_url 为稳定产品页。"""
    response = SESSION.get(page_url, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    parser = AnchorParser()
    parser.feed(response.text)
    documents: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in parser.links:
        url = urljoin(page_url, clean(item.get("href", "")))
        if not url or url in seen or not urlparse(url).path.lower().endswith(".pdf"):
            continue
        seen.add(url)
        label = clean(" ".join((item.get("title", ""), item.get("text", ""), os.path.basename(urlparse(url).path))))
        documents.append({"url": url, "label": label})
    if not documents:
        raise RuntimeError(f"平安官网产品页未解析到 PDF：{page_url}")
    return documents


def select_latest_document(documents: list[dict[str, str]], report_kind: str) -> dict[str, str]:
    """按报告类型选择最新正文；参数 documents 为候选，report_kind 为类型。"""
    markers = {"monthly": MONTHLY_MARKERS, "semiannual": SEMIANNUAL_MARKERS, "annual": ANNUAL_MARKERS}.get(report_kind)
    if markers is None:
        raise ValueError("report_kind must be monthly, semiannual, or annual")
    raw = [item for item in documents if any(marker.casefold() in item["label"].casefold() for marker in markers)]
    candidates = [item for item in raw if not (report_kind in {"semiannual", "annual"} and _is_notice(item["label"]))]
    if not candidates:
        if raw and report_kind in {"semiannual", "annual"} and all(_is_notice(item["label"]) for item in raw):
            raise RuntimeError(f"平安产品页仅找到{report_kind}公告，未找到报告正文")
        raise RuntimeError(f"平安产品页没有匹配的{report_kind}正文")
    return sorted(candidates, key=lambda item: (_parse_date(item["label"]), _parse_date(item["url"]), item["url"]), reverse=True)[0]


def download_pdf(url: str, destination: str) -> tuple[int, str]:
    """下载并校验 PDF；参数 url 为下载地址，destination 为临时路径。"""
    temporary = destination + ".part"
    with SESSION.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True) as response:
        response.raise_for_status()
        first = True
        with open(temporary, "wb") as handle:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                if first and not chunk.startswith(b"%PDF-"):
                    raise RuntimeError(f"平安官网下载内容不是 PDF：{response.url}")
                first = False
                handle.write(chunk)
        final_url = response.url
    os.replace(temporary, destination)
    return os.path.getsize(destination), final_url


def main(argv: list[str] | None = None) -> None:
    """命令行入口；参数依次为产品页、基金名、报告类型。"""
    args = list(sys.argv[1:] if argv is None else argv)
    page_url = args[0] if args and args[0].startswith(("http://", "https://")) else "https://asset.pingan.com.hk/en/PACSIF-PAMMF"
    fund_name = args[1] if len(args) > 1 else "平安货币基金"
    report_kind = (args[2] if len(args) > 2 else "monthly").lower()
    selected = select_latest_document(list_documents(page_url), report_kind)
    report_date = _parse_date(selected["label"])
    fallback_date = report_date.strftime("%Y-%m-%d") if report_date != datetime.min else "latest"
    os.makedirs(SAVE_DIR, exist_ok=True)
    temporary = os.path.join(SAVE_DIR, f".pinganhk_{os.getpid()}.pdf")
    print(f"[*] Fund: {fund_name}; kind: {report_kind}; page: {page_url}")
    print(f"[*] Selected: {selected['label']}; PDF: {selected['url']}")
    try:
        size, final_url = download_pdf(selected["url"], temporary)
        output_path, report_month, _ = finalize_pdf_to_monthly_path(temporary, COMPANY, re.sub(r"[^\w.-]+", "_", fund_name), fallback_date=fallback_date)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)
    print(f"[*] Final URL: {final_url}")
    print(f"[*] Updated ({size / 1024:.1f} KB) [{report_month or fallback_date}] -> {output_path}")


if __name__ == "__main__":
    main()
