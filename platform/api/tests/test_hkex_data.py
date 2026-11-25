"""Tests against the authoritative HKEX workbook."""

from pathlib import Path

import pytest
from openpyxl import Workbook

from app.importers.crawler_data import read_crawler_sources
from app.importers.hkex_data import read_hkex_workbook


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_hkex_workbook_has_expected_authoritative_scope() -> None:
    """HKEX 主数据必须保留完整代码和快照。"""

    workbook = read_hkex_workbook(
        PROJECT_ROOT / "HKEX基金综合资料库_完整_20261008.xlsx"
    )

    assert len(workbook.funds) == 2479
    assert len({row.fund_code for row in workbook.funds}) == 2479
    assert len({row.manager_name for row in workbook.funds}) == 187
    assert len({row.umbrella_code for row in workbook.funds if row.umbrella_code}) == 306
    assert len(workbook.renames) == 661
    assert len(workbook.scale_headers) == 23


def test_crawler_workbook_is_only_a_supplement() -> None:
    """Crawler 工作簿应读取当前快照的补充行，包括南方东英完整记录。"""

    rows = read_crawler_sources(PROJECT_ROOT / "爬虫list.xlsx")

    # 行数随管理人接入持续增加，不再绑定历史 853 行快照；保留最低覆盖基线。
    assert len(rows) >= 853
    serials = [row.source_serial for row in rows]
    assert len(set(serials)) >= 819
    assert serials.count("1") >= 4

    csop_rows = [row for row in rows if row.sheet_name == "南方东英"]
    assert len(csop_rows) == 70
    assert all(row.annual_report_url and row.annual_run_command for row in csop_rows)


def test_previous_crawler_name_gaps_exist_in_rename_history() -> None:
    """此前未匹配的 crawler 名称应通过改名记录关联到 HKEX 代码。"""

    workbook = read_hkex_workbook(
        PROJECT_ROOT / "HKEX基金综合资料库_完整_20261008.xlsx"
    )
    aliases = {row.old_name: row.fund_code for row in workbook.renames}

    assert aliases["南方东英富时亚洲科技指数ETF"] == "BXJ950"
    assert aliases["嘉实中美科技50 ETF"] == "BWY617"
    assert aliases["可持续环球基建基金"] == "BSY416"
    assert aliases["欧洲特别时机基金"] == "AHU083"


def test_hkex_workbook_accepts_a_new_dynamic_fund_count(tmp_path: Path) -> None:
    """新快照基金行数变化时仍按代码完整性校验，而非固定历史行数。"""

    path = tmp_path / "hkex.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "主数据"
    sheet.append(["基金代码", "基金名称"])
    sheet.append(["TEST001", "测试基金一"])
    sheet.append(["TEST002", "测试基金二"])
    workbook.save(path)

    parsed = read_hkex_workbook(path)

    assert [row.fund_code for row in parsed.funds] == ["TEST001", "TEST002"]


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        ([{"基金代码": "", "基金名称": "代码缺失测试"}], "空基金代码"),
        ([{"基金代码": "TEST001", "基金名称": "测试一"}, {"基金代码": "TEST001", "基金名称": "测试二"}], "不唯一"),
    ],
)
def test_hkex_workbook_keeps_structural_code_validation(
    tmp_path: Path,
    rows: list[dict[str, str]],
    message: str,
) -> None:
    """动态行数不应削弱基金代码非空与唯一性约束。"""

    path = tmp_path / "invalid-hkex.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "主数据"
    sheet.append(["基金代码", "基金名称"])
    for row in rows:
        sheet.append([row["基金代码"], row.get("基金名称", "")])
    workbook.save(path)

    with pytest.raises(ValueError, match=message):
        read_hkex_workbook(path)
