"""Reader for crawler worksheet URL and command supplements."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from openpyxl import load_workbook

from app.importers.common import normalize_text


NON_COMPANY_SHEETS = {"Sheet1", "Sheet2", "下载方法说明"}


@dataclass(frozen=True)
class CrawlerReportRow:
    """One crawler worksheet row carrying source navigation metadata.

    参数 sheet_name：公司页表；excel_row：Excel 行号；
    参数 source_serial：原“序号”列，仅作为补充匹配线索；
    参数 manager_name：来源行管理人；fund_name/umbrella_name：来源名称；
    参数 monthly_report_url：站点或月报链接；fallback_report_url：可选备选链接；
    run_command：现有月报下载命令；semiannual_report_url/semiannual_run_command：
    可选半年报/年报稳定入口和下载命令。
    """

    sheet_name: str
    excel_row: int
    source_serial: str
    manager_name: str
    fund_name: str
    umbrella_name: str
    monthly_report_url: str
    fallback_report_url: str
    run_command: str
    semiannual_report_url: str
    semiannual_run_command: str
    annual_report_url: str
    annual_run_command: str


def _header_index(header: tuple[object, ...]) -> dict[str, int]:
    """建立表头索引；参数 header：工作表首行。"""

    return {
        text: index
        for index, value in enumerate(header)
        if (text := normalize_text(value))
    }


def _cell(row: tuple[object, ...], columns: dict[str, int], name: str) -> str:
    """按表头读取单元格；参数 row/columns/name 描述读取目标。"""

    index = columns.get(name)
    return normalize_text(row[index]) if index is not None and index < len(row) else ""


def read_crawler_sources(path: Path) -> list[CrawlerReportRow]:
    """读取公司页表中的链接与运行方式；参数 path：爬虫工作簿路径。"""

    workbook = load_workbook(path, read_only=True, data_only=True)
    result: list[CrawlerReportRow] = []
    try:
        for sheet_name in workbook.sheetnames:
            if sheet_name in NON_COMPANY_SHEETS:
                continue
            worksheet = workbook[sheet_name]
            rows = worksheet.iter_rows(values_only=True)
            try:
                columns = _header_index(next(rows))
            except StopIteration:
                continue
            if "基金" not in columns:
                continue
            for excel_row, row in enumerate(rows, start=2):
                if not any(value not in (None, "") for value in row):
                    continue
                if not _cell(row, columns, "基金"):
                    continue
                result.append(
                    CrawlerReportRow(
                        sheet_name=sheet_name,
                        excel_row=excel_row,
                        source_serial=_cell(row, columns, "序号"),
                        manager_name=_cell(row, columns, "管理人") or sheet_name,
                        fund_name=_cell(row, columns, "基金"),
                        umbrella_name=_cell(row, columns, "伞"),
                        monthly_report_url=_cell(row, columns, "月报链接"),
                        fallback_report_url=_cell(row, columns, "备选链接(内地站)"),
                        run_command=_cell(row, columns, "运行方式"),
                        semiannual_report_url=_cell(row, columns, "半年报链接"),
                        semiannual_run_command=_cell(row, columns, "半年报运行方式"),
                        annual_report_url=_cell(row, columns, "年报链接"),
                        annual_run_command=_cell(row, columns, "年报运行方式"),
                    )
                )
    finally:
        workbook.close()
    return result
