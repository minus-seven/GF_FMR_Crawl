#!/usr/bin/env python
"""Verify page references, hashes, idempotency keys and vector coverage."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any

from sqlalchemy import create_engine, text


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def parse_args() -> argparse.Namespace:
    """解析验证命令参数；参数为空。"""

    parser = argparse.ArgumentParser(description="Verify the knowledge index.")
    parser.add_argument(
        "--database-url",
        default=os.environ.get("PLATFORM_DATABASE_URL", ""),
        help="数据库连接地址；默认读取 PLATFORM_DATABASE_URL。",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=PROJECT_ROOT,
        help="项目根目录，用于检查来源文件路径。",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=(
            PROJECT_ROOT
            / "docs"
            / "development"
            / "artifacts"
            / "20261027-day2"
            / "knowledge-index-quality.json"
        ),
        help="质量验证 JSON 输出路径。",
    )
    return parser.parse_args()


def main() -> int:
    """检查正式索引并写质量报告；参数为空。"""

    args = parse_args()
    if not args.database_url:
        raise SystemExit("PLATFORM_DATABASE_URL or --database-url is required")
    project_root = args.project_root.resolve()
    engine = create_engine(args.database_url, pool_pre_ping=True)
    failures: list[str] = []
    with engine.connect() as connection:
        current_snapshot = connection.execute(
            text("SELECT current_batch_id FROM publication_state WHERE id=1")
        ).scalar_one()
        runs = [
            dict(row)
            for row in connection.execute(
                text(
                    """
                    SELECT DISTINCT ON (snapshot_id)
                           snapshot_id,status,index_version,parser_version,
                           embedding_model,embedding_version,counts_json
                    FROM knowledge_index_runs
                    ORDER BY snapshot_id, started_at DESC NULLS LAST
                    """
                )
            ).mappings()
        ]
        chunks = [
            dict(row)
            for row in connection.execute(
                text(
                    """
                    SELECT id,snapshot_id,fund_id,document_version_id,
                           report_period,page_number,section_title,chunk_type,
                           citation_tier,
                           content,content_hash,source_path
                    FROM document_chunks
                    """
                )
            ).mappings()
        ]
        pages = {
            (row[0], row[1])
            for row in connection.execute(
                text("SELECT document_version_id,page_number FROM document_pages")
            )
        }
        embeddings = [
            dict(row)
            for row in connection.execute(
                text(
                    """
                    SELECT chunk_id,model_name,model_version,dimensions,content_hash
                    FROM chunk_embeddings
                    """
                )
            ).mappings()
        ]
    engine.dispose()

    for run in runs:
        if run["status"] != "completed":
            failures.append(f"index run not completed: {run}")
    for chunk in chunks:
        if not all(
            chunk.get(field)
            for field in (
                "snapshot_id",
                "fund_id",
                "document_version_id",
                "report_period",
                "page_number",
                "content",
                "content_hash",
                "source_path",
            )
        ):
            failures.append(f"chunk missing required field: {chunk['id']}")
        if (chunk["document_version_id"], chunk["page_number"]) not in pages:
            failures.append(
                f"chunk references missing page: "
                f"{chunk['document_version_id']}:{chunk['page_number']}"
            )
        if chunk["chunk_type"] == "page_text":
            hash_source = (
                f"{chunk['page_number']}\npage_text\n{chunk['content']}"
            )
        else:
            hash_source = (
                f"{chunk['page_number']}\n{chunk['chunk_type']}\n"
                f"{chunk['section_title'] or ''}\n{chunk['content']}"
            )
        calculated = hashlib.sha256(hash_source.encode("utf-8")).hexdigest()
        if calculated != chunk["content_hash"]:
            failures.append(f"invalid chunk content hash: {chunk['id']}")
    missing_paths = sorted(
        {
            chunk["source_path"]
            for chunk in chunks
            if not (project_root / chunk["source_path"]).is_file()
        }
    )
    if missing_paths:
        failures.extend(f"source path missing: {path}" for path in missing_paths)

    embedding_by_chunk = {
        item["chunk_id"]: item
        for item in embeddings
        if item["model_name"] == "BAAI/bge-m3"
        and item["model_version"] == "v1"
    }
    missing_embeddings = [
        chunk["id"] for chunk in chunks if chunk["id"] not in embedding_by_chunk
    ]
    if missing_embeddings:
        failures.append(f"{len(missing_embeddings)} chunks lack BGE-M3 vectors")
    if any(int(item["dimensions"]) != 1024 for item in embeddings):
        failures.append("embedding dimension differs from 1024")
    chunk_hash_by_id = {chunk["id"]: chunk["content_hash"] for chunk in chunks}
    stale_embeddings = [
        item["chunk_id"]
        for item in embeddings
        if item["chunk_id"] in chunk_hash_by_id
        and item["content_hash"] != chunk_hash_by_id[item["chunk_id"]]
    ]
    if stale_embeddings:
        failures.append(f"{len(stale_embeddings)} embeddings reference stale chunks")

    by_snapshot: dict[str, dict[str, Any]] = {}
    for chunk in chunks:
        snapshot = str(chunk["snapshot_id"])
        entry = by_snapshot.setdefault(
            snapshot,
            {
                "chunks": 0,
                "funds": set(),
                "documents": set(),
                "citation_tiers": Counter(),
                "embeddings": 0,
            },
        )
        entry["chunks"] += 1
        entry["funds"].add(str(chunk["fund_id"]))
        entry["documents"].add(str(chunk["document_version_id"]))
        entry["citation_tiers"][chunk["citation_tier"]] += 1
        if chunk["id"] in embedding_by_chunk:
            entry["embeddings"] += 1
    serialized_snapshots = {}
    for snapshot, entry in by_snapshot.items():
        serialized_snapshots[snapshot] = {
            "chunks": entry["chunks"],
            "funds": len(entry["funds"]),
            "documents": len(entry["documents"]),
            "embeddings": entry["embeddings"],
            "embedding_coverage_percent": round(
                entry["embeddings"] / entry["chunks"] * 100,
                2,
            )
            if entry["chunks"]
            else None,
            "citation_tiers": dict(entry["citation_tiers"]),
        }
    report = {
        "schema_version": "knowledge-index-quality-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "passed" if not failures else "failed",
        "current_snapshot_id": str(current_snapshot),
        "checks": {
            "index_runs_completed": len(runs),
            "chunks": len(chunks),
            "pages": len(pages),
            "embeddings": len(embeddings),
            "missing_source_paths": len(missing_paths),
            "missing_embeddings": len(missing_embeddings),
            "stale_embeddings": len(stale_embeddings),
            "invalid_chunk_hashes": sum(
                1 for item in failures if item.startswith("invalid chunk content")
            ),
            "missing_or_invalid_pages": sum(
                1 for item in failures if "missing page" in item
            ),
        },
        "snapshots": serialized_snapshots,
        "failures": failures,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
