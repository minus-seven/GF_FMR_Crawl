# -*- coding: utf-8 -*-
"""招商证券资产管理（香港）基金文件下载器。

CMSAM 的开放式基金页面使用 ``FundProduct?view=<代码>`` 稳定入口，页面内
同时有 Documents 和 Notices。每次运行重新读取 PDF 链接，按月报/半年报/年报
选择正文，公告/通知不能作为结构化输入。
"""

from __future__ import annotations

import html
import os
import re
import sys
from datetime import datetime
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlparse

import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common_paths import configure_session, company_data_dir, finalize_pdf_to_monthly_path


COMPANY = "cmsam"
BASE_URL = "https://www.cmschina.com.hk"
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

MONTHLY_MARKERS = ("monthly fund factsheet", "monthly factsheet", "factsheet", "月报", "月報")
SEMIANNUAL_MARKERS = (
    "semi-annual report",
    "semiannual report",
    "interim report",
    "半年报",
    "半年報",
    "中期报告",
    "中期報告",
)
ANNUAL_MARKERS = ("annual report", "年报", "年報", "年度报告", "年度報告")
ANNOUNCEMENT_MARKERS = ("announcement", "notice", "公告", "通知", "通告")


class AnchorParser(HTMLParser):
    """收集页面中的 PDF 下载锚点。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.links: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """开始解析锚点；参数 tag 为标签名，attrs 为标签属性。"""
        if tag.lower() != "a":
            return
        attr_map = {key.lower(): value or "" for key, value in attrs}
        self._current = {
            "href": attr_map.get("href", ""),
            "title": attr_map.get("title", ""),
            "download": attr_map.get("download", ""),
            "text": "",
        }

    def handle_data(self, data: str) -> None:
        """累积锚点文本；参数 data 为文本片段。"""
        if self._current is not None:
            self._current["text"] += data

    def handle_entityref(self, name: str) -> None:
        """保留 HTML 实体；参数 name 为实体名称。"""
        if self._current is not None:
            self._current["text"] += f"&{name};"

    def handle_charref(self, name: str) -> None:
        """保留数字实体；参数 name 为数字实体内容。"""
        if self._current is not None:
            self._current["text"] += f"&#{name};"

    def handle_endtag(self, tag: str) -> None:
        """结束解析锚点；参数 tag 为标签名。"""
        if tag.lower() == "a" and self._current is not None:
            self.links.append(self._current)
            self._current = None


def clean_text(value: str) -> str:
    """清理 HTML 文本；参数 value 为原始文本。"""
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def safe_filename(value: str) -> str:
    """生成安全文件名；参数 value 为基金名。"""
    text = re.sub(r"\s+", "_", unquote(value or "").strip())
    return re.sub(r'[\\/:*?"<>|]', "_", text)


def fetch_page(page_url: str) -> str:
    """读取 CMSAM 基金页；参数 page_url 为稳定基金入口。"""
    response = SESSION.get(page_url, headers={**HEADERS, "Referer": BASE_URL + "/"}, timeout=TIMEOUT)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or response.encoding
    return response.text


def nearby_document_context(page_html: str, raw_href: str) -> str:
    """提取下载锚点前的标题；参数 page_html 为页面源码，raw_href 为原始 href。"""
    if not raw_href:
        return ""
    href_pos = page_html.find(raw_href)
    if href_pos < 0:
        href_pos = page_html.find(html.escape(raw_href, quote=True))
    if href_pos < 0:
        return ""
    before = page_html[max(0, href_pos - 1200):href_pos]
    anchor_pos = before.lower().rfind("<a")
    if anchor_pos >= 0:
        before = before[:anchor_pos]
    close_candidates = [
        (before.lower().rfind(token), len(token))
        for token in ("</li>", "</div>", "</p>", "</tr>")
    ]
    last_close, close_length = max(close_candidates, default=(-1, 0))
    if last_close >= 0:
        before = before[last_close + close_length:]
    return clean_text(re.sub(r"<[^>]+>", " ", before))


def _is_pdf_url(url: str) -> bool:
    """判断链接是否为 PDF；参数 url 为候选 URL。"""
    return urlparse(url).path.lower().endswith(".pdf")


def list_documents(page_url: str) -> list[dict[str, str]]:
    """解析基金页 PDF；参数 page_url 为稳定入口。"""
    page_html = fetch_page(page_url)
    parser = AnchorParser()
    parser.feed(page_html)
    records: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in parser.links:
        href = clean_text(item.get("href", ""))
        if not href:
            continue
        url = urljoin(page_url, href)
        if not _is_pdf_url(url) or url in seen:
            continue
        seen.add(url)
        label = clean_text(
            " ".join(
                part
                for part in (
                    nearby_document_context(page_html, href),
                    item.get("title", ""),
                    item.get("download", ""),
                    item.get("text", ""),
                    os.path.basename(urlparse(url).path),
                )
                if part
            )
        )
        records.append({"label": label, "url": url})
    if not records:
        raise RuntimeError(f"CMSAM 基金页未解析到 PDF 下载链接：{page_url}")
    return records


def _parse_date(text: str) -> datetime:
    """解析报告日期；参数 text 为标题或 URL。"""
    value = clean_text(text)
    patterns = (
        r"(20\d{2})\s*[年./_-]\s*(\d{1,2})\s*[月./_-]\s*(\d{1,2})?",
        r"(20\d{2})[-_](\d{2})(?:[-_](\d{2}))?",
        r"(20\d{2})(\d{2})(\d{2})",
        r"\b(20\d{2})(0[1-9]|1[0-2])\b",
    )
    for pattern in patterns:
        match = re.search(pattern, value)
        if not match:
            continue
        year, month = int(match.group(1)), int(match.group(2))
        day = int(match.group(3) or 1) if match.lastindex and match.lastindex >= 3 else 1
        try:
            return datetime(year, month, day)
        except ValueError:
            continue
    match = re.search(
        r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+(20\d{2})\b",
        value,
        re.I,
    )
    if match:
        month = datetime.strptime(match.group(1)[:3], "%b").month
        return datetime(int(match.group(2)), month, 1)
    return datetime.min


def _is_announcement(label: str) -> bool:
    """判断候选是否为公告/通知；参数 label 为标题及文件名。"""
    text = clean_text(label).casefold()
    return any(marker.casefold() in text for marker in ANNOUNCEMENT_MARKERS)


def _kind_matches(label: str, report_kind: str) -> bool:
    """判断报告类型；参数 label 为标题，report_kind 为报告类型。"""
    text = clean_text(label).casefold()
    if report_kind == "monthly":
        return any(marker.casefold() in text for marker in MONTHLY_MARKERS)
    if report_kind == "semiannual":
        return any(marker.casefold() in text for marker in SEMIANNUAL_MARKERS) and not _is_announcement(label)
    if report_kind == "annual":
        return any(marker.casefold() in text for marker in ANNUAL_MARKERS) and not _is_announcement(label)
    raise ValueError("report_kind must be monthly, semiannual, or annual")


def select_latest_document(documents: list[dict[str, str]], report_kind: str) -> dict[str, str]:
    """选择最新正文报告；参数 documents 为候选，report_kind 为报告类型。"""
    markers = {
        "monthly": MONTHLY_MARKERS,
        "semiannual": SEMIANNUAL_MARKERS,
        "annual": ANNUAL_MARKERS,
    }.get(report_kind)
    if markers is None:
        raise ValueError("report_kind must be monthly, semiannual, or annual")
    raw_candidates = [
        item for item in documents
        if any(marker.casefold() in item["label"].casefold() for marker in markers)
    ]
    candidates = [item for item in raw_candidates if _kind_matches(item["label"], report_kind)]
    if not candidates:
        if report_kind in {"semiannual", "annual"} and raw_candidates and all(
            _is_announcement(item["label"]) for item in raw_candidates
        ):
            raise RuntimeError(f"CMSAM 产品页仅找到{report_kind}公告，未找到报告正文")
        raise RuntimeError(f"CMSAM 产品页没有匹配的{report_kind}正文")
    return sorted(
        candidates,
        key=lambda item: (_parse_date(item["label"]), _parse_date(item["url"]), item["url"]),
        reverse=True,
    )[0]


def download_pdf(url: str, destination: str) -> tuple[int, str]:
    """下载并校验 PDF；参数 url 为下载地址，destination 为临时路径。"""
    temporary = destination + ".part"
    with SESSION.get(url, headers={**HEADERS, "Referer": BASE_URL + "/"}, timeout=TIMEOUT, stream=True) as response:
        response.raise_for_status()
        first = True
        with open(temporary, "wb") as handle:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                if first and not chunk.startswith(b"%PDF-"):
                    raise RuntimeError(f"CMSAM 下载内容不是 PDF：{response.url}")
                first = False
                handle.write(chunk)
        final_url = response.url
    os.replace(temporary, destination)
    return os.path.getsize(destination), final_url


def main(argv: list[str] | None = None) -> None:
    """命令行入口；参数 argv 为页面、基金名和报告类型。"""
    args = list(sys.argv[1:] if argv is None else argv)
    page_url = args[0] if args and args[0].startswith(("http://", "https://")) else BASE_URL + "/en/AM/FundProduct?view=CMMF"
    fund_name = args[1] if len(args) > 1 else "招商货币市场基金"
    report_kind = (args[2] if len(args) > 2 else "monthly").strip().lower()
    selected = select_latest_document(list_documents(page_url), report_kind)
    report_date = _parse_date(selected["label"])
    fallback_date = report_date.strftime("%Y-%m-%d") if report_date != datetime.min else "latest"
    os.makedirs(SAVE_DIR, exist_ok=True)
    temporary = os.path.join(SAVE_DIR, f".cmsam_{os.getpid()}.pdf")
    print(f"[*] Page: {page_url}")
    print(f"[*] Report kind: {report_kind}; fund: {fund_name}")
    print(f"[*] Selected: {selected['label']}")
    print(f"[*] PDF: {selected['url']}")
    try:
        size, final_url = download_pdf(selected["url"], temporary)
        output_path, report_month, _ = finalize_pdf_to_monthly_path(
            temporary,
            COMPANY,
            safe_filename(fund_name),
            fallback_date=fallback_date,
        )
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)
    print(f"[*] Final URL: {final_url}")
    print(f"[*] Updated ({size / 1024:.1f} KB) [{report_month or fallback_date}] -> {output_path}")


if __name__ == "__main__":
    main()
