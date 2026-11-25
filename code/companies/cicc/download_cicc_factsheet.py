# -*- coding: utf-8 -*-
"""中金香港资产管理基金文件下载器。

中金官网的产品文件页是稳定入口，月报与半年报 PDF 文件名会随发布日变化。
本脚本每次运行重新读取产品文件页，按报告类型、份额类别和报告期选择最新 PDF。

用法：
  python download_cicc_factsheet.py
  python download_cicc_factsheet.py "https://cicchkam.com/sc/product/documents/4?pro=1" monthly listed "中金美元货币市场ETF"
  python download_cicc_factsheet.py "https://cicchkam.com/sc/product/documents/4?pro=1" semiannual all "中金美元货币市场ETF"

monthly 的类别参数支持 listed（上市类别）、unlisted（非上市类别）和 all；
semiannual/annual 不按份额类别过滤，因为中金采用基金系列报告。最后一个可选参数
是目标基金名，用于同页存在多个子基金报告时优先选对报告。
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


COMPANY = "cicc"
BASE_URL = "https://cicchkam.com"
DEFAULT_PAGE_URL = BASE_URL + "/sc/product/documents/4?pro=1"
DEFAULT_REPORT_KIND = "monthly"
DEFAULT_SHARE_CLASS = "listed"
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

MONTHLY_MARKERS = (
    "月报",
    "月報",
    "monthly",
    "fundfacts",
    "fund facts",
)
SEMIANNUAL_MARKERS = (
    "半年报",
    "半年報",
    "中期报告",
    "中期報告",
    "interim report",
    "semi-annual",
    "semiannual",
)
ANNUAL_MARKERS = ("年报", "年報", "年度报告", "年度報告", "annual report", "annual")
# 中金产品页会把“中期报告公告”和真正的中期报告同时挂出。公告通常
# 只有一页，不能作为结构化输入；这些词只用于排除公告，不改变月报匹配。
ANNOUNCEMENT_MARKERS = (
    "公告",
    "通知",
    "通告",
    "announcement",
    "notice",
)
REPORT_BODY_MARKERS = {
    "semiannual": (
        "未经审核中期报告",
        "未經審核中期報告",
        "unaudited interim report",
        "interim financial report",
        "fund series final",
    ),
    "annual": (
        "未经审核年度报告",
        "未經審核年度報告",
        "年度报告 (基金系列)",
        "年度報告 (基金系列)",
        "annual report",
        "fund series final",
    ),
}


class AnchorParser(HTMLParser):
    """收集产品文件页上的下载锚点。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.links: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """处理开始标签；参数 tag/attrs 为 HTML 标签及属性。"""
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
        """保留 HTML 实体；参数 name 为实体名。"""
        if self._current is not None:
            self._current["text"] += f"&{name};"

    def handle_charref(self, name: str) -> None:
        """保留数字实体；参数 name 为数字实体内容。"""
        if self._current is not None:
            self._current["text"] += f"&#{name};"

    def handle_endtag(self, tag: str) -> None:
        """结束锚点并保存；参数 tag 为 HTML 标签名。"""
        if tag.lower() == "a" and self._current is not None:
            self.links.append(self._current)
            self._current = None


def safe_filename(value: str) -> str:
    """生成安全文件名；参数 value 为原始名称。"""
    text = re.sub(r"\s+", "_", unquote(value or "").strip())
    return re.sub(r'[\\/:*?"<>|]', "_", text)


def clean_text(value: str) -> str:
    """清理 HTML 文本；参数 value 为原始文本。"""
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def strip_tags(value: str) -> str:
    """去除 HTML 标签；参数 value 为 HTML 片段。"""
    return clean_text(re.sub(r"<[^>]+>", " ", value or ""))


def nearby_document_context(page_html: str, raw_href: str) -> str:
    """提取下载锚点外的标题文本；参数 page_html/raw_href 为页面和原始链接。"""
    if not raw_href:
        return ""
    href_pos = page_html.find(raw_href)
    if href_pos < 0:
        href_pos = page_html.find(html.escape(raw_href, quote=True))
    if href_pos < 0:
        return ""
    before = page_html[max(0, href_pos - 1000) : href_pos]
    anchor_pos = before.lower().rfind("<a")
    if anchor_pos >= 0:
        before = before[:anchor_pos]
    close_candidates = [
        (before.lower().rfind(token), len(token))
        for token in ("</li>", "</div>", "</p>", "</tr>")
    ]
    last_close, close_length = max(close_candidates, default=(-1, 0))
    if last_close >= 0:
        before = before[last_close + close_length :]
    return strip_tags(before)


def fetch_page(page_url: str) -> str:
    """读取产品文件页；参数 page_url 为稳定官网入口。"""
    response = SESSION.get(
        page_url,
        headers={**HEADERS, "Referer": BASE_URL + "/"},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    response.encoding = response.apparent_encoding or response.encoding
    return response.text


def _is_pdf_url(url: str) -> bool:
    """判断链接是否指向 PDF；参数 url 为候选链接。"""
    return urlparse(url).path.lower().endswith(".pdf")


def list_documents(page_url: str) -> list[dict[str, str]]:
    """解析产品页文档列表；参数 page_url 为稳定官网入口。"""
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
        # 官网常把文档标题放在锚点外、锚点内只写“下載”；保留链接前的
        # 短上下文，才能识别“截至 2026-06-30 中期报告”这类半年报。
        context = nearby_document_context(page_html, item.get("href", ""))
        label = clean_text(
            " ".join(
                part for part in (
                    context,
                    item.get("download", ""),
                    item.get("title", ""),
                    item.get("text", ""),
                    os.path.basename(urlparse(url).path),
                )
                if part
            )
        )
        records.append({"label": label, "url": url})
    if not records:
        raise RuntimeError(f"中金产品文件页未解析到 PDF 下载链接：{page_url}")
    return records


def _parse_date(text: str) -> datetime:
    """解析报告截止/发布日期；参数 text 为文档标题或 URL。"""
    value = clean_text(text)
    patterns = (
        r"(20\d{2})\s*[年./_-]\s*(\d{1,2})\s*[月./_-]\s*(\d{1,2})?\s*日?",
        r"(20\d{2})[-_](\d{2})(?:[-_](\d{2}))?",
        r"(20\d{2})(\d{2})(\d{2})",
        r"\b(20\d{2})(0[1-9]|1[0-2])\b",
    )
    for pattern in patterns:
        match = re.search(pattern, value)
        if match:
            year, month = int(match.group(1)), int(match.group(2))
            day_text = match.group(3) if (match.lastindex or 0) >= 3 else None
            day = int(day_text or 1)
            try:
                return datetime(year, month, day)
            except ValueError:
                continue
    month_names = {
        "january": 1, "february": 2, "march": 3, "april": 4,
        "may": 5, "june": 6, "july": 7, "august": 8,
        "september": 9, "october": 10, "november": 11, "december": 12,
    }
    match = re.search(
        r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+(20\d{2})\b",
        value,
        flags=re.I,
    )
    if match:
        return datetime(int(match.group(2)), month_names[match.group(1).lower()], 1)
    return datetime.min


def _kind_matches(label: str, report_kind: str) -> bool:
    """判断文档类型；参数 label 为文档标题，report_kind 为目标类型。"""
    text = label.lower()
    if report_kind == "monthly":
        return any(marker.lower() in text for marker in MONTHLY_MARKERS)
    if report_kind == "semiannual":
        return (
            any(marker.lower() in text for marker in SEMIANNUAL_MARKERS)
            and not _is_announcement(label)
        )
    if report_kind == "annual":
        return (
            any(marker.lower() in text for marker in ANNUAL_MARKERS)
            and not any(marker.lower() in text for marker in SEMIANNUAL_MARKERS)
            and not _is_announcement(label)
        )
    raise ValueError("report_kind must be monthly, semiannual, or annual")


def _is_announcement(label: str) -> bool:
    """判断文档是否为公告/通知；参数 label 为文档标题及文件名组合。"""
    text = clean_text(label).casefold()
    return any(marker.casefold() in text for marker in ANNOUNCEMENT_MARKERS)


def _report_body_score(label: str, report_kind: str) -> int:
    """计算正文优先级；参数 label 为文档标题，report_kind 为目标报告类型。"""
    if _is_announcement(label):
        return -100
    markers = REPORT_BODY_MARKERS.get(report_kind, ())
    return sum(marker.casefold() in clean_text(label).casefold() for marker in markers)


def _share_class_matches(label: str, share_class: str) -> bool:
    """判断上市/非上市类别；参数 label 为标题，share_class 为类别。"""
    text = label.lower()
    if share_class in {"", "all", "any"}:
        return True
    listed = "上市类别" in text or "listed class" in text
    unlisted = "非上市类别" in text or "unlisted class" in text
    if share_class == "listed":
        return listed and not unlisted
    if share_class == "unlisted":
        return unlisted
    raise ValueError("share_class must be listed, unlisted, or all")


def select_latest_document(
    documents: list[dict[str, str]],
    report_kind: str,
    share_class: str = "all",
    target_fund: str = "",
) -> dict[str, str]:
    """选择最新文档；参数 documents/report_kind/share_class/target_fund 描述候选、类别和目标基金。"""
    raw_candidates = [
        item
        for item in documents
        if any(
            marker.lower() in item["label"].lower()
            for marker in (
                MONTHLY_MARKERS
                if report_kind == "monthly"
                else SEMIANNUAL_MARKERS
                if report_kind == "semiannual"
                else ANNUAL_MARKERS
            )
        )
    ]
    candidates = [
        item
        for item in raw_candidates
        if _kind_matches(item["label"], report_kind)
        and _share_class_matches(item["label"], share_class)
    ]
    if not candidates:
        if report_kind in {"semiannual", "annual"} and raw_candidates and all(
            _is_announcement(item["label"]) for item in raw_candidates
        ):
            raise RuntimeError(
                f"中金产品页仅找到{report_kind}公告，未找到报告正文；已拒绝把公告作为结构化输入"
            )
        raise RuntimeError(
            f"中金产品页没有匹配的{report_kind}文档（份额类别：{share_class}）"
        )
    # 同一产品页可能同时挂有基金系列报告和某一子基金报告。若调用方给出
    # 目标基金名，优先选择标题中明确包含该名称的子基金报告；没有命中时
    # 再回退到系列报告，保证货币 ETF 等只提供系列报告的产品仍可下载。
    target = clean_text(target_fund).lower()
    if target:
        target_tokens = [
            token.lower()
            for token in re.split(r"\s+|[-_/（）()]+", target)
            if len(token) >= 2
        ]
        targeted = [
            item
            for item in candidates
            if target in item["label"].lower()
            or (target_tokens and sum(token in item["label"].lower() for token in target_tokens) >= 2)
        ]
        if targeted:
            candidates = targeted
    candidates.sort(
        key=lambda item: (
            _report_body_score(item["label"], report_kind),
            _parse_date(item["label"]),
            _parse_date(item["url"]),
            item["url"],
        ),
        reverse=True,
    )
    return candidates[0]


def download_pdf(url: str, destination: str) -> tuple[int, str]:
    """下载并校验 PDF；参数 url/destination 为源地址和临时文件路径。"""
    temporary = destination + ".part"
    with SESSION.get(
        url,
        headers={**HEADERS, "Referer": DEFAULT_PAGE_URL},
        timeout=TIMEOUT,
        stream=True,
        allow_redirects=True,
    ) as response:
        response.raise_for_status()
        first = True
        with open(temporary, "wb") as handle:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                if first and not chunk.startswith(b"%PDF-"):
                    raise RuntimeError(f"中金下载内容不是 PDF：{response.url}")
                first = False
                handle.write(chunk)
        final_url = response.url
    os.replace(temporary, destination)
    return os.path.getsize(destination), final_url


def resolve_args(args: list[str]) -> tuple[str, str, str, str]:
    """解析命令行参数；参数 args 为页面、报告类型、份额类别和目标基金。"""
    page_url = DEFAULT_PAGE_URL
    report_kind = DEFAULT_REPORT_KIND
    share_class = DEFAULT_SHARE_CLASS
    target_fund = ""
    if args:
        if args[0].startswith(("http://", "https://")):
            page_url = args[0]
            if len(args) > 1:
                report_kind = args[1].strip().lower()
            if len(args) > 2:
                share_class = args[2].strip().lower()
            if len(args) > 3:
                target_fund = args[3].strip()
        else:
            report_kind = args[0].strip().lower()
            if len(args) > 1:
                share_class = args[1].strip().lower()
            if len(args) > 2:
                target_fund = args[2].strip()
    if report_kind in {"semiannual", "annual"} and share_class == DEFAULT_SHARE_CLASS:
        share_class = "all"
    return page_url, report_kind, share_class, target_fund


def main(argv: list[str] | None = None) -> None:
    """命令行入口；参数 argv 为可选的命令行参数列表。"""
    page_url, report_kind, share_class, target_fund = resolve_args(
        list(sys.argv[1:] if argv is None else argv)
    )
    documents = list_documents(page_url)
    selected = select_latest_document(documents, report_kind, share_class, target_fund)
    report_date = _parse_date(selected["label"])
    fallback_date = report_date.strftime("%Y-%m-%d") if report_date != datetime.min else "latest"
    label = safe_filename(selected["label"][:180])
    os.makedirs(SAVE_DIR, exist_ok=True)
    temporary = os.path.join(SAVE_DIR, f".cicc_{os.getpid()}.pdf")
    print(f"[*] Page: {page_url}")
    print(f"[*] Report kind: {report_kind}; share class: {share_class}; target: {target_fund or 'series fallback'}")
    print(f"[*] Selected: {selected['label']}")
    print(f"[*] PDF: {selected['url']}")
    try:
        size, final_url = download_pdf(selected["url"], temporary)
        output_path, report_month, moved = finalize_pdf_to_monthly_path(
            temporary,
            COMPANY,
            label,
            fallback_date=fallback_date,
        )
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)
    print(f"[*] Final URL: {final_url}")
    if moved:
        print(f"[*] Updated ({size / 1024:.1f} KB) [{report_month or fallback_date}] -> {output_path}")
    else:
        print(f"[*] Existing [{report_month or fallback_date}], skipped -> {output_path}")


if __name__ == "__main__":
    main()
