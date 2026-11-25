"""Readers for the authoritative HKEX fund master workbook."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
import re
from typing import Any, Iterable

from openpyxl import load_workbook

from app.importers.common import normalize_text


SCALE_HEADER_RE = re.compile(r"^(?P<date>20\d{2}-\d{2}-\d{2})规模（亿港币）$")


@dataclass(frozen=True)
class HKEXFundRow:
    """One authoritative fund row from HKEX `主数据`.

    参数 values：按原表头保存的完整行，用于字段归一和来源追溯。
    """

    values: dict[str, Any]

    @property
    def fund_code(self) -> str:
        """返回 HKEX 唯一基金代码。"""

        return normalize_text(self.values.get("基金代码"))

    @property
    def fund_id(self) -> str:
        """返回 HKEX 主数据 ID。"""

        return normalize_text(self.values.get("ID"))

    @property
    def name_cn(self) -> str:
        """返回基金中文名。"""

        return normalize_text(self.values.get("基金名称"))

    @property
    def name_en(self) -> str:
        """返回基金英文名。"""

        return normalize_text(self.values.get("Fund name"))

    @property
    def manager_name(self) -> str:
        """返回基金管理人完整中文名。"""

        return normalize_text(self.values.get("基金管理人"))

    @property
    def manager_short_name(self) -> str:
        """返回基金管理人简称。"""

        return normalize_text(self.values.get("管理人简称"))

    @property
    def umbrella_code(self) -> str:
        """返回伞型基金中央编号。"""

        return normalize_text(self.values.get("伞型基金中央编号"))

    @property
    def umbrella_name(self) -> str:
        """返回伞型基金中文名。"""

        return normalize_text(self.values.get("伞型基金名称"))

    @property
    def category(self) -> str:
        """返回资产类别一级分类。"""

        return normalize_text(self.values.get("资产类别一级分类"))

    def get(self, name: str, default: str = "") -> str:
        """按原表头读取清理后的文本；参数 name/default 为字段和缺省值。"""

        return normalize_text(self.values.get(name)) or default


@dataclass(frozen=True)
class HKEXRenameRow:
    """One HKEX fund rename record.

    参数 fund_code：基金代码；old_name/new_name：旧新名称；
    detected_at：来源检测日期。
    """

    fund_code: str
    old_name: str
    new_name: str
    detected_at: date


@dataclass(frozen=True)
class HKEXWorkbook:
    """Parsed HKEX workbook.

    参数 funds：主数据工作表中的基金行；renames：改名记录；
    scale_headers：规模列与原日期。
    """

    funds: list[HKEXFundRow]
    renames: list[HKEXRenameRow]
    scale_headers: list[tuple[str, date]]


def _rows_as_dicts(worksheet) -> Iterable[dict[str, Any]]:
    """把工作表行转换为表头字典；参数 worksheet：OpenPyXL 工作表。"""

    rows = worksheet.iter_rows(values_only=True)
    try:
        header = next(rows)
    except StopIteration:
        return
    names = [normalize_text(value) for value in header]
    for row in rows:
        if not any(value not in (None, "") for value in row):
            continue
        yield {
            name: row[index] if index < len(row) else None
            for index, name in enumerate(names)
            if name
        }


def parse_hkex_date(value: object) -> date | None:
    """解析 HKEX 日期单元格；参数 value：ISO、YYYYMMDD 或日期值。"""

    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = normalize_text(value)
    for pattern in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def read_hkex_workbook(path: Path) -> HKEXWorkbook:
    """读取并严格校验 HKEX 权威主数据工作簿；参数 path：工作簿路径。"""

    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        if "主数据" not in workbook.sheetnames:
            raise ValueError(f"{path} 缺少“主数据”工作表")
        fund_rows = [HKEXFundRow(row) for row in _rows_as_dicts(workbook["主数据"])]
        codes = [row.fund_code for row in fund_rows]
        # 行数随 HKEX 发布快照变化，不能再用历史快照的固定行数校验。
        # 仍保留“至少一行 + 代码非空 + 代码唯一”三项结构性校验，
        # 防止把空表、错表或重复导入的工作簿发布成正式主数据。
        if not fund_rows:
            raise ValueError("HKEX 主数据没有基金行")
        if any(not code for code in codes):
            raise ValueError("HKEX 主数据存在空基金代码")
        if len(codes) != len(set(codes)):
            raise ValueError("HKEX 主数据基金代码不唯一")

        rename_rows = []
        if "改名" in workbook.sheetnames:
            for row in _rows_as_dicts(workbook["改名"]):
                detected = parse_hkex_date(row.get("变动检测日期"))
                if detected is None:
                    continue
                rename_rows.append(
                    HKEXRenameRow(
                        fund_code=normalize_text(row.get("基金代码")),
                        old_name=normalize_text(row.get("旧的基金名称")),
                        new_name=normalize_text(row.get("新的基金名称")),
                        detected_at=detected,
                    )
                )

        header = next(workbook["主数据"].iter_rows(values_only=True))
        scale_headers = []
        for value in header:
            name = normalize_text(value)
            match = SCALE_HEADER_RE.match(name)
            if match:
                scale_headers.append(
                    (name, datetime.strptime(match.group("date"), "%Y-%m-%d").date())
                )
        return HKEXWorkbook(
            funds=fund_rows,
            renames=rename_rows,
            scale_headers=scale_headers,
        )
    finally:
        workbook.close()


def detected_at_utc(value: date) -> datetime:
    """把 HKEX 检测日期转换为 UTC 时间戳；参数 value：检测日期。"""

    return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
