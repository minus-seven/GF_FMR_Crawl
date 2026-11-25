#!/usr/bin/env python
"""Audit the published snapshot and local artifacts for knowledge-base readiness."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Iterable
from uuid import UUID

import pymupdf
from sqlalchemy import create_engine, text


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = (
    PROJECT_ROOT / "docs" / "development" / "artifacts" / "20261026-day1"
)
REPORT_MONTH_PATTERNS = (
    re.compile(r"基金報[告告]\s*[|｜]\s*(20\d{2})年(\d{1,2})月"),
    re.compile(r"(?<!\d)(20\d{2})\s+(\d{1,2})\s*月\s*表現"),
    re.compile(
        r"(?:所有資料均|數據截至|資料截至|截至|as of|data as at)"
        r"[^\d]{0,30}(20\d{2})[年/\-. ]+(\d{1,2})",
        re.IGNORECASE,
    ),
)


def parse_args() -> argparse.Namespace:
    """解析审计命令参数；无外部参数。"""

    default_database = os.environ.get(
        "PLATFORM_DATABASE_URL",
        (
            "postgresql+psycopg://"
            f"{getpass.getuser()}@127.0.0.1:55432/fund_research"
        ),
    )
    parser = argparse.ArgumentParser(
        description="Generate the day-one knowledge-base readiness audit."
    )
    parser.add_argument(
        "--database-url",
        default=default_database,
        help="正式快照数据库连接地址；默认使用 PLATFORM_DATABASE_URL。",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=PROJECT_ROOT,
        help="项目根目录，用于解析文档相对路径。",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="审计 JSON 输出目录。",
    )
    parser.add_argument(
        "--skip-asset-hash",
        action="store_true",
        help="跳过全量 PDF/SHA-256 扫描，只审计当前快照。",
    )
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    """计算文件 SHA-256；参数 path 为待哈希文件。"""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_value(value: Any) -> Any:
    """转换为可序列化值；参数 value 为数据库或文件字段。"""

    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Path):
        return str(value)
    return value


def row_dict(row: Any) -> dict[str, Any]:
    """把 SQLAlchemy 行转换为普通字典；参数 row 为查询结果行。"""

    return {key: json_value(value) for key, value in row._mapping.items()}


def percent(numerator: int, denominator: int) -> float | None:
    """计算百分比；参数 numerator 为分子，denominator 为分母。"""

    return round(numerator / denominator * 100, 2) if denominator else None


def _content_list_for_manifest(manifest_path: Path) -> Path | None:
    """查找 manifest 对应 content_list；参数 manifest_path 为 manifest 路径。"""

    candidates = sorted(manifest_path.parent.glob("*_content_list.json"))
    if not candidates:
        candidates = sorted(
            manifest_path.parent.glob("artifacts/*_content_list.json")
        )
    if not candidates:
        candidates = sorted(
            manifest_path.parent.glob("**/*_content_list*.json")
        )
    return candidates[0] if candidates else None


def inspect_content_list(path: Path | None) -> dict[str, Any]:
    """核对切片页码完整性；参数 path 为 content_list 路径。"""

    if path is None:
        return {
            "exists": False,
            "item_count": 0,
            "page_complete": False,
            "page_count": 0,
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "exists": True,
            "item_count": 0,
            "page_complete": False,
            "page_count": 0,
        }
    items = payload if isinstance(payload, list) else []
    page_indices = {
        int(item["page_idx"])
        for item in items
        if isinstance(item, dict) and isinstance(item.get("page_idx"), int)
    }
    return {
        "exists": True,
        "item_count": len(items),
        "page_complete": bool(items)
        and all(
            isinstance(item, dict) and isinstance(item.get("page_idx"), int)
            for item in items
        ),
        "page_count": len(page_indices),
    }


def extract_report_month(pdf_path: Path) -> tuple[str | None, str | None]:
    """从 PDF 正文提取报告月份；参数 pdf_path 为来源 PDF。"""

    try:
        document = pymupdf.open(pdf_path)
    except Exception:
        return None, None
    try:
        for page_index, page in enumerate(document):
            normalized = re.sub(r"\s+", " ", page.get_text("text"))
            for pattern in REPORT_MONTH_PATTERNS:
                match = pattern.search(normalized)
                if match:
                    month = f"{int(match.group(1)):04d}-{int(match.group(2)):02d}"
                    start = max(0, match.start() - 80)
                    end = min(len(normalized), match.end() + 120)
                    return month, normalized[start:end]
            if page_index >= 1:
                # Most factsheets expose the as-of date in the first two pages.
                break
    finally:
        document.close()
    return None, None


def inspect_pdf(path: Path) -> tuple[int | None, str | None, str | None]:
    """读取 PDF 页数和报告月份；参数 path 为 PDF 文件。"""

    try:
        document = pymupdf.open(path)
    except Exception:
        return None, None, None
    try:
        page_count = document.page_count
    finally:
        document.close()
    report_month, excerpt = extract_report_month(path)
    return page_count, report_month, excerpt


def scan_manifests() -> list[dict[str, Any]]:
    """扫描项目内 MinerU manifest；参数为空。"""

    manifests = []
    for manifest_path in sorted((PROJECT_ROOT / "data").rglob("manifest.json")):
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        content_list = _content_list_for_manifest(manifest_path)
        content_stats = inspect_content_list(content_list)
        manifests.append(
            {
                "path": str(manifest_path.relative_to(PROJECT_ROOT)),
                "source_sha256": payload.get("source_sha256"),
                "status": payload.get("status"),
                "mode": payload.get("mode"),
                "model_version": payload.get("model_version"),
                "content_list": (
                    str(content_list.relative_to(PROJECT_ROOT))
                    if content_list
                    else None
                ),
                **content_stats,
                "mtime": manifest_path.stat().st_mtime,
            }
        )
    return manifests


def select_manifest(
    manifests: Iterable[dict[str, Any]],
    pdf_sha256: str,
) -> dict[str, Any] | None:
    """选取同一 PDF 最佳的 MinerU 产物；参数 manifests 为候选，pdf_sha256 为 PDF 哈希。"""

    candidates = [
        item for item in manifests if item.get("source_sha256") == pdf_sha256
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: (
            bool(item.get("page_complete")),
            bool(item.get("content_list")),
            item.get("mode") == "precise",
            item.get("status") == "done",
            float(item.get("mtime") or 0),
        ),
    )


def scan_historical_assets() -> dict[str, Any]:
    """去重盘点全部 PDF、MinerU 和结构化 JSON；参数为空。"""

    data_root = PROJECT_ROOT / "data"
    pdf_by_sha: dict[str, list[dict[str, Any]]] = defaultdict(list)
    pdf_files = [path for path in data_root.rglob("*.pdf") if path.is_file()]
    for path in pdf_files:
        digest = sha256_file(path)
        pdf_by_sha[digest].append(
            {
                "path": str(path.relative_to(PROJECT_ROOT)),
                "size_bytes": path.stat().st_size,
            }
        )

    manifests = scan_manifests()
    manifest_by_sha: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in manifests:
        if item.get("source_sha256"):
            manifest_by_sha[item["source_sha256"]].append(item)
    manifest_unique = {
        sha: select_manifest(items, sha)
        for sha, items in manifest_by_sha.items()
    }

    structured_by_sha: dict[str, list[str]] = defaultdict(list)
    structured_files = [
        path
        for pattern in ("*.fixed_income.json", "*.equity_fund.json")
        for path in data_root.rglob(pattern)
        if path.is_file()
    ]
    for path in structured_files:
        structured_by_sha[sha256_file(path)].append(
            str(path.relative_to(PROJECT_ROOT))
        )

    pdf_items = []
    for digest, copies in sorted(pdf_by_sha.items()):
        manifest = manifest_unique.get(digest)
        pdf_items.append(
            {
                "sha256": digest,
                "size_bytes": copies[0]["size_bytes"],
                "copies": copies,
                "manifest": manifest,
                "has_manifest": manifest is not None,
            }
        )

    mode_counts = Counter(item.get("mode") or "unknown" for item in manifests)
    unique_mode_counts = Counter(
        (item or {}).get("mode") or "unknown"
        for item in manifest_unique.values()
    )
    page_complete_unique = sum(
        bool((item or {}).get("page_complete"))
        for item in manifest_unique.values()
    )
    return {
        "summary": {
            "pdf_files": len(pdf_files),
            "pdf_unique_sha256": len(pdf_by_sha),
            "pdf_duplicate_files": len(pdf_files) - len(pdf_by_sha),
            "pdf_total_bytes": sum(
                item["size_bytes"]
                for copies in pdf_by_sha.values()
                for item in copies
            ),
            "manifest_files": len(manifests),
            "manifest_unique_pdf_sha256": len(manifest_unique),
            "manifest_mode_counts": dict(mode_counts),
            "manifest_unique_mode_counts": dict(unique_mode_counts),
            "manifest_page_complete_unique": page_complete_unique,
            "pdf_unique_with_manifest": sum(
                1 for digest in pdf_by_sha if digest in manifest_unique
            ),
            "pdf_unique_without_manifest": sum(
                1 for digest in pdf_by_sha if digest not in manifest_unique
            ),
            "manifest_coverage_percent": percent(
                sum(1 for digest in pdf_by_sha if digest in manifest_unique),
                len(pdf_by_sha),
            ),
            "structured_per_pdf_files": len(structured_files),
            "structured_unique_content_sha256": len(structured_by_sha),
            "structured_duplicate_files": (
                len(structured_files) - len(structured_by_sha)
            ),
        },
        "pdfs": pdf_items,
        "structured_json": [
            {"sha256": digest, "copies": copies}
            for digest, copies in sorted(structured_by_sha.items())
        ],
    }


def fetch_database_audit(database_url: str) -> dict[str, Any]:
    """读取发布快照和覆盖率事实；参数 database_url 为数据库连接。"""

    engine = create_engine(database_url, pool_pre_ping=True)
    with engine.connect() as connection:
        snapshot = row_dict(
            connection.execute(
                text(
                    """
                    SELECT b.id AS snapshot_id, b.status, b.coverage,
                           b.source_kind, b.created_at, b.published_at
                    FROM publication_state p
                    JOIN dataset_batches b ON b.id = p.current_batch_id
                    WHERE p.id = 1
                    """
                )
            ).one()
        )
        total_funds = connection.execute(
            text("SELECT count(*) FROM funds")
        ).scalar_one()
        source_status = [
            row_dict(row)
            for row in connection.execute(
                text(
                    """
                    SELECT is_active,
                           count(*) AS sources,
                           count(DISTINCT fund_id) AS funds,
                           count(*) FILTER (
                               WHERE nullif(btrim(monthly_report_url), '') IS NOT NULL
                                 AND nullif(btrim(run_command), '') IS NOT NULL
                           ) AS complete_ij,
                           count(*) FILTER (
                               WHERE nullif(btrim(monthly_report_url), '') IS NULL
                                  OR nullif(btrim(run_command), '') IS NULL
                           ) AS gaps
                    FROM fund_report_sources
                    GROUP BY is_active
                    ORDER BY is_active DESC
                    """
                )
            )
        ]
        source_by_manager = [
            row_dict(row)
            for row in connection.execute(
                text(
                    """
                    SELECT manager_name_source,
                           count(*) AS sources,
                           count(*) FILTER (WHERE is_active) AS complete_ij,
                           count(*) FILTER (WHERE NOT is_active) AS gaps
                    FROM fund_report_sources
                    GROUP BY manager_name_source
                    ORDER BY gaps DESC, manager_name_source
                    """
                )
            )
        ]
        documents = [
            row_dict(row)
            for row in connection.execute(
                text(
                    """
                    WITH current_docs AS (
                        SELECT DISTINCT document_version_id
                        FROM fund_metrics
                        WHERE batch_id = (SELECT current_batch_id FROM publication_state)
                        UNION
                        SELECT DISTINCT document_version_id
                        FROM fund_holdings
                        WHERE batch_id = (SELECT current_batch_id FROM publication_state)
                        UNION
                        SELECT DISTINCT document_version_id
                        FROM fund_allocations
                        WHERE batch_id = (SELECT current_batch_id FROM publication_state)
                    )
                    SELECT f.id AS fund_id, f.fund_code, f.name_cn,
                           manager.name_cn AS manager_name,
                           dv.id AS document_version_id, dv.sha256,
                           dv.report_date, dv.mime_type, dv.file_path,
                           dv.source_url, ds.start_page, ds.end_page
                    FROM current_docs cd
                    JOIN document_versions dv ON dv.id = cd.document_version_id
                    JOIN document_subjects ds
                      ON ds.document_version_id = dv.id
                    JOIN funds f ON f.id = ds.fund_id
                    JOIN managers manager ON manager.id = f.manager_id
                    ORDER BY f.fund_code
                    """
                )
            )
        ]
        quality = [
            row_dict(row)
            for row in connection.execute(
                text(
                    """
                    SELECT severity, status, issue_type, count(*) AS count
                    FROM quality_issues
                    WHERE batch_id = (SELECT current_batch_id FROM publication_state)
                    GROUP BY severity, status, issue_type
                    ORDER BY count DESC
                    """
                )
            )
        ]
        metric_provenance = row_dict(
            connection.execute(
                text(
                    """
                    SELECT count(*) AS metrics,
                           count(*) FILTER (WHERE source_page IS NULL)
                               AS metrics_without_source_page
                    FROM fund_metrics
                    WHERE batch_id = (SELECT current_batch_id FROM publication_state)
                    """
                )
            ).one()
        )
        rename_count = connection.execute(
            text("SELECT count(*) FROM fund_name_history")
        ).scalar_one()
        extension_rows = [
            row_dict(row)
            for row in connection.execute(
                text(
                    """
                    SELECT extname, extversion
                    FROM pg_extension
                    WHERE extname IN ('vector', 'pg_trgm')
                    ORDER BY extname
                    """
                )
            )
        ]
    engine.dispose()
    return {
        "snapshot": snapshot,
        "total_funds": total_funds,
        "source_status": source_status,
        "source_by_manager": source_by_manager,
        "current_documents": documents,
        "quality_issues": quality,
        "metric_provenance": metric_provenance,
        "fund_name_history_count": rename_count,
        "database_extensions": extension_rows,
    }


def classify_current_documents(
    documents: list[dict[str, Any]],
    manifests: list[dict[str, Any]],
    project_root: Path,
) -> list[dict[str, Any]]:
    """为当前快照文档标记页码等级和报告期一致性；参数为文档、manifest 和根目录。"""

    classified = []
    for document in documents:
        file_path = document.get("file_path")
        path = (
            Path(file_path)
            if file_path and Path(file_path).is_absolute()
            else project_root / str(file_path or "")
        )
        file_exists = bool(file_path) and path.is_file()
        manifest = select_manifest(manifests, str(document.get("sha256") or ""))
        page_count, report_month, report_month_excerpt = (
            inspect_pdf(path) if file_exists else (None, None, None)
        )
        database_month = str(document.get("report_date") or "")[:7] or None
        if report_month is None:
            period_status = "metadata_only"
        elif database_month == report_month:
            period_status = "confirmed"
        else:
            period_status = "mismatch"
        if file_exists and (page_count or 0) > 0 and (manifest or {}).get(
            "page_complete"
        ):
            citation_tier = "A_content_list_page_map"
        elif file_exists and (page_count or 0) > 0:
            citation_tier = "B_pdf_page_map"
        else:
            citation_tier = "excluded_no_page_source"
        classified.append(
            {
                **document,
                "resolved_file_path": str(path),
                "file_exists": file_exists,
                "pdf_page_count": page_count,
                "mineru_mode": (manifest or {}).get("mode"),
                "mineru_manifest_path": (manifest or {}).get("path"),
                "content_list_path": (manifest or {}).get("content_list"),
                "content_list_page_complete": bool(
                    (manifest or {}).get("page_complete")
                ),
                "citation_tier": citation_tier,
                "pdf_report_month": report_month,
                "report_month_excerpt": report_month_excerpt,
                "report_period_status": period_status,
                "index_eligible": citation_tier != "excluded_no_page_source"
                and period_status != "mismatch",
            }
        )
    return classified


def summarize(
    database: dict[str, Any],
    documents: list[dict[str, Any]],
    assets: dict[str, Any] | None,
) -> dict[str, Any]:
    """汇总周一冻结指标；参数为数据库、当前文档和历史资产。"""

    source_rows = database["source_status"]
    total_sources = sum(int(row["sources"]) for row in source_rows)
    complete_sources = sum(
        int(row["complete_ij"])
        for row in source_rows
        if row["is_active"]
    )
    current_count = len(documents)
    eligible = [item for item in documents if item["index_eligible"]]
    tier_a = [
        item for item in eligible
        if item["citation_tier"] == "A_content_list_page_map"
    ]
    tier_b = [
        item for item in eligible
        if item["citation_tier"] == "B_pdf_page_map"
    ]
    tiers = Counter(item["citation_tier"] for item in documents)
    period_status = Counter(item["report_period_status"] for item in documents)
    return {
        "coverage": {
            "source_complete": complete_sources,
            "source_total": total_sources,
            "source_complete_percent": percent(complete_sources, total_sources),
            "structured_detail": current_count,
            "structured_detail_percent": percent(current_count, total_sources),
            "structured_with_pdf": sum(
                1 for item in documents if item["file_exists"]
            ),
            "structured_with_pdf_percent": percent(
                sum(1 for item in documents if item["file_exists"]),
                total_sources,
            ),
            "historical_prd_baseline": {
                "covered": 683,
                "denominator": 775,
                "percent": 88.13,
                "current_measurement": False,
            },
        },
        "current_page_evidence": {
            "documents": current_count,
            "eligible": len(eligible),
            "excluded": current_count - len(eligible),
            "citation_tier_counts": dict(tiers),
            "tier_a_content_list": len(tier_a),
            "tier_b_pdf_pages": len(tier_b),
            "report_period_status": dict(period_status),
        },
        "historical_assets": (assets or {}).get("summary"),
    }


def main() -> int:
    """执行审计并写出冻结 JSON；参数为空。"""

    args = parse_args()
    project_root = args.project_root.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    database = fetch_database_audit(args.database_url)
    manifests = scan_manifests()
    documents = classify_current_documents(
        database.pop("current_documents"),
        manifests,
        project_root,
    )
    assets = None if args.skip_asset_hash else scan_historical_assets()
    summary = summarize(database, documents, assets)

    generated_at = datetime.now(timezone.utc).isoformat()
    readiness = {
        "schema_version": "knowledge-readiness-v1",
        "generated_at": generated_at,
        "project_root": str(project_root),
        "database": database,
        "summary": summary,
        "current_documents": documents,
        "embedding_runtime": {
            "recommended_model": "BAAI/bge-m3",
            "dense_dimensions": 1024,
            "vector_column": "vector(1024)",
            "reranker_model": "BAAI/bge-reranker-v2-m3",
            "recommended_python": "3.12",
            "recommended_environment": ".venv-embed",
            "hardware": "Apple M5, 16 GB unified memory",
            "installed_local_runtime": False,
            "fallback": "PostgreSQL full-text search remains available",
        },
    }
    scope = {
        "schema_version": "knowledge-index-scope-v1",
        "snapshot_id": database["snapshot"]["snapshot_id"],
        "generated_at": generated_at,
        "scope_definition": {
            "source": "published snapshot referenced by fund_metrics",
            "document_type": "monthly",
            "required": [
                "fund_id",
                "document_version_id",
                "report_period",
                "source PDF",
                "real page number",
            ],
            "excluded": [
                "missing PDF",
                "unreadable PDF",
                "report month conflicts with PDF",
            ],
        },
        "counts": summary["current_page_evidence"],
        "documents": [
            {
                key: item.get(key)
                for key in (
                    "fund_id",
                    "fund_code",
                    "name_cn",
                    "manager_name",
                    "document_version_id",
                    "sha256",
                    "report_date",
                    "pdf_page_count",
                    "mineru_mode",
                    "mineru_manifest_path",
                    "content_list_path",
                    "citation_tier",
                    "report_period_status",
                    "index_eligible",
                )
            }
            for item in documents
        ],
    }
    (output_dir / "readiness-audit.json").write_text(
        json.dumps(readiness, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "knowledge-index-scope.json").write_text(
        json.dumps(scope, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if assets is not None:
        (output_dir / "historical-assets.json").write_text(
            json.dumps(assets, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
