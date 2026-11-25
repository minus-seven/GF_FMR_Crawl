"""Tests for manager-specific extraction profiles."""

from __future__ import annotations

import json
from pathlib import Path

from structured_data.manager_profiles import (
    ManagerProfile,
    load_manager_map,
    profile_for_pdf,
)


def _write_json(path: Path, value: dict) -> None:
    """写入测试 JSON；参数 path/value 为文件与内容。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_profile_for_pdf_matches_normalized_manager_map(tmp_path: Path) -> None:
    """PDF 文件名应通过标准化键匹配管理人并加载 profile。"""

    profiles_path = tmp_path / "profiles.json"
    map_path = tmp_path / "manager_map.json"
    _write_json(
        profiles_path,
        {
            "profiles": {
                "汇丰投资管理": {
                    "aliases": {"portfolio_duration_years": ["組合久期"]},
                    "absent_fields": ["duration_distribution"],
                    "skip_refinements": ["matrix_flash"],
                    "vision_review": False,
                }
            }
        },
    )
    _write_json(
        map_path,
        {"pdfs": {"汇丰环球投资基金 - 超短期债券": "汇丰投资管理"}},
    )

    profile = profile_for_pdf(
        tmp_path / "汇丰环球投资基金_-_超短期债券.pdf",
        profiles_path=profiles_path,
        map_path=map_path,
    )

    assert profile.manager == "汇丰投资管理"
    assert profile.configured is True
    assert profile.absent_fields == frozenset({"duration_distribution"})
    assert profile.skips("matrix_flash") is True
    assert profile.vision_review is False


def test_absent_field_is_not_skipped_when_current_context_has_alias() -> None:
    """当前报告出现管理人别名时，历史缺失结论不得覆盖新披露。"""

    profile = ManagerProfile(
        manager="示例管理人",
        aliases={"duration_distribution": ("久期分布", "Duration distribution")},
        absent_fields=frozenset({"duration_distribution"}),
    )

    assert profile.field_is_absent("duration_distribution", "本页无相关字段") is True
    assert profile.field_is_absent("duration_distribution", "久期分布 1-3年 20%") is False


def test_skip_refinements_supports_matrix_shorthand() -> None:
    """matrix 简写应同时跳过 flash 和 fallback 矩阵复核。"""

    profile = ManagerProfile(skip_refinements=frozenset({"matrix"}))

    assert profile.skips("matrix_flash") is True
    assert profile.skips("matrix_fallback") is True
    assert profile.skips("vision") is False


def test_load_manager_map_accepts_structured_values(tmp_path: Path) -> None:
    """管理人名映射既支持字符串也支持带 manager 字段的对象。"""

    map_path = tmp_path / "manager_map.json"
    _write_json(
        map_path,
        {
            "pdfs": {
                "基金 A": {"manager": "管理人甲"},
                "基金 B": "管理人乙",
            }
        },
    )

    assert load_manager_map(map_path) == {
        "基金 A": "管理人甲",
        "基金 B": "管理人乙",
    }
