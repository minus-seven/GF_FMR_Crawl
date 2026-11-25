"""Idempotent page and chunk indexing for published and historical documents."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable
from uuid import UUID

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.config import get_settings, resolve_managed_path
from app.importers.common import (
    normalize_name,
    parse_report_month,
    relative_path,
    sha256_file,
    stable_uuid,
)
from app.knowledge.chunking import (
    ChunkRecord,
    PageRecord,
    chunks_from_content_list,
    chunks_from_pdf_pages,
    extract_pdf_pages,
    sha256_text,
)
from app.models import (
    ChunkEmbedding,
    DatasetBatch,
    Document,
    DocumentChunk,
    DocumentPage,
    DocumentSubject,
    DocumentVersion,
    Fund,
    KnowledgeIndexRun,
    ParseRun,
)


PARSER_VERSION = "mineru-pdf-pages-v1"
INDEX_VERSION = "knowledge-index-v1"
HISTORICAL_SCOPE = "knowledge-history-v1"
REPORT_MONTH_PATTERNS = (
    re.compile(r"基金報[告告]\s*[|｜]\s*(20\d{2})年(\d{1,2})月"),
    re.compile(r"(?<!\d)(20\d{2})\s+(\d{1,2})\s*月\s*表現"),
    re.compile(
        r"(?:所有資料均|數據截至|資料截至|截至|as of|data as at)"
        r"[^\d]{0,30}(20\d{2})[年/\-. ]+(\d{1,2})",
        re.IGNORECASE,
    ),
)


@dataclass(frozen=True)
class ManifestInfo:
    """Resolved MinerU manifest and optional page-level content list."""

    path: Path
    payload: dict[str, Any]
    content_list_path: Path | None
    page_complete: bool


@dataclass(frozen=True)
class DocumentSource:
    """One document selected for indexing."""

    snapshot_id: UUID
    fund_id: UUID
    fund_code: str
    fund_name: str
    document_version_id: UUID
    sha256: str
    report_period: date
    pdf_path: Path
    source_url: str | None
    citation_tier: str


@dataclass
class IndexStats:
    """Counts produced by one index build."""

    documents_seen: int = 0
    documents_indexed: int = 0
    documents_skipped: int = 0
    pages_written: int = 0
    chunks_written: int = 0
    chunks_deleted: int = 0
    embeddings_written: int = 0
    structured_jsons_seen: int = 0
    structured_jsons_indexed: int = 0
    structured_jsons_skipped: int = 0
    skipped: list[dict[str, str]] | None = None
    errors: list[dict[str, str]] | None = None

    def __post_init__(self) -> None:
        """初始化可变明细列表；参数为空。"""

        if self.skipped is None:
            self.skipped = []
        if self.errors is None:
            self.errors = []


def utc_now() -> datetime:
    """返回 UTC 当前时间；参数为空。"""

    return datetime.now(timezone.utc)


def _content_list_path(manifest_path: Path) -> Path | None:
    """查找 manifest 对应 content_list；参数 manifest_path 为 manifest。"""

    candidates = sorted(manifest_path.parent.glob("*_content_list.json"))
    if not candidates:
        candidates = sorted(
            manifest_path.parent.glob("artifacts/*_content_list.json")
        )
    if not candidates:
        candidates = sorted(manifest_path.parent.glob("**/*_content_list*.json"))
    return candidates[0] if candidates else None


def _content_list_is_complete(path: Path | None) -> bool:
    """判断 content_list 是否每项都有页码；参数 path 为文件。"""

    if path is None:
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(payload, list) and bool(payload) and all(
        isinstance(item, dict) and isinstance(item.get("page_idx"), int)
        for item in payload
    )


def load_manifest_catalog(
    data_root: Path,
    *,
    fund_name_counts: dict[str, int] | None = None,
) -> dict[str, ManifestInfo]:
    """按 PDF SHA 建立最佳 manifest 索引；参数为数据目录和基金名计数。"""

    by_sha: dict[str, list[ManifestInfo]] = {}
    for manifest_path in sorted(data_root.rglob("manifest.json")):
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        digest = str(payload.get("source_sha256") or "")
        if not digest or payload.get("status") != "done":
            continue
        content_list = _content_list_path(manifest_path)
        by_sha.setdefault(digest, []).append(
            ManifestInfo(
                path=manifest_path,
                payload=payload,
                content_list_path=content_list,
                page_complete=_content_list_is_complete(content_list),
            )
        )

    def candidate_key(item: ManifestInfo) -> tuple[bool, bool, bool, float]:
        """按唯一基金映射、页码完整度和解析质量选择 manifest。"""

        source_name = Path(
            str(item.payload.get("source_pdf") or "").replace("\\", "/")
        ).stem
        unique_fund = (
            fund_name_counts is None
            or fund_name_counts.get(normalize_name(source_name), 0) == 1
        )
        return (
            unique_fund,
            item.page_complete,
            item.payload.get("mode") == "precise",
            item.path.stat().st_mtime,
        )

    return {
        digest: max(candidates, key=candidate_key)
        for digest, candidates in by_sha.items()
    }


def extract_report_month(pdf_path: Path) -> str | None:
    """从 PDF 前两页提取报告月份；参数 pdf_path 为 PDF。"""

    try:
        import pymupdf

        document = pymupdf.open(pdf_path)
    except Exception:
        return None
    try:
        for page_index in range(min(2, document.page_count)):
            normalized = re.sub(r"\s+", " ", document[page_index].get_text("text"))
            for pattern in REPORT_MONTH_PATTERNS:
                match = pattern.search(normalized)
                if match:
                    return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}"
    finally:
        document.close()
    return None


def _resolve_managed_path(project_root: Path, value: str | None) -> Path | None:
    """解析数据库中的受管路径；参数 project_root/value 为根目录和路径。"""

    if not value:
        return None
    path = Path(value)
    if path.is_absolute():
        return path
    return resolve_managed_path(get_settings(), value)


def current_document_sources(
    session: Session,
    *,
    project_root: Path,
) -> tuple[UUID, list[DocumentSource], list[dict[str, str]]]:
    """读取当前快照可索引文档；参数 session/project_root 为数据库与项目根。"""

    snapshot_id = session.execute(
        text("SELECT current_batch_id FROM publication_state WHERE id = 1")
    ).scalar_one()
    rows = session.execute(
        text(
            """
            WITH current_docs AS (
                SELECT DISTINCT document_version_id
                FROM fund_metrics
                WHERE batch_id = :snapshot_id
                UNION
                SELECT DISTINCT document_version_id
                FROM fund_holdings
                WHERE batch_id = :snapshot_id
                UNION
                SELECT DISTINCT document_version_id
                FROM fund_allocations
                WHERE batch_id = :snapshot_id
            )
            SELECT f.id AS fund_id, f.fund_code, f.name_cn AS fund_name,
                   dv.id AS document_version_id, dv.sha256, dv.report_date,
                   dv.file_path, dv.source_url, d.document_type
            FROM current_docs cd
            JOIN document_versions dv ON dv.id = cd.document_version_id
            JOIN documents d ON d.id = dv.document_id
            JOIN document_subjects ds ON ds.document_version_id = dv.id
            JOIN funds f ON f.id = ds.fund_id
            ORDER BY f.fund_code
            """
        ),
        {"snapshot_id": snapshot_id},
    ).mappings()
    sources: list[DocumentSource] = []
    skipped: list[dict[str, str]] = []
    seen: set[UUID] = set()
    for row in rows:
        version_id = row["document_version_id"]
        if version_id in seen:
            continue
        seen.add(version_id)
        pdf_path = _resolve_managed_path(project_root, row["file_path"])
        if pdf_path is None or not pdf_path.is_file():
            skipped.append(
                {
                    "document_version_id": str(version_id),
                    "fund_code": row["fund_code"],
                    "reason": "source_pdf_missing",
                }
            )
            continue
        if pdf_path.suffix.lower() != ".pdf":
            skipped.append(
                {
                    "document_version_id": str(version_id),
                    "fund_code": row["fund_code"],
                    "reason": (
                        "non_pdf_artifact:"
                        f"{row.get('document_type') or 'unknown'}"
                    ),
                }
            )
            continue
        database_month = str(row["report_date"] or "")[:7]
        pdf_month = extract_report_month(pdf_path)
        if pdf_month and database_month and pdf_month != database_month:
            skipped.append(
                {
                    "document_version_id": str(version_id),
                    "fund_code": row["fund_code"],
                    "reason": (
                        f"report_period_conflict:{database_month}!={pdf_month}"
                    ),
                }
            )
            continue
        sources.append(
            DocumentSource(
                snapshot_id=snapshot_id,
                fund_id=row["fund_id"],
                fund_code=row["fund_code"],
                fund_name=row["fund_name"],
                document_version_id=version_id,
                sha256=row["sha256"],
                report_period=row["report_date"],
                pdf_path=pdf_path,
                source_url=row["source_url"],
                citation_tier="pending",
            )
        )
    return snapshot_id, sources, skipped


def _stable_page_id(document_version_id: UUID, page_number: int) -> UUID:
    """生成稳定页面 ID；参数 document_version_id/page_number 为定位键。"""

    return stable_uuid("knowledge-document-page", document_version_id, page_number)


def _stable_chunk_id(
    snapshot_id: UUID,
    document_version_id: UUID,
    chunk_index: int,
) -> UUID:
    """生成稳定切片 ID；参数为快照、文档版本和切片序号。"""

    return stable_uuid(
        "knowledge-document-chunk",
        snapshot_id,
        document_version_id,
        chunk_index,
    )


def _upsert_pages(
    session: Session,
    *,
    document_version_id: UUID,
    pages: list[PageRecord],
) -> tuple[int, int]:
    """幂等写入 PDF 页面；参数 session/document_version_id/pages 为页面数据。"""

    existing = {
        item.page_number: item
        for item in session.scalars(
            select(DocumentPage).where(
                DocumentPage.document_version_id == document_version_id
            )
        )
    }
    written = 0
    for page in pages:
        record = existing.get(page.page_number)
        if record is None:
            record = DocumentPage(
                id=_stable_page_id(document_version_id, page.page_number),
                document_version_id=document_version_id,
                page_number=page.page_number,
                text=page.text,
                width=page.width,
                height=page.height,
                content_hash=page.content_hash,
            )
            session.add(record)
            written += 1
        elif record.content_hash != page.content_hash:
            record.text = page.text
            record.width = page.width
            record.height = page.height
            record.content_hash = page.content_hash
            written += 1
    stale = [
        record.id
        for page_number, record in existing.items()
        if page_number > len(pages)
    ]
    if stale:
        session.execute(delete(DocumentPage).where(DocumentPage.id.in_(stale)))
    return written, len(stale)


def _choose_chunks(
    *,
    source: DocumentSource,
    manifest: ManifestInfo | None,
    pages: list[PageRecord],
) -> tuple[list[ChunkRecord], str, str | None]:
    """选择 A/B 切片来源；参数 source/manifest/pages 为文档上下文。"""

    if manifest and manifest.page_complete and manifest.content_list_path:
        try:
            chunks = chunks_from_content_list(
                manifest.content_list_path,
                page_count=len(pages),
            )
            if chunks:
                return chunks, "A_content_list_page_map", str(
                    manifest.content_list_path
                )
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    return (
        chunks_from_pdf_pages(pages),
        "B_pdf_page_map",
        str(manifest.path) if manifest else None,
    )


def _upsert_chunks(
    session: Session,
    *,
    source: DocumentSource,
    chunks: list[ChunkRecord],
    citation_tier: str,
    source_artifact: str | None,
    project_root: Path,
) -> tuple[int, int]:
    """幂等写入文档切片；参数 source/chunks/tier/artifact/root 为文档数据。"""

    existing = {
        item.chunk_index: item
        for item in session.scalars(
            select(DocumentChunk).where(
                DocumentChunk.snapshot_id == source.snapshot_id,
                DocumentChunk.document_version_id
                == source.document_version_id
            )
        )
    }
    source_path = relative_path(source.pdf_path, project_root)
    written = 0
    for chunk in chunks:
        record = existing.get(chunk.chunk_index)
        metadata = {
            **chunk.metadata,
            "source_artifact": source_artifact,
            "parser_version": PARSER_VERSION,
        }
        if record is None:
            record = DocumentChunk(
                id=_stable_chunk_id(
                    source.snapshot_id,
                    source.document_version_id,
                    chunk.chunk_index,
                ),
                document_version_id=source.document_version_id,
                fund_id=source.fund_id,
                snapshot_id=source.snapshot_id,
                report_period=source.report_period,
                page_number=chunk.page_number,
                chunk_index=chunk.chunk_index,
                section_title=chunk.section_title,
                chunk_type=chunk.chunk_type,
                content=chunk.content,
                content_hash=chunk.content_hash,
                source_path=source_path,
                citation_tier=citation_tier,
                metadata_json=metadata,
            )
            session.add(record)
            written += 1
        elif (
            record.content_hash != chunk.content_hash
            or record.snapshot_id != source.snapshot_id
            or record.page_number != chunk.page_number
            or record.citation_tier != citation_tier
        ):
            record.fund_id = source.fund_id
            record.snapshot_id = source.snapshot_id
            record.report_period = source.report_period
            record.page_number = chunk.page_number
            record.section_title = chunk.section_title
            record.chunk_type = chunk.chunk_type
            record.content = chunk.content
            record.content_hash = chunk.content_hash
            record.source_path = source_path
            record.citation_tier = citation_tier
            record.metadata_json = metadata
            written += 1
    stale = [
        record.id
        for chunk_index, record in existing.items()
        if chunk_index >= len(chunks)
    ]
    if stale:
        session.execute(delete(DocumentChunk).where(DocumentChunk.id.in_(stale)))
    return written, len(stale)


def index_document(
    session: Session,
    *,
    source: DocumentSource,
    manifest: ManifestInfo | None,
    project_root: Path,
) -> tuple[IndexStats, str]:
    """索引单份文档并返回统计；参数为数据库、文档、manifest 和根目录。"""

    stats = IndexStats(documents_seen=1)
    pages = extract_pdf_pages(source.pdf_path)
    if not pages:
        raise ValueError(f"PDF has no readable pages: {source.pdf_path}")
    page_written, _ = _upsert_pages(
        session,
        document_version_id=source.document_version_id,
        pages=pages,
    )
    chunks, tier, artifact = _choose_chunks(
        source=source,
        manifest=manifest,
        pages=pages,
    )
    chunk_written, chunk_deleted = _upsert_chunks(
        session,
        source=source,
        chunks=chunks,
        citation_tier=tier,
        source_artifact=artifact,
        project_root=project_root,
    )
    run = session.scalar(
        select(ParseRun).where(
            ParseRun.document_version_id == source.document_version_id,
            ParseRun.parser_name == "mineru+pdf",
            ParseRun.parser_version == PARSER_VERSION,
        )
    )
    if run is None:
        run = ParseRun(
            id=stable_uuid(
                "parse-run",
                source.document_version_id,
                "mineru+pdf",
                PARSER_VERSION,
            ),
            document_version_id=source.document_version_id,
            parser_name="mineru+pdf",
            parser_version=PARSER_VERSION,
            artifact_path=artifact,
            page_mapping_tier=tier,
            status="completed",
            started_at=utc_now(),
            finished_at=utc_now(),
        )
        session.add(run)
    else:
        run.artifact_path = artifact
        run.page_mapping_tier = tier
        run.status = "completed"
        run.error_code = None
        run.finished_at = utc_now()
    stats.documents_indexed = 1
    stats.pages_written = page_written
    stats.chunks_written = chunk_written
    stats.chunks_deleted = chunk_deleted
    return stats, tier


def _merge_stats(target: IndexStats, value: IndexStats) -> None:
    """合并索引统计；参数 target/value 为累计和本次统计。"""

    target.documents_indexed += value.documents_indexed
    target.documents_skipped += value.documents_skipped
    target.pages_written += value.pages_written
    target.chunks_written += value.chunks_written
    target.chunks_deleted += value.chunks_deleted
    target.embeddings_written += value.embeddings_written
    target.structured_jsons_seen += value.structured_jsons_seen
    target.structured_jsons_indexed += value.structured_jsons_indexed
    target.structured_jsons_skipped += value.structured_jsons_skipped
    target.skipped.extend(value.skipped or [])
    target.errors.extend(value.errors or [])


def _run_record(
    session: Session,
    *,
    snapshot_id: UUID,
    scope_hash: str,
    embedding_model: str | None = None,
    embedding_version: str | None = None,
) -> KnowledgeIndexRun:
    """创建或复用索引运行记录；参数为快照、范围和模型。"""

    record = session.scalar(
        select(KnowledgeIndexRun).where(
            KnowledgeIndexRun.snapshot_id == snapshot_id,
            KnowledgeIndexRun.scope_hash == scope_hash,
            KnowledgeIndexRun.index_version == INDEX_VERSION,
        )
    )
    if record is None:
        record = KnowledgeIndexRun(
            id=stable_uuid(
                "knowledge-index-run",
                snapshot_id,
                scope_hash,
                INDEX_VERSION,
            ),
            snapshot_id=snapshot_id,
            scope_hash=scope_hash,
            index_version=INDEX_VERSION,
            parser_version=PARSER_VERSION,
            embedding_model=embedding_model,
            embedding_version=embedding_version,
            status="running",
            counts_json={},
            started_at=utc_now(),
        )
        session.add(record)
    else:
        record.status = "running"
        record.error_message = None
        record.embedding_model = embedding_model
        record.embedding_version = embedding_version
        record.started_at = utc_now()
        record.finished_at = None
    session.flush()
    return record


def index_current_snapshot(
    session: Session,
    *,
    project_root: Path,
    manifests: dict[str, ManifestInfo] | None = None,
) -> tuple[UUID, IndexStats]:
    """索引当前快照中通过页码门槛的文档；参数为数据库和项目根。"""

    snapshot_id, sources, skipped = current_document_sources(
        session,
        project_root=project_root,
    )
    settings = get_settings()
    manifests = manifests or load_manifest_catalog(settings.data_root)
    scope_hash = sha256_text(
        json.dumps(
            sorted(str(item.document_version_id) for item in sources),
            separators=(",", ":"),
        )
    )
    run = _run_record(
        session,
        snapshot_id=snapshot_id,
        scope_hash=scope_hash,
    )
    stats = IndexStats(documents_seen=len(sources) + len(skipped))
    stats.documents_skipped += len(skipped)
    stats.skipped.extend(skipped)
    try:
        for source in sources:
            try:
                document_stats, _ = index_document(
                    session,
                    source=source,
                    manifest=manifests.get(source.sha256),
                    project_root=project_root,
                )
                _merge_stats(stats, document_stats)
            except Exception as exc:
                stats.documents_skipped += 1
                stats.errors.append(
                    {
                        "document_version_id": str(source.document_version_id),
                        "fund_code": source.fund_code,
                        "reason": f"{type(exc).__name__}: {exc}",
                    }
                )
        run.status = "failed" if stats.errors else "completed"
        run.counts_json = asdict(stats)
        run.finished_at = utc_now()
        session.flush()
        return snapshot_id, stats
    except Exception as exc:
        run.status = "failed"
        run.error_message = f"{type(exc).__name__}: {exc}"
        run.finished_at = utc_now()
        session.flush()
        raise


def _ensure_historical_batch(session: Session) -> DatasetBatch:
    """创建或复用历史知识批次；参数 session 为数据库会话。"""

    batch_id = stable_uuid("dataset-batch", HISTORICAL_SCOPE)
    batch = session.get(DatasetBatch, batch_id)
    if batch is None:
        batch = DatasetBatch(
            id=batch_id,
            status="published",
            scope_hash=sha256_text(HISTORICAL_SCOPE),
            source_kind="knowledge-history",
            manifest_path=None,
            published_at=utc_now(),
        )
        session.add(batch)
        session.flush()
    return batch


def _pdf_candidates(data_root: Path) -> dict[tuple[str, int], list[Path]]:
    """按文件名和大小建立 PDF 候选；参数 data_root 为数据目录。"""

    candidates: dict[tuple[str, int], list[Path]] = {}
    for path in data_root.rglob("*.pdf"):
        if path.is_file():
            candidates.setdefault(
                (path.name, path.stat().st_size),
                [],
            ).append(path)
    return candidates


def _pdfs_by_normalized_stem(data_root: Path) -> dict[str, list[Path]]:
    """按规范化文件名索引 PDF；参数 data_root 为数据目录。"""

    result: dict[str, list[Path]] = {}
    for path in data_root.rglob("*.pdf"):
        if path.is_file():
            result.setdefault(normalize_name(path.stem), []).append(path)
    return result


def _structured_json_catalog(
    data_root: Path,
) -> dict[tuple[str, date], Path]:
    """按基金名和报告月去重结构化 JSON；参数 data_root 为数据目录。"""

    result: dict[tuple[str, date], Path] = {}
    for path in sorted(data_root.rglob("*.json")):
        if not (
            path.name.endswith(".fixed_income.json")
            or path.name.endswith(".money_market.json")
            or path.name.endswith(".equity_fund.json")
        ):
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        fund_name = normalize_name(payload.get("fund_name"))
        report_period = parse_report_month(payload.get("report_date"))
        if fund_name and report_period:
            result.setdefault((fund_name, report_period), path)
    return result


def _resolve_manifest_pdf(
    manifest: ManifestInfo,
    *,
    pdf_candidates: dict[tuple[str, int], list[Path]],
) -> Path | None:
    """按 manifest 源路径和 SHA 定位本地 PDF；参数 manifest/候选为上下文。"""

    source_name = Path(
        str(manifest.payload.get("source_pdf") or "").replace("\\", "/")
    ).name
    source_size = int(manifest.payload.get("source_size_bytes") or 0)
    candidates = pdf_candidates.get((source_name, source_size), [])
    digest = manifest.payload.get("source_sha256")
    for path in candidates:
        if not digest or sha256_file(path) == digest:
            return path
    return None


def _historical_version(
    session: Session,
    *,
    fund: Fund,
    pdf_path: Path,
    sha256: str,
    report_period: date,
    batch: DatasetBatch,
    project_root: Path,
) -> DocumentVersion:
    """复用或创建可追溯历史文档版本；参数为基金、PDF、报告期和批次。"""

    existing = session.scalar(
        select(DocumentVersion).where(DocumentVersion.sha256 == sha256)
    )
    if existing is not None:
        return existing
    document_id = stable_uuid("document", f"sha256:{sha256}")
    document = session.get(Document, document_id)
    if document is None:
        document = Document(
            id=document_id,
            document_type="monthly",
            umbrella_id=fund.umbrella_id,
            source_id=f"sha256:{sha256}",
            first_seen_at=utc_now(),
        )
        session.add(document)
    version_id = stable_uuid("document-version", document_id, sha256)
    version = DocumentVersion(
        id=version_id,
        document_id=document_id,
        batch_id=batch.id,
        source_url=None,
        report_date=report_period,
        sha256=sha256,
        mime_type="application/pdf",
        size_bytes=pdf_path.stat().st_size,
        file_path=relative_path(pdf_path, project_root),
    )
    session.add(version)
    session.flush()
    return version


def _upsert_document_subject(
    session: Session,
    *,
    document_version_id: UUID,
    fund_id: UUID,
    subject_name: str,
    start_page: int,
    end_page: int,
    match_method: str,
) -> None:
    """按文档、基金和页范围幂等写入 subject；参数为业务键和匹配信息。"""

    statement = pg_insert(DocumentSubject).values(
        id=stable_uuid(
            "document-subject",
            document_version_id,
            fund_id,
            start_page,
            end_page,
        ),
        document_version_id=document_version_id,
        fund_id=fund_id,
        subject_name=subject_name,
        start_page=start_page,
        end_page=end_page,
        match_method=match_method,
        match_score=1,
    )
    session.execute(
        statement.on_conflict_do_nothing(constraint="uq_document_subject_range")
    )


def index_historical_assets(
    session: Session,
    *,
    project_root: Path,
    manifests: dict[str, ManifestInfo] | None = None,
) -> tuple[UUID, IndexStats]:
    """索引可追溯的历史 MinerU 文档，不改变当前发布指针。"""

    data_root = get_settings().data_root
    batch = _ensure_historical_batch(session)
    fund_rows = session.execute(
        select(Fund.id, Fund.fund_code, Fund.name_cn, Fund.umbrella_id)
    ).all()
    funds_by_name: dict[str, list[Any]] = {}
    for row in fund_rows:
        funds_by_name.setdefault(normalize_name(row.name_cn), []).append(row)
    if manifests is None:
        manifests = load_manifest_catalog(
            data_root,
            fund_name_counts={
                name: len(matches) for name, matches in funds_by_name.items()
            },
        )
    processed_version_ids = {
        row[0]
        for row in session.execute(
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
                SELECT document_version_id FROM current_docs
                """
            )
        )
    }
    processed_digests: set[str] = set()
    pdf_candidates = _pdf_candidates(data_root)
    pdfs_by_stem = _pdfs_by_normalized_stem(data_root)
    structured_catalog = _structured_json_catalog(data_root)
    digest_cache: dict[Path, str] = {}
    stats = IndexStats(documents_seen=len(manifests))
    scope_hash = sha256_text(
        json.dumps(sorted(manifests), separators=(",", ":"))
    )
    run = _run_record(
        session,
        snapshot_id=batch.id,
        scope_hash=scope_hash,
    )
    try:
        for digest, manifest in sorted(manifests.items()):
            pdf_path = _resolve_manifest_pdf(
                manifest,
                pdf_candidates=pdf_candidates,
            )
            if pdf_path is None:
                stats.documents_skipped += 1
                stats.skipped.append(
                    {"sha256": digest, "reason": "local_pdf_missing"}
                )
                continue
            fund_name = Path(
                str(manifest.payload.get("source_pdf") or "").replace("\\", "/")
            ).stem
            fund_matches = funds_by_name.get(normalize_name(fund_name), [])
            if len(fund_matches) != 1:
                stats.documents_skipped += 1
                stats.skipped.append(
                    {
                        "sha256": digest,
                        "reason": (
                            "fund_match_missing"
                            if not fund_matches
                            else "fund_match_ambiguous"
                        ),
                    }
                )
                continue
            row = fund_matches[0]
            report_month = extract_report_month(pdf_path)
            if not report_month:
                stats.documents_skipped += 1
                stats.skipped.append(
                    {"sha256": digest, "reason": "report_month_unresolved"}
                )
                continue
            report_period = date.fromisoformat(f"{report_month}-01")
            fund = session.get(Fund, row.id)
            if fund is None:
                stats.documents_skipped += 1
                continue
            version = _historical_version(
                session,
                fund=fund,
                pdf_path=pdf_path,
                sha256=digest,
                report_period=report_period,
                batch=batch,
                project_root=project_root,
            )
            if version.id in processed_version_ids:
                stats.documents_skipped += 1
                stats.skipped.append(
                    {
                        "sha256": digest,
                        "document_version_id": str(version.id),
                        "reason": "already_in_current_snapshot",
                    }
                )
                continue
            processed_version_ids.add(version.id)
            processed_digests.add(digest)
            page_count = len(extract_pdf_pages(pdf_path))
            _upsert_document_subject(
                session,
                document_version_id=version.id,
                fund_id=fund.id,
                subject_name=fund.name_cn,
                start_page=1,
                end_page=page_count,
                match_method="historical_manifest_sha",
            )
            source = DocumentSource(
                snapshot_id=batch.id,
                fund_id=fund.id,
                fund_code=fund.fund_code,
                fund_name=fund.name_cn,
                document_version_id=version.id,
                sha256=digest,
                report_period=report_period,
                pdf_path=pdf_path,
                source_url=None,
                citation_tier="pending",
            )
            try:
                document_stats, _ = index_document(
                    session,
                    source=source,
                    manifest=manifest,
                    project_root=project_root,
                )
                _merge_stats(stats, document_stats)
            except Exception as exc:
                stats.documents_skipped += 1
                stats.errors.append(
                    {
                        "sha256": digest,
                        "fund_code": fund.fund_code,
                        "reason": f"{type(exc).__name__}: {exc}",
                    }
                )
        for (fund_name_key, report_period), structured_path in sorted(
            structured_catalog.items()
        ):
            stats.structured_jsons_seen += 1
            fund_matches = funds_by_name.get(fund_name_key, [])
            if len(fund_matches) != 1:
                stats.structured_jsons_skipped += 1
                stats.skipped.append(
                    {
                        "structured_json": str(
                            relative_path(structured_path, project_root)
                        ),
                        "reason": (
                            "structured_fund_match_missing"
                            if not fund_matches
                            else "structured_fund_match_ambiguous"
                        ),
                    }
                )
                continue
            row = fund_matches[0]
            selected_pdf: Path | None = None
            selected_digest: str | None = None
            for candidate_pdf in pdfs_by_stem.get(fund_name_key, []):
                digest = digest_cache.get(candidate_pdf)
                if digest is None:
                    digest = sha256_file(candidate_pdf)
                    digest_cache[candidate_pdf] = digest
                if digest in processed_digests:
                    continue
                if extract_report_month(candidate_pdf) == report_period.strftime("%Y-%m"):
                    selected_pdf = candidate_pdf
                    selected_digest = digest
                    break
            if selected_pdf is None or selected_digest is None:
                stats.structured_jsons_skipped += 1
                stats.skipped.append(
                    {
                        "structured_json": str(
                            relative_path(structured_path, project_root)
                        ),
                        "reason": "structured_pdf_or_version_already_indexed",
                    }
                )
                continue
            fund = session.get(Fund, row.id)
            if fund is None:
                stats.structured_jsons_skipped += 1
                continue
            version = _historical_version(
                session,
                fund=fund,
                pdf_path=selected_pdf,
                sha256=selected_digest,
                report_period=report_period,
                batch=batch,
                project_root=project_root,
            )
            if version.id in processed_version_ids:
                stats.structured_jsons_skipped += 1
                continue
            processed_version_ids.add(version.id)
            processed_digests.add(selected_digest)
            page_count = len(extract_pdf_pages(selected_pdf))
            _upsert_document_subject(
                session,
                document_version_id=version.id,
                fund_id=fund.id,
                subject_name=fund.name_cn,
                start_page=1,
                end_page=page_count,
                match_method="historical_structured_json",
            )
            source = DocumentSource(
                snapshot_id=batch.id,
                fund_id=fund.id,
                fund_code=fund.fund_code,
                fund_name=fund.name_cn,
                document_version_id=version.id,
                sha256=selected_digest,
                report_period=report_period,
                pdf_path=selected_pdf,
                source_url=None,
                citation_tier="pending",
            )
            try:
                document_stats, _ = index_document(
                    session,
                    source=source,
                    manifest=manifests.get(selected_digest),
                    project_root=project_root,
                )
                _merge_stats(stats, document_stats)
                stats.structured_jsons_indexed += 1
            except Exception as exc:
                stats.structured_jsons_skipped += 1
                stats.errors.append(
                    {
                        "structured_json": str(
                            relative_path(structured_path, project_root)
                        ),
                        "fund_code": fund.fund_code,
                        "reason": f"{type(exc).__name__}: {exc}",
                    }
                )
        run.status = "failed" if stats.errors else "completed"
        run.counts_json = asdict(stats)
        run.finished_at = utc_now()
        session.flush()
        return batch.id, stats
    except Exception as exc:
        run.status = "failed"
        run.error_message = f"{type(exc).__name__}: {exc}"
        run.finished_at = utc_now()
        session.flush()
        raise


def index_embeddings(
    session: Session,
    *,
    index_run_id: UUID,
    model_name: str,
    model_version: str,
    embed: Any,
) -> int:
    """为索引运行覆盖的切片写入向量；参数 session/run/model/embed。"""

    run = session.get(KnowledgeIndexRun, index_run_id)
    if run is None:
        raise ValueError("knowledge index run does not exist")
    chunks = list(
        session.scalars(
            select(DocumentChunk).where(DocumentChunk.snapshot_id == run.snapshot_id)
        )
    )
    chunks_by_hash: dict[str, list[DocumentChunk]] = {}
    for chunk in chunks:
        chunks_by_hash.setdefault(chunk.content_hash, []).append(chunk)
    existing_embeddings = {
        item.chunk_id: item
        for item in session.scalars(
            select(ChunkEmbedding).where(
                ChunkEmbedding.chunk_id.in_([item.id for item in chunks]),
                ChunkEmbedding.model_name == model_name,
                ChunkEmbedding.model_version == model_version,
            )
        )
    } if chunks else {}
    reusable_vectors: dict[str, list[float]] = {}
    if chunks:
        for content_hash, embedding in session.execute(
            select(DocumentChunk.content_hash, ChunkEmbedding.embedding)
            .join(ChunkEmbedding, ChunkEmbedding.chunk_id == DocumentChunk.id)
            .where(
                ChunkEmbedding.model_name == model_name,
                ChunkEmbedding.model_version == model_version,
            )
        ):
            reusable_vectors.setdefault(content_hash, list(embedding))
    record_hashes = list(
        dict.fromkeys(
            chunk.content_hash
            for chunk in chunks
            if chunk.id not in existing_embeddings
            or existing_embeddings[chunk.id].content_hash != chunk.content_hash
        )
    )
    vectors_by_hash: dict[str, list[float]] = dict(reusable_vectors)
    hashes_to_encode = [
        content_hash
        for content_hash in record_hashes
        if content_hash not in vectors_by_hash
    ]
    written = 0
    batch_size = 32
    for start in range(0, len(hashes_to_encode), batch_size):
        hash_batch = hashes_to_encode[start : start + batch_size]
        text_batch = [chunks_by_hash[key][0].content for key in hash_batch]
        vectors = embed(text_batch)
        if len(vectors) != len(hash_batch):
            raise ValueError("embedding provider returned a mismatched vector count")
        for content_hash, vector in zip(hash_batch, vectors):
            if len(vector) != 1024:
                raise ValueError("embedding dimension must be 1024")
            vectors_by_hash[content_hash] = [float(value) for value in vector]
    for content_hash in record_hashes:
        hash_chunks = chunks_by_hash[content_hash]
        vector = vectors_by_hash[content_hash]
        for chunk in hash_chunks:
            record = existing_embeddings.get(chunk.id)
            if record is None:
                record = ChunkEmbedding(
                    id=stable_uuid(
                        "chunk-embedding",
                        chunk.id,
                        model_name,
                        model_version,
                    ),
                    chunk_id=chunk.id,
                    model_name=model_name,
                    model_version=model_version,
                    dimensions=1024,
                    content_hash=chunk.content_hash,
                    embedding=vector,
                )
                session.add(record)
                written += 1
            elif record.content_hash != chunk.content_hash:
                record.content_hash = chunk.content_hash
                record.embedding = vector
                written += 1
            else:
                continue
    run.embedding_model = model_name
    run.embedding_version = model_version
    session.flush()
    return written


def latest_index_run(
    session: Session,
    *,
    snapshot_id: UUID,
) -> KnowledgeIndexRun | None:
    """读取快照最新索引运行；参数 session/snapshot_id 为数据库和快照。"""

    return session.scalar(
        select(KnowledgeIndexRun)
        .where(
            KnowledgeIndexRun.snapshot_id == snapshot_id,
            KnowledgeIndexRun.index_version == INDEX_VERSION,
        )
        .order_by(KnowledgeIndexRun.started_at.desc().nullslast())
        .limit(1)
    )
