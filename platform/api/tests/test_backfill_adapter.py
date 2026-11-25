"""Tests for the platform-side crawler backfill adapter."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import threading
import time

import pytest
from openpyxl import Workbook, load_workbook

from app.worker.adapters import (
    AdapterError,
    DownloadCandidate,
    _backfill_failure_reason,
    _backfill_command,
    _run_download_batch,
    _run_backfill_for_sheet,
    _parse_backfill_contract,
    _prepare_backfill_workbook,
)

from common import fund_link_backfill


def test_parse_backfill_contract_keeps_success_and_skip_reasons() -> None:
    """补全输出应解析 I/J、跳过原因，并忽略范围外行号。"""

    output = "\n".join(
        [
            "2\t基金二\t伞二",
            "OK\t2\t基金二\t伞二\thttps://example.com/fund\tpython download.py fund",
            "SKIP\t3\t基金三\t伞三\t\t官网未收录",
            "OK\t99\t范围外\t伞\tignored\tpython download.py outside",
        ]
    )

    successes, skips, probed = _parse_backfill_contract(
        output,
        expected_rows={2, 3},
        script_rel="companies/demo/demo_fund_link.py",
    )

    assert probed == {2}
    assert successes[2].monthly_report_url == "https://example.com/fund"
    assert successes[2].run_command == "python download.py fund"
    assert skips[3].reason == "官网未收录"
    assert 99 not in successes


def test_backfill_command_rejects_non_company_script() -> None:
    """补全命令只能调用 companies 下的 *_fund_link.py。"""

    with pytest.raises(AdapterError):
        _backfill_command(
            "run_all_downloaders.py",
            excel_path=Path("爬虫list.xlsx"),
            sheet_name="测试",
            row_numbers={2},
            probe=False,
        )


def test_shared_backfill_runner_filters_excel_rows(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--rows 应限制共享补全逻辑只处理任务冻结的行。"""

    rows = [
        {"excel_row": 2, "fund": "基金二", "umbrella": "伞二"},
        {"excel_row": 3, "fund": "基金三", "umbrella": "伞三"},
    ]
    monkeypatch.setattr(
        fund_link_backfill,
        "scan_incomplete_rows",
        lambda *args, **kwargs: rows,
    )

    result = fund_link_backfill.run_backfill(
        "unused.xlsx",
        "测试",
        catalog_loader=lambda: [],
        index_builder=lambda catalog: {},
        resolver=lambda item, index: (
            "https://example.com/fund",
            "python download.py fund",
            "",
        ),
        row_numbers={2},
    )

    output = capsys.readouterr().out
    assert result == 1
    assert "OK\t2\t" in output
    assert "OK\t3\t" not in output


def test_shared_backfill_retries_catalog_and_row_network_errors(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """目录加载和单行查询遇到瞬时网络错误时不应中止整张表。"""

    rows = [
        {"excel_row": 2, "fund": "基金二", "umbrella": "伞二"},
        {"excel_row": 3, "fund": "基金三", "umbrella": "伞三"},
    ]
    catalog_attempts = 0
    resolver_attempts = 0
    monkeypatch.setattr(
        fund_link_backfill,
        "scan_incomplete_rows",
        lambda *args, **kwargs: rows,
    )
    monkeypatch.setattr(fund_link_backfill.time, "sleep", lambda *_: None)

    def catalog_loader():
        """首次目录加载模拟超时。"""

        nonlocal catalog_attempts
        catalog_attempts += 1
        if catalog_attempts == 1:
            raise TimeoutError("temporary timeout")
        return [{"name": "catalog"}]

    def resolver(item, index):
        """第一行首次查询模拟连接失败。"""

        nonlocal resolver_attempts
        resolver_attempts += 1
        if item["excel_row"] == 2 and resolver_attempts == 1:
            raise ConnectionError("temporary connection error")
        return (
            f"https://example.com/{item['excel_row']}",
            f"python download.py {item['excel_row']}",
            "",
        )

    result = fund_link_backfill.run_backfill(
        "unused.xlsx",
        "测试",
        catalog_loader=catalog_loader,
        index_builder=lambda catalog: catalog,
        resolver=resolver,
    )

    output = capsys.readouterr().out
    assert result == 2
    assert catalog_attempts == 2
    assert "OK\t2\t" in output
    assert "OK\t3\t" in output


def test_shared_backfill_catalog_failure_is_row_level_skip(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """目录重试耗尽后应逐行返回 SKIP，而不是让整个进程退出 2。"""

    rows = [
        {"excel_row": 2, "fund": "基金二", "umbrella": "伞二"},
        {"excel_row": 3, "fund": "基金三", "umbrella": "伞三"},
    ]
    monkeypatch.setattr(
        fund_link_backfill,
        "scan_incomplete_rows",
        lambda *args, **kwargs: rows,
    )
    monkeypatch.setattr(fund_link_backfill.time, "sleep", lambda *_: None)

    def failed_catalog():
        """始终模拟 DNS 失败。"""

        raise ConnectionError("DNS unavailable")

    result = fund_link_backfill.run_backfill(
        "unused.xlsx",
        "测试",
        catalog_loader=failed_catalog,
        index_builder=lambda catalog: catalog,
        resolver=lambda item, index: ("", "", "unused"),
    )

    output = capsys.readouterr().out
    assert result == 0
    assert output.count("SKIP\t") == 2
    assert "官网目录读取失败" in output


def test_backfill_failure_reason_exposes_underlying_network_error() -> None:
    """退出码 2 的对外原因应包含脚本输出的简短根因。"""

    reason = _backfill_failure_reason(
        "\n[!] 查询失败：ReadTimeout: upstream timed out\n",
        2,
    )
    assert reason == "补全脚本退出码 2：ReadTimeout: upstream timed out"


def test_multi_script_backfill_writes_success_before_next_script(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """同一页表第二个脚本只应处理第一个脚本尚未补全的行。"""

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "富达"
    sheet.append(["基金", "伞", "大类", "小类", "管理人", "备注", "", "", "月报链接", "运行方式"])
    sheet.append(["基金二", "伞二", "权益", "", "富达", "", "", "", None, None])
    sheet.append(["基金三", "伞三", "权益", "", "富达", "", "", "", None, None])
    excel_path = tmp_path / "爬虫list.xlsx"
    workbook.save(excel_path)
    workbook.close()
    commands: list[list[str]] = []

    def fake_process(command, **kwargs):
        """模拟 HK/TW 两个脚本分别认领不同行。"""

        commands.append(command)
        row = command[command.index("--rows") + 1]
        if row == "2,3":
            output = "OK\t2\t基金二\t伞二\thttps://example.com/2\tpython two.py\n"
        elif row == "3":
            output = "OK\t3\t基金三\t伞三\thttps://example.com/3\tpython three.py\n"
        else:
            output = ""
        return 0, False, False, output

    monkeypatch.setattr(
        "app.worker.adapters._run_captured_process",
        fake_process,
    )
    sources = [
        SimpleNamespace(source_sheet="富达", source_row=2),
        SimpleNamespace(source_sheet="富达", source_row=3),
    ]
    outcomes = _run_backfill_for_sheet(
        sources,
        excel_path=excel_path,
        script_rels=[
            "companies/fidelity_hk/fidelity_fund_link.py",
            "companies/fidelity_tw/fidelity_tw_fund_link.py",
        ],
        log_path=tmp_path / "worker.log",
        cancel_event=threading.Event(),
        event_callback=lambda *args: None,
    )

    assert commands[0][commands[0].index("--rows") + 1] == "2,3"
    assert commands[1][commands[1].index("--rows") + 1] == "3"
    assert outcomes[2].status == "succeeded"
    assert outcomes[3].status == "succeeded"
    written = load_workbook(excel_path)["富达"]
    assert written.cell(2, 9).value == "https://example.com/2"
    assert written.cell(3, 9).value == "https://example.com/3"


def test_multi_script_unclaimed_rows_get_explicit_skip_reason(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """HK/TW 两套脚本均不认领时，不应再显示模糊的“未返回结果”。"""

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "富达"
    sheet.append(["基金", "伞", "大类", "小类", "管理人", "备注", "", "", "月报链接", "运行方式"])
    sheet.append(["基金二", "伞二", "权益", "", "富达", "", "", "", None, None])
    excel_path = tmp_path / "爬虫list.xlsx"
    workbook.save(excel_path)
    workbook.close()
    monkeypatch.setattr(
        "app.worker.adapters._run_captured_process",
        lambda command, **kwargs: (0, False, False, ""),
    )

    outcomes = _run_backfill_for_sheet(
        [SimpleNamespace(source_sheet="富达", source_row=2)],
        excel_path=excel_path,
        script_rels=[
            "companies/fidelity_hk/fidelity_fund_link.py",
            "companies/fidelity_tw/fidelity_tw_fund_link.py",
        ],
        log_path=tmp_path / "worker.log",
        cancel_event=threading.Event(),
        event_callback=lambda *args: None,
    )

    assert outcomes[2].status == "skipped"
    assert outcomes[2].reason == "港台两套补全脚本均未匹配到月报"


def test_task_workbook_clears_only_database_missing_rows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """任务副本清空缺来源行 I/J，正式 Excel 和其他行保持不变。"""

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "摩根"
    sheet.append(["基金", "伞", "大类", "小类", "管理人", "备注", "扩展", "扩展2", "月报链接", "运行方式"])
    sheet.append(["待补全基金", "伞一", "权益", "", "摩根大通", "", "", "", "旧链接", "旧命令"])
    sheet.append(["已完整基金", "伞二", "权益", "", "摩根大通", "", "", "", "保留链接", "保留命令"])
    original = tmp_path / "爬虫list.xlsx"
    workbook.save(original)
    workbook.close()
    monkeypatch.setattr("app.worker.adapters.PROJECT_ROOT", tmp_path)

    missing = SimpleNamespace(
        source_sheet="摩根",
        source_row=2,
        monthly_report_url=None,
        run_command=None,
    )
    complete = SimpleNamespace(
        source_sheet="摩根",
        source_row=3,
        monthly_report_url="保留链接",
        run_command="保留命令",
    )
    candidates = [
        DownloadCandidate(SimpleNamespace(), None, None, missing),
        DownloadCandidate(SimpleNamespace(), None, None, complete),
    ]

    task_copy = _prepare_backfill_workbook(
        candidates,
        job_root=tmp_path / "job",
        log_path=tmp_path / "worker.log",
    )

    assert task_copy is not None
    task_sheet = load_workbook(task_copy)["摩根"]
    assert task_sheet.cell(2, 9).value is None
    assert task_sheet.cell(2, 10).value is None
    assert task_sheet.cell(3, 9).value == "保留链接"
    assert task_sheet.cell(3, 10).value == "保留命令"
    original_sheet = load_workbook(original)["摩根"]
    assert original_sheet.cell(2, 9).value == "旧链接"
    assert original_sheet.cell(2, 10).value == "旧命令"


def test_download_batch_parallelizes_managers_and_serializes_same_manager(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """批量下载应对不同管理人并发、同一管理人保持串行。"""

    candidates = []
    for index, manager_id in enumerate(("a", "a", "b", "c", "d"), start=1):
        candidates.append(
            DownloadCandidate(
                scope=SimpleNamespace(fund_code_snapshot=f"F{index}"),
                fund=SimpleNamespace(name_cn=f"基金{index}", manager_id=manager_id),
                classification=SimpleNamespace(level_1="权益", level_2=None),
                source=SimpleNamespace(
                    run_command="python download.py",
                    manager_name_source=manager_id,
                    source_row=index,
                    monthly_report_url="https://example.com",
                ),
            )
        )

    lock = threading.Lock()
    active_total = 0
    active_managers: set[str] = set()
    max_total = 0
    duplicate_manager = False

    def fake_download(job, spec, **kwargs):
        nonlocal active_total, max_total, duplicate_manager
        with lock:
            if spec.manager_key in active_managers:
                duplicate_manager = True
            active_managers.add(spec.manager_key)
            active_total += 1
            max_total = max(max_total, active_total)
        time.sleep(0.05)
        with lock:
            active_total -= 1
            active_managers.discard(spec.manager_key)
        return "success", None, {"seed": spec.index}

    monkeypatch.setattr("app.worker.adapters._run_download", fake_download)
    outcomes = _run_download_batch(
        SimpleNamespace(id="job-test"),
        candidates,
        lookup={},
        staging_root=tmp_path / "staging",
        log_path=tmp_path / "worker.log",
        cancel_event=threading.Event(),
        event_callback=lambda *args: None,
    )

    assert len(outcomes) == 5
    assert max_total > 1
    assert duplicate_manager is False
