"""综合资料库任务日志进度解析测试。"""

from datetime import datetime
from pathlib import Path

from app.services.master_database import dated_master_filename, parse_crawler_progress


def test_parse_master_database_crawler_progress() -> None:
    """旧爬虫的基金计数应转换为受控的 0..total 进度。"""

    assert parse_crawler_progress("成功获取中文详情页 [42/2504]: BYQ136") == (42, 2504)
    assert parse_crawler_progress("成功获取中文详情页 [ 7 / 10 ]: TEST001") == (7, 10)
    assert parse_crawler_progress("阶段4: 数据合并与更新...") is None
    assert parse_crawler_progress("完成 [999/0]") is None


def test_dated_master_filename_follows_reference_template() -> None:
    """缓存下载名应复用参考文件前缀并替换为本次更新日期。"""

    assert dated_master_filename(
        Path("HKEX基金综合资料库_完整_20261111.xlsx"),
        datetime(2026, 11, 26),
    ) == "HKEX基金综合资料库_完整_20261126.xlsx"

