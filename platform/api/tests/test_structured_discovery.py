"""Tests for structured batch discovery."""

from pathlib import Path

from app.importers.service import discover_combined_files, find_latest_structured_batch
from run_all_structured_data import command_worker_count, split_stage_workers


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_latest_structured_batch_contains_expected_sources() -> None:
    """最新结构化批次应可发现固收、权益和混合输入。"""

    batch = find_latest_structured_batch(PROJECT_ROOT / "data")
    files = discover_combined_files(batch)
    names = {path.name for path in files}

    assert batch.name == "20260908162205"
    assert "固收_基金提取结果_combined.json" in names
    assert "权益_基金提取结果_combined.json" in names
    assert "混合_基金提取结果_combined.json" in names


def test_structured_pipeline_distributes_workers_across_categories() -> None:
    """混合类别应并发执行并保持总模型并发上限。"""

    assert split_stage_workers(16, 1) == 16
    assert split_stage_workers(16, 2) == 8
    command = ["python", "run_batch.py", "--workers", "16", "--source", "mineru"]
    assert command_worker_count(command, 8) == [
        "python",
        "run_batch.py",
        "--workers",
        "8",
        "--source",
        "mineru",
    ]
