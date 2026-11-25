"""Optional integration checks against an explicitly configured test database."""

from __future__ import annotations

import os
from pathlib import Path
import uuid

import pytest
from sqlalchemy import delete, func, select

from app.db.session import create_session_factory
from app.importers.service import D1ImportService
from app.models import (
    DatasetBatch,
    DocumentVersion,
    Fund,
    FundAllocation,
    FundHolding,
    FundMetric,
    FundListing,
    FundNameHistory,
    FundRegion,
    FundReportSource,
    FundScaleHistory,
    FundSourceId,
    PublicationState,
    QualityIssue,
    ShareClass,
)


TEST_DATABASE_URL = os.environ.get("PLATFORM_TEST_DATABASE_URL", "")


@pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="PLATFORM_TEST_DATABASE_URL is not configured",
)
def test_imported_database_invariants() -> None:
    """已导入测试库必须保留基金数、来源映射和发布指针。"""

    database_name = TEST_DATABASE_URL.rsplit("/", 1)[-1].split("?", 1)[0]
    assert database_name.endswith("_test"), "integration tests require a dedicated *_test database"

    factory = create_session_factory(TEST_DATABASE_URL)
    with factory() as session:
        fund_count = session.scalar(select(func.count()).select_from(Fund))
        source_count = session.scalar(select(func.count()).select_from(FundSourceId))
        share_count = session.scalar(select(func.count()).select_from(ShareClass))
        listing_count = session.scalar(select(func.count()).select_from(FundListing))
        region_count = session.scalar(select(func.count()).select_from(FundRegion))
        scale_count = session.scalar(select(func.count()).select_from(FundScaleHistory))
        rename_count = session.scalar(
            select(func.count()).select_from(FundNameHistory)
        )
        report_source_count = session.scalar(
            select(func.count()).select_from(FundReportSource)
        )
        matched_source_count = session.scalar(
            select(func.count())
            .select_from(FundReportSource)
            .where(FundReportSource.match_status == "matched")
        )
        batch_count = session.scalar(select(func.count()).select_from(DatasetBatch))
        batch = session.scalar(
            select(DatasetBatch)
            .where(DatasetBatch.source_kind.like("hkex-master%"))
            .order_by(DatasetBatch.created_at)
        )
        ambiguous_count = session.scalar(
            select(func.count())
            .select_from(QualityIssue)
            .where(QualityIssue.issue_type == "ambiguous_structured_fund")
        )
        state = session.get(PublicationState, 1)
        current_metric_count = session.scalar(
            select(func.count())
            .select_from(FundMetric)
            .where(FundMetric.batch_id == state.current_batch_id)
        )
        current_holding_count = session.scalar(
            select(func.count())
            .select_from(FundHolding)
            .where(FundHolding.batch_id == state.current_batch_id)
        )
        current_allocation_count = session.scalar(
            select(func.count())
            .select_from(FundAllocation)
            .where(FundAllocation.batch_id == state.current_batch_id)
        )
        current_scale_count = session.scalar(
            select(func.count())
            .select_from(FundScaleHistory)
            .where(FundScaleHistory.source_batch_id == state.current_batch_id)
        )

    assert fund_count == 2479
    assert source_count == 2479
    assert share_count == 2479
    assert listing_count == 481
    assert region_count == 7437
    assert scale_count >= 29563
    assert rename_count == 661
    assert report_source_count == 853
    assert matched_source_count == 853
    assert batch_count >= 1
    assert ambiguous_count == 0
    assert batch is not None
    assert batch.status == "published"
    assert state is not None
    assert current_metric_count == 62
    assert current_holding_count == 200
    assert current_allocation_count == 266
    assert current_scale_count == 29563


@pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="PLATFORM_TEST_DATABASE_URL is not configured",
)
def test_repeating_import_does_not_roll_back_published_snapshot() -> None:
    """重复 D1 导入必须先落当前输入，再对相同输入执行 no-op。"""

    factory = create_session_factory(TEST_DATABASE_URL)
    project_root = Path(__file__).resolve().parents[3]
    with factory() as session:
        first_service = D1ImportService(
            session,
            project_root=project_root,
            hkex_workbook_path=project_root / "HKEX基金综合资料库_完整_20261008.xlsx",
            crawler_workbook_path=project_root / "爬虫list.xlsx",
        )
        first_service.import_all(
            structured_root=project_root / "data" / "20260908162205",
            activate=True,
        )
        session.commit()
        before_repeat = session.get(PublicationState, 1).current_batch_id

        second_service = D1ImportService(
            session,
            project_root=project_root,
            hkex_workbook_path=project_root / "HKEX基金综合资料库_完整_20261008.xlsx",
            crawler_workbook_path=project_root / "爬虫list.xlsx",
        )
        summary = second_service.import_all(
            structured_root=project_root / "data" / "20260908162205",
            activate=True,
        )
        session.commit()
        after = session.get(PublicationState, 1).current_batch_id

    assert summary.no_op
    assert after == before_repeat


@pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="PLATFORM_TEST_DATABASE_URL is not configured",
)
def test_structured_import_upserts_existing_allocation_key() -> None:
    """同一业务唯一键的配置行应更新而不是触发唯一约束。"""

    factory = create_session_factory(TEST_DATABASE_URL)
    project_root = Path(__file__).resolve().parents[3]
    target_id = uuid.uuid4()
    with factory() as session:
        source = session.scalar(
            select(FundAllocation)
            .where(FundAllocation.dimension == "geography")
            .limit(1)
        )
        assert source is not None
        batch = DatasetBatch(
            id=target_id,
            status="staging",
            scope_hash=f"publish-upsert-{target_id}",
            source_kind="test",
        )
        session.add(batch)
        session.add(
            FundAllocation(
                id=uuid.uuid4(),
                fund_id=source.fund_id,
                batch_id=target_id,
                document_version_id=source.document_version_id,
                report_period=source.report_period,
                dimension=source.dimension,
                category=source.category,
                weight=1,
                unit=source.unit,
                source_page=None,
                metadata_json={},
            )
        )
        session.commit()

        fund = session.get(Fund, source.fund_id)
        version = session.get(DocumentVersion, source.document_version_id)
        assert fund is not None and version is not None
        service = D1ImportService(session, project_root=project_root)
        service._import_allocations(
            batch,
            fund,
            version,
            source.report_period,
            {
                "geographic_allocation": [
                    {
                        "region": source.category,
                        "weight_percent": 12.3,
                    }
                ]
            },
        )
        session.commit()
        imported = session.scalar(
            select(FundAllocation).where(
                FundAllocation.batch_id == target_id,
                FundAllocation.fund_id == source.fund_id,
                FundAllocation.document_version_id == source.document_version_id,
                FundAllocation.report_period == source.report_period,
                FundAllocation.dimension == source.dimension,
                FundAllocation.category == source.category,
            )
        )
        assert imported is not None
        assert float(imported.weight or 0) == pytest.approx(12.3)

        session.execute(
            delete(FundAllocation).where(FundAllocation.batch_id == target_id)
        )
        session.execute(delete(DatasetBatch).where(DatasetBatch.id == target_id))
        session.commit()
