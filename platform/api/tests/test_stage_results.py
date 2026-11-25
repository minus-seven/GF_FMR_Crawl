"""Unit tests for per-fund parse and structure result mapping."""

import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from app.worker.adapters import (
    MODEL_WORKERS,
    _append_scope_result,
    _configured_mineru_mode,
    _fund_file_key,
    _manager_map_for_scopes,
    _missing_pdf_results,
    _stage_failure_message,
    _write_manager_map,
    run_structure_stage,
)
from run_all_mineru import _workers_for_mode
from app.services.jobs import _stage_runtime_progress
from app.worker.main import Worker, artifact_name_key
from structured_data import extract_fixed_income


def _scope(code: str, name: str) -> SimpleNamespace:
    """构造匹配测试所需的冻结基金字段；参数 code/name 为基金代码和名称。"""

    return SimpleNamespace(fund_code_snapshot=code, fund_name_snapshot=name)


def test_fund_file_key_matches_pdf_safe_names() -> None:
    """基金名与 PDF 文件名应忽略空白和标点后匹配。"""

    assert _fund_file_key("汇丰环球投资基金 - 超短期债券.pdf") == _fund_file_key(
        "汇丰环球投资基金_-_超短期债券"
    )


def test_manager_map_matches_scope_names_and_writes_pdf_paths(tmp_path) -> None:
    """结构化阶段应生成基金名和 PDF 绝对路径到管理人的映射。"""

    fund_id = uuid4()
    scope = SimpleNamespace(
        fund_id=fund_id,
        fund_code_snapshot="AAA001",
        fund_name_snapshot="汇丰环球投资基金 - 超短期债券",
    )
    run_root = tmp_path / "batch"
    pdf = run_root / "factsheet" / "固收" / "汇丰环球投资基金_-_超短期债券.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")

    class FakeSession:
        def execute(self, statement: object) -> SimpleNamespace:
            """返回一条基金到管理人的查询结果；参数 statement 为查询对象。"""

            return SimpleNamespace(all=lambda: [(fund_id, "汇丰投资管理")])

    mapping = _manager_map_for_scopes(FakeSession(), [scope])  # type: ignore[arg-type]
    target = _write_manager_map(run_root, mapping)

    assert mapping == {
        _fund_file_key("汇丰环球投资基金 - 超短期债券"): "汇丰投资管理"
    }
    assert target is not None
    payload = __import__("json").loads(target.read_text(encoding="utf-8"))
    assert payload["pdfs"][str(pdf.resolve())] == "汇丰投资管理"


def test_structure_stage_passes_manager_map_to_child_process(
    tmp_path,
    monkeypatch,
) -> None:
    """结构化子进程应收到本次任务生成的 manager map 路径。"""

    job_id = uuid4()
    fund_id = uuid4()
    job_root = tmp_path / "data" / "jobs" / str(job_id)
    run_root = job_root / "staging" / str(job_id)
    pdf = run_root / "factsheet" / "固收" / "基金 A.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    scope = SimpleNamespace(
        fund_id=fund_id,
        fund_code_snapshot="AAA001",
        fund_name_snapshot="基金 A",
    )

    class FakeResult:
        def __init__(self, rows: list) -> None:
            self.rows = rows

        def all(self) -> list:
            """返回预设查询结果；参数为空。"""

            return self.rows

    class FakeSession:
        def scalars(self, statement: object) -> list:
            """返回冻结基金范围；参数 statement 为查询对象。"""

            return [scope]

        def execute(self, statement: object) -> FakeResult:
            """管理人查询返回一行，分类查询返回空。"""

            return FakeResult(
                [(fund_id, "管理人甲")]
                if "managers" in str(statement)
                else []
            )

    captured: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> tuple[int, bool, bool]:
        """捕获子进程环境；参数 command/kwargs 为运行配置。"""

        captured["command"] = command
        captured["kwargs"] = kwargs
        return 0, False, False

    monkeypatch.setattr(
        "app.worker.adapters._run_fixed_process",
        fake_run,
    )
    result = run_structure_stage(
        FakeSession(),  # type: ignore[arg-type]
        SimpleNamespace(id=job_id, requested_stages=["structure"]),  # type: ignore[arg-type]
        job_root=job_root,
        cancel_event=__import__("threading").Event(),
        event_callback=lambda *args: None,
    )

    assert result.status == "success"
    extra_env = captured["kwargs"]["extra_env"]  # type: ignore[index]
    assert extra_env["FACTSHEET_MANAGER_MAP"].endswith("manager_map.json")
    assert Path(extra_env["FACTSHEET_MANAGER_MAP"]).is_file()


def test_local_structure_stage_passes_explicit_local_model_route(
    tmp_path,
    monkeypatch,
) -> None:
    """本地结构化任务必须显式传递本地 provider、模型和单并发参数。"""

    job_id = uuid4()
    fund_id = uuid4()
    job_root = tmp_path / "data" / "jobs" / str(job_id)
    run_root = job_root / "staging" / str(job_id)
    pdf = run_root / "factsheet" / "固收" / "基金 A.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    scope = SimpleNamespace(
        fund_id=fund_id,
        fund_code_snapshot="AAA001",
        fund_name_snapshot="基金 A",
    )

    class FakeResult:
        def all(self) -> list:
            """返回空查询结果；参数为空。"""

            return []

    class FakeSession:
        def scalars(self, statement: object) -> list:
            """返回冻结基金范围；参数 statement 为查询对象。"""

            return [scope]

        def execute(self, statement: object) -> FakeResult:
            """返回空管理人与分类映射；参数 statement 为查询对象。"""

            return FakeResult()

    captured: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> tuple[int, bool, bool]:
        """捕获本地子进程命令；参数 command/kwargs 为运行配置。"""

        captured["command"] = command
        captured["kwargs"] = kwargs
        return 0, False, False

    monkeypatch.setattr("app.worker.adapters._run_fixed_process", fake_run)
    result = run_structure_stage(
        FakeSession(),  # type: ignore[arg-type]
        SimpleNamespace(
            id=job_id,
            requested_stages=["structure"],
            scope_definition={
                "model_policy": {
                    "structured_provider": "local",
                    "local_model": "qwen3:4b",
                    "local_workers": 1,
                }
            },
        ),  # type: ignore[arg-type]
        job_root=job_root,
        cancel_event=__import__("threading").Event(),
        event_callback=lambda *args: None,
    )

    command = captured["command"]  # type: ignore[assignment]
    assert result.status == "success"
    assert command[command.index("--primary-model") + 1] == "qwen3:4b"
    assert command[command.index("--fallback-model") + 1] == "qwen3:4b"
    assert command[command.index("--vision-model") + 1] == ""
    assert command[command.index("--workers") + 1] == "1"
    extra_env = captured["kwargs"]["extra_env"]  # type: ignore[index]
    assert extra_env["FACTSHEET_STRUCTURED_PROVIDER"] == "local"


def test_stage_results_mark_missing_pdf_and_selected_failures() -> None:
    """阶段结果应保留无 PDF 跳过和报告失败原因。"""

    scope_a = _scope("AAA001", "基金 A")
    scope_b = _scope("BBB002", "基金 B")
    by_file = {
        _fund_file_key(scope_a.fund_name_snapshot): [scope_a],
        _fund_file_key(scope_b.fund_name_snapshot): [scope_b],
    }
    results, existing = _missing_pdf_results(by_file, {_fund_file_key("基金 B")})

    assert results == [
        {
            "fund_code": "AAA001",
            "fund_name": "基金 A",
            "status": "skipped",
            "reason": "没有下载 PDF，跳过当前阶段",
        }
    ]

    _append_scope_result(
        results,
        existing,
        [scope_b],
        status="failed",
        reason="解析接口超时",
    )
    assert results[-1]["fund_code"] == "BBB002"
    assert results[-1]["reason"] == "解析接口超时"


def test_stage_runtime_progress_uses_latest_running_event() -> None:
    """当前阶段进度应优先采用事件中的 processed/total。"""

    class FakeSession:
        def scalar(self, statement: object) -> SimpleNamespace:
            """返回固定事件；参数 statement 为未使用的查询对象。"""

            return SimpleNamespace(
                payload={
                    "stage": "parse",
                    "status": "running",
                    "processed": 14,
                    "total": 45,
                }
            )

    result = _stage_runtime_progress(
        FakeSession(),  # type: ignore[arg-type]
        SimpleNamespace(id=uuid4(), current_stage="parse"),  # type: ignore[arg-type]
    )
    assert result == (14, 45)


def test_parse_continuation_copies_only_required_artifacts(tmp_path) -> None:
    """解析续跑只复制 PDF，不复制 Markdown、结构化或报告。"""

    job_id = uuid4()
    source_id = uuid4()
    source_root = (
        tmp_path / "data" / "jobs" / str(source_id)
        / "staging" / str(source_id)
    )
    for relative, filename in (
        ("factsheet/固收", "fund.pdf"),
        ("markdown/固收", "full.md"),
        ("structure_data", "result.json"),
        ("reports", "report.json"),
    ):
        path = source_root / relative
        path.mkdir(parents=True)
        (path / filename).write_text("fixture", encoding="utf-8")
    job_root = tmp_path / "data" / "jobs" / str(job_id)
    worker = Worker.__new__(Worker)
    worker.settings = SimpleNamespace(project_root=tmp_path)
    job = SimpleNamespace(
        id=job_id,
        requested_stages=["parse"],
        scope_definition={"input_job_id": str(source_id)},
    )

    worker._copy_input_job_artifacts(job, job_root)  # type: ignore[arg-type]

    target = job_root / "staging" / str(job_id)
    assert (target / "factsheet/固收/fund.pdf").is_file()
    assert os.path.samefile(
        target / "factsheet/固收/fund.pdf",
        source_root / "factsheet/固收/fund.pdf",
    )
    assert not (target / "markdown").exists()
    assert not (target / "structure_data").exists()
    assert not (target / "reports").exists()


def test_subset_continuation_copies_only_selected_fund_artifacts(tmp_path) -> None:
    """从全量前序任务续跑子集时只复制子集 PDF 与 MinerU 目录。"""

    job_id = uuid4()
    source_id = uuid4()
    source_root = (
        tmp_path / "data" / "jobs" / str(source_id)
        / "staging" / str(source_id)
    )
    for name in ("基金 A", "基金 B"):
        pdf = source_root / "factsheet" / "权益" / f"{name}.pdf"
        pdf.parent.mkdir(parents=True, exist_ok=True)
        pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
        markdown = source_root / "markdown" / "权益" / f"{name}__hash"
        markdown.mkdir(parents=True, exist_ok=True)
        (markdown / "manifest.json").write_text(
            __import__("json").dumps({"source_pdf": f"{name}.pdf"}),
            encoding="utf-8",
        )
        (markdown / "full.md").write_text(name, encoding="utf-8")

    class FakeSession:
        def scalars(self, statement: object) -> list:
            """返回当前子集的冻结范围；参数 statement 为查询对象。"""

            return [SimpleNamespace(fund_name_snapshot="基金 A")]

    worker = Worker.__new__(Worker)
    worker.settings = SimpleNamespace(project_root=tmp_path)
    job = SimpleNamespace(
        id=job_id,
        requested_stages=["structure"],
        scope_definition={"input_job_id": str(source_id)},
    )

    worker._copy_input_job_artifacts(  # type: ignore[arg-type]
        job,
        tmp_path / "data" / "jobs" / str(job_id),
        session=FakeSession(),  # type: ignore[arg-type]
    )

    target = tmp_path / "data" / "jobs" / str(job_id) / "staging" / str(job_id)
    assert (target / "factsheet/权益/基金 A.pdf").is_file()
    assert not (target / "factsheet/权益/基金 B.pdf").exists()
    assert (target / "markdown/权益/基金 A__hash/full.md").is_file()
    assert not (target / "markdown/权益/基金 B__hash").exists()
    assert artifact_name_key("基金 A.pdf") == artifact_name_key("基金 A")


def test_structure_stage_uses_configured_workers_and_incremental_cache(
    tmp_path,
    monkeypatch,
) -> None:
    """结构化应显式使用配置并发并复用已有逐 PDF 结果。"""

    job_id = uuid4()
    fund_id = uuid4()
    job_root = tmp_path / "data" / "jobs" / str(job_id)
    pdf = job_root / "staging" / str(job_id) / "factsheet" / "固收" / "基金 A.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    scope = SimpleNamespace(
        fund_id=fund_id,
        fund_code_snapshot="AAA001",
        fund_name_snapshot="基金 A",
    )

    class FakeResult:
        def all(self) -> list:
            """返回空分类映射；参数为空。"""

            return []

    class FakeSession:
        def scalars(self, statement: object) -> list:
            """返回冻结基金范围；参数 statement 为查询对象。"""

            return [scope]

        def execute(self, statement: object) -> FakeResult:
            """返回空分类查询结果；参数 statement 为查询对象。"""

            return FakeResult()

    captured: dict[str, list[str]] = {}

    def fake_run(command: list[str], **kwargs: object) -> tuple[int, bool, bool]:
        """捕获阶段命令；参数 command/kwargs 为子进程调用配置。"""

        captured["command"] = command
        return 0, False, False

    monkeypatch.setattr(
        "app.worker.adapters._run_fixed_process",
        fake_run,
    )
    result = run_structure_stage(
        FakeSession(),  # type: ignore[arg-type]
        SimpleNamespace(id=job_id, requested_stages=["structure"]),  # type: ignore[arg-type]
        job_root=job_root,
        cancel_event=__import__("threading").Event(),
        event_callback=lambda *args: None,
    )

    assert result.status == "success"
    assert captured["command"][captured["command"].index("--workers") + 1] == str(
        MODEL_WORKERS
    )
    assert "--skip-existing" in captured["command"]


def test_mineru_route_defaults_to_lightweight_and_token_uses_auto(
    monkeypatch,
) -> None:
    """轻量路线直接 Agent，Token 路线使用可降级的 auto。"""

    monkeypatch.setattr(
        "app.worker.adapters.config_value",
        lambda *args: "lightweight",
    )
    assert _configured_mineru_mode() == "agent"
    monkeypatch.setattr(
        "app.worker.adapters.config_value",
        lambda *args: "token",
    )
    assert _configured_mineru_mode() == "auto"


def test_agent_parse_mode_caps_workers_for_result_rate_limit() -> None:
    """Agent 模式应按 500 次/分钟限制降低并发。"""

    assert _workers_for_mode("agent", 36, 100) <= 18
    assert _workers_for_mode("auto", 36, 100) <= 18
    assert _workers_for_mode("precise", 36, 100) == 36


def test_stage_failure_message_appends_concise_reason() -> None:
    """阶段错误应在退出码后附上简明原因。"""

    message = _stage_failure_message(
        "MinerU ",
        2,
        [
            {
                "status": "failed",
                "reason": "HTTP 429: You have exceeded the requests/min limit",
            }
        ],
    )
    assert message == "MinerU 退出码 2；1 份基金失败：接口触发限流"


def test_stage_failure_message_breaks_down_mixed_reasons() -> None:
    """多种失败原因应按数量拆分展示，避免把全部失败归咎于首个原因。"""

    message = _stage_failure_message(
        "结构化",
        2,
        [
            {"status": "failed", "reason": "HTTP 429: too many requests"},
            {"status": "failed", "reason": "HTTP 429: too many requests"},
            {"status": "failed", "reason": "Insufficient Balance"},
            {"status": "failed", "reason": "模型账户不可用，批次已熔断"},
        ],
    )

    assert message == (
        "结构化退出码 2；4 份基金失败"
        "（接口触发限流 2；模型账户不可用，批次已熔断 1；模型账户余额不足 1）"
    )


def test_fixed_income_complex_reviews_share_one_visual_call(
    monkeypatch,
) -> None:
    """三个复杂字段应共享一次视觉请求，减少重复图片 Token。"""

    calls: list[dict] = []

    def fake_images(*args: object, **kwargs: object) -> list[str]:
        """返回测试图片；参数为空。"""

        return ["data:image/png;base64,test"]

    def fake_chat(*args: object, **kwargs: object) -> dict:
        """返回三项视觉结果；参数为模型调用配置。"""

        calls.append(kwargs)
        return {
            "top_holdings": {"disclosed": True, "items": []},
            "duration_distribution": {"disclosed": False, "items": []},
            "geographic_allocation": {"disclosed": False, "items": []},
        }

    monkeypatch.setattr(
        extract_fixed_income,
        "pdf_page_image_urls",
        fake_images,
    )
    monkeypatch.setattr(
        extract_fixed_income,
        "_chat_image_json",
        fake_chat,
    )
    reviews = extract_fixed_income._review_complex_fields_from_pdf_images(
        SimpleNamespace(),  # type: ignore[arg-type]
        pdf_path=Path("fund.pdf"),
        vision_model="deepseek-flash",
        attempts=1,
        max_pages=6,
    )

    assert len(calls) == 1
    assert calls[0]["call_name"] == "fixed_income_complex_fields_visual_review"
    assert set(reviews) == {
        "top_holdings",
        "duration_distribution",
        "geographic_allocation",
    }
