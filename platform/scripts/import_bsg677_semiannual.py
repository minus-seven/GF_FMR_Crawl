#!/usr/bin/env python3
"""Slice, parse and import BSG677's portfolio pages from the 2026 interim report.

The source PDF is a multi-fund report.  This importer deliberately slices PDF
pages 26-31 (the BSG677 portfolio table) before text extraction, then stores
all 60 source rows while the UI presents the top ten by NAV weight.
"""

from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any

from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import create_session_factory, session_scope
from app.importers.common import relative_path, sha256_file, stable_uuid, utc_now
from app.models import (
    DatasetBatch,
    Document,
    DocumentSubject,
    DocumentVersion,
    Fund,
    FundAllocation,
    FundHolding,
    FundMetric,
    FundReportSource,
    PublicationState,
)


SOURCE_URL = (
    "https://www.bosera.com.hk/api/infobase/downloadattfile.do?"
    "attachmentId=43787A6E6D7172527A39347172494664355431696A472B63764A6538662F56483755384F6259683342436A6E"
)
REPORT_PERIOD = date(2026, 6, 30)
# The report's printed pages are 26-31; the PDF has three preliminary pages,
# so the physical PDF pages to slice are 29-34.
REPORT_PAGE_FIRST = 26
REPORT_PAGE_LAST = 31
PDF_PAGE_FIRST = 29
PDF_PAGE_LAST = 34
NAV = 9_466_406_309.0
ROW_RE = re.compile(
    r"^(.+?)\s+([\d,]+)\s+([\d,]+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)%$"
)


def _tool(name: str) -> str:
    """Resolve a PDF command from PATH or the bundled Poppler runtime."""

    found = shutil.which(name)
    if found:
        return found
    bundled = (
        Path.home()
        / ".cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/poppler/bin"
        / name
    )
    if bundled.is_file():
        return str(bundled)
    raise FileNotFoundError(f"缺少 {name}，无法执行 PDF 切片/解析")


def slice_pdf(pdf_path: Path, slice_path: Path) -> Path:
    """Create the BSG677-only PDF slice before parsing the report."""

    if slice_path.is_file() and slice_path.stat().st_mtime >= pdf_path.stat().st_mtime:
        return slice_path
    slice_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="bsg677-pages-") as tmp:
        page_paths: list[Path] = []
        for page in range(PDF_PAGE_FIRST, PDF_PAGE_LAST + 1):
            output = Path(tmp) / f"page-{page}.pdf"
            subprocess.run(
                [_tool("pdfseparate"), "-f", str(page), "-l", str(page), str(pdf_path), str(output)],
                check=True,
                capture_output=True,
                text=True,
            )
            page_paths.append(output)
        subprocess.run(
            [_tool("pdfunite"), *(str(path) for path in page_paths), str(slice_path)],
            check=True,
            capture_output=True,
            text=True,
        )
    return slice_path


def extract_text(slice_path: Path) -> str:
    """Extract layout-preserving text from the already sliced PDF."""

    result = subprocess.run(
        [_tool("pdftotext"), "-layout", str(slice_path), "-"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def parse_portfolio(text: str) -> list[dict[str, Any]]:
    """Parse only the BSG677 title section from the six-page slice."""

    marker = "博時美元貨幣市場基金"
    start = text.find(marker)
    if start < 0:
        raise ValueError("切片中未找到 BSG677 的基金标题")
    section = text[start:]
    rows: list[dict[str, Any]] = []
    category: str | None = None
    holding_type: str | None = None
    registration_place: str | None = None
    for page_index, page in enumerate(section.split("\f")):
        for raw_line in page.splitlines():
            line = raw_line.strip()
            if "上市交易所買賣基金" in line and "總額" not in line:
                category = "money_market_etf"
                holding_type = "上市交易所買賣基金"
            elif "非上市交易所買賣基金" in line and "總額" not in line:
                category = "money_market_etf"
                holding_type = "非上市交易所買賣基金"
            elif "掛牌債券" in line and "總額" not in line:
                category = "bond"
                holding_type = "掛牌債券"
            elif "未掛牌債券" in line and "總額" not in line:
                category = "bond"
                holding_type = "未掛牌債券"
            elif "非上市存款證" in line and "總額" not in line:
                category = "certificate_of_deposit"
                holding_type = "非上市存款證"
            if line.startswith("註冊地"):
                registration_place = re.sub(r"^註冊地\s*[-–—:]?\s*", "", line).strip() or None
            elif line in {"香港聯交所", "香港交易所"}:
                # Listed ETF rows disclose the exchange rather than a separate
                # registration-place label; retain that source wording.
                registration_place = line
            match = ROW_RE.match(line)
            if not match or category is None:
                continue
            name, quantity, market_value, weight = match.groups()
            if any(token in name for token in ("小計", "總額", "投資總額", "其他淨資產", "股份持有人")):
                continue
            rows.append(
                {
                    "name": name.strip(),
                    "holding_quantity": int(quantity.replace(",", "")),
                    "market_value": float(market_value.replace(",", "")),
                    "weight_percent": float(weight),
                    "asset_type": category,
                    "registration_place": registration_place,
                    "holding_type": holding_type,
                    "source_page": REPORT_PAGE_FIRST + page_index,
                }
            )
    if len(rows) != 60:
        raise ValueError(f"BSG677 切片持仓行数异常：期望 60，实际 {len(rows)}")
    total = sum(row["market_value"] for row in rows)
    if abs(total - 3_015_743_266.0) > 0.5:
        raise ValueError(f"BSG677 持仓市值合计校验失败：{total}")
    return rows


def _metric(
    session,
    *,
    batch: DatasetBatch,
    fund: Fund,
    version: DocumentVersion,
    name: str,
    numeric_value: float | None,
    text_value: str | None,
    unit: str,
    metadata: dict[str, Any],
) -> None:
    query = select(FundMetric).where(
        FundMetric.batch_id == batch.id,
        FundMetric.fund_id == fund.id,
        FundMetric.document_version_id == version.id,
        FundMetric.report_period == REPORT_PERIOD,
        FundMetric.metric_name == name,
    )
    row = session.scalar(query)
    if row is None:
        row = FundMetric(
            id=stable_uuid("bsg677-metric", batch.id, fund.id, version.id, name),
            fund_id=fund.id,
            batch_id=batch.id,
            document_version_id=version.id,
            report_period=REPORT_PERIOD,
            metric_name=name,
        )
        session.add(row)
    row.numeric_value = numeric_value
    row.text_value = text_value
    row.unit = unit
    row.confidence = "source_table"
    row.review_status = "approved"
    row.source_page = 26
    row.metadata_json = metadata


def import_report(pdf_path: Path, slice_path: Path, database_url: str | None) -> dict[str, Any]:
    """Slice, parse and upsert BSG677 holdings and equity/debt metrics."""

    settings = get_settings()
    project_root = settings.project_root
    slice_pdf(pdf_path, slice_path)
    rows = parse_portfolio(extract_text(slice_path))
    source_sha = sha256_file(pdf_path)
    slice_sha = sha256_file(slice_path)
    factory = create_session_factory(database_url or settings.database_url)
    with session_scope(factory) as session:
        state = session.get(PublicationState, 1)
        if state is None or state.current_batch_id is None:
            raise RuntimeError("当前没有已发布数据快照")
        batch = session.get(DatasetBatch, state.current_batch_id)
        if batch is None or batch.status != "published":
            raise RuntimeError("当前发布快照无效")
        fund = session.scalar(select(Fund).where(Fund.fund_code == "BSG677"))
        if fund is None:
            raise RuntimeError("数据库不存在基金 BSG677")

        source_id = f"bosera-bsg677-semiannual:{source_sha}"
        document = session.scalar(select(Document).where(Document.source_id == source_id))
        if document is None:
            document = Document(
                id=stable_uuid("bsg677-document", source_sha),
                document_type="semiannual_report",
                source_id=source_id,
                first_seen_at=utc_now(),
            )
            session.add(document)
            session.flush()
        version = session.scalar(select(DocumentVersion).where(DocumentVersion.sha256 == source_sha))
        if version is None:
            version = DocumentVersion(
                id=stable_uuid("bsg677-document-version", source_sha),
                document_id=document.id,
                batch_id=batch.id,
                source_url=SOURCE_URL,
                report_date=REPORT_PERIOD,
                sha256=source_sha,
                mime_type="application/pdf",
                size_bytes=pdf_path.stat().st_size,
                file_path=relative_path(pdf_path, project_root),
            )
            session.add(version)
            session.flush()
        subject = session.scalar(
            select(DocumentSubject).where(
                DocumentSubject.document_version_id == version.id,
                DocumentSubject.fund_id == fund.id,
            )
        )
        if subject is None:
            session.add(
                DocumentSubject(
                    id=stable_uuid("bsg677-document-subject", version.id, fund.id),
                    document_version_id=version.id,
                    fund_id=fund.id,
                    subject_name=fund.name_cn,
                    start_page=REPORT_PAGE_FIRST,
                    end_page=REPORT_PAGE_LAST,
                    match_method="fund_title_page_slice",
                    match_score=1,
                )
            )

        metadata = {
            "source": "Bosera 2026 interim report",
            "source_url": SOURCE_URL,
            "report_period": REPORT_PERIOD.isoformat(),
            "original_pdf_path": relative_path(pdf_path, project_root),
            "slice_pdf_path": relative_path(slice_path, project_root),
            "slice_sha256": slice_sha,
            "slice_pages": [REPORT_PAGE_FIRST, REPORT_PAGE_LAST],
            "pdf_physical_pages": [PDF_PAGE_FIRST, PDF_PAGE_LAST],
            "extraction_method": "slice-first pdftotext layout parser",
            "holding_count": len(rows),
            "nav_usd": NAV,
        }
        for index, item in enumerate(rows, start=1):
            holding_id = stable_uuid("bsg677-holding", batch.id, fund.id, version.id, index)
            holding = session.get(FundHolding, holding_id)
            if holding is None:
                holding = session.scalar(
                    select(FundHolding).where(
                        FundHolding.batch_id == batch.id,
                        FundHolding.fund_id == fund.id,
                        FundHolding.document_version_id == version.id,
                        FundHolding.report_period == REPORT_PERIOD,
                        FundHolding.security_name == item["name"],
                        FundHolding.ticker.is_(None),
                        FundHolding.holding_quantity == item["holding_quantity"],
                        FundHolding.market_value == item["market_value"],
                    )
                )
            if holding is None:
                holding = session.scalar(
                    select(FundHolding).where(
                        FundHolding.batch_id == batch.id,
                        FundHolding.fund_id == fund.id,
                        FundHolding.document_version_id == version.id,
                        FundHolding.report_period == REPORT_PERIOD,
                        FundHolding.security_name == item["name"],
                        FundHolding.ticker.is_(None),
                        FundHolding.holding_quantity.is_(None),
                    )
                )
            if holding is None:
                holding = FundHolding(
                    id=holding_id,
                    fund_id=fund.id,
                    batch_id=batch.id,
                    document_version_id=version.id,
                    report_period=REPORT_PERIOD,
                    security_name=item["name"],
                    ticker=None,
                )
                session.add(holding)
            holding.holding_quantity = item["holding_quantity"]
            holding.weight = item["weight_percent"]
            holding.market_value = item["market_value"]
            holding.registration_place = item["registration_place"]
            holding.holding_type = item["holding_type"]
            holding.source_page = item["source_page"]
            duplicates = session.scalars(
                select(FundHolding).where(
                    FundHolding.batch_id == batch.id,
                    FundHolding.fund_id == fund.id,
                    FundHolding.document_version_id == version.id,
                    FundHolding.report_period == REPORT_PERIOD,
                    FundHolding.security_name == item["name"],
                    FundHolding.ticker.is_(None),
                    FundHolding.id != holding.id,
                    FundHolding.holding_quantity.is_(None),
                )
            ).all()
            for duplicate in duplicates:
                session.delete(duplicate)

        by_type: dict[str, float] = {}
        for item in rows:
            by_type[item["asset_type"]] = by_type.get(item["asset_type"], 0.0) + item["market_value"]
        by_type["other_net_assets"] = NAV - sum(by_type.values())
        allocation_labels = {
            "bond": "债券",
            "certificate_of_deposit": "存款证",
            "money_market_etf": "货币市场ETF",
            "other_net_assets": "其他净资产",
        }
        for asset_type, label in allocation_labels.items():
            weight = by_type.get(asset_type, 0.0) / NAV * 100
            allocation_id = stable_uuid("bsg677-allocation", batch.id, fund.id, version.id, label)
            allocation = session.scalar(
                select(FundAllocation).where(
                    FundAllocation.batch_id == batch.id,
                    FundAllocation.fund_id == fund.id,
                    FundAllocation.document_version_id == version.id,
                    FundAllocation.report_period == REPORT_PERIOD,
                    FundAllocation.dimension == "asset_type",
                    FundAllocation.category == label,
                )
            )
            if allocation is None:
                allocation = FundAllocation(
                    id=allocation_id,
                    fund_id=fund.id,
                    batch_id=batch.id,
                    document_version_id=version.id,
                    report_period=REPORT_PERIOD,
                    dimension="asset_type",
                    category=label,
                )
                session.add(allocation)
            allocation.weight = weight
            allocation.unit = "percent"
            allocation.source_page = 26

        classified_debt = by_type.get("bond", 0.0) + by_type.get("certificate_of_deposit", 0.0)
        classified_total = classified_debt  # no equity securities in this portfolio table
        equity_weight = 0.0
        debt_weight = classified_debt / classified_total * 100 if classified_total else 0.0
        _metric(session, batch=batch, fund=fund, version=version, name="equity_weight_percent", numeric_value=equity_weight, text_value=None, unit="percent", metadata=metadata)
        _metric(session, batch=batch, fund=fund, version=version, name="debt_weight_percent", numeric_value=debt_weight, text_value=None, unit="percent", metadata=metadata)
        ratio_metadata = {**metadata, "ratio_basis": "classified portfolio holdings; money-market ETFs excluded"}
        _metric(session, batch=batch, fund=fund, version=version, name="equity_debt_ratio", numeric_value=None, text_value="0:100", unit="ratio", metadata=ratio_metadata)

        source_key = f"bosera-bsg677-semiannual:{source_sha}"
        source = session.scalar(select(FundReportSource).where(FundReportSource.source_key == source_key))
        source_metadata = {
            **metadata,
            "document_version_id": str(version.id),
            "source_label": "2026半年报 · 投资组合表（BSG677切片第26–31页）",
            "report_kind": "semiannual",
            "language": "zh-HK",
            "available_sections": ["全部持仓", "前十持仓", "资产类型", "股债比"],
            "equity_debt_ratio": "0:100",
            "debt_value_usd": classified_debt,
            "equity_value_usd": 0,
        }
        if source is None:
            source = FundReportSource(
                id=stable_uuid("bsg677-report-source", source_sha),
                fund_id=fund.id,
                source_system="bosera",
                source_key=source_key,
                source_sheet="BSG677_2026_h1_portfolio_slice",
                source_row=1,
                fund_name_source=fund.name_cn,
                monthly_report_url=SOURCE_URL,
                match_status="matched",
                match_method="fund_title_page_slice",
                match_confidence="high",
                is_active=True,
                metadata_json=source_metadata,
            )
            session.add(source)
        else:
            source.monthly_report_url = SOURCE_URL
            source.is_active = True
            source.metadata_json = source_metadata

        return {
            "fund_code": "BSG677",
            "report_period": REPORT_PERIOD.isoformat(),
            "slice_pages": [REPORT_PAGE_FIRST, REPORT_PAGE_LAST],
            "pdf_physical_pages": [PDF_PAGE_FIRST, PDF_PAGE_LAST],
            "holding_count": len(rows),
            "top10_displayed": 10,
            "equity_debt_ratio": "0:100",
            "source_sha256": source_sha,
            "slice_sha256": slice_sha,
            "document_version_id": str(version.id),
        }


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, default=settings.project_root / "case/sources/BSG677_2026_h1.pdf")
    parser.add_argument("--slice", type=Path, default=settings.project_root / "case/sources/BSG677_2026_h1_slice.pdf")
    parser.add_argument("--database-url", default=settings.database_url)
    args = parser.parse_args()
    try:
        result = import_report(args.pdf.resolve(), args.slice.resolve(), args.database_url)
    except Exception as exc:  # noqa: BLE001
        print(f"[FAILED] BSG677 半年报导入失败：{type(exc).__name__}: {exc}")
        return 2
    print("[OK] BSG677 半年报已切片、解析并落库 " + json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
