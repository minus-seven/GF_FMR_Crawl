# -*- coding: utf-8 -*-
"""汇丰基金 —— 通过港交所 IFP 搜索并切分伞级财报章节。

数据源：港交所综合基金平台（IFP）
  搜索 API：/fund-repository/ifp/api/v1/fund/getFundList
  文件 API：/fund-repository/ifp/api/v1/fund/getFundFileList

IFP 可以覆盖汇丰香港基金中心目录未上架、但仍在证监会认可基金库中的产品。
本脚本先用官方搜索接口把基金名解析为 fundId，再从新到旧检查财务报告；
对没有逐基金目录或目录页码存在偏移的报告，按“基金名在连续页面中出现”的
区间定位章节，找出最新一份真正包含该基金的财报后切分落盘。

用法：
  python download_hsbc_it_factsheet.py "HSBC Index Tracker Investment Funds - American Index Fund"
  python download_hsbc_it_factsheet.py "HSBC Global Investment Funds - Asia ESG Bond"
  python download_hsbc_it_factsheet.py "HSBC Managed Stable Growth Fund"
"""

import hashlib
import os
import re
import sys
import tempfile

import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from common_paths import PROJECT_ROOT, company_data_dir, finalize_pdf_to_monthly_path  # noqa: E402
from common import annual_report_split as split_lib  # noqa: E402
from common.browser_fetch import launch_browser, new_context  # noqa: E402
from common.fund_link_backfill import normalize_name as _cn_norm  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402


COMPANY = "hsbc"
DEFAULT_FUND_ID = "AGE753"
SEARCH_API = "https://ifp.hkex.com.hk/fund-repository/ifp/api/v1/fund/getFundList"
API_URL = "https://ifp.hkex.com.hk/fund-repository/ifp/api/v1/fund/getFundFileList"
PDF_BASE = "https://ifp.hkex.com.hk/fund-repository/ifp/api/v1/fund/"
CACHE_DIR = os.path.join(PROJECT_ROOT, "data", "_cache", "hsbc_index_tracker")
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_STATE: dict = {}


def _api_headers(
    fund_id: str = DEFAULT_FUND_ID, lang: str = "zh_HK"
) -> dict[str, str]:
    """构造港交所 IFP API 请求头。

    参数 fund_id：当前基金中央编号；lang：IFP 语言代码；返回对应请求头。
    """
    return {
        "User-Agent": UA,
        "Referer": f"https://ifp.hkex.com.hk/fund-repository/zh-HK/fund/{fund_id}",
        "Accept": "application/json, text/plain, */*",
        "x-requested-lang": lang,
    }


def _browser_cookies(fund_id: str = DEFAULT_FUND_ID) -> dict[str, str]:
    """打开 IFP 基金页，返回其会话 cookie。

    参数 fund_id：基金中央编号；返回 cookie 字典，仅在 API 返回 5xx 时使用。
    """
    cache_key = f"cookies:{fund_id}"
    if cache_key in _STATE:
        return _STATE[cache_key]
    with sync_playwright() as playwright:
        browser = launch_browser(playwright, headless=True)
        context = new_context(browser, locale="zh-HK")
        page = context.new_page()
        page.goto(
            f"https://ifp.hkex.com.hk/fund-repository/zh-HK/fund/{fund_id}",
            wait_until="domcontentloaded",
            timeout=90000,
        )
        page.wait_for_timeout(6000)
        _STATE[cache_key] = {cookie["name"]: cookie["value"] for cookie in context.cookies()}
        browser.close()
    return _STATE[cache_key]


def _get_json(
    url: str,
    params: dict,
    fund_id: str = DEFAULT_FUND_ID,
    lang: str = "zh_HK",
) -> dict:
    """请求 IFP JSON；普通请求失败时带浏览器 cookie 重试一次。

    参数 url：API 地址；params：查询参数；fund_id：用于生成 Referer/会话的基金编号；
    lang：IFP 语言代码。
    """
    response = requests.get(
        url, params=params, headers=_api_headers(fund_id, lang), timeout=60
    )
    if response.status_code >= 500:
        response = requests.get(
            url,
            params=params,
            headers=_api_headers(fund_id, lang),
            cookies=_browser_cookies(fund_id),
            timeout=60,
        )
    response.raise_for_status()
    return response.json()


def search_funds(fund_name: str) -> list[dict]:
    """按基金全名或去掉伞名后的短名搜索港交所基金目录。

    参数 fund_name：页表英文基金名；返回 IFP 搜索命中的原始记录。
    """
    raw = str(fund_name or "").strip()
    short = re.sub(
        r"^HSBC\s+(?:Global\s+(?:Investment\s+Funds|Funds\s+ICAV)|"
        r"Index\s+Tracker\s+Investment\s+Funds|Managed\s+Funds)\s*[-–—]?\s*",
        "",
        raw,
        flags=re.I,
    ).strip()
    records: list[dict] = []
    seen: set[str] = set()
    for query in dict.fromkeys(item for item in (raw, short) if item):
        data = _get_json(
            SEARCH_API,
            {"page": 1, "size": 100, "fund": query, "lang": "en_HK"},
            lang="en_HK",
        )
        for item in data.get("data") or []:
            fund_id = str(item.get("fundId") or "").strip()
            if fund_id and fund_id not in seen:
                seen.add(fund_id)
                records.append(item)
    return records


def find_fund_id(fund_name: str, chinese_name: str = "") -> str:
    """按中英文名从 IFP 搜索结果中选取唯一汇丰基金编号。

    参数 fund_name：页表英文基金名；chinese_name：页表中文基金名；返回 fundId。
    """
    records = search_funds(fund_name)
    target_cn = _cn_norm(chinese_name).replace("匯", "滙").replace("汇", "滙")
    target_en = _cn_norm(fund_name).replace("匯", "滙").replace("汇", "滙")
    scored: list[tuple[int, str]] = []
    for item in records:
        if "HSBC" not in str(item.get("issuer") or "").upper():
            continue
        score = 0
        for candidate in (
            str(item.get("fundName") or ""),
            str(item.get("fundAndUmbrellaName") or ""),
        ):
            candidate_key = _cn_norm(candidate).replace("匯", "滙").replace("汇", "滙")
            if target_cn and candidate_key:
                if target_cn == candidate_key:
                    score = max(score, 1000)
                elif target_cn in candidate_key or candidate_key in target_cn:
                    score = max(score, 800 + min(len(target_cn), len(candidate_key)))
            if target_en and candidate_key:
                if target_en == candidate_key:
                    score = max(score, 950)
                elif target_en in candidate_key or candidate_key in target_en:
                    score = max(score, 750 + min(len(target_en), len(candidate_key)))
        if score and item.get("fundId"):
            scored.append((score, str(item["fundId"])))
    if not scored:
        raise RuntimeError(f"港交所 IFP 未找到基金：{fund_name}")
    scored.sort(key=lambda item: item[0], reverse=True)
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        raise RuntimeError(f"港交所 IFP 基金匹配不唯一：{fund_name}")
    return scored[0][1]


def list_reports(fund_id: str = DEFAULT_FUND_ID) -> list[dict]:
    """返回指定基金的全部财务报告记录（按 docDate 倒序）。

    参数 fund_id：基金中央编号；返回按报告期截止日倒序的报告记录。
    """
    data = _get_json(
        API_URL,
        {
            "fundId": fund_id,
            "docTypeParent": "FINANCIAL_REPORT",
            "page": 1,
            "size": 50,
            "lang": "zh_HK",
        },
        fund_id=fund_id,
    )
    records = data.get("data") or []
    records.sort(key=lambda item: str(item.get("docDate") or ""), reverse=True)
    return records


def _report_file(record: dict) -> tuple[str, str, str]:
    """从一条 IFP 财务报告记录中取文件信息。

    参数 record：IFP 报告记录；返回 (PDF URL, 报告期日期, 原始文件名)。
    """
    files = record.get("fundFileVOList") or []
    if not files:
        raise RuntimeError("财务报告记录没有文件条目")
    file_info = files[0]
    return (
        PDF_BASE + str(file_info["docUrl"]),
        str(record.get("docDate") or ""),
        str(file_info.get("fileName") or ""),
    )


def _report_cache_path(url: str, filename: str) -> str:
    """为不可变 IFP 文件 URL 生成稳定且不冲突的缓存文件名。

    参数 url：PDF 直链；filename：报告原名；返回用于缓存的文件名。
    """
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
    safe_name = re.sub(r"[^\w.\-]+", "_", filename or "hsbc_ifp_report.pdf")
    return f"{digest}_{safe_name}"


def _find_section_in_report(
    path: str, fund_name: str, chinese_name: str = ""
) -> tuple[int, int, str] | None:
    """在财报正文中通过连续页定位基金章节。

    参数 path：本地财报 PDF；fund_name：英文基金名；chinese_name：中文基金名；
    返回物理页 (起始索引, 结束索引, 命中名称)。
    """
    short = re.sub(
        r"^HSBC\s+(?:Global\s+(?:Investment\s+Funds|Funds\s+ICAV)|"
        r"Index\s+Tracker\s+Investment\s+Funds|Managed\s+Funds)\s*[-–—]?\s*",
        "",
        fund_name,
        flags=re.I,
    ).strip()
    aliases = [value for value in (short, chinese_name) if value and value != fund_name]
    return split_lib.find_contiguous_section(
        path,
        fund_name,
        aliases=aliases,
        heading_keywords=(
            "Schedule of Investments",
            "Statement of Investments",
            "Statement of Financial Position",
            "Portfolio Statement",
            "Statement of Comprehensive Income",
        ),
    )


def resolve_report_section(fund_name: str, chinese_name: str = "") -> dict | None:
    """返回最新一份实际包含目标基金章节的 IFP 报告及物理页范围。

    参数 fund_name：页表英文基金名；chinese_name：页表中文基金名；
    返回含 fundId/URL/日期/本地路径/起止页的字典，未找到时返回 None。
    """
    cache_key = f"section:{fund_name}:{chinese_name}"
    if cache_key in _STATE:
        return _STATE[cache_key]
    fund_id = find_fund_id(fund_name, chinese_name)
    for record in list_reports(fund_id):
        url, report_date, report_name = _report_file(record)
        filename = _report_cache_path(url, report_name)
        path = split_lib.download_report(
            url,
            CACHE_DIR,
            filename=filename,
            headers=_api_headers(fund_id),
        )
        section = _find_section_in_report(path, fund_name, chinese_name)
        if section:
            start, end, matched = section
            result = {
                "fund_id": fund_id,
                "url": url,
                "date": report_date,
                "report_name": report_name,
                "path": path,
                "start": start,
                "end": end,
                "matched": matched,
            }
            _STATE[cache_key] = result
            return result
    _STATE[cache_key] = None
    return None


def resolve_section(fund_name: str) -> tuple[int, int, str] | None:
    """兼容旧接口：返回报告物理页 (起始索引, 结束索引, 命中名称)。

    参数 fund_name：英文基金名；返回章节页范围或 None。
    """
    section = resolve_report_section(fund_name)
    if not section:
        return None
    return section["start"], section["end"], section["matched"]


def main():
    """命令行入口：搜索 IFP 基金并切分最新可用财报章节。"""
    if len(sys.argv) < 2:
        print("用法: python download_hsbc_it_factsheet.py <子基金英文名>")
        sys.exit(1)
    fund_name = sys.argv[1].strip()
    section = resolve_report_section(fund_name)
    if not section:
        print(f"[!] 港交所财报中未找到子基金：{fund_name}")
        sys.exit(1)
    print(
        f"[*] 子基金：{fund_name} → {section['fund_id']} / "
        f"{section['date']}（PDF 索引 {section['start']}-{section['end']}）"
    )
    os.makedirs(company_data_dir(COMPANY), exist_ok=True)
    with tempfile.NamedTemporaryFile(
        delete=False, suffix=".pdf", dir=company_data_dir(COMPANY)
    ) as tmp:
        tmp_path = tmp.name
    try:
        pages = split_lib.extract_pages(
            section["path"], section["start"], section["end"], tmp_path
        )
        output_path, _month, _moved = finalize_pdf_to_monthly_path(
            tmp_path,
            COMPANY,
            os.path.basename(fund_name),
            fallback_date=section["date"],
        )
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    print(f"[*] 已切分 {pages} 页 → {output_path}")


if __name__ == "__main__":
    main()
