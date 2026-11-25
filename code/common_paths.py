# -*- coding: utf-8 -*-
"""Shared output-path and validation helpers for fund document downloaders."""

import os
import re
import shutil
import sys
from datetime import date, datetime


PROJECT_ROOT = os.path.abspath(
    os.environ.get("FACTSHEET_PROJECT_ROOT")
    or os.path.join(os.path.dirname(__file__), "..")
)
_configured_data_root = str(os.environ.get("FACTSHEET_DATA_ROOT") or "").strip()
if _configured_data_root:
    DATA_ROOT = os.path.abspath(os.path.expanduser(os.path.expandvars(_configured_data_root)))
else:
    _state_root = str(os.environ.get("FACTSHEET_STATE_ROOT") or "").strip()
    if not _state_root:
        _state_root = os.path.abspath(os.path.join(PROJECT_ROOT, "..", "..", "fund-research-state"))
    DATA_ROOT = os.path.abspath(os.path.join(os.path.expanduser(os.path.expandvars(_state_root)), "data"))
RUN_ID_ENV = "FACTSHEET_RUN_ID"
CATEGORY_ENV = "FACTSHEET_OUTPUT_CATEGORY"
FUND_ENV = "FACTSHEET_OUTPUT_FUND"
TRUST_ENV_ENV = "FACTSHEET_TRUST_ENV"
PROXY_ENV = "FACTSHEET_PROXY"
OUTPUT_ROOT_ENV = "FACTSHEET_OUTPUT_ROOT"
STAGING_DIR = "_staging"
FACTSHEET_DIR = "factsheet"
UNKNOWN_DATE_TOKENS = {"", "latest", "nodate", "unknown", "none", "null"}

MONTH_NAMES = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


class FactsheetError(RuntimeError):
    """Base error for expected downloader failures."""


class MissingDocumentError(FactsheetError):
    """Raised when the expected document link cannot be found."""


class InvalidDownloadError(FactsheetError):
    """Raised when the downloaded response is not the expected document."""


class SourceWebsiteError(FactsheetError):
    """Raised when the source website shape or status is not usable."""


def env_truthy(name):
    return str(os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def configure_session(session):
    proxy = str(os.environ.get(PROXY_ENV) or "").strip()
    session.trust_env = env_truthy(TRUST_ENV_ENV)
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})
    return session


def safe_component(value):
    value = str(value or "").strip() or "unknown"
    value = re.sub(r"\s+", "_", value)
    value = re.sub(r'[\\/:*?"<>|]', "_", value)
    return value[:160].strip("._ ") or "unknown"


def current_output_root():
    value = str(os.environ.get(OUTPUT_ROOT_ENV) or "").strip()
    if not value:
        return DATA_ROOT
    value = os.path.expandvars(os.path.expanduser(value))
    if not os.path.isabs(value):
        value = os.path.join(PROJECT_ROOT, value)
    return os.path.abspath(value)


def normalize_date(value):
    text = str(value or "").strip()
    if not text:
        return date.today().strftime("%Y-%m-%d")

    match = re.search(r"(20\d{2})[-_./](\d{1,2})(?:[-_./](\d{1,2}))?", text)
    if match:
        year = match.group(1)
        month = int(match.group(2))
        day = int(match.group(3) or 1)
        return f"{year}-{month:02d}-{day:02d}"

    match = re.search(r"(20\d{2})(\d{2})(\d{2})", text)
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"

    match = re.search(r"(20\d{2})(\d{2})", text)
    if match:
        return f"{match.group(1)}-{match.group(2)}-01"

    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass

    return date.today().strftime("%Y-%m-%d")


def normalize_report_month(value):
    if isinstance(value, datetime):
        return value.strftime("%Y-%m")
    if isinstance(value, date):
        return value.strftime("%Y-%m")

    text = str(value or "").strip()
    if text.lower() in UNKNOWN_DATE_TOKENS:
        return None

    # 既支持 YYYY-MM-DD，也支持 factsheet 常见的 DD/MM/YYYY。
    match = re.search(r"(20\d{2})[-_./](\d{1,2})(?:[-_./](\d{1,2}))?", text)
    if match:
        month = int(match.group(2))
        if 1 <= month <= 12:
            return f"{match.group(1)}-{month:02d}"

    match = re.search(r"(\d{1,2})[-/](\d{1,2})[-/](20\d{2})", text)
    if match:
        month = int(match.group(2))
        if 1 <= month <= 12:
            return f"{match.group(3)}-{month:02d}"

    match = re.search(r"(20\d{2})(0[1-9]|1[0-2])(?:[0-3]\d)?", text)
    if match:
        return f"{match.group(1)}-{match.group(2)}"

    match = re.search(r"(20\d{2})\s*年\s*(\d{1,2})\s*月", text)
    if match:
        month = int(match.group(2))
        if 1 <= month <= 12:
            return f"{match.group(1)}-{month:02d}"

    month_pattern = "|".join(sorted(MONTH_NAMES, key=len, reverse=True))
    match = re.search(rf"\b(\d{{1,2}})\s+({month_pattern})\.?,?\s+(20\d{{2}})\b", text, flags=re.I)
    if match:
        month = MONTH_NAMES[match.group(2).lower().rstrip(".")]
        return f"{match.group(3)}-{month:02d}"

    match = re.search(rf"\b({month_pattern})\.?\s+(\d{{1,2}}),?\s+(20\d{{2}})\b", text, flags=re.I)
    if match:
        month = MONTH_NAMES[match.group(1).lower().rstrip(".")]
        return f"{match.group(3)}-{month:02d}"

    match = re.search(rf"\b({month_pattern})\.?\s+(20\d{{2}})\b", text, flags=re.I)
    if match:
        month = MONTH_NAMES[match.group(1).lower().rstrip(".")]
        return f"{match.group(2)}-{month:02d}"

    return None


def company_data_dir(company):
    return current_output_root()


def fund_data_dir(company, fund_name):
    return current_output_root()


def current_run_id():
    value = os.environ.get(RUN_ID_ENV, "").strip()
    if value:
        return safe_component(value)
    return datetime.now().strftime("%Y%m%d%H%M%S")


def output_category(default_company):
    return safe_component(os.environ.get(CATEGORY_ENV) or default_company)


def output_fund_name(default_fund_name):
    return safe_component(os.environ.get(FUND_ENV) or default_fund_name)


def monthly_output_path(category, fund_name, report_month, ext=".pdf"):
    report_month = normalize_report_month(report_month)
    if not report_month:
        raise ValueError(f"Cannot determine report month from: {report_month!r}")
    ext = ext if ext.startswith(".") else f".{ext}"
    output_dir = os.path.join(current_output_root(), report_month, safe_component(category))
    path = os.path.join(output_dir, f"{safe_component(fund_name)}{ext}")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def run_output_path(run_id, category, fund_name, ext=".pdf"):
    ext = ext if ext.startswith(".") else f".{ext}"
    output_dir = os.path.join(
        current_output_root(),
        safe_component(run_id),
        FACTSHEET_DIR,
        safe_component(category),
    )
    path = os.path.join(output_dir, f"{safe_component(fund_name)}{ext}")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def staging_output_path(run_id, category, fund_name, ext=".pdf"):
    ext = ext if ext.startswith(".") else f".{ext}"
    output_dir = os.path.join(current_output_root(), STAGING_DIR, current_run_id() if not run_id else safe_component(run_id), safe_component(category))
    path = os.path.join(output_dir, f"{safe_component(fund_name)}{ext}")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def _extract_text_from_pdf(pdf_path, max_pages=2):
    try:
        import fitz
    except ImportError:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError("PyMuPDF or pypdf is required to extract the report month from PDF text.") from exc

        reader = PdfReader(pdf_path)
        return "\n".join((page.extract_text() or "") for page in reader.pages[:max_pages])

    text_parts = []
    with fitz.open(pdf_path) as doc:
        for page in doc[:max_pages]:
            text_parts.append(page.get_text("text") or "")
    return "\n".join(text_parts)


def _candidate_months(text):
    candidates = []
    patterns = [
        r"20\d{2}[-_./]\d{1,2}(?:[-_./]\d{1,2})?",
        # 部分海外 factsheet 采用 “As of 30/9/2026” 的日/月/年格式。
        r"\d{1,2}[-/]\d{1,2}[-/]20\d{2}",
        r"20\d{2}\s*年\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*日)?",
        r"\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t)?(?:ember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?,?\s+20\d{2}",
        r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t)?(?:ember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+\d{1,2},?\s+20\d{2}",
        r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t)?(?:ember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+20\d{2}",
        r"20\d{2}(?:0[1-9]|1[0-2])(?:[0-3]\d)?",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.I):
            month = normalize_report_month(match.group(0))
            if month:
                candidates.append(month)
    return candidates


def extract_report_month_from_pdf(pdf_path, fallback_date=None):
    text = _extract_text_from_pdf(pdf_path, max_pages=2)
    keyword_re = re.compile(
        r"(截至|截至于|資料截至|资料截至|數據截至|数据截至|as\s+of|as\s+at|month\s+ended|data\s+as\s+at|date\s+as\s+of)",
        flags=re.I,
    )
    for match in keyword_re.finditer(text):
        window = text[match.start() : match.start() + 160]
        candidates = _candidate_months(window)
        if candidates:
            return max(candidates)

    candidates = _candidate_months(text)
    if candidates:
        return max(candidates)

    return normalize_report_month(fallback_date)


def finalize_pdf_to_monthly_path(pdf_path, company, fund_name, fallback_date=None, ext=".pdf"):
    if os.environ.get(RUN_ID_ENV) or os.environ.get(CATEGORY_ENV) or os.environ.get(FUND_ENV):
        target = run_output_path(current_run_id(), output_category(company), output_fund_name(fund_name), ext=ext)
        report_month = ""
    else:
        target = dated_output_path(company, fund_name, fallback_date, ext=ext)
        report_month = normalize_report_month(fallback_date) or ""

    if os.path.abspath(pdf_path) == os.path.abspath(target):
        return target, report_month, False
    if os.path.exists(target):
        os.remove(pdf_path)
        return target, report_month, False
    shutil.move(pdf_path, target)
    return target, report_month, True


def dated_output_path(company, fund_name, date_value, ext=".pdf"):
    ext = ext if ext.startswith(".") else f".{ext}"
    if os.environ.get(RUN_ID_ENV) or os.environ.get(CATEGORY_ENV) or os.environ.get(FUND_ENV):
        path = run_output_path(current_run_id(), output_category(company), output_fund_name(fund_name), ext)
        if ext.lower() == ".pdf" and os.path.exists(path):
            # 不能把上一次限流返回的 HTML 错误页当作有效缓存；仅对
            # 具有 PDF 魔数的文件短路，坏缓存交给下载器覆盖重试。
            try:
                with open(path, "rb") as handle:
                    valid_pdf = handle.read(5) == b"%PDF-"
            except OSError:
                valid_pdf = False
            if valid_pdf:
                print(f"[*] Existing in current run, skipped -> {path}")
                raise SystemExit(0)
            print(f"[*] Existing output is not a PDF, will overwrite -> {path}")
        return path
    else:
        output_dir = os.path.join(current_output_root(), normalize_date(date_value))
        filename = f"{safe_component(company)}_{safe_component(fund_name)}{ext}"
    path = os.path.join(output_dir, filename)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path
