#!/usr/bin/env python
"""Build page-level full-text and optional vector knowledge indexes."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any


API_ROOT = Path(__file__).resolve().parents[1] / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.db.session import create_session_factory  # noqa: E402
from app.knowledge.indexing import (  # noqa: E402
    IndexStats,
    index_current_snapshot,
    index_embeddings,
    index_historical_assets,
    latest_index_run,
    load_manifest_catalog,
)


def parse_args() -> argparse.Namespace:
    """解析索引命令参数；参数为空。"""

    parser = argparse.ArgumentParser(description="Build the fund report knowledge index.")
    parser.add_argument(
        "--database-url",
        default=os.environ.get("PLATFORM_DATABASE_URL", ""),
        help="数据库连接地址；默认读取 PLATFORM_DATABASE_URL。",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="项目根目录，用于定位 data 和 PDF 文件。",
    )
    parser.add_argument(
        "--scope",
        choices=("current", "historical", "both"),
        default="current",
        help="索引当前发布快照、可追溯历史资产或两者。",
    )
    parser.add_argument(
        "--embedding-model",
        default="",
        help="可选 BGE-M3 模型名；留空只构建全文索引。",
    )
    parser.add_argument(
        "--embedding-model-path",
        type=Path,
        help="可选本地模型目录；缺省时按 embedding-model 在线加载。",
    )
    parser.add_argument(
        "--embedding-version",
        default="v1",
        help="Embedding 模型版本标识。",
    )
    parser.add_argument(
        "--embedding-device",
        default="mps",
        help="PyTorch 设备，例如 mps、cpu。",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="可选索引质量 JSON 报告输出路径。",
    )
    return parser.parse_args()


def bge_m3_embedder(model_source: str, *, device: str):
    """加载本地 BGE-M3；参数 model_source/device 为模型来源和设备。"""

    try:
        from FlagEmbedding import BGEM3FlagModel
    except ImportError as exc:
        raise RuntimeError(
            "FlagEmbedding is not installed; use the isolated Python 3.12 "
            ".venv-embed runtime from platform/requirements-embeddings.txt"
        ) from exc
    model = BGEM3FlagModel(model_source, use_fp16=device != "cpu", devices=device)

    def embed(texts: list[str]) -> list[list[float]]:
        """生成 dense 向量；参数 texts 为切片文本。"""

        result = model.encode(
            texts,
            batch_size=8,
            max_length=2048,
        )
        return [list(map(float, vector)) for vector in result["dense_vecs"]]

    return embed


def stats_dict(stats: IndexStats) -> dict[str, Any]:
    """转换索引统计；参数 stats 为统计对象。"""

    return {
        "documents_seen": stats.documents_seen,
        "documents_indexed": stats.documents_indexed,
        "documents_skipped": stats.documents_skipped,
        "pages_written": stats.pages_written,
        "chunks_written": stats.chunks_written,
        "chunks_deleted": stats.chunks_deleted,
        "embeddings_written": stats.embeddings_written,
        "structured_jsons_seen": stats.structured_jsons_seen,
        "structured_jsons_indexed": stats.structured_jsons_indexed,
        "structured_jsons_skipped": stats.structured_jsons_skipped,
        "skipped": stats.skipped or [],
        "errors": stats.errors or [],
    }


def main() -> int:
    """执行索引构建并输出质量报告；参数为空。"""

    args = parse_args()
    if not args.database_url:
        raise SystemExit("PLATFORM_DATABASE_URL or --database-url is required")
    project_root = args.project_root.resolve()
    factory = create_session_factory(args.database_url)
    manifests = load_manifest_catalog(project_root / "data")
    report: dict[str, Any] = {
        "schema_version": "knowledge-index-report-v1",
        "project_root": str(project_root),
        "scope": args.scope,
        "embedding_model": args.embedding_model or None,
        "embedding_version": args.embedding_version,
        "manifest_count": len(manifests),
        "current": None,
        "historical": None,
    }
    embed = (
        bge_m3_embedder(
            str(
                (args.embedding_model_path or args.embedding_model).resolve()
                if args.embedding_model_path
                else args.embedding_model
            ),
            device=args.embedding_device,
        )
        if args.embedding_model
        else None
    )
    with factory() as session:
        if args.scope in {"current", "both"}:
            snapshot_id, stats = index_current_snapshot(
                session,
                project_root=project_root,
                manifests=manifests,
            )
            session.flush()
            if embed:
                run = latest_index_run(session, snapshot_id=snapshot_id)
                if run is None:
                    raise RuntimeError("current knowledge index run is missing")
                stats.embeddings_written += index_embeddings(
                    session,
                    index_run_id=run.id,
                    model_name=args.embedding_model,
                    model_version=args.embedding_version,
                    embed=embed,
                )
            report["current"] = {
                "snapshot_id": str(snapshot_id),
                **stats_dict(stats),
            }
        if args.scope in {"historical", "both"}:
            snapshot_id, stats = index_historical_assets(
                session,
                project_root=project_root,
            )
            session.flush()
            if embed:
                run = latest_index_run(session, snapshot_id=snapshot_id)
                if run is None:
                    raise RuntimeError("historical knowledge index run is missing")
                stats.embeddings_written += index_embeddings(
                    session,
                    index_run_id=run.id,
                    model_name=args.embedding_model,
                    model_version=args.embedding_version,
                    embed=embed,
                )
            report["historical"] = {
                "snapshot_id": str(snapshot_id),
                **stats_dict(stats),
            }
        session.commit()
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    errors = sum(
        len((report[key] or {}).get("errors") or [])
        for key in ("current", "historical")
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
