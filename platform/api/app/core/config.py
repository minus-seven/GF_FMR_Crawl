"""Runtime configuration for the D1 database platform."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_DATABASE_URL = "postgresql+psycopg:///fund_research"
DEFAULT_STATE_ROOT = PROJECT_ROOT.parent.parent / "fund-research-state"
DEFAULT_HKEX_CRAWLER_ROOT = PROJECT_ROOT / "vendor" / "HKEX_Fund_Crawl_v4"


def _path_from_env(name: str, fallback: Path) -> Path:
    value = str(os.environ.get(name) or "").strip()
    return Path(value).expanduser().resolve() if value else fallback.resolve()


@dataclass(frozen=True)
class Settings:
    """Configuration shared by migration, import and test commands.

    参数 database_url：SQLAlchemy 使用的 PostgreSQL 连接地址；默认连接本机
    `fund_research` 数据库。
    参数 project_root：资金研究项目根目录，用于解析 Excel、批次和报告路径。
    """

    database_url: str
    project_root: Path
    state_root: Path
    data_root: Path
    hkex_crawler_root: Path | None = None
    hkex_workbook: Path | None = None
    crawler_workbook: Path | None = None


def get_settings() -> Settings:
    """读取环境变量并返回不可变配置；参数为空，返回 Settings。"""

    project_root = Path(
        os.environ.get("FACTSHEET_PROJECT_ROOT", PROJECT_ROOT)
    ).resolve()
    state_root = _path_from_env(
        "FACTSHEET_STATE_ROOT",
        project_root.parent.parent / "fund-research-state",
    )
    legacy_data_root = project_root / "data"
    data_root = _path_from_env(
        "FACTSHEET_DATA_ROOT",
        legacy_data_root if legacy_data_root.exists() else state_root / "data",
    )
    hkex_crawler_root = _path_from_env(
        "HKEX_CRAWLER_ROOT",
        project_root / "vendor" / "HKEX_Fund_Crawl_v4",
    )
    hkex_workbook = _path_from_env(
        "HKEX_MASTER_WORKBOOK",
        state_root / "inputs" / "HKEX基金综合资料库_完整_20261008.xlsx",
    )
    if not hkex_workbook.is_file():
        legacy = project_root / "HKEX基金综合资料库_完整_20261008.xlsx"
        hkex_workbook = legacy if legacy.exists() else hkex_workbook
    crawler_workbook = _path_from_env(
        "FACTSHEET_CRAWLER_WORKBOOK",
        state_root / "inputs" / "爬虫list.xlsx",
    )
    if not crawler_workbook.is_file():
        legacy = project_root / "爬虫list.xlsx"
        crawler_workbook = legacy if legacy.exists() else crawler_workbook
    os.environ.setdefault("FACTSHEET_STATE_ROOT", str(state_root))
    os.environ.setdefault("FACTSHEET_DATA_ROOT", str(data_root))
    return Settings(
        database_url=os.environ.get("PLATFORM_DATABASE_URL", DEFAULT_DATABASE_URL),
        project_root=project_root,
        state_root=state_root,
        data_root=data_root,
        hkex_crawler_root=hkex_crawler_root,
        hkex_workbook=hkex_workbook,
        crawler_workbook=crawler_workbook,
    )


def resolve_managed_path(settings: Settings, value: str | Path) -> Path:
    """Resolve a stored project-relative path against the external data root."""

    path = Path(value)
    if path.is_absolute():
        return path.resolve()
    if path.parts and path.parts[0].casefold() == "data":
        return (settings.data_root.joinpath(*path.parts[1:])).resolve()
    return (settings.project_root / path).resolve()


def logical_data_path(path: Path, data_root: Path) -> Path:
    """Return the stable ``data/...`` path stored in database manifests."""

    return Path("data") / path.resolve().relative_to(data_root.resolve())
