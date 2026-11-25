#!/usr/bin/env python
"""Resolve manager-specific extraction profiles for one PDF."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
from typing import Any
import unicodedata


PROJECT_ROOT = Path(
    os.environ.get("FACTSHEET_PROJECT_ROOT")
    or Path(__file__).resolve().parents[2]
).resolve()
DEFAULT_PROFILES_PATH = PROJECT_ROOT / "data" / "_cache" / "manager_profiles.json"
PROFILE_ENV = "FACTSHEET_MANAGER_PROFILES"
MAP_ENV = "FACTSHEET_MANAGER_MAP"


def _normalize_key(value: Any) -> str:
    """规范化基金名或文件名；参数 value 为待比较文本。"""

    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)


def _read_json(path: Path | None) -> dict[str, Any]:
    """读取 JSON 对象；参数 path 为配置文件路径。"""

    if path is None:
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> tuple[str, ...]:
    """把单个字符串或列表规范为去重字符串元组；参数 value 为配置值。"""

    if isinstance(value, str):
        values = [value]
    elif isinstance(value, (list, tuple)):
        values = [str(item) for item in value]
    else:
        values = []
    return tuple(dict.fromkeys(item.strip() for item in values if item.strip()))


def _as_aliases(value: Any) -> dict[str, tuple[str, ...]]:
    """把字段别名配置规范为字段到标签元组的映射；参数 value 为配置值。"""

    if not isinstance(value, dict):
        return {}
    aliases: dict[str, tuple[str, ...]] = {}
    for field_name, raw_aliases in value.items():
        labels = _as_list(raw_aliases)
        if labels:
            aliases[str(field_name)] = labels
    return aliases


def _as_bool(value: Any, default: bool = True) -> bool:
    """解析布尔配置；参数 value/default 为原值和默认值。"""

    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().casefold() not in {"0", "false", "no", "off"}
    return default if value is None else bool(value)


def _drift_threshold(value: Any) -> float:
    """解析版式漂移阈值；参数 value 为 profile 中的覆盖率阈值。"""

    try:
        threshold = float(value)
    except (TypeError, ValueError):
        threshold = 0.8
    return min(1.0, max(0.0, threshold))


@dataclass(frozen=True)
class ManagerProfile:
    """一名管理人的抽取策略；空 profile 表示保持原有行为。"""

    manager: str | None = None
    aliases: dict[str, tuple[str, ...]] = field(default_factory=dict)
    absent_fields: frozenset[str] = field(default_factory=frozenset)
    skip_refinements: frozenset[str] = field(default_factory=frozenset)
    vision_review: bool = True
    layout_id: str | None = None
    field_pipeline: str | None = None
    drift_threshold: float = 0.8
    source: str | None = None

    @property
    def configured(self) -> bool:
        """返回是否配置了任何非默认策略；参数为空。"""

        return bool(
            self.aliases
            or self.absent_fields
            or self.skip_refinements
            or not self.vision_review
            or self.layout_id
            or self.field_pipeline
        )

    def field_is_absent(self, field_name: str, context: str = "") -> bool:
        """判断字段是否可按管理人历史稳定缺失跳过。

        参数 field_name/context 为字段名和当前报告文本；若当前文本出现该字段的
        管理人别名，则不跳过，避免新报告开始披露后仍沿用历史结论。
        """

        if field_name not in self.absent_fields:
            return False
        text = str(context or "").casefold()
        if text:
            for alias in self.aliases.get(field_name, ()):
                if alias.casefold() in text:
                    return False
        return True

    def skips(self, refinement: str) -> bool:
        """判断是否跳过某类复核；参数 refinement 为复核名称。"""

        if refinement in self.skip_refinements:
            return True
        return refinement.startswith("matrix_") and "matrix" in self.skip_refinements

    def alias_hint(self, fields: list[str] | tuple[str, ...]) -> str:
        """构造字段标签提示；参数 fields 为本次要复核的字段。"""

        lines = []
        for field_name in fields:
            labels = self.aliases.get(field_name) or ()
            if labels:
                lines.append(f"- {field_name}: {' / '.join(labels)}")
        if not lines:
            return ""
        return "该管理人月报常用标签（仅作定位提示，不据此推导）：\n" + "\n".join(lines)

    def to_meta(self) -> dict[str, Any]:
        """返回写入结果元数据的可审计摘要；参数为空。"""

        return {
            "manager": self.manager,
            "source": self.source,
            "configured": self.configured,
            "absent_fields": sorted(self.absent_fields),
            "skip_refinements": sorted(self.skip_refinements),
            "vision_review": self.vision_review,
            "alias_fields": sorted(self.aliases),
            "layout_id": self.layout_id,
            "field_pipeline": self.field_pipeline,
            "drift_threshold": self.drift_threshold,
        }


def load_profiles(path: Path | None = None) -> dict[str, ManagerProfile]:
    """读取管理人 profile；参数 path 为配置路径，默认读环境变量或 data/_cache。"""

    configured = path or os.environ.get(PROFILE_ENV) or DEFAULT_PROFILES_PATH
    config_path = Path(configured)
    raw = _read_json(config_path)
    entries = raw.get("profiles")
    if not isinstance(entries, dict):
        entries = raw
    profiles: dict[str, ManagerProfile] = {}
    for manager, value in entries.items():
        if manager in {"version", "updated_at"} or not isinstance(value, dict):
            continue
        profiles[str(manager)] = ManagerProfile(
            manager=str(manager),
            aliases=_as_aliases(value.get("aliases")),
            absent_fields=frozenset(_as_list(value.get("absent_fields"))),
            skip_refinements=frozenset(_as_list(value.get("skip_refinements"))),
            vision_review=_as_bool(value.get("vision_review"), True),
            layout_id=str(value.get("layout_id") or "").strip() or None,
            field_pipeline=str(value.get("field_pipelines") or value.get("field_pipeline") or "").strip() or None,
            drift_threshold=_drift_threshold(value.get("drift_threshold")),
            source=str(config_path),
        )
    return profiles


def load_manager_map(path: Path | None = None) -> dict[str, str]:
    """读取 PDF 到管理人的映射；参数 path 为映射文件路径。"""

    configured = path or os.environ.get(MAP_ENV)
    if not configured:
        return {}
    raw = _read_json(Path(configured))
    entries = raw.get("pdfs")
    if not isinstance(entries, dict):
        entries = raw
    mapping: dict[str, str] = {}
    for key, value in entries.items():
        if key in {"version", "generated_at", "updated_at"}:
            continue
        manager = value.get("manager") if isinstance(value, dict) else value
        manager_name = str(manager or "").strip()
        if manager_name:
            mapping[str(key)] = manager_name
    return mapping


def manager_for_pdf(pdf_path: Path, map_path: Path | None = None) -> str | None:
    """查找 PDF 所属管理人；参数 pdf_path/map_path 为 PDF 和映射路径。"""

    mapping = load_manager_map(map_path)
    if not mapping:
        return None
    path = Path(pdf_path)
    candidates = [str(path), str(path.resolve()), path.name, path.stem]
    for candidate in candidates:
        if candidate in mapping:
            return mapping[candidate]
    target = _normalize_key(path.stem)
    target_name = _normalize_key(path.name)
    for key, manager in mapping.items():
        if _normalize_key(Path(key).stem) == target:
            return manager
        if _normalize_key(key) == target_name:
            return manager
    return None


def profile_for_pdf(
    pdf_path: Path,
    *,
    profiles_path: Path | None = None,
    map_path: Path | None = None,
) -> ManagerProfile:
    """返回 PDF 对应的管理人 profile；参数为 PDF 与可选配置路径。"""

    manager = manager_for_pdf(pdf_path, map_path)
    profiles = load_profiles(profiles_path)
    if manager:
        if manager in profiles:
            return profiles[manager]
        normalized = _normalize_key(manager)
        for name, profile in profiles.items():
            if _normalize_key(name) == normalized:
                return profile
    return ManagerProfile(manager=manager)
