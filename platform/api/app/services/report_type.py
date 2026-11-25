"""Detect and label the report type of a manually supplemented source."""

from __future__ import annotations

from datetime import date
import re


REPORT_KIND_MONTHLY = "monthly"
REPORT_KIND_SEMIANNUAL = "semiannual"
REPORT_KIND_ANNUAL = "annual"
REPORT_KIND_SUPPLEMENTARY = "supplementary"


def _compact(value: str | None) -> str:
    """Normalize report text for marker matching."""

    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def detect_report_kind(
    text: str | None,
    report_date: date | None = None,
    filename: str | None = None,
) -> str:
    """Return ``monthly``, ``semiannual``, ``annual`` or ``supplementary``.

    Explicit annual/semiannual markers win over date heuristics.  Many Hong
    Kong fund factsheets only print ``2026年9月`` and do not literally print
    “月报”, so a standalone year-month heading is treated as a monthly report
    after the annual/interim checks.  Ambiguous June/December documents remain
    supplementary unless they contain a monthly marker.
    """

    content = _compact(" ".join((text or "", filename or "")))
    # 终止/清盘通知里经常出现“本月报”等字样，但并不是月报事实资料表；
    # 先归为补充来源，避免把通知日期或事件日期写入规模趋势。
    if re.search(
        r"终止|終止|清盘|清算|termination\s+notice|notice\s+of\s+termination|wind[- ]?up",
        content,
    ):
        return REPORT_KIND_SUPPLEMENTARY
    if re.search(
        r"半年報|半年报|半年度報告|半年度报告|中期報告|中期报告|中報|中报|interim\s+report|semi[- ]?annual|half[- ]year",
        content,
    ):
        return REPORT_KIND_SEMIANNUAL
    if re.search(
        r"年報|年报|年度報告|年度报告|年度財務報告|年度财务报告|(?<!semi-)annual\s+(?:financial\s+)?report|year[- ]end\s+report",
        content,
    ):
        return REPORT_KIND_ANNUAL
    if re.search(
        r"月報|月报|基金月報|基金月报|monthly\s+(?:fund\s+)?(?:report|factsheet)|monthly\s+newsletter",
        content,
    ):
        return REPORT_KIND_MONTHLY

    year_month = re.search(r"(?:20\d{2})\s*[年/-]\s*(1[0-2]|0?[1-9])\s*月?", content)
    if year_month:
        month = int(year_month.group(1))
        if report_date is None or month not in {6, 12}:
            return REPORT_KIND_MONTHLY
        # A June/December report is only called monthly when the document has
        # a clear factsheet-style as-of marker; otherwise keep it conservative.
        if re.search(r"截至|截止|資料截至|资料截至|as\s+(?:of|at)|data\s+as\s+at", content):
            return REPORT_KIND_MONTHLY

    return REPORT_KIND_SUPPLEMENTARY


def report_kind_label(kind: str, report_date: date | None) -> str:
    """Return the canonical Chinese display label for a report kind.

    All report cards use this single formatter: monthly reports are labelled
    ``x月月报`` using the report cutoff month, while interim/annual reports
    use ``x年半年报``/``x年年报``.  The date remains the precise cutoff date
    in the data payload; this function only controls the human-facing label.
    """

    normalized_kind = str(kind or "").strip().casefold()
    aliases = {
        "month": REPORT_KIND_MONTHLY,
        "monthly_report": REPORT_KIND_MONTHLY,
        "monthly factsheet": REPORT_KIND_MONTHLY,
        "semi-annual": REPORT_KIND_SEMIANNUAL,
        "semiannual_report": REPORT_KIND_SEMIANNUAL,
        "interim": REPORT_KIND_SEMIANNUAL,
        "annual_report": REPORT_KIND_ANNUAL,
        "yearly": REPORT_KIND_ANNUAL,
    }
    normalized_kind = aliases.get(normalized_kind, normalized_kind)
    if normalized_kind == REPORT_KIND_MONTHLY and report_date:
        return f"{report_date.month}月月报"
    if normalized_kind == REPORT_KIND_SEMIANNUAL and report_date:
        return f"{report_date.year}年半年报"
    if normalized_kind == REPORT_KIND_ANNUAL and report_date:
        return f"{report_date.year}年年报"
    if report_date:
        return f"补充来源 · {report_date.isoformat()}"
    return "补充来源"
