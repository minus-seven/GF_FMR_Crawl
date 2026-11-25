"""Canonical per-batch directory layout with read-only legacy PDF support."""

from __future__ import annotations

import re
from pathlib import Path


# HKEX 一级资产分类；货币基金走专用月报结构化提示词，但沿用统一产物布局。
CATEGORIES = ("固收", "权益", "中国精选", "货币")
FACTSHEET_DIRNAME = "factsheet"
MARKDOWN_DIRNAME = "markdown"
STRUCTURE_DIRNAME = "structure_data"
WORK_DIRNAME = "_work"
REPORTS_DIRNAME = "reports"
REPORT_MONTH_RE = re.compile(r"^20\d{2}-(0[1-9]|1[0-2])$")


def factsheet_root(run_root: Path) -> Path:
    return Path(run_root) / FACTSHEET_DIRNAME


def markdown_root(run_root: Path) -> Path:
    return Path(run_root) / MARKDOWN_DIRNAME


def structure_root(run_root: Path) -> Path:
    return Path(run_root) / STRUCTURE_DIRNAME


def work_root(run_root: Path) -> Path:
    return structure_root(run_root) / WORK_DIRNAME


def reports_root(run_root: Path) -> Path:
    return Path(run_root) / REPORTS_DIRNAME


def pdf_category_dir(run_root: Path, category: str, *, allow_legacy: bool = True) -> Path:
    """Return the new PDF category directory, falling back to the old layout for reads."""
    run_root = Path(run_root)
    current = factsheet_root(run_root) / category
    legacy = run_root / category
    if allow_legacy and not current.is_dir() and legacy.is_dir():
        return legacy
    return current


def pdf_categories_with_files(run_root: Path) -> tuple[str, ...]:
    """返回批次中实际含 PDF 的类别名（优先新布局，兼容旧布局）。

    参数 run_root：批次根目录；返回值按固定顺序（固收/权益/中国精选）在前，
    其余类别按名称排序，方便下拉框稳定展示。
    """
    run_root = Path(run_root)
    found: set[str] = set()
    modern = factsheet_root(run_root)
    modern_has_pdf = modern.is_dir() and any(
        child.is_dir() and any(child.glob("*.pdf"))
        for child in modern.iterdir()
    )
    if modern_has_pdf:
        for child in modern.iterdir():
            if child.is_dir() and any(child.glob("*.pdf")):
                found.add(child.name)
    else:
        for category in CATEGORIES:
            legacy = run_root / category
            if legacy.is_dir() and any(legacy.glob("*.pdf")):
                found.add(category)
    ordered = [category for category in CATEGORIES if category in found]
    ordered.extend(
        sorted(category for category in found if category not in CATEGORIES)
    )
    return tuple(ordered)


def is_batch_root(path: Path, categories: tuple[str, ...] | None = None) -> bool:
    """判断批次目录是否可处理。

    参数 path：批次根目录；categories：None 表示只要有任意含 PDF 的类别目录即可，
    否则要求传入的每个类别目录都存在。
    """
    path = Path(path)
    if categories:
        return all(pdf_category_dir(path, category).is_dir() for category in categories)
    return bool(pdf_categories_with_files(path))


def latest_batch_root(data_root: Path, categories: tuple[str, ...] | None = None) -> Path:
    """返回最新可处理批次。

    参数 data_root：data 根目录；categories：None 表示任意含 PDF 类别即可，
    否则要求包含传入的全部类别目录。
    """
    candidates = [
        child
        for child in Path(data_root).iterdir()
        if child.is_dir()
        and not child.name.startswith("_")
        and not REPORT_MONTH_RE.match(child.name)
        and is_batch_root(child, categories)
    ]
    if not candidates:
        raise FileNotFoundError(
            f"找不到包含 {' / '.join(categories)} 的批次目录：{data_root}"
        )
    return max(candidates, key=lambda child: child.stat().st_mtime)


def batch_root_from_pdf_input(path: Path) -> Path | None:
    """Find the batch root for a PDF, category directory, or factsheet directory."""
    path = Path(path).resolve()
    current = path.parent if path.is_file() else path
    if current.name in CATEGORIES:
        if current.parent.name == FACTSHEET_DIRNAME:
            return current.parent.parent
        return current.parent
    if current.name == FACTSHEET_DIRNAME:
        return current.parent
    if (current / FACTSHEET_DIRNAME).is_dir() or any(
        (current / category).is_dir() for category in CATEGORIES
    ):
        return current
    return None
