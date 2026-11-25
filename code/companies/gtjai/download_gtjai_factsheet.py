# -*- coding: utf-8 -*-
"""国泰君安资产管理（亚洲）基金报告下载器。"""

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

COMPANY = "gtjai"
SAVE_DIR = company_data_dir(COMPANY)
REPORT_PAGE = "https://www.gtjai.com/tc/fund_gugcfa_report"
IFP_API = "https://ifp.hkex.com.hk/fund-repository/ifp/api/v1/fund/getFundFileList"
IFP_PDF_BASE = "https://ifp.hkex.com.hk/fund-repository/ifp/api/v1/fund/"
TIMEOUT = 60
SESSION = requests.Session()
configure_session(SESSION)
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
MONTHLY_MARKERS = ("monthly report", "月報", "月报")
SEMIANNUAL_MARKERS = ("interim report", "semi-annual report", "semiannual report", "中期報告", "中期报告")
ANNUAL_MARKERS = ("annual report", "年度報告", "年度报告")
NOTICE_MARKERS = ("notice", "announcement", "通告", "公告", "通知")


class AnchorParser(HTMLParser):
    """收集 GTJAI 报告页中的 PDF 锚点。"""

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
        """累积文本；参数 data 为片段。"""
        if self.current is not None:
            self.current["text"] += data

    def handle_endtag(self, tag: str) -> None:
        """结束锚点；参数 tag 为标签。"""
        if tag.lower() == "a" and self.current is not None:
            self.links.append(self.current)
            self.current = None


def clean(value: str) -> str:
    """压缩 HTML 文本；参数 value 为原始文本。"""
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def _parse_date(text: str) -> datetime:
    """从标题/URL解析日期；参数 text 为候选文本。"""
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


def _looks_like_month_label(label: str) -> bool:
    """判断链接标题是否呈现月份；GTJAI 页面常用“8月”而非“月报”。"""
    lowered = clean(label).casefold()
    return any(marker.casefold() in lowered for marker in MONTHLY_MARKERS) or bool(re.search(r"(?:20\d{2}\s*)?\d{1,2}\s*月", lowered))


def list_monthly_documents(page_url: str, fund_name: str, english_name: str) -> list[dict[str, str]]:
    """读取指定基金的 GTJAI 月报目录；参数为页面地址及中英文基金名。"""
    response = SESSION.get(page_url, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    parser = AnchorParser()
    parser.feed(response.text)
    documents: list[dict[str, str]] = []
    for item in parser.links:
        url = urljoin(page_url, clean(item.get("href", "")))
        if not urlparse(url).path.lower().endswith(".pdf"):
            continue
        label = clean(" ".join((item.get("title", ""), item.get("text", ""), os.path.basename(urlparse(url).path))))
        name_match = any(name and name.casefold() in label.casefold() for name in (fund_name, english_name))
        # 每只 GTJAI 基金有独立的 *_report 页面，链接标题只有“8月/7月”，不重复基金名。
        dedicated_fund_page = "/fund_" in urlparse(page_url).path.lower()
        if not name_match and not dedicated_fund_page:
            continue
        if not _looks_like_month_label(label):
            continue
        documents.append({"url": url, "label": label})
    if not documents:
        raise RuntimeError(f"GTJAI 官方月报目录没有匹配的月报：{fund_name}")
    return sorted(documents, key=lambda item: (_parse_date(item["label"]), item["url"]), reverse=True)


def list_ifp_reports(fund_id: str) -> list[dict]:
    """读取 HKEX 财务报告；参数 fund_id 为基金中央编号。"""
    response = SESSION.get(IFP_API, params={"fundId": fund_id, "docTypeParent": "FINANCIAL_REPORT", "page": 1, "size": 100, "lang": "zh_HK"}, headers={**HEADERS, "x-requested-lang": "zh_HK"}, timeout=TIMEOUT)
    response.raise_for_status()
    records = response.json().get("data") or []
    return sorted(records, key=lambda item: str(item.get("docDate") or ""), reverse=True)


def select_ifp_report(records: list[dict], report_kind: str) -> dict[str, str]:
    """选择正文财报；参数 records 为 IFP 记录，report_kind 为类型。"""
    target = {"semiannual": "SEMIANNUAL", "annual": "ANNUAL"}.get(report_kind)
    if not target:
        raise ValueError("IFP 仅支持 semiannual 或 annual")
    for record in records:
        for item in record.get("fundFileVOList") or []:
            if str(item.get("docType") or "").upper() == target:
                return {"url": IFP_PDF_BASE + str(item.get("docUrl") or ""), "date": str(record.get("docDate") or ""), "label": str(item.get("fileName") or item.get("displayDocName") or "")}
    raise RuntimeError(f"HKEX IFP 没有匹配的{report_kind}正文")


def download_pdf(url: str, destination: str) -> tuple[int, str]:
    """下载并校验 PDF；参数 url 为地址，destination 为路径。"""
    temporary = destination + ".part"
    with SESSION.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True) as response:
        response.raise_for_status()
        first = True
        with open(temporary, "wb") as handle:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                if first and not chunk.startswith(b"%PDF-"):
                    raise RuntimeError(f"GTJAI 下载内容不是 PDF：{response.url}")
                first = False
                handle.write(chunk)
        final_url = response.url
    os.replace(temporary, destination)
    return os.path.getsize(destination), final_url


def main(argv: list[str] | None = None) -> None:
    """命令行入口；参数依次为产品入口、基金名、报告类型、基金代码、英文名。"""
    args = list(sys.argv[1:] if argv is None else argv)
    page_url = args[0] if args and args[0].startswith(("http://", "https://")) else REPORT_PAGE
    fund_name = args[1] if len(args) > 1 else ""
    report_kind = (args[2] if len(args) > 2 else "monthly").lower()
    fund_id = (args[3] if len(args) > 3 else "").strip().upper()
    english_name = args[4].strip() if len(args) > 4 else ""
    if report_kind == "monthly":
        selected = list_monthly_documents(page_url, fund_name, english_name)[0]
        fallback_date = _parse_date(selected["label"]).strftime("%Y-%m-%d")
    else:
        if not fund_id:
            match = re.search(r"/fund/([A-Z0-9]+)", page_url)
            fund_id = match.group(1) if match else ""
        if not fund_id:
            raise RuntimeError("半年报/年报任务必须提供 HKEX 基金代码")
        selected = select_ifp_report(list_ifp_reports(fund_id), report_kind)
        fallback_date = selected["date"] or "latest"
    os.makedirs(SAVE_DIR, exist_ok=True)
    temporary = os.path.join(SAVE_DIR, f".gtjai_{os.getpid()}.pdf")
    print(f"[*] Fund: {fund_name} ({fund_id}); kind: {report_kind}; PDF: {selected['url']}")
    try:
        size, final_url = download_pdf(selected["url"], temporary)
        output_path, report_month, _ = finalize_pdf_to_monthly_path(temporary, COMPANY, re.sub(r"[^\w.-]+", "_", fund_name or fund_id), fallback_date=fallback_date)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)
    print(f"[*] Final URL: {final_url}")
    print(f"[*] Updated ({size / 1024:.1f} KB) [{report_month or fallback_date}] -> {output_path}")


if __name__ == "__main__":
    main()
