"""D1 master and structured data import orchestration."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from pypdf import PdfReader
from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.importers.common import (
    infer_batch_category,
    normalize_name,
    normalize_text,
    parse_report_month,
    relative_path,
    sha256_file,
    sha256_json,
    stable_uuid,
    utc_now,
)
from app.importers.crawler_data import CrawlerReportRow, read_crawler_sources
from app.importers.hkex_data import (
    HKEXFundRow,
    HKEXWorkbook,
    detected_at_utc,
    read_hkex_workbook,
)
from app.models import (
    DatasetBatch,
    Document,
    DocumentSubject,
    DocumentVersion,
    Fund,
    FundAllocation,
    FundClassification,
    FundHolding,
    FundListing,
    FundMetric,
    FundNameHistory,
    FundScaleHistory,
    FundRegion,
    FundReportSource,
    FundSourceId,
    FundTag,
    Manager,
    PublicationState,
    QualityIssue,
    ShareClass,
    Umbrella,
)
from app.services.download_sources import (
    DOWNLOAD_CANDIDATES_KEY,
    infer_download_sources,
    serialize_download_sources,
)
from app.services.allocation_labels import canonical_allocation_label
from app.services.report_type import detect_report_kind


@dataclass
class ImportSummary:
    """Counts and identifiers produced by one D1 import.

    参数 batch_id：本次不可变批次 ID。
    参数 scope_hash：输入数据指纹。
    参数 no_op：输入已完整导入时为真。
    参数其余字段：导入或发现的记录数量。
    """

    batch_id: str
    scope_hash: str
    no_op: bool = False
    managers: int = 0
    umbrellas: int = 0
    funds: int = 0
    source_ids: int = 0
    share_classes: int = 0
    listings: int = 0
    classifications: int = 0
    regions: int = 0
    tags: int = 0
    name_history: int = 0
    report_sources: int = 0
    matched_report_sources: int = 0
    unmatched_report_sources: int = 0
    documents: int = 0
    document_versions: int = 0
    document_subjects: int = 0
    metrics: int = 0
    holdings: int = 0
    allocations: int = 0
    scale_history: int = 0
    quality_issues: int = 0
    structured_files: int = 0
    structured_records: int = 0
    skipped_records: int = 0
    warnings: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        """返回可序列化的导入摘要；参数为空。"""

        payload = asdict(self)
        payload["warnings"] = list(self.warnings or [])
        return payload


def find_latest_structured_batch(data_root: Path) -> Path:
    """查找最新含结构化产物批次；参数 data_root：data 目录。"""

    candidates = []
    complete_candidates = []
    for path in data_root.iterdir() if data_root.exists() else []:
        if not path.is_dir():
            continue
        work = path / "structure_data" / "_work"
        combined = list(work.glob("*/combined/*_combined.json"))
        if not combined:
            continue
        candidates.append(path)
        names = {item.name for item in combined}
        # 增量类别批次（例如仅货币基金）不能遮蔽上一批完整的可导入快照。
        if {
            "固收_基金提取结果_combined.json",
            "权益_基金提取结果_combined.json",
            "混合_基金提取结果_combined.json",
        }.issubset(names):
            complete_candidates.append(path)
    if not candidates:
        raise FileNotFoundError(f"{data_root} 下没有可导入的结构化批次")
    return sorted(complete_candidates or candidates, key=lambda value: value.name)[-1]


def discover_combined_files(batch_root: Path) -> list[Path]:
    """发现批次内固收、权益和货币合并 JSON；参数 batch_root：数据批次目录。"""

    return sorted(
        (batch_root / "structure_data" / "_work").glob("*/combined/*_combined.json")
    )


class D1ImportService:
    """Import HKEX master, crawler supplements and structured outputs."""

    IMPORTER_VERSION = "d1-hkex-v2"

    def __init__(
        self,
        session: Session,
        *,
        project_root: Path,
        hkex_workbook_path: Path | None = None,
        crawler_workbook_path: Path | None = None,
    ):
        """初始化导入服务。

        参数 session：当前数据库事务。
        参数 project_root：项目根目录。
        参数 hkex_workbook_path：HKEX 基金综合资料库路径。
        参数 crawler_workbook_path：爬虫链接与运行方式工作簿路径。
        """

        self.session = session
        self.project_root = project_root.resolve()
        self.hkex_workbook_path = (
            hkex_workbook_path.resolve() if hkex_workbook_path else None
        )
        self.crawler_workbook_path = (
            crawler_workbook_path.resolve() if crawler_workbook_path else None
        )
        self._fund_by_code: dict[str, Fund] = {}
        self._fund_by_id: dict[Any, Fund] = {}
        self._fund_by_name: dict[str, list[Fund]] = {}
        self._fund_by_manager_name: dict[tuple[str, str], list[Fund]] = {}
        self._fund_by_manager_name_umbrella: dict[tuple[str, str, str], list[Fund]] = {}
        self._fund_by_manager_historical_name: dict[tuple[str, str], list[Fund]] = {}
        self._fund_by_historical_name: dict[str, list[Fund]] = {}
        self._fund_categories: dict[Any, str] = {}
        self._fund_ids_by_report_url: dict[str, list[Any]] = {}
        self._summary = ImportSummary(batch_id="", scope_hash="", warnings=[])

    def import_all(
        self,
        *,
        structured_root: Path | None = None,
        activate: bool = True,
    ) -> ImportSummary:
        """执行 HKEX 主数据、crawler 补充和结构化数据导入。

        参数 structured_root：要导入的结构化批次；为空时选择最新批次。
        参数 activate：是否把新批次设置为当前发布快照。
        返回：导入摘要。
        """

        if self.hkex_workbook_path is None or not self.hkex_workbook_path.is_file():
            raise FileNotFoundError(f"HKEX 主数据工作簿不存在：{self.hkex_workbook_path}")
        if (
            self.crawler_workbook_path is None
            or not self.crawler_workbook_path.is_file()
        ):
            raise FileNotFoundError(f"爬虫补充工作簿不存在：{self.crawler_workbook_path}")
        hkex_workbook = read_hkex_workbook(self.hkex_workbook_path)
        crawler_rows = read_crawler_sources(self.crawler_workbook_path)

        selected_structured = structured_root or find_latest_structured_batch(
            get_settings().data_root
        )
        structured_files = discover_combined_files(selected_structured)
        if not structured_files:
            raise FileNotFoundError(
                f"{selected_structured} 下没有可导入的结构化合并 JSON"
            )

        input_fingerprint = {
            "schema": self.IMPORTER_VERSION,
            "hkex_workbook": sha256_file(self.hkex_workbook_path),
            "crawler_workbook": sha256_file(self.crawler_workbook_path),
            "structured_batch": selected_structured.name,
            "structured_files": {
                relative_path(path, self.project_root): sha256_file(path)
                for path in structured_files
            },
        }
        scope_hash = sha256_json(input_fingerprint)
        batch_id = stable_uuid("dataset-batch", scope_hash)
        existing = self.session.get(DatasetBatch, batch_id)
        if existing and existing.status == "published":
            if activate:
                self._activate_batch(existing)
            self._summary = ImportSummary(
                batch_id=str(batch_id),
                scope_hash=scope_hash,
                no_op=True,
                warnings=["相同输入已发布，未重复写入"],
            )
            return self._summary

        batch = DatasetBatch(
            id=batch_id,
            status="staging",
            scope_hash=scope_hash,
            source_kind="hkex-master+crawler-supplement+structured",
            source_sha256=sha256_file(self.hkex_workbook_path),
            coverage=None,
        )
        self.session.add(batch)
        self.session.flush()
        self._summary = ImportSummary(
            batch_id=str(batch_id),
            scope_hash=scope_hash,
            warnings=[],
        )

        self._import_hkex_master(batch, hkex_workbook)
        self._import_crawler_sources(batch, crawler_rows)
        self._import_structured(batch, selected_structured, structured_files)
        self._summary.structured_files = len(structured_files)
        self.session.flush()
        self._summary.metrics = int(
            self.session.scalar(
                select(func.count())
                .select_from(FundMetric)
                .where(FundMetric.batch_id == batch.id)
            )
            or 0
        )
        self._summary.holdings = int(
            self.session.scalar(
                select(func.count())
                .select_from(FundHolding)
                .where(FundHolding.batch_id == batch.id)
            )
            or 0
        )
        self._summary.allocations = int(
            self.session.scalar(
                select(func.count())
                .select_from(FundAllocation)
                .where(FundAllocation.batch_id == batch.id)
            )
            or 0
        )
        self._summary.scale_history = int(
            self.session.scalar(
                select(func.count())
                .select_from(FundScaleHistory)
                .where(FundScaleHistory.source_batch_id == batch.id)
            )
            or 0
        )
        self._summary.quality_issues = int(
            self.session.scalar(
                select(func.count())
                .select_from(QualityIssue)
                .where(QualityIssue.batch_id == batch.id)
            )
            or 0
        )
        self._finish_batch(batch, activate=activate)
        return self._summary

    def import_hkex_master_workbook(
        self,
        workbook_path: Path,
        *,
        activate: bool = True,
    ) -> ImportSummary:
        """只用综合资料库 xlsx 更新主数据并保留当前结构化结果。

        综合资料库任务不重新解析月报/半年报；先把当前发布批次的指标、持仓、
        配置和质量问题复制到新批次，再用新 xlsx 更新基金主数据、分类、标签、
        规模和改名记录。这样数据库发布仍保持不可变批次语义。
        """

        workbook_path = workbook_path.resolve()
        if not workbook_path.is_file():
            raise FileNotFoundError(f"综合资料库文件不存在：{workbook_path}")
        workbook = read_hkex_workbook(workbook_path)
        digest = sha256_file(workbook_path)
        batch_id = stable_uuid("dataset-batch-master", digest)
        existing = self.session.get(DatasetBatch, batch_id)
        if existing and existing.status == "published":
            if activate:
                self._activate_batch(existing)
            return ImportSummary(
                batch_id=str(batch_id),
                scope_hash=digest,
                no_op=True,
                warnings=["相同综合资料库 SHA 已发布，未重复写入"],
            )

        state = self.session.get(PublicationState, 1)
        current = self.session.get(DatasetBatch, state.current_batch_id) if state and state.current_batch_id else None
        if current is None:
            raise ValueError("没有当前发布批次，无法执行综合资料库增量导入")
        batch = DatasetBatch(
            id=batch_id,
            job_id=None,
            status="staging",
            scope_hash=digest,
            source_kind="hkex-master-task",
            source_sha256=digest,
            coverage=1,
        )
        self.session.add(batch)
        self.session.flush()
        self._summary = ImportSummary(
            batch_id=str(batch.id),
            scope_hash=digest,
            warnings=[],
        )

        # 保持当前报告结构化结果，主数据任务只替换其权威主数据输入。
        for statement in (
            """
            INSERT INTO fund_metrics (
              id, batch_id, fund_id, document_version_id, report_period,
              metric_name, numeric_value, text_value, unit, confidence,
              review_status, source_page, metadata_json
            )
            SELECT gen_random_uuid(), :target, fund_id, document_version_id,
                   report_period, metric_name, numeric_value, text_value, unit,
                   confidence, review_status, source_page, metadata_json
            FROM fund_metrics WHERE batch_id = :source
            """,
            """
            INSERT INTO fund_holdings (
              id, batch_id, fund_id, document_version_id, report_period,
              security_name, ticker, holding_quantity, registration_place,
              holding_type, weight, market_value, source_page
            )
            SELECT gen_random_uuid(), :target, fund_id, document_version_id,
                   report_period, security_name, ticker, holding_quantity,
                   registration_place, holding_type, weight, market_value,
                   source_page
            FROM fund_holdings WHERE batch_id = :source
            """,
            """
            INSERT INTO fund_allocations (
              id, batch_id, fund_id, document_version_id, report_period,
              dimension, category, weight, unit, source_page, metadata_json
            )
            SELECT gen_random_uuid(), :target, fund_id, document_version_id,
                   report_period, dimension, category, weight, unit, source_page,
                   metadata_json
            FROM fund_allocations WHERE batch_id = :source
            """,
            """
            INSERT INTO fund_scale_history (
              id, fund_id, source_batch_id, as_of_date, fund_size, currency,
              source_unit
            )
            SELECT gen_random_uuid(), fund_id, :target, as_of_date, fund_size,
                   currency, source_unit
            FROM fund_scale_history
            WHERE source_batch_id = :source AND as_of_date <= CURRENT_DATE
            """,
            """
            INSERT INTO quality_issues (
              id, batch_id, fund_id, severity, issue_type, details, status,
              metadata_json
            )
            SELECT gen_random_uuid(), :target, fund_id, severity, issue_type,
                   details, status, metadata_json
            FROM quality_issues WHERE batch_id = :source
            """,
        ):
            self.session.execute(text(statement), {"source": current.id, "target": batch.id})

        fund_ids = [stable_uuid("fund-code", row.fund_code) for row in workbook.funds]
        # 这些表是基金当前主数据属性，不携带批次；用新权威工作簿完整替换。
        for model in (FundClassification, FundRegion, FundTag):
            self.session.execute(delete(model).where(model.fund_id.in_(fund_ids)))
        self._import_hkex_master(batch, workbook)

        # FundSourceId 以 stable id 幂等存在，但 raw_row 必须跟随最新 xlsx 更新，
        # 否则币种/北上/二级类别筛选仍会读取旧快照。
        for row in workbook.funds:
            source_id = self.session.get(
                FundSourceId,
                stable_uuid("fund-source", "hkex", row.fund_id or row.fund_code),
            )
            if source_id is not None:
                source_id.primary_code = row.get("产品主级别代码") or None
                source_id.metadata_json = {
                    **(source_id.metadata_json or {}),
                    "fund_code": row.fund_code,
                    "raw_row": self._json_safe_row(row.values),
                }

        self._finish_batch(batch, activate=activate)
        self.session.flush()
        return self._summary

    def import_structured_into_batch(
        self,
        batch: DatasetBatch,
        batch_root: Path,
        fund_ids: set[Any],
    ) -> ImportSummary:
        """把任务产出的结构化结果导入已存在的批次。

        参数 batch：当前任务批次；batch_root：任务 staging 根目录；
        fund_ids：任务创建时冻结的基金 ID，用于限制名称匹配范围。
        """

        funds = list(
            self.session.scalars(
                select(Fund).where(Fund.id.in_(fund_ids))
            )
        )
        for fund in funds:
            self._fund_by_id[fund.id] = fund
            self._fund_by_name.setdefault(
                normalize_name(fund.name_cn),
                [],
            ).append(fund)
            classification = self.session.scalar(
                select(FundClassification).where(
                    FundClassification.fund_id == fund.id
                )
            )
            if classification is not None:
                self._fund_categories[fund.id] = classification.level_1

        structured_files = discover_combined_files(batch_root)
        if not structured_files:
            raise FileNotFoundError(
                f"{batch_root} 下没有可导入的结构化合并 JSON"
            )
        self._summary = ImportSummary(
            batch_id=str(batch.id),
            scope_hash=batch.scope_hash,
            warnings=[],
        )
        self._import_structured(batch, batch_root, structured_files)
        self._summary.structured_files = len(structured_files)
        return self._summary

    def _finish_batch(self, batch: DatasetBatch, *, activate: bool) -> None:
        """执行最小发布指针切换；参数 batch/activate 描述目标批次。"""

        batch.status = "published"
        batch.published_at = utc_now()
        if activate:
            self._activate_batch(batch)

    def _activate_batch(self, batch: DatasetBatch) -> None:
        """切换当前发布指针；参数 batch：已成功导入的批次。"""

        state = self.session.get(PublicationState, 1)
        if state is None:
            state = PublicationState(id=1)
            self.session.add(state)
        elif state.current_batch_id:
            current_batch = self.session.get(DatasetBatch, state.current_batch_id)
            if (
                current_batch is not None
                and current_batch.created_at
                and batch.created_at
                and current_batch.created_at > batch.created_at
            ):
                return
        state.current_batch_id = batch.id
        state.published_at = batch.published_at or utc_now()
        state.published_by = "d1-import"
        state.release_version = self.IMPORTER_VERSION

    def _import_hkex_master(
        self,
        batch: DatasetBatch,
        workbook: HKEXWorkbook,
    ) -> None:
        """导入 HKEX 权威主数据；参数 batch/workbook 为批次和解析结果。"""

        manager_rows: dict[str, HKEXFundRow] = {}
        umbrella_rows: dict[str, HKEXFundRow] = {}
        for row in workbook.funds:
            if not row.manager_name:
                raise ValueError(f"{row.fund_code} 缺少基金管理人")
            manager_rows.setdefault(row.manager_name, row)
            if row.umbrella_code:
                umbrella_rows[row.umbrella_code] = row

        for name, row in manager_rows.items():
            self.session.merge(
                Manager(
                    id=stable_uuid("manager", name),
                    name_cn=name,
                    name_en=row.get("Fund manager") or None,
                    short_name=row.manager_short_name or None,
                    ownership_type=row.get("管理人中外资") or None,
                )
            )
        for code, row in umbrella_rows.items():
            self.session.merge(
                Umbrella(
                    id=stable_uuid("umbrella", code),
                    umbrella_code=code,
                    name_cn=row.umbrella_name,
                )
            )
        self.session.flush()

        source_rows = []
        share_rows = []
        classification_rows = []
        region_rows = []
        tag_rows = []
        scale_rows = []
        funds: list[Fund] = []
        for row in workbook.funds:
            manager_id = stable_uuid("manager", row.manager_name)
            umbrella_id = (
                stable_uuid("umbrella", row.umbrella_code)
                if row.umbrella_code
                else None
            )
            fund_id = stable_uuid("fund-code", row.fund_code)
            fund = Fund(
                id=fund_id,
                fund_code=row.fund_code,
                manager_id=manager_id,
                umbrella_id=umbrella_id,
                source_batch_id=batch.id,
                name_cn=row.name_cn,
                name_en=row.name_en or None,
                domicile=row.get("基金注册地") or None,
                base_currency=row.get("基础货币") or None,
                management_style=row.get("主被动") or None,
                status=row.get("运作状态") or "UNKNOWN",
                sfc_authorized_at=self._parse_optional_date(row.get("证监会认可日期")),
                regulation_category=row.get("基金分类") or None,
                financial_year_end=row.get("财政年度终结日") or None,
                trading_frequency=row.get("交易频密程度") or None,
                custodian_name=row.get("基金保管人|受托人") or None,
                custodian_short_name=row.get("基金保管人|受托人简称") or None,
                esg_theme=row.get("ESG投资主题") or None,
                esg_strategy=row.get("ESG投资策略") or None,
                tracking_index=row.get("跟踪指数") or None,
                listing_status=row.get("上市一级分类-上市与否") or None,
                leverage_inverse_status=row.get("上市二级分类-是否杠反") or None,
                listed_share_class_status=row.get("上市三级分类-是否有上市份额")
                or None,
                api_listing_status=row.get("上市一级分类-是否上市（API）") or None,
                sixty_forty_classification=row.get("60-40") or None,
            )
            funds.append(fund)
            self._fund_by_code[row.fund_code] = fund
            self._fund_by_id[fund_id] = fund
            self._fund_by_name.setdefault(normalize_name(row.name_cn), []).append(fund)
            manager_key = (normalize_name(row.manager_short_name), normalize_name(row.name_cn))
            self._fund_by_manager_name.setdefault(manager_key, []).append(fund)
            self._fund_by_manager_name_umbrella.setdefault(
                (*manager_key, normalize_name(row.umbrella_name)),
                [],
            ).append(fund)
            self._fund_categories[fund_id] = row.category

            source_rows.append(
                {
                    "id": stable_uuid("fund-source", "hkex", row.fund_id or row.fund_code),
                    "fund_id": fund_id,
                    "source_system": "hkex-master",
                    "external_id": row.fund_id or row.fund_code,
                    "primary_code": row.get("产品主级别代码") or None,
                    "metadata_json": {
                        "fund_code": row.fund_code,
                        "raw_row": self._json_safe_row(row.values),
                    },
                }
            )
            share_class_id = stable_uuid("share-class", fund_id, row.fund_code)
            share_rows.append(
                {
                    "id": share_class_id,
                    "fund_id": fund_id,
                    "share_class_code": row.fund_code,
                    "currency": row.get("基础货币") or None,
                    "isin": None,
                    "distribution_status": None,
                }
            )
            listing_rows = self._listing_rows(row, share_class_id)
            classification_rows.append(
                {
                    "id": stable_uuid("classification", fund_id),
                    "fund_id": fund_id,
                    "level_1": row.get("资产类别一级分类") or None,
                    "level_2": row.get("资产类别二级分类") or None,
                    "level_3": row.get("资产类别三级分类") or None,
                    "level_4": row.get("资产类别四级分类") or None,
                }
            )
            for level, column in (
                ("investment_region_1", "投资地域一级分类"),
                ("investment_region_2", "投资地域二级分类"),
                ("region", "所属地域"),
            ):
                value = row.get(column)
                if value:
                    region_rows.append(
                        {
                            "id": stable_uuid("fund-region", fund_id, level, value),
                            "fund_id": fund_id,
                            "region_level": level,
                            "region_name": value,
                        }
                    )
            for tag_type, column in (
                ("competitor_category", "竞品库分类"),
                ("northbound", "北上标签"),
                ("cross_border_wealth_management_date", "跨境理财通标签更新日期"),
                ("etf_connect", "ETF互联互通标签"),
                ("etf_mutual_listing", "ETF互挂标签"),
                ("mpf", "MPF标签"),
                ("stock_code", "股份代号"),
            ):
                value = row.get(column)
                if value:
                    valid_from = (
                        self._parse_optional_date(value, patterns=("%Y%m%d",))
                        if tag_type == "cross_border_wealth_management_date"
                        else None
                    )
                    tag_rows.append(
                        {
                            "id": stable_uuid("fund-tag", fund_id, tag_type, value),
                            "fund_id": fund_id,
                            "tag_type": tag_type,
                            "tag_value": value,
                            "valid_from": valid_from,
                            "valid_to": None,
                        }
                    )
            for header, as_of_date in workbook.scale_headers:
                value = row.values.get(header)
                if value in (None, ""):
                    continue
                try:
                    numeric_value = float(value)
                except (TypeError, ValueError):
                    continue
                scale_rows.append(
                    {
                        "id": stable_uuid(
                            "fund-scale",
                            fund_id,
                            as_of_date,
                            batch.id,
                        ),
                        "fund_id": fund_id,
                        "source_batch_id": batch.id,
                        "as_of_date": as_of_date,
                        "fund_size": numeric_value,
                        "currency": "HKD",
                        "source_unit": "100m",
                    }
                )

        for fund in funds:
            self.session.merge(fund)
        self.session.flush()
        self._bulk_insert(FundSourceId, source_rows)
        self._bulk_insert(ShareClass, share_rows)
        listing_values = [
            row
            for fund in workbook.funds
            for row in self._listing_rows(
                fund,
                stable_uuid(
                    "share-class",
                    stable_uuid("fund-code", fund.fund_code),
                    fund.fund_code,
                ),
            )
        ]
        self._bulk_insert(FundListing, listing_values)
        self._bulk_insert(FundClassification, classification_rows)
        self._bulk_insert(FundRegion, region_rows)
        self._bulk_insert(FundTag, tag_rows)
        # 主数据规模是同一日期的权威值；综合资料库导入前会先复制旧快照，
        # 因此这里必须更新同批次同日期的旧结构化候选，而不是静默跳过。
        self._bulk_upsert_scale(scale_rows)

        manager_short_by_code = {
            row.fund_code: row.manager_short_name for row in workbook.funds
        }
        rename_rows = []
        for rename in workbook.renames:
            fund = self._fund_by_code.get(rename.fund_code)
            if fund is None:
                continue
            historical_key = normalize_name(rename.old_name)
            self._fund_by_historical_name.setdefault(historical_key, []).append(fund)
            self._fund_by_manager_historical_name.setdefault(
                (
                    normalize_name(manager_short_by_code.get(rename.fund_code, "")),
                    historical_key,
                ),
                [],
            ).append(fund)
            rename_rows.append(
                {
                    "id": stable_uuid(
                        "fund-rename",
                        rename.fund_code,
                        rename.old_name,
                        rename.new_name,
                        rename.detected_at,
                    ),
                    "fund_id": fund.id,
                    "source_batch_id": batch.id,
                    "old_name": rename.old_name,
                    "new_name": rename.new_name,
                    "detected_at": detected_at_utc(rename.detected_at),
                }
            )
        self._bulk_insert(FundNameHistory, rename_rows)

        self._summary.managers = len(manager_rows)
        self._summary.umbrellas = len(umbrella_rows)
        self._summary.funds = len(funds)
        self._summary.source_ids = len(source_rows)
        self._summary.share_classes = len(share_rows)
        self._summary.listings = len(listing_values)
        self._summary.classifications = len(classification_rows)
        self._summary.regions = len(region_rows)
        self._summary.tags = len(tag_rows)
        self._summary.scale_history = len(scale_rows)
        self._summary.name_history = len(rename_rows)

    def _import_crawler_sources(
        self,
        batch: DatasetBatch,
        rows: list[CrawlerReportRow],
    ) -> None:
        """导入 crawler 链接与运行方式；参数 batch/rows 为补充数据。"""

        report_rows = []
        for row in rows:
            matches, method = self._match_crawler_row(row)
            fund = matches[0] if len(matches) == 1 else None
            match_status = (
                "matched"
                if fund is not None
                else ("ambiguous" if len(matches) > 1 else "unmatched")
            )
            source_key = f"crawler-list-v1:{row.sheet_name}:{row.excel_row}"
            metadata = {
                "fund_code": fund.fund_code if fund else None,
                "candidate_codes": [item.fund_code for item in matches],
                "source_serial": row.source_serial,
            }
            download_sources = infer_download_sources(
                manager_name=row.manager_name,
                monthly_report_url=row.monthly_report_url,
                fallback_report_url=row.fallback_report_url,
                run_command=row.run_command,
            )
            if download_sources:
                metadata[DOWNLOAD_CANDIDATES_KEY] = serialize_download_sources(
                    download_sources
                )
            report_rows.append(
                {
                    "id": stable_uuid("fund-report-source", source_key),
                    "fund_id": fund.id if fund else None,
                    "source_system": "crawler-list-v1",
                    "source_key": source_key,
                    "source_sheet": row.sheet_name,
                    "source_row": row.excel_row,
                    "manager_name_source": row.manager_name or None,
                    "fund_name_source": row.fund_name,
                    "umbrella_name_source": row.umbrella_name or None,
                    "monthly_report_url": row.monthly_report_url or None,
                    "fallback_report_url": row.fallback_report_url or None,
                    "semiannual_report_url": row.semiannual_report_url or None,
                    "semiannual_run_command": row.semiannual_run_command or None,
                    "annual_report_url": row.annual_report_url or None,
                    "annual_run_command": row.annual_run_command or None,
                    "run_command": row.run_command or None,
                    "match_status": match_status,
                    "match_method": method or None,
                    "match_confidence": (
                        "high"
                        if method
                        in {
                            "serial_exact",
                            "manager_name_umbrella",
                            "manager_historical_name",
                            "historical_name",
                        }
                        else "medium"
                        if fund
                        else None
                    ),
                    "is_active": bool(
                        row.monthly_report_url
                        or row.fallback_report_url
                        or row.semiannual_report_url
                        or row.semiannual_run_command
                        or row.annual_report_url
                        or row.annual_run_command
                        or row.run_command
                    ),
                    "metadata_json": metadata,
                }
            )
            if fund is not None:
                self._summary.matched_report_sources += 1
                if row.monthly_report_url:
                    self._fund_ids_by_report_url.setdefault(
                        row.monthly_report_url,
                        [],
                    ).append(fund.id)
            else:
                self._summary.unmatched_report_sources += 1
                self._add_quality_issue(
                    batch,
                    None,
                    severity="warning",
                    issue_type="crawler_source_unmatched",
                    details=(
                        f"{row.sheet_name}:{row.excel_row} "
                        f"“{row.fund_name}”无法唯一匹配 HKEX 主数据"
                    ),
                    metadata={
                        "candidate_codes": [item.fund_code for item in matches],
                        "source_key": source_key,
                    },
                )
        if report_rows:
            for start in range(0, len(report_rows), 500):
                statement = pg_insert(FundReportSource).values(
                    report_rows[start : start + 500]
                )
                self.session.execute(
                    statement.on_conflict_do_update(
                        index_elements=[FundReportSource.source_key],
                        set_={
                            "fund_id": statement.excluded.fund_id,
                            # 平台爬虫补全直接写数据库；重新导入旧 Excel 时，
                            # 空值不得覆盖已经补全的可靠来源。
                            "monthly_report_url": func.coalesce(
                                statement.excluded.monthly_report_url,
                                FundReportSource.monthly_report_url,
                            ),
                            "fallback_report_url": func.coalesce(
                                statement.excluded.fallback_report_url,
                                FundReportSource.fallback_report_url,
                            ),
                            "semiannual_report_url": func.coalesce(
                                statement.excluded.semiannual_report_url,
                                FundReportSource.semiannual_report_url,
                            ),
                            "semiannual_run_command": func.coalesce(
                                statement.excluded.semiannual_run_command,
                                FundReportSource.semiannual_run_command,
                            ),
                            "annual_report_url": func.coalesce(
                                statement.excluded.annual_report_url,
                                FundReportSource.annual_report_url,
                            ),
                            "annual_run_command": func.coalesce(
                                statement.excluded.annual_run_command,
                                FundReportSource.annual_run_command,
                            ),
                            "run_command": func.coalesce(
                                statement.excluded.run_command,
                                FundReportSource.run_command,
                            ),
                            "match_status": statement.excluded.match_status,
                            "match_method": statement.excluded.match_method,
                            "match_confidence": statement.excluded.match_confidence,
                            "is_active": or_(
                                statement.excluded.is_active,
                                FundReportSource.is_active,
                            ),
                            "metadata_json": FundReportSource.metadata_json.op("||")(
                                statement.excluded.metadata_json
                            ),
                            "updated_at": func.now(),
                        },
                    )
                )
        self._summary.report_sources = len(report_rows)

    def _match_crawler_row(
        self,
        row: CrawlerReportRow,
    ) -> tuple[list[Fund], str]:
        """用代码、管理人、名称和伞匹配 crawler 行；参数 row：补充行。"""

        direct = self._fund_by_code.get(row.source_serial)
        if direct is None:
            # 部分页表把“伞名称|基金名称”等复合文本放在序号列。
            tokens = [
                token
                for token in re.findall(r"[A-Za-z0-9]{6}", row.source_serial)
                if token in self._fund_by_code
            ]
            if len(set(tokens)) == 1:
                direct = self._fund_by_code[tokens[0]]
        if direct is not None:
            return [direct], "serial_exact"

        manager = normalize_name(row.manager_name)
        fund_name = normalize_name(row.fund_name)
        umbrella = normalize_name(row.umbrella_name)
        exact_umbrella = self._fund_by_manager_name_umbrella.get(
            (manager, fund_name, umbrella),
            [],
        )
        if len(exact_umbrella) == 1:
            return exact_umbrella, "manager_name_umbrella"
        exact = self._fund_by_manager_name.get((manager, fund_name), [])
        if len(exact) == 1:
            return exact, "manager_name"
        historical_exact = self._fund_by_manager_historical_name.get(
            (manager, fund_name),
            [],
        )
        if len(historical_exact) == 1:
            return historical_exact, "manager_historical_name"
        name_only = self._fund_by_name.get(fund_name, [])
        if len(name_only) == 1:
            return name_only, "name"
        historical_name = self._fund_by_historical_name.get(fund_name, [])
        if len(historical_name) == 1:
            return historical_name, "historical_name"
        return exact_umbrella or exact or name_only or historical_exact or historical_name, ""

    @staticmethod
    def _json_safe_row(values: dict[str, Any]) -> dict[str, Any]:
        """把原始 Excel 行转换为可写入 JSONB 的标量；参数 values：原始行。"""

        return {
            key: (
                value.isoformat()
                if isinstance(value, (date,))
                else value
            )
            for key, value in values.items()
        }

    @staticmethod
    def _parse_optional_date(
        value: object,
        *,
        patterns: tuple[str, ...] = ("%Y-%m-%d", "%Y%m%d"),
    ):
        """解析可选日期；参数 value/patterns 为原值和格式集合。"""

        if patterns == ("%Y-%m-%d", "%Y%m%d"):
            from app.importers.hkex_data import parse_hkex_date

            return parse_hkex_date(value)
        text = normalize_text(value)
        for pattern in patterns:
            try:
                return datetime.strptime(text, pattern).date()
            except ValueError:
                continue
        return None

    @staticmethod
    def _listing_rows(
        row: HKEXFundRow,
        share_class_id: Any,
    ) -> list[dict[str, Any]]:
        """构造股份代号上市记录；参数 row/share_class_id 为基金和份额。"""

        stock_codes = re.findall(r"\d{5,6}", row.get("股份代号"))
        return [
            {
                "id": stable_uuid("fund-listing", share_class_id, "HKEX", code),
                "share_class_id": share_class_id,
                "exchange": "HKEX",
                "stock_code": code,
                "listing_type": row.get("上市一级分类-上市与否") or None,
                "trading_currency": row.get("基础货币") or None,
            }
            for code in dict.fromkeys(stock_codes)
        ]

    def _bulk_insert(self, model: Any, values: list[dict[str, Any]]) -> None:
        """幂等批量插入 ORM 表；参数 model/values 为模型和行数据。"""

        if not values:
            return
        for start in range(0, len(values), 1000):
            statement = pg_insert(model).values(values[start : start + 1000])
            self.session.execute(statement.on_conflict_do_nothing())

    def _bulk_upsert_scale(self, values: list[dict[str, Any]]) -> None:
        """按基金、日期和批次覆盖规模；参数 values 为规模历史行。"""

        if not values:
            return
        for start in range(0, len(values), 1000):
            statement = pg_insert(FundScaleHistory).values(values[start : start + 1000])
            self.session.execute(
                statement.on_conflict_do_update(
                    constraint="uq_fund_scale_batch",
                    set_={
                        "fund_size": statement.excluded.fund_size,
                        "currency": statement.excluded.currency,
                        "source_unit": statement.excluded.source_unit,
                    },
                )
            )

    def _upsert(
        self,
        model: Any,
        values: dict[str, Any],
        *,
        constraint: str,
        update_columns: tuple[str, ...],
    ) -> None:
        """按业务唯一键插入或更新；参数 model/values/constraint/columns 为数据。"""

        statement = pg_insert(model).values(values)
        self.session.execute(
            statement.on_conflict_do_update(
                constraint=constraint,
                set_={
                    column: getattr(statement.excluded, column)
                    for column in update_columns
                },
            )
        )

    def _resolve_fund(
        self,
        record: dict[str, Any],
        category: str,
        source_url: str | None,
    ) -> Fund | None:
        """查找结构化记录对应的基金；参数 record/category/source_url 为匹配上下文。"""

        if source_url:
            linked = self._fund_ids_by_report_url.get(source_url, [])
            if len(linked) == 1 and linked[0] in self._fund_by_id:
                return self._fund_by_id[linked[0]]
        candidates = self._fund_by_name.get(normalize_name(record.get("fund_name")), [])
        if len(candidates) == 1:
            return candidates[0]
        matching = [
            fund for fund in candidates if self._fund_categories.get(fund.id) == category
        ]
        return matching[0] if len(matching) == 1 else None

    def _combined_meta_path(self, combined_path: Path, record: dict[str, Any]) -> Path | None:
        """定位结构化记录对应 meta；参数 combined_path/record 为输入记录。"""

        source_name = Path(normalize_text(record.get("_source_json"))).name
        if not source_name:
            return None
        per_pdf_dir = combined_path.parent.parent / "per_pdf"
        direct = per_pdf_dir / source_name
        if direct.is_file():
            candidate = direct.with_suffix(".meta.json")
            if candidate.is_file():
                return candidate
        for path in per_pdf_dir.glob("*.meta.json"):
            if path.name.startswith(source_name.rsplit(".", 2)[0]):
                return path
        return None

    def _pdf_lookup(self, batch_root: Path) -> dict[str, Path]:
        """建立批次 PDF 文件名索引；参数 batch_root：批次目录。"""

        root = batch_root / "factsheet"
        return {path.name: path for path in root.rglob("*.pdf")} if root.exists() else {}

    def _download_url_map(self, batch_root: Path) -> dict[tuple[str, str], str]:
        """读取下载报告中的来源 URL；参数 batch_root：批次目录。"""

        result: dict[tuple[str, str], str] = {}
        for report in sorted((batch_root / "reports").glob("download_run_report_*.json")):
            try:
                payload = json.loads(report.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for item in payload.get("results") or []:
                if item.get("source_url"):
                    result[
                        (
                            normalize_name(item.get("category")),
                            normalize_name(item.get("fund_name")),
                        )
                    ] = str(item["source_url"])
        return result

    def _import_structured(
        self,
        batch: DatasetBatch,
        batch_root: Path,
        combined_files: list[Path],
    ) -> None:
        """导入结构化合并文件；参数 batch/batch_root/files 描述数据源。"""

        pdf_lookup = self._pdf_lookup(batch_root)
        source_urls = self._download_url_map(batch_root)
        for combined_path in combined_files:
            category = infer_batch_category(combined_path)
            payload = json.loads(combined_path.read_text(encoding="utf-8"))
            records = payload.get("records") or []
            for record in records:
                self._summary.structured_records += 1
                source_url = source_urls.get(
                    (normalize_name(category), normalize_name(record.get("fund_name")))
                )
                fund = self._resolve_fund(record, category, source_url)
                if fund is None:
                    self._summary.skipped_records += 1
                    self._add_quality_issue(
                        batch,
                        None,
                        severity="warning",
                        issue_type="ambiguous_structured_fund",
                        details=(
                            f"{combined_path.name} 中基金"
                            f"“{record.get('fund_name') or '未命名'}”无法唯一匹配主数据"
                        ),
                        metadata={"category": category, "source": str(combined_path)},
                    )
                    continue
                self._import_record(
                    batch,
                    batch_root,
                    combined_path,
                    category,
                    fund,
                    record,
                    pdf_lookup,
                    source_urls,
                )
                # merge 在 autoflush=False 下不会立即发现前一条同 ID 记录；
                # 每条基金处理完即落 flush，保证重复结构化结果按唯一键更新。
                self.session.flush()

    def _import_record(
        self,
        batch: DatasetBatch,
        batch_root: Path,
        combined_path: Path,
        category: str,
        fund: Fund,
        record: dict[str, Any],
        pdf_lookup: dict[str, Path],
        source_urls: dict[tuple[str, str], str],
    ) -> None:
        """导入一只基金的单条结构化记录；参数描述记录与来源上下文。"""

        report_period = parse_report_month(record.get("report_date"))
        if report_period is None:
            self._add_quality_issue(
                batch,
                fund.id,
                severity="error",
                issue_type="invalid_report_month",
                details=f"报告日期无法解析：{record.get('report_date')!r}",
                metadata={"source": str(combined_path)},
            )
            return

        meta_path = self._combined_meta_path(combined_path, record)
        meta = self._read_json(meta_path)
        source_pdf = self._resolve_source_pdf(meta, record, batch_root, pdf_lookup)
        source_json = self._resolve_source_json(combined_path, record)
        report_kind = self._detect_report_kind(
            source_pdf,
            record,
            report_period,
        )
        review_status = normalize_text(meta.get("status")) or "review"
        confidence = "high" if review_status == "ok" else "review"
        source_category = normalize_name(category)
        fund_name_key = normalize_name(fund.name_cn)
        source_url = source_urls.get((source_category, fund_name_key))

        document, version = self._ensure_document(
            batch,
            fund,
            category,
            source_pdf,
            source_json,
            report_period,
            source_url,
            document_type=report_kind,
        )
        # 发布任务会先克隆上一批次；同一 PDF SHA 重新结构化时，克隆批次
        # 里可能已经有旧月份/旧模型结果。按基金+文档版本清理后再导入，
        # 避免同一来源在详情页重复出现，并让精确报告日期替换旧月初日期。
        for model in (FundMetric, FundHolding, FundAllocation):
            self.session.execute(
                delete(model).where(
                    model.batch_id == batch.id,
                    model.fund_id == fund.id,
                    model.document_version_id == version.id,
                )
            )
        page_count = self._pdf_page_count(source_pdf)
        statement = pg_insert(DocumentSubject).values(
            id=stable_uuid(
                "document-subject",
                version.id,
                fund.id,
                1,
                page_count,
            ),
            document_version_id=version.id,
            fund_id=fund.id,
            subject_name=fund.name_cn,
            start_page=1,
            end_page=page_count,
            match_method="structured_import",
            match_score=1,
        )
        self.session.execute(
            statement.on_conflict_do_nothing(
                constraint="uq_document_subject_range"
            )
        )
        self._summary.document_subjects += 1

        if source_pdf is None:
            self._add_quality_issue(
                batch,
                fund.id,
                severity="warning",
                issue_type="source_pdf_missing",
                details=f"{fund.name_cn} 的结构化记录未找到对应 PDF",
                metadata={"source_json": str(source_json or combined_path)},
            )

        self._import_metrics(
            batch,
            fund,
            version,
            report_period,
            record,
            confidence,
            category,
        )
        self._import_holdings(batch, fund, version, report_period, record)
        self._import_allocations(batch, fund, version, report_period, record)
        if report_kind in {"semiannual", "annual"}:
            self._import_derived_portfolio_metrics(
                batch,
                fund,
                version,
                report_period,
                record,
                category,
                confidence,
            )
        self._import_scale(batch, fund, report_period, record)

        validation_issues = (meta.get("_meta") or {}).get("validation_issues") or []
        for issue in validation_issues:
            text = normalize_text(issue)
            if not text:
                continue
            severity = (
                "error"
                if any(word in text for word in ("异常", "错配", "疑似"))
                else "warning"
            )
            self._add_quality_issue(
                batch,
                fund.id,
                severity=severity,
                issue_type="structured_validation",
                details=text,
                metadata={"source_json": str(source_json or combined_path)},
            )
        if review_status == "failed":
            self._add_quality_issue(
                batch,
                fund.id,
                severity="blocker",
                issue_type="structured_extraction_failed",
                details=f"{fund.name_cn} 的结构化任务状态为 failed",
                metadata={"source_json": str(source_json or combined_path)},
            )

    def _ensure_document(
        self,
        batch: DatasetBatch,
        fund: Fund,
        category: str,
        source_pdf: Path | None,
        source_json: Path | None,
        report_period: date,
        source_url: str | None,
        document_type: str | None = None,
    ) -> tuple[Document, DocumentVersion]:
        """创建或复用文档版本；参数描述基金、文件与报告期。"""

        if source_pdf and source_pdf.is_file():
            digest = sha256_file(source_pdf)
            document_type = document_type or "monthly"
            mime_type = "application/pdf"
            file_path = relative_path(source_pdf, self.project_root)
            source_id = f"sha256:{digest}"
            size_bytes = source_pdf.stat().st_size
        else:
            fallback = source_json
            if fallback and fallback.is_file():
                digest = sha256_file(fallback)
                source_id = f"structured-json:{digest}"
            else:
                digest = hashlib.sha256(
                    f"{fund.id}:{fund.name_cn}:{report_period}".encode("utf-8")
                ).hexdigest()
                source_id = f"missing-source:{digest}"
            document_type = document_type or "monthly_structured"
            mime_type = "application/json" if fallback else None
            file_path = (
                relative_path(fallback, self.project_root) if fallback else None
            )
            size_bytes = fallback.stat().st_size if fallback and fallback.is_file() else None

        document_id = stable_uuid("document", source_id)
        document = self.session.get(Document, document_id)
        if document is None:
            document = Document(
                id=document_id,
                document_type=document_type,
                umbrella_id=fund.umbrella_id,
                source_id=source_id,
                first_seen_at=utc_now(),
            )
            self.session.add(document)
            self._summary.documents += 1
        elif document_type:
            document.document_type = document_type

        existing_version = self.session.scalar(
            select(DocumentVersion).where(DocumentVersion.sha256 == digest)
        )
        if existing_version is None:
            existing_version = DocumentVersion(
                id=stable_uuid("document-version", document_id, digest),
                document_id=document_id,
                batch_id=batch.id,
                source_url=source_url,
                report_date=report_period,
                sha256=digest,
                mime_type=mime_type,
                size_bytes=size_bytes,
                file_path=file_path,
            )
            self.session.add(existing_version)
            self.session.flush()
            self._summary.document_versions += 1
        else:
            if source_url and not existing_version.source_url:
                existing_version.source_url = source_url
            if file_path and not existing_version.file_path:
                existing_version.file_path = file_path
            # 同一 SHA 的旧任务可能只识别到月份（例如 2026-09-01），
            # 后续 MinerU/原生文本已识别出 PDF 原文的精确截止日时，应
            # 回写文档版本日期。否则数据卡片仍会显示旧的月初日期。
            if report_period and existing_version.report_date != report_period:
                existing_version.report_date = report_period
        return document, existing_version

    @staticmethod
    def _detect_report_kind(
        source_pdf: Path | None,
        record: dict[str, Any],
        report_period: date,
    ) -> str:
        """从结构化记录、文件名和 PDF 首页识别报告类型。"""

        hints = [
            normalize_text(record.get("report_kind")),
            normalize_text(record.get("document_type")),
            normalize_text(record.get("report_title")),
            source_pdf.name if source_pdf else "",
        ]
        if source_pdf and source_pdf.is_file():
            try:
                reader = PdfReader(str(source_pdf))
                # Cover/目录页足以识别“半年报/年报”，避免为每条记录
                # 扫描完整报告；补充来源通常已经先切片成单基金 PDF。
                hints.extend(
                    (page.extract_text() or "")
                    for page in reader.pages[: min(6, len(reader.pages))]
                )
            except Exception:  # noqa: BLE001
                pass
        return detect_report_kind("\n".join(hints), report_period, source_pdf.name if source_pdf else None)

    @staticmethod
    def _holding_items(record: dict[str, Any]) -> list[dict[str, Any]]:
        """把不同结构化模型的持仓容器统一为列表。"""

        raw = record.get("top_holdings") or []
        if isinstance(raw, dict):
            raw = raw.get("items") or raw.get("holdings") or []
        return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []

    @staticmethod
    def _holding_weight(item: dict[str, Any]) -> float | None:
        """读取持仓比例，兼容模型返回的常见字段名。"""

        for key in ("weight_percent", "weight", "percentage", "portfolio_weight"):
            value = item.get(key)
            if value in (None, ""):
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return None

    @staticmethod
    def _holding_bucket(item: dict[str, Any]) -> str | None:
        """按原文类型/名称把持仓归入 equity、debt 或未知。"""

        text = " ".join(
            normalize_text(item.get(key))
            for key in (
                "holding_type",
                "asset_type",
                "security_type",
                "type",
                "category",
                "name",
                "holding_name",
            )
        ).casefold()
        if any(token in text for token in ("equity", "stock", "share", "股票", "股份", "权益")):
            return "equity"
        if any(
            token in text
            for token in (
                "bond",
                "debt",
                "fixed income",
                "treasury",
                "bill",
                "note",
                "deposit",
                "certificate",
                "存款",
                "存款证",
                "债",
                "票据",
                "定期",
                "货币市场",
                "money market",
            )
        ):
            return "debt"
        return None

    @staticmethod
    def _allocation_items(raw: Any, *, name_keys: tuple[str, ...]) -> list[dict[str, Any]]:
        """把列表/字典形式的资产配置转为统一的名称与比例字段。"""

        if isinstance(raw, dict):
            raw = [
                {"name": name, "weight_percent": value}
                for name, value in raw.items()
            ]
        if not isinstance(raw, list):
            return []
        normalized: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = next((item.get(key) for key in name_keys if item.get(key) not in (None, "")), None)
            value = next(
                (
                    item.get(key)
                    for key in ("weight_percent", "weight", "percentage", "value", "percent")
                    if item.get(key) not in (None, "")
                ),
                None,
            )
            if name in (None, "") or value in (None, ""):
                continue
            normalized.append({**item, "name": name, "weight_percent": value})
        return normalized

    def _import_derived_portfolio_metrics(
        self,
        batch: DatasetBatch,
        fund: Fund,
        version: DocumentVersion,
        report_period: date,
        record: dict[str, Any],
        category: str,
        confidence: str,
    ) -> None:
        """为半年报/年报补齐股债比；已有原文值优先保留。"""

        explicit_equity = record.get("equity_weight_percent")
        explicit_debt = record.get("debt_weight_percent")
        ratio = record.get("equity_debt_ratio")
        if explicit_equity in (None, "") or explicit_debt in (None, ""):
            totals = {"equity": 0.0, "debt": 0.0}
            for item in self._holding_items(record):
                bucket = self._holding_bucket(item)
                weight = self._holding_weight(item)
                if bucket and weight is not None:
                    totals[bucket] += weight
            if totals["equity"] == 0 and totals["debt"] == 0:
                money_metrics = record.get("money_market_metrics")
                money_metrics = money_metrics if isinstance(money_metrics, dict) else {}
                raw_allocations = (
                    money_metrics.get("asset_type_allocation")
                    or record.get("asset_type_allocation")
                    or record.get("asset_allocation")
                    or record.get("portfolio_structure")
                    or record.get("asset_type_distribution")
                    or []
                )
                for item in self._allocation_items(
                    raw_allocations,
                    name_keys=("type", "asset_type", "category", "name", "label"),
                ):
                    bucket = self._holding_bucket(item)
                    weight = self._holding_weight(item)
                    if bucket and weight is not None:
                        totals[bucket] += weight
            # 有些补充来源的合并目录使用 ``money_market`` 英文名，另一些
            # 使用“货币”中文分类；同时保留基金主数据名称作为兜底，避免
            # 报告本身没有逐笔类型字段时漏掉货币基金的 0:100 结论。
            category_hint = " ".join(
                (
                    normalize_text(category),
                    normalize_text(fund.name_cn),
                    normalize_text(fund.name_en),
                )
            ).casefold()
            is_money_market = any(
                token in category_hint
                for token in ("货币", "money market", "money_market")
            )
            if totals["equity"] == 0 and totals["debt"] == 0 and is_money_market:
                # 货币基金没有权益仓位；若原文没有披露单笔类型，按基金
                # 类别给出保守的 0:100，并保留字段来源为 derived。
                totals["debt"] = 100.0
            classified_total = totals["equity"] + totals["debt"]
            if classified_total:
                explicit_equity = totals["equity"] / classified_total * 100
                explicit_debt = totals["debt"] / classified_total * 100
                ratio = f"{explicit_equity:.2f}:{explicit_debt:.2f}"
        try:
            equity_value = float(explicit_equity) if explicit_equity not in (None, "") else None
        except (TypeError, ValueError):
            equity_value = None
        try:
            debt_value = float(explicit_debt) if explicit_debt not in (None, "") else None
        except (TypeError, ValueError):
            debt_value = None
        metadata = {
            "derived": True,
            "basis": "semiannual_or_annual_holdings",
            "ratio_basis": "classified_asset_allocation_excluding_other_net_assets",
        }
        if equity_value is not None:
            self._merge_metric(
                batch, fund, version, report_period, "equity_weight_percent",
                numeric_value=equity_value, unit="percent", confidence=confidence,
                category=category, metadata=metadata,
            )
        if debt_value is not None:
            self._merge_metric(
                batch, fund, version, report_period, "debt_weight_percent",
                numeric_value=debt_value, unit="percent", confidence=confidence,
                category=category, metadata=metadata,
            )
        if ratio in (None, "") and equity_value is not None and debt_value is not None:
            ratio = f"{equity_value:.2f}:{debt_value:.2f}"
        if ratio not in (None, ""):
            self._merge_metric(
                batch, fund, version, report_period, "equity_debt_ratio",
                text_value=str(ratio), unit="ratio", confidence=confidence,
                category=category, metadata=metadata,
            )

    def _import_metrics(
        self,
        batch: DatasetBatch,
        fund: Fund,
        version: DocumentVersion,
        report_period: date,
        record: dict[str, Any],
        confidence: str,
        category: str,
    ) -> None:
        """导入结构化标量指标；参数描述基金、文档与记录。"""

        numeric_metrics = {
            "fund_size_hkd_100m": ("HKD_100M", record.get("fund_size_hkd_100m")),
            "portfolio_duration_years": (
                "years",
                record.get("portfolio_duration_years"),
            ),
            "portfolio_ytm_percent": (
                "percent",
                record.get("portfolio_ytm_percent"),
            ),
            "top_holdings_total_percent": (
                "percent",
                record.get("top_holdings_total_percent"),
            ),
            "equity_weight_percent": ("percent", record.get("equity_weight_percent")),
            "debt_weight_percent": ("percent", record.get("debt_weight_percent")),
            "dividend_per_unit": (None, record.get("dividend_per_unit")),
            "annualized_dividend_yield_percent": (
                "percent",
                record.get("annualized_dividend_yield_percent"),
            ),
        }
        money_metrics = record.get("money_market_metrics") or {}
        if isinstance(money_metrics, dict):
            for metric_name in (
                "seven_day_yield_percent",
                "annualized_yield_percent",
                "weighted_average_maturity_days",
                "weighted_average_life_days",
                "daily_liquid_assets_percent",
                "weekly_liquid_assets_percent",
            ):
                unit = "days" if metric_name.endswith("_days") else "percent"
                numeric_metrics[metric_name] = (unit, money_metrics.get(metric_name))
        # Candidate sources such as semi-annual financial statements may expose
        # audited statement values that are outside the monthly factsheet schema.
        # Keep them as traceable metrics instead of dropping them at import time.
        additional_metrics = record.get("additional_metrics") or {}
        if isinstance(additional_metrics, dict):
            for metric_name, raw_value in additional_metrics.items():
                if isinstance(raw_value, dict):
                    unit = raw_value.get("unit")
                    value = raw_value.get("value")
                else:
                    unit = None
                    value = raw_value
                normalized_name = str(metric_name)
                if str(unit or "").upper() in {"HKD", "HKD_M", "HKD_MILLION"}:
                    try:
                        value = float(value) / 100
                        unit = "HKD_100M"
                    except (TypeError, ValueError):
                        pass
                elif str(unit or "").upper() in {"USD", "USD_M", "USD_MILLION"}:
                    try:
                        value = float(value) / 1_000_000 if str(unit).upper() == "USD" else float(value)
                        unit = "USD_M"
                    except (TypeError, ValueError):
                        pass
                numeric_metrics[normalized_name] = (unit, value)
        original_size = record.get("fund_size_original") or record.get("fund_size_amount")
        if isinstance(original_size, dict):
            currency = str(original_size.get("currency") or "").upper()
            amount = original_size.get("amount", original_size.get("value"))
            try:
                if currency == "HKD":
                    numeric_metrics["fund_size_hkd_100m"] = ("HKD_100M", float(amount) / 100_000_000)
                elif currency == "USD":
                    numeric_metrics["fund_size_usd_million"] = ("USD_M", float(amount) / 1_000_000)
            except (TypeError, ValueError):
                pass
        dividend = record.get("dividend") or {}
        for key, unit in (
            ("dividend_per_unit", None),
            ("annualized_dividend_yield_percent", "percent"),
        ):
            if numeric_metrics[key][1] is None and dividend.get(key) is not None:
                numeric_metrics[key] = (unit, dividend.get(key))

        text_metrics = {
            "inception_date": record.get("inception_date"),
            "listing_date": record.get("listing_date"),
            "credit_rating": record.get("credit_rating"),
            "dividend_share_class": record.get("dividend_share_class")
            or dividend.get("share_class"),
            "dividend_currency": record.get("dividend_currency")
            or dividend.get("currency"),
            "dividend_frequency": record.get("dividend_frequency")
            or dividend.get("frequency"),
            "dividend_note": record.get("dividend_note") or dividend.get("note"),
            "investment_objective": record.get("investment_objective") or record.get("investment_objective_text"),
            "investment_strategy": record.get("investment_strategy") or record.get("investment_strategy_text"),
            "portfolio_ytm_note": record.get("portfolio_ytm_note") or record.get("ytm_note"),
        }
        field_notes = record.get("field_notes") or {}
        if isinstance(field_notes, dict):
            for field_name, note in field_notes.items():
                if note not in (None, ""):
                    text_metrics[f"{field_name}_note"] = note
        for metric_name, (unit, value) in numeric_metrics.items():
            self._merge_metric(
                batch,
                fund,
                version,
                report_period,
                metric_name,
                numeric_value=value,
                unit=unit,
                confidence=confidence,
                category=category,
            )
        for metric_name, value in text_metrics.items():
            self._merge_metric(
                batch,
                fund,
                version,
                report_period,
                metric_name,
                text_value=value,
                confidence=confidence,
                category=category,
            )

    def _merge_metric(
        self,
        batch: DatasetBatch,
        fund: Fund,
        version: DocumentVersion,
        report_period: date,
        metric_name: str,
        *,
        numeric_value: Any = None,
        text_value: Any = None,
        unit: str | None = None,
        confidence: str,
        category: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """合并单个指标；参数描述指标来源和值。"""

        if numeric_value in (None, "") and text_value in (None, ""):
            return
        value: float | None = None
        if numeric_value not in (None, ""):
            try:
                value = float(numeric_value)
            except (TypeError, ValueError):
                text_value = str(numeric_value)
        self._upsert(
            FundMetric,
            {
                "id": stable_uuid(
                    "fund-metric",
                    batch.id,
                    fund.id,
                    version.id,
                    report_period,
                    metric_name,
                ),
                "fund_id": fund.id,
                "batch_id": batch.id,
                "document_version_id": version.id,
                "report_period": report_period,
                "metric_name": metric_name,
                "numeric_value": value,
                "text_value": normalize_text(text_value) or None,
                "unit": unit,
                "confidence": confidence,
                "review_status": "pending",
                "source_page": None,
                "metadata_json": {"category": category, **(metadata or {})},
            },
            constraint="uq_fund_metric_value",
            update_columns=(
                "numeric_value",
                "text_value",
                "unit",
                "confidence",
                "review_status",
                "source_page",
                "metadata_json",
            ),
        )
        self._summary.metrics += 1

    def _import_holdings(
        self,
        batch: DatasetBatch,
        fund: Fund,
        version: DocumentVersion,
        report_period: date,
        record: dict[str, Any],
    ) -> None:
        """导入持仓；参数描述基金、文档期和结构化记录。"""

        for index, item in enumerate(self._holding_items(record), start=1):
            if not isinstance(item, dict):
                continue
            name = normalize_text(item.get("name") or item.get("holding_name"))
            if not name:
                continue
            ticker = normalize_text(item.get("ticker")) or None
            self._upsert(
                FundHolding,
                {
                    "id": stable_uuid(
                        "fund-holding",
                        batch.id,
                        fund.id,
                        version.id,
                        report_period,
                        name,
                        ticker,
                        index,
                    ),
                    "fund_id": fund.id,
                    "batch_id": batch.id,
                    "document_version_id": version.id,
                    "report_period": report_period,
                    "security_name": name,
                    "ticker": ticker,
                    "holding_quantity": item.get("holding_quantity")
                    or item.get("quantity"),
                    "registration_place": normalize_text(item.get("registration_place")) or None,
                    "holding_type": normalize_text(item.get("holding_type")) or None,
                    "weight": item.get("weight_percent"),
                    "market_value": item.get("market_value"),
                    "source_page": None,
                },
                constraint="uq_fund_holding_value",
                update_columns=(
                    "holding_quantity",
                    "registration_place",
                    "holding_type",
                    "weight",
                    "market_value",
                    "source_page",
                ),
            )
            self._summary.holdings += 1

    def _import_allocations(
        self,
        batch: DatasetBatch,
        fund: Fund,
        version: DocumentVersion,
        report_period: date,
        record: dict[str, Any],
    ) -> None:
        """导入地区、行业和久期分布；参数描述基金与记录。"""

        money_metrics = record.get("money_market_metrics")
        money_metrics = money_metrics if isinstance(money_metrics, dict) else {}
        asset_type_raw = (
            money_metrics.get("asset_type_allocation")
            or record.get("asset_type_allocation")
            or record.get("asset_allocation")
            or record.get("portfolio_structure")
            or record.get("asset_type_distribution")
            or []
        )
        asset_type_items = self._allocation_items(
            asset_type_raw,
            name_keys=("type", "asset_type", "category", "name", "label"),
        )
        if not asset_type_items:
            by_bucket: dict[str, float] = {}
            for holding in self._holding_items(record):
                bucket = self._holding_bucket(holding)
                weight = self._holding_weight(holding)
                if bucket and weight is not None:
                    label = "股票/权益" if bucket == "equity" else "债券/固定收益"
                    by_bucket[label] = by_bucket.get(label, 0.0) + weight
            asset_type_items = [
                {"name": name, "weight_percent": value}
                for name, value in by_bucket.items()
            ]
        dimensions = (
            ("geography", record.get("geographic_allocation") or [], "region", "percent"),
            ("sector", record.get("sector_allocation") or [], "sector", "percent"),
            (
                "duration",
                record.get("duration_distribution") or [],
                "bucket",
                "percent",
            ),
            (
                "asset_type",
                asset_type_items,
                "type",
                "percent",
            ),
            (
                "bank_exposure",
                money_metrics.get("bank_exposure", []),
                "name",
                "percent",
            ),
        )
        for dimension, items, name_key, default_unit in dimensions:
            aggregated: dict[str, dict[str, Any]] = {}
            for item in items:
                if not isinstance(item, dict):
                    continue
                name = normalize_text(item.get(name_key) or item.get("name"))
                if not name:
                    continue
                if dimension == "asset_type":
                    name = canonical_allocation_label(name)
                    if not name:
                        continue
                current = aggregated.setdefault(
                    name,
                    {
                        "weight": 0.0,
                        "unit": item.get("unit") or default_unit,
                        "metadata": {},
                    },
                )
                value = item.get("weight_percent") or item.get("weight") or item.get("percentage") or item.get("value")
                try:
                    current["weight"] += float(value)
                except (TypeError, ValueError):
                    continue
                for key in ("bucket_original", "measure_type", "region_original"):
                    if item.get(key) is not None:
                        current["metadata"][key] = item[key]
            for name, value in aggregated.items():
                self._upsert(
                    FundAllocation,
                    {
                        "id": stable_uuid(
                            "fund-allocation",
                            batch.id,
                            fund.id,
                            version.id,
                            report_period,
                            dimension,
                            name,
                        ),
                        "fund_id": fund.id,
                        "batch_id": batch.id,
                        "document_version_id": version.id,
                        "report_period": report_period,
                        "dimension": dimension,
                        "category": name,
                        "weight": value["weight"],
                        "unit": value["unit"],
                        "source_page": None,
                        "metadata_json": value["metadata"],
                    },
                    constraint="uq_fund_allocation_value",
                    update_columns=(
                        "weight",
                        "unit",
                        "source_page",
                        "metadata_json",
                    ),
                )
                self._summary.allocations += 1

    def _import_scale(
        self,
        batch: DatasetBatch,
        fund: Fund,
        report_period: date,
        record: dict[str, Any],
    ) -> None:
        """导入月末规模历史；参数描述批次、基金和记录。"""

        value = record.get("fund_size_hkd_100m")
        if value in (None, ""):
            return
        if report_period > date.today():
            # 旧版模型曾把持仓到期日误当报告期；未来日期不能进入规模历史。
            self._summary.warnings = self._summary.warnings or []
            self._summary.warnings.append(
                f"{fund.fund_code}: 忽略未来报告期 {report_period.isoformat()} 的规模记录"
            )
            return
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return
        self._upsert(
            FundScaleHistory,
            {
                "id": stable_uuid(
                    "fund-scale",
                    fund.id,
                    report_period,
                    batch.id,
                ),
                "fund_id": fund.id,
                "source_batch_id": batch.id,
                "as_of_date": report_period,
                "fund_size": numeric,
                "currency": "HKD",
                "source_unit": "100m",
            },
            constraint="uq_fund_scale_batch",
            update_columns=(
                "fund_size",
                "currency",
                "source_unit",
            ),
        )
        self._summary.scale_history += 1

    def _add_quality_issue(
        self,
        batch: DatasetBatch,
        fund_id: Any,
        *,
        severity: str,
        issue_type: str,
        details: str,
        metadata: dict[str, Any],
    ) -> None:
        """写入质量问题；参数描述批次、基金和问题内容。"""

        self.session.merge(
            QualityIssue(
                id=stable_uuid(
                    "quality-issue",
                    batch.id,
                    fund_id,
                    issue_type,
                    details,
                ),
                batch_id=batch.id,
                fund_id=fund_id,
                severity=severity,
                issue_type=issue_type,
                details=details,
                status="open",
                metadata_json=metadata,
            )
        )
        self._summary.quality_issues += 1

    @staticmethod
    def _read_json(path: Path | None) -> dict[str, Any]:
        """安全读取 JSON；参数 path：JSON 文件或空值。"""

        if path is None or not path.is_file():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _resolve_source_json(
        combined_path: Path,
        record: dict[str, Any],
    ) -> Path | None:
        """定位结构化单基金 JSON；参数 combined_path/record 为上下文。"""

        name = Path(normalize_text(record.get("_source_json"))).name
        if not name:
            return None
        candidate = combined_path.parent.parent / "per_pdf" / name
        return candidate if candidate.is_file() else None

    @staticmethod
    def _resolve_source_pdf(
        meta: dict[str, Any],
        record: dict[str, Any],
        batch_root: Path,
        pdf_lookup: dict[str, Path],
    ) -> Path | None:
        """定位结构化记录的来源 PDF；参数描述元数据和批次。"""

        source_file = normalize_text((meta.get("_meta") or {}).get("source_file"))
        if source_file:
            candidate = pdf_lookup.get(Path(source_file).name)
            if candidate:
                return candidate
        fund_name = normalize_text(record.get("fund_name"))
        return pdf_lookup.get(f"{fund_name}.pdf")

    @staticmethod
    def _pdf_page_count(path: Path | None) -> int | None:
        """读取 PDF 页数；参数 path：PDF 路径。"""

        if path is None or not path.is_file():
            return None
        try:
            return len(PdfReader(str(path)).pages)
        except Exception:
            return None
