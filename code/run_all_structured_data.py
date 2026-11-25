# -*- coding: utf-8 -*-
"""One-click structured extraction driven by actual batch categories."""

from __future__ import annotations

import argparse
import concurrent.futures
from datetime import datetime
import hashlib
import json
import os
import queue
import shutil
import subprocess
import sys
from pathlib import Path
import threading
import time
import unicodedata

from batch_layout import (
    latest_batch_root,
    markdown_root,
    pdf_category_dir,
    pdf_categories_with_files,
    structure_root,
    work_root,
)
from batch_selector import select_batch_root
from app_config import apply_environment
from structured_data.token_usage import merge_model_usage

apply_environment()

CODE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = Path(os.environ.get("FACTSHEET_PROJECT_ROOT") or CODE_DIR.parent).resolve()
DATA_DIR = Path(os.environ.get("FACTSHEET_DATA_ROOT") or PROJECT_ROOT / "data").resolve()
RUN_FIXED = CODE_DIR / "run_all_fixed_income.py"
RUN_EQUITY = CODE_DIR / "run_all_equity_fund.py"
CURRENCY_PROMPT = CODE_DIR / "structured_data" / "money_market_prompt.md"
CATEGORY_TO_EXTRACTOR = {
    # 类别名 -> 使用的抽取器：fixed=固收抽取器，equity=权益抽取器
    "固收": "fixed",
    "权益": "equity",
    "中国精选": "equity",
    "混合": "equity",
    "货币": "money",
}


class StructuredDataProgress:
    """Render model extraction progress on one terminal line."""

    def __init__(self, expected_pdfs: list[Path], stream=None, bar_width: int = 20):
        self.expected = {
            str(path.resolve()): path for path in expected_pdfs
        }
        content_groups: dict[str, set[str]] = {}
        for pdf_key, pdf_path in self.expected.items():
            group_key = pdf_key
            if pdf_path.is_file():
                digest = hashlib.sha256()
                with pdf_path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                group_key = digest.hexdigest()
            content_groups.setdefault(group_key, set()).add(pdf_key)
        self.related_paths = {
            pdf_key: group
            for group in content_groups.values()
            for pdf_key in group
        }
        self.total = len(self.expected)
        self.stream = stream or sys.stdout
        self.bar_width = max(10, int(bar_width))
        self.completed_paths: set[str] = set()
        self.succeeded = 0
        self.failed = 0
        self.active: dict[str, str] = {}
        self.failures: list[dict[str, str]] = []
        self.model_usage: dict[str, dict[str, int]] = {}
        self.stage = "准备"
        self.started_monotonic = time.monotonic()
        self.activity_tick = 0
        self.last_width = 0
        self.closed = False
        self.lock = threading.Lock()
        isatty = getattr(self.stream, "isatty", None)
        self.interactive = bool(isatty and isatty()) or os.environ.get("FACTSHEET_PROGRESS_PIPE") == "1"

    @staticmethod
    def _display_width(value: str) -> int:
        return sum(
            2 if unicodedata.east_asian_width(character) in {"F", "W"} else 1
            for character in value
        )

    @staticmethod
    def _short_name(value: str, limit: int = 14) -> str:
        value = str(value or "").strip()
        if len(value) <= limit:
            return value
        return value[: max(1, limit - 1)] + "…"

    @classmethod
    def _fit_terminal_width(cls, value: str, columns: int) -> str:
        limit = max(10, int(columns) - 1)
        if cls._display_width(value) <= limit:
            return value
        suffix = "…"
        available = max(1, limit - cls._display_width(suffix))
        result = []
        used = 0
        for character in value:
            width = cls._display_width(character)
            if used + width > available:
                break
            result.append(character)
            used += width
        return "".join(result) + suffix

    def _line(self) -> str:
        completed = len(self.completed_paths)
        ratio = completed / self.total if self.total else 1
        filled = min(self.bar_width, int(ratio * self.bar_width))
        bar = "█" * filled + "░" * (self.bar_width - filled)
        pulse = "|/-\\"[self.activity_tick % 4]
        active_names = list(self.active.values())
        if active_names:
            visible = self._short_name(active_names[0])
            if len(active_names) > 1:
                visible += f" 等{len(active_names)}项"
            current_note = f"  处理中{len(active_names)}项：{visible}"
        else:
            current_note = f"  阶段：{self.stage}"
        elapsed = max(0, int(time.monotonic() - self.started_monotonic))
        elapsed_text = (
            f"{elapsed // 3600}:{(elapsed % 3600) // 60:02d}:{elapsed % 60:02d}"
            if elapsed >= 3600
            else f"{elapsed // 60:02d}:{elapsed % 60:02d}"
        )
        return (
            f"Excel 解析 {pulse} [{bar}] {completed}/{self.total}"
            f"  成功 {self.succeeded}  失败 {self.failed}"
            f"  运行 {elapsed_text}{current_note}"
        )

    def _render(self, *, final: bool = False) -> None:
        if not self.interactive and not final:
            return
        columns = shutil.get_terminal_size(fallback=(100, 24)).columns
        line = self._fit_terminal_width(self._line(), columns)
        line_width = self._display_width(line)
        self.stream.write(
            "\r" + line + " " * max(0, self.last_width - line_width)
        )
        if final:
            self.stream.write("\n")
        self.stream.flush()
        self.last_width = line_width

    def set_stage(self, stage: str) -> None:
        with self.lock:
            if self.closed:
                return
            self.stage = stage
            self.activity_tick += 1
            self._render()

    def pulse(self) -> None:
        with self.lock:
            if self.closed:
                return
            self.activity_tick += 1
            self._render()

    def handle_event(self, event: dict) -> None:
        pdf_value = str(event.get("pdf") or "").strip()
        if not pdf_value:
            return
        pdf_key = str(Path(pdf_value).resolve())
        with self.lock:
            if self.closed:
                return
            event_name = event.get("event")
            if event_name == "started":
                self.active[pdf_key] = Path(pdf_value).name
            elif event_name == "finished" or event.get("status"):
                related_paths = self.related_paths.get(pdf_key, {pdf_key})
                newly_completed = related_paths - self.completed_paths
                if not newly_completed:
                    return
                for related_path in related_paths:
                    self.active.pop(related_path, None)
                self.completed_paths.update(newly_completed)
                if event.get("status") == "failed":
                    self.failed += len(newly_completed)
                    error = str(
                        event.get("error")
                        or event.get("first_failure")
                        or "模型提取失败"
                    )
                    for completed_path in sorted(newly_completed):
                        self.failures.append(
                            {
                                "pdf": str(
                                    self.expected.get(
                                        completed_path,
                                        Path(completed_path),
                                    )
                                ),
                                "error": error,
                            }
                        )
                else:
                    self.succeeded += len(newly_completed)
                merge_model_usage(self.model_usage, event.get("model_usage"))
            self.activity_tick += 1
            self._render()

    def mark_missing_failed(self) -> None:
        for pdf_key, pdf_path in self.expected.items():
            if pdf_key not in self.completed_paths:
                self.handle_event(
                    {
                        "event": "finished",
                        "pdf": str(pdf_path),
                        "status": "failed",
                        "error": "子任务中断，未返回该基金的处理结果",
                    }
                )

    def finish(self) -> None:
        with self.lock:
            if self.closed:
                return
            self.active.clear()
            self.closed = True
            self._render(final=True)


def _read_process_output(stream, output_queue: queue.Queue) -> None:
    try:
        for line in iter(stream.readline, ""):
            output_queue.put(line)
    finally:
        stream.close()
        output_queue.put(None)


def decode_json_objects(value: str) -> list[dict]:
    """Recover one or more JSON objects even when concurrent output is joined."""
    decoder = json.JSONDecoder()
    objects: list[dict] = []
    position = 0
    while position < len(value):
        start = value.find("{", position)
        if start < 0:
            break
        try:
            item, end = decoder.raw_decode(value, start)
        except json.JSONDecodeError:
            position = start + 1
            continue
        if isinstance(item, dict):
            objects.append(item)
        position = max(end, start + 1)
    return objects


def run_structured_command(
    command: list[str],
    *,
    progress: StructuredDataProgress,
    log_file,
    log_lock: threading.Lock,
) -> int:
    """运行一个结构化类别命令；参数 command/progress/log/lock 为运行上下文。"""

    with log_lock:
        log_file.write(
            "\n$ " + " ".join(f'"{part}"' if " " in part else part for part in command) + "\n"
        )
        log_file.flush()
    process = subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
        env={
            **os.environ,
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUNBUFFERED": "1",
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    output_queue: queue.Queue = queue.Queue()
    reader = threading.Thread(
        target=_read_process_output,
        args=(process.stdout, output_queue),
        name="structured-output-reader",
        daemon=True,
    )
    reader.start()
    try:
        while True:
            try:
                line = output_queue.get(timeout=1)
            except queue.Empty:
                progress.pulse()
                continue
            if line is None:
                break
            with log_lock:
                log_file.write(line)
                log_file.flush()
            for event in decode_json_objects(line):
                progress.handle_event(event)
    except KeyboardInterrupt:
        process.terminate()
        raise
    finally:
        reader.join(timeout=2)
        log_file.flush()
    return int(process.wait())


def command_worker_count(command: list[str], workers: int) -> list[str]:
    """返回使用指定并发的命令；参数 command/workers 为原命令和目标并发。"""

    updated = list(command)
    try:
        index = updated.index("--workers")
    except ValueError:
        return updated
    if index + 1 < len(updated):
        updated[index + 1] = str(max(1, int(workers)))
    return updated


def split_stage_workers(total_workers: int, stage_count: int) -> int:
    """在类别流水线间分配并发；参数 total_workers/stage_count 为总量和类别数。"""

    return max(1, int(total_workers) // max(1, int(stage_count)))


def latest_run_root() -> Path:
    return latest_batch_root(DATA_DIR)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="一次按实际类别映射执行结构化并生成 Excel；货币基金使用专用月报提示词。"
    )
    parser.add_argument(
        "run_root",
        nargs="?",
        type=Path,
        help="包含 factsheet/* 的批次目录（按实际类别执行）；默认使用最新批次。",
    )
    parser.add_argument("--workers", type=int, default=int(os.environ.get("FACTSHEET_MODEL_WORKERS", "32")), help="大模型解析并发数；默认由 config.ini 设置，不影响 MinerU 的 vlm 精度。")
    parser.add_argument("--attempts", type=int, default=int(os.environ.get("FACTSHEET_API_ATTEMPTS", "3")))
    parser.add_argument("--timeout", type=int, default=int(os.environ.get("FACTSHEET_API_TIMEOUT", "300")))
    parser.add_argument("--primary-model", default=os.environ.get("FACTSHEET_PRIMARY_MODEL", "deepseek-flash"))
    parser.add_argument("--fallback-model", default=os.environ.get("FACTSHEET_FALLBACK_MODEL", "deepseek-v4-pro"))
    parser.add_argument("--vision-model", default=os.environ.get("FACTSHEET_VISION_MODEL", "deepseek-flash"))
    parser.add_argument("--source", choices=("mineru", "auto", "file-id"), default="mineru")
    parser.add_argument("--mineru-model", choices=("vlm", "pipeline"), default=os.environ.get("FACTSHEET_MINERU_MODEL", "vlm"))
    parser.add_argument("--mineru-force", action="store_true")
    parser.add_argument(
        "--allow-mineru-api",
        action="store_true",
        help="允许 Excel 阶段补调 MinerU；默认严格只读缓存。",
    )
    parser.add_argument("--mineru-poll-timeout", type=int, default=900)
    parser.add_argument(
        "--no-thinking",
        action="store_true",
        default=os.environ.get("FACTSHEET_STRUCTURED_THINKING", "false").lower()
        not in {"1", "true", "yes"},
        help="关闭模型思考模式；DeepSeek 默认关闭以降低延迟和成本。",
    )
    parser.add_argument("--thinking-budget", type=int)
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument(
        "--structure-outdir",
        "--combined-outdir",
        dest="structure_outdir",
        type=Path,
        help="最终 Excel 输出目录；默认使用批次下的 structure_data。",
    )
    parser.add_argument(
        "--select-batch",
        action="store_true",
        help="运行前列出 data 下的批次文件夹并交互选择。",
    )
    return parser.parse_args(argv)


def common_options(args: argparse.Namespace) -> list[str]:
    options = [
        "--workers", str(args.workers),
        "--attempts", str(args.attempts),
        "--timeout", str(args.timeout),
        "--primary-model", args.primary_model,
        "--fallback-model", args.fallback_model,
        "--vision-model", args.vision_model,
        "--source", args.source,
        "--mineru-model", args.mineru_model,
        "--mineru-poll-timeout", str(args.mineru_poll_timeout),
    ]
    if args.no_thinking:
        options.append("--no-thinking")
    if args.thinking_budget is not None:
        options.extend(["--thinking-budget", str(args.thinking_budget)])
    if args.skip_existing:
        options.append("--skip-existing")
    if args.mineru_force:
        options.append("--mineru-force")
    if not args.allow_mineru_api:
        options.append("--mineru-cache-only")
    return options


def script_command(script: Path, *arguments: str) -> list[str]:
    worker_exe = str(os.environ.get("FACTSHEET_WORKER_EXE") or "").strip()
    if worker_exe:
        return [
            worker_exe,
            "--worker-script",
            f"code/{script.relative_to(CODE_DIR).as_posix()}",
            *arguments,
        ]
    return [sys.executable, str(script), *arguments]


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.select_batch and args.run_root:
        raise ValueError("使用 --select-batch 时不能同时指定 run_root。")
    if args.select_batch:
        selected = select_batch_root(DATA_DIR, action_name=" Excel 解析")
        if selected is None:
            return 0
        run_root = selected.resolve()
    else:
        run_root = args.run_root.resolve() if args.run_root else latest_run_root()
    present_categories = pdf_categories_with_files(run_root)
    supported_categories = [
        category
        for category in present_categories
        if category in CATEGORY_TO_EXTRACTOR
    ]
    unsupported_categories = [
        category
        for category in present_categories
        if category not in CATEGORY_TO_EXTRACTOR
    ]
    if not supported_categories:
        if present_categories:
            raise FileNotFoundError(
                f"{run_root} 仅包含暂不支持结构化的类别："
                f"{'、'.join(present_categories)}"
            )
        raise FileNotFoundError(f"{run_root} 的 factsheet 下没有可处理的 PDF")
    if not args.allow_mineru_api:
        missing_markdown = [
            category
            for category in supported_categories
            if not (markdown_root(run_root) / category).is_dir()
        ]
        if missing_markdown:
            raise FileNotFoundError(
                f"{run_root} 缺少 Markdown 分类目录：{', '.join(missing_markdown)}。"
                "请先运行第二步 MinerU。"
            )

    final_outdir = (args.structure_outdir or structure_root(run_root)).resolve()
    process_root = work_root(run_root)
    process_root.mkdir(parents=True, exist_ok=True)
    fixed_work = process_root / "fixed_income"
    money_work = process_root / "money_market"
    equity_work = process_root / "equity"
    mineru_root = markdown_root(run_root)
    options = common_options(args)
    fixed_categories = [
        category
        for category in supported_categories
        if CATEGORY_TO_EXTRACTOR[category] == "fixed"
    ]
    money_categories = [
        category
        for category in supported_categories
        if CATEGORY_TO_EXTRACTOR[category] == "money"
    ]
    equity_categories = [
        category
        for category in supported_categories
        if CATEGORY_TO_EXTRACTOR[category] == "equity"
    ]
    jobs = []
    if fixed_categories:
        fixed_category = fixed_categories[0]
        jobs.append(
            (
                fixed_category,
                script_command(
                    RUN_FIXED,
                    str(pdf_category_dir(run_root, fixed_category)),
                    *options,
                    "--mineru-outdir",
                    str(mineru_root / fixed_category),
                    "--per-pdf-outdir",
                    str(fixed_work / "per_pdf"),
                    "--combined-outdir",
                    str(fixed_work / "combined"),
                    "--excel-outdir",
                    str(final_outdir),
                    "--output-stem",
                    f"{fixed_category}_基金提取结果",
                ),
            )
        )
    for money_category in money_categories:
        jobs.append(
            (
                money_category,
                script_command(
                    RUN_FIXED,
                    str(pdf_category_dir(run_root, money_category)),
                    *options,
                    "--prompt",
                    str(CURRENCY_PROMPT),
                    "--category",
                    "money",
                    "--output-suffix",
                    "money_market",
                    "--mineru-outdir",
                    str(mineru_root / money_category),
                    "--per-pdf-outdir",
                    str(money_work / "per_pdf"),
                    "--combined-outdir",
                    str(money_work / "combined"),
                    "--excel-outdir",
                    str(final_outdir),
                    "--output-stem",
                    f"{money_category}_基金提取结果",
                ),
            )
        )
    if equity_categories:
        equity_input = str(pdf_category_dir(run_root, equity_categories[0]).parent)
        equity_args = [equity_input]
        for category in equity_categories:
            equity_args.extend(["--category", category])
        equity_args.extend(
            [
                *options,
                "--mineru-root",
                str(mineru_root),
                "--per-pdf-outdir",
                str(equity_work / "per_pdf"),
                "--combined-outdir",
                str(equity_work / "combined"),
                "--excel-outdir",
                str(final_outdir),
            ]
        )
        jobs.append((" / ".join(equity_categories), script_command(RUN_EQUITY, *equity_args)))

    stage_workers = split_stage_workers(args.workers, len(jobs))
    jobs = [
        (stage, command_worker_count(command, stage_workers))
        for stage, command in jobs
    ]

    expected_pdfs = [
        pdf
        for category in supported_categories
        for pdf in sorted(pdf_category_dir(run_root, category).glob("*.pdf"))
    ]
    unsupported_counts = [
        {
            "category": category,
            "pdf_count": len(list(pdf_category_dir(run_root, category).glob("*.pdf"))),
        }
        for category in unsupported_categories
    ]
    progress = StructuredDataProgress(expected_pdfs)
    run_stamp = f"{datetime.now():%Y%m%d_%H%M%S}"
    log_path = process_root / f"structured_run_{run_stamp}.log"
    summary_path = process_root / f"structured_run_summary_{run_stamp}.json"
    print(f"[*] 批次目录: {run_root}")
    print(
        f"[*] 共发现 {len(expected_pdfs)} 个 PDF；大模型总并发上限："
        f"{max(1, args.workers)}，类别流水线并发：{stage_workers}"
    )
    for skipped in unsupported_counts:
        print(
            f"[!] 暂不支持结构化，跳过：{skipped['category']}"
            f"（{skipped['pdf_count']} 个 PDF）"
        )
    print(f"[*] Excel 输出目录: {final_outdir}")
    return_codes: list[int] = []
    stage_failures: list[str] = []
    log_lock = threading.Lock()

    def run_pipeline(stage: str, command: list[str]) -> tuple[str, int]:
        """运行一个类别流水线；参数 stage/command 为类别和命令。"""

        progress.set_stage(stage)
        return (
            stage,
            run_structured_command(
                command,
                progress=progress,
                log_file=log_file,
                log_lock=log_lock,
            ),
        )

    try:
        with log_path.open("w", encoding="utf-8", newline="") as log_file:
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=max(1, len(jobs)),
                thread_name_prefix="structured-pipeline",
            ) as executor:
                futures = [
                    executor.submit(run_pipeline, stage, command)
                    for stage, command in jobs
                ]
                for future in concurrent.futures.as_completed(futures):
                    stage, return_code = future.result()
                    return_codes.append(return_code)
                    if return_code != 0:
                        stage_failures.append(
                            f"{stage}汇总或 Excel 生成失败（退出码 {return_code}）"
                        )
        progress.mark_missing_failed()
    finally:
        progress.finish()

    print(
        f"[*] Excel 解析完成：成功 {progress.succeeded}，"
        f"失败 {progress.failed}，共 {progress.total}"
    )
    if progress.failures:
        print("[!] 失败基金：")
        for failure in progress.failures:
            print(f"    - {Path(failure['pdf']).name}：{failure['error']}")
    if stage_failures:
        print("[!] 汇总阶段异常：")
        for failure in stage_failures:
            print(f"    - {failure}")
    print("[*] 模型调用与 Token 统计：")
    if progress.model_usage:
        for model, usage in sorted(progress.model_usage.items()):
            print(
                f"    - {model}：调用 {usage['calls']} 次，"
                f"输入 {usage['input_tokens']}，输出 {usage['output_tokens']}，"
                f"合计 {usage['total_tokens']} Token"
            )
    else:
        print("    - 本次运行没有记录到模型调用。")
    summary_path.write_text(
        json.dumps(
            {
                "run_root": str(run_root),
                "total_funds": progress.total,
                "succeeded": progress.succeeded,
                "failed": progress.failed,
                "failures": progress.failures,
                "skipped_unsupported": unsupported_counts,
                "stage_failures": stage_failures,
                "stage_workers": stage_workers,
                "model_usage": progress.model_usage,
                "log_path": str(log_path),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"[*] 详细日志：{log_path}")
    print(f"[*] 运行统计：{summary_path}")
    return (
        0
        if all(code == 0 for code in return_codes)
        and progress.failed == 0
        and not stage_failures
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
