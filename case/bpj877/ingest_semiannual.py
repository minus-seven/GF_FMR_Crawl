#!/usr/bin/env python3
"""Ingest the BPJ877 2026 semi-annual report as a second evidence source.

This case adapter deliberately keeps the English source text and records every
value only when it is printed in the report.  It is reusable for a manually
downloaded candidate report while the general crawler source remains unchanged.
"""

from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

from sqlalchemy import select

from app.db.session import create_session_factory
from app.importers.service import D1ImportService
from app.models import DatasetBatch, DocumentSubject, DocumentVersion, Fund, FundReportSource, PublicationState


ROOT = Path(__file__).resolve().parents[2]
PDF = ROOT / "data/case/bpj877/BPJ877_2026_H1.pdf"
STAGING = ROOT / "data/case/bpj877/semiannual_staging"
URL = "https://www.gaotengasset.com/website/885261760423002112.pdf"


def record() -> dict:
    return {
        "fund_name": "高腾微金美元货币基金",
        "fund_name_en": "GAOTENG WEVALUE USD MONEY MARKET FUND",
        "_source_json": "BPJ877_2026_H1.money_market.json",
        "document_type": "semiannual",
        "report_date": "2026-06-30",
        "fund_size_hkd_100m": None,
        "portfolio_duration_years": None,
        "portfolio_ytm_percent": None,
        "credit_rating": None,
        "top_holdings": [
            {"name": "Mirae Asset Securities Financial Products Ltd F% 04032027 EMTN", "weight_percent": 1.42},
            {"name": "GF Global Capital Ltd/HK F% 08102026 EMTN", "weight_percent": 1.02},
            {"name": "Intesa Sanpaolo SpA/London F% 06112026 CD", "weight_percent": 1.02},
            {"name": "Intesa Sanpaolo SpA/London F% 16102026 CD", "weight_percent": 1.02},
            {"name": "Intesa Sanpaolo SpA/London F% 28102026 CD", "weight_percent": 1.02},
            {"name": "Saudi National Bank/Singapore 4.155% 03082026 FXCD", "weight_percent": 0.81},
            {"name": "Saudi National Bank/Singapore 4.155% 11092026 FXCD", "weight_percent": 0.81},
            {"name": "GF Global Capital Ltd/HK F% 06012027 EMTN", "weight_percent": 0.81},
            {"name": "Intesa Sanpaolo SpA/London F% 05022027 ECD", "weight_percent": 0.91},
            {"name": "Intesa Sanpaolo SpA/London F% 23032027", "weight_percent": 0.91},
        ],
        "top_holdings_total_percent": 9.75,
        "duration_distribution": [],
        "geographic_allocation": [
            {"region": "British Virgin Islands", "weight_percent": 1.72},
            {"region": "People's Republic of China", "weight_percent": 3.86},
            {"region": "Hong Kong", "weight_percent": 4.57},
            {"region": "Singapore", "weight_percent": 20.03},
            {"region": "Italy", "weight_percent": 13.09},
            {"region": "Kuwait", "weight_percent": 1.53},
            {"region": "Money market funds - Hong Kong", "weight_percent": 4.96},
        ],
        "dividend": {
            "share_class": "USD Class F (distribution)",
            "currency": "USD",
            "frequency": "monthly",
            "dividend_per_unit": 0.02731,
            "note": "The report lists monthly distributions for January-June 2026; this is the first listed payment, not an annualized yield.",
        },
        "money_market_metrics": {
            "seven_day_yield_percent": None,
            "annualized_yield_percent": None,
            "weighted_average_maturity_days": 53.36,
            "weighted_average_life_days": 88.26,
            "daily_liquid_assets_percent": 57.77,
            "weekly_liquid_assets_percent": 58.30,
            "asset_type_allocation": [
                {"type": "Debt securities and certificates of deposits", "weight_percent": 44.80},
                {"type": "Money market funds", "weight_percent": 4.96},
                {"type": "Other net assets", "weight_percent": 50.24},
            ],
            "bank_exposure": [],
        },
        "additional_metrics": {
            "net_assets_usd": {"value": 4919625169, "unit": "USD"},
            "total_assets_usd": {"value": 4922142042, "unit": "USD"},
            "total_income_usd": {"value": 100056614, "unit": "USD"},
            "operating_profit_usd": {"value": 88069825, "unit": "USD"},
            "distribution_paid_usd": {"value": 1924886, "unit": "USD"},
        },
    }


def write_artifacts() -> Path:
    if not PDF.is_file():
        raise FileNotFoundError(PDF)
    if STAGING.exists():
        shutil.rmtree(STAGING)
    pdf_target = STAGING / "factsheet/货币/BPJ877_2026_H1.pdf"
    pdf_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(PDF, pdf_target)
    txt = PDF.with_suffix(".txt")
    if not txt.is_file():
        raise FileNotFoundError(txt)
    markdown = STAGING / "markdown/货币/BPJ877_2026_H1/full.md"
    markdown.parent.mkdir(parents=True, exist_ok=True)
    markdown.write_text(
        "# GAOTENG WEVALUE USD MONEY MARKET FUND\n\n"
        "## Semi-annual report (unaudited)\n\nFor the period ended 30 June 2026\n\n"
        + txt.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    output = STAGING / "structure_data/_work/money_market/per_pdf/BPJ877_2026_H1.money_market.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record(), ensure_ascii=False, indent=2), encoding="utf-8")
    meta = {
        "status": "ok",
        "_meta": {
            "source_file": str(pdf_target),
            "source_url": URL,
            "source_label": "2026半年报",
            "report_kind": "semiannual",
            "language": "en",
            "available_sections": ["前十大持仓", "流动性", "资产类别", "地域分布", "半年财务摘要"],
            "parse_method": "pypdf-native-text",
            "structured_method": "deterministic-report-table-adapter",
            "preserved_english": True,
            "evidence_pages": {"portfolio": [11, 12, 13, 14, 15], "liquidity": [15], "statements": [2, 3, 4, 5]},
        },
    }
    (output.with_suffix(".meta.json")).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    combined = STAGING / "structure_data/_work/money_market/combined/货币_基金提取结果_combined.json"
    combined.parent.mkdir(parents=True, exist_ok=True)
    combined.write_text(json.dumps({"records": [record()]}, ensure_ascii=False, indent=2), encoding="utf-8")
    report = STAGING / "reports/download_run_report_semiannual.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({"results": [{"category": "货币", "fund_name": "高腾微金美元货币基金", "source_url": URL}]}, ensure_ascii=False, indent=2), encoding="utf-8")
    return STAGING


def import_into_current_batch(staging: Path) -> None:
    factory = create_session_factory()
    with factory() as session:
        state = session.get(PublicationState, 1)
        if state is None or state.current_batch_id is None:
            raise RuntimeError("没有当前发布批次")
        batch = session.get(DatasetBatch, state.current_batch_id)
        fund = session.scalar(select(Fund).where(Fund.fund_code == "BPJ877"))
        if batch is None or fund is None:
            raise RuntimeError("找不到当前批次或 BPJ877")
        existing = session.scalar(
            select(DocumentVersion)
            .join(DocumentSubject, DocumentSubject.document_version_id == DocumentVersion.id)
            .where(
                DocumentSubject.fund_id == fund.id,
                DocumentVersion.batch_id == batch.id,
                DocumentVersion.report_date == date(2026, 6, 30),
            )
        )
        if existing is None or not existing.file_path:
            service = D1ImportService(session, project_root=ROOT)
            service.import_structured_into_batch(batch, staging, {fund.id})
            existing = session.scalar(
                select(DocumentVersion)
                .join(DocumentSubject, DocumentSubject.document_version_id == DocumentVersion.id)
                .where(
                    DocumentSubject.fund_id == fund.id,
                    DocumentVersion.batch_id == batch.id,
                    DocumentVersion.report_date == date(2026, 6, 30),
                )
                .order_by(DocumentVersion.file_path.is_(None))
            )
        if existing is not None:
            existing.source_url = URL
            existing.file_path = "data/case/bpj877/BPJ877_2026_H1.pdf"
        source = session.scalar(select(FundReportSource).where(FundReportSource.source_key == "gaoteng:BPJ877:semiannual:2026"))
        metadata = {
            "source_label": "2026半年报",
            "report_kind": "semiannual",
            "report_period": "2026-06-30",
            "language": "en",
            "source_url": URL,
            "preserved_english": True,
            "available_sections": ["前十大持仓", "流动性", "资产类别", "地域分布", "半年财务摘要"],
        }
        if source is None:
            session.add(FundReportSource(
                source_key="gaoteng:BPJ877:semiannual:2026",
                fund_id=fund.id,
                source_system="gaoteng-candidate",
                source_sheet="候选半年报",
                source_row=1,
                manager_name_source="GaoTeng Global Asset Management Limited",
                fund_name_source="GAOTENG WEVALUE USD MONEY MARKET FUND",
                umbrella_name_source="GaoTeng WeFund",
                monthly_report_url=None,
                fallback_report_url=URL,
                run_command=f"curl -L -o {PDF}",
                match_status="matched",
                match_method="fund_code_manual_candidate",
                match_confidence="high",
                is_active=True,
                metadata_json=metadata,
            ))
        else:
            source.metadata_json = metadata
            source.fallback_report_url = URL
            source.is_active = True
        session.commit()


if __name__ == "__main__":
    root = write_artifacts()
    import_into_current_batch(root)
    print(json.dumps({"staging": str(root), "source_url": URL, "source_label": "2026半年报"}, ensure_ascii=False))
