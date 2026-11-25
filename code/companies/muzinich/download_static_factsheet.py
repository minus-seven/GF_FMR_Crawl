#!/usr/bin/env python3
"""Download a PDF or locate the current PDF link on an official product page."""
from __future__ import annotations
import os, re, sys, tempfile
from datetime import date
from urllib.parse import urljoin
import requests
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common_paths import extract_report_month_from_pdf, finalize_pdf_to_monthly_path
def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2: raise SystemExit("usage: download_static_factsheet.py <url> <fund> [company]")
    url, fund, company = args[0], args[1], (args[2] if len(args) > 2 else "static")
    s = requests.Session(); s.trust_env = False
    r = s.get(url, timeout=120, headers={"User-Agent":"Mozilla/5.0"})
    r.raise_for_status()
    pdf_url = r.url
    if not r.content.startswith(b"%PDF-"):
        # Prefer a PDF anchor whose visible URL/text contains the requested
        # fund name.  This is needed for listing pages such as Momentum MGF.
        compact = re.sub(r"[^a-z0-9]+", "", fund.lower())
        anchors = re.findall(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', r.text, re.I | re.S)
        scored = []
        for href, label in anchors:
            hay = re.sub(r"[^a-z0-9]+", "", (href + " " + re.sub("<[^>]+>", " ", label)).lower())
            # A listing page may expose a PDF directly or a report endpoint
            # whose URL does not end in .pdf until after a redirect.
            if not ("pdf" in href.lower() or any(token in hay for token in ("factsheet", "monthly", "月报", "fundreport", "report"))):
                continue
            score = sum(token in hay for token in (compact, "harmonyportfolios"))
            scored.append((score, href))
        scored.sort(reverse=True)
        links = [item[1] for item in scored] or re.findall(r'(?:href|src)=["\']([^"\']+?\.pdf(?:\?[^"\']*)?)["\']', r.text, re.I)
        if not links: raise RuntimeError(f"官方页面未找到 PDF：{url}")
        pdf_url = urljoin(r.url, links[0])
        r = s.get(pdf_url, timeout=120, headers={"User-Agent":"Mozilla/5.0"}); r.raise_for_status()
    if not r.content.startswith(b"%PDF-"): raise RuntimeError(f"下载内容不是 PDF：{pdf_url}")
    fd, tmp = tempfile.mkstemp(prefix="factsheet_", suffix=".pdf"); os.close(fd)
    try:
        open(tmp, "wb").write(r.content)
        fallback = date.today().strftime("%Y-%m-%d")
        try: period = extract_report_month_from_pdf(tmp, fallback_date=fallback)
        except Exception: period = fallback
        target, _, created = finalize_pdf_to_monthly_path(tmp, company, re.sub(r"[^\w.-]+", "_", fund), fallback_date=period)
        print(f"[*] PDF: {pdf_url}\n[*] {'完成' if created else '已存在'} -> {target}")
    finally:
        if os.path.exists(tmp): os.remove(tmp)
    return 0
if __name__ == "__main__": raise SystemExit(main())
