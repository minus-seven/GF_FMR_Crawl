"""Unit tests for deterministic MinerU manifest selection."""

from __future__ import annotations

import json
import os
from pathlib import Path

from app.importers.common import normalize_name
from app.knowledge.indexing import load_manifest_catalog


def _write_manifest(
    root: Path,
    directory: str,
    *,
    source_name: str,
    sha256: str,
    modified_at: int,
) -> Path:
    """写入用于选择测试的最小 manifest；参数为目录、来源和修改时间。"""

    path = root / directory / "manifest.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "status": "done",
                "mode": "agent",
                "source_pdf": f"/data/{source_name}.pdf",
                "source_sha256": sha256,
            }
        ),
        encoding="utf-8",
    )
    os.utime(path, (modified_at, modified_at))
    return path


def test_manifest_catalog_prefers_uniquely_mapped_fund(tmp_path: Path) -> None:
    """同一 SHA 下应优先选择能唯一映射基金的 manifest。"""

    sha256 = "a" * 64
    unique = _write_manifest(
        tmp_path,
        "old-unique",
        source_name="摩根基金_-_全方位新兴市场基金",
        sha256=sha256,
        modified_at=100,
    )
    newer_ambiguous = _write_manifest(
        tmp_path,
        "new-ambiguous",
        source_name="新兴市场股票基金",
        sha256=sha256,
        modified_at=200,
    )

    default_catalog = load_manifest_catalog(tmp_path)
    fund_aware_catalog = load_manifest_catalog(
        tmp_path,
        fund_name_counts={
            normalize_name("摩根基金_-_全方位新兴市场基金"): 1,
            normalize_name("新兴市场股票基金"): 6,
        },
    )

    assert default_catalog[sha256].path == newer_ambiguous
    assert fund_aware_catalog[sha256].path == unique
