# -*- coding: utf-8 -*-
"""一键把批次内各分类 PDF 转换为 MinerU 缓存。"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
from io import StringIO
import json
import sys
from datetime import datetime
import os
from pathlib import Path
import threading
import unicodedata
from typing import Any

from batch_layout import (
    latest_batch_root,
    markdown_root,
    pdf_category_dir,
    pdf_categories_with_files,
    reports_root,
)
from batch_selector import available_batch_roots, select_batch_root
from app_config import apply_environment
from structured_data.parse_mineru_pdf import parse_local_pdf

apply_environment()

CODE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = Path(os.environ.get("FACTSHEET_PROJECT_ROOT") or CODE_DIR.parent).resolve()
DATA_DIR = Path(os.environ.get("FACTSHEET_DATA_ROOT") or PROJECT_ROOT / "data").resolve()


class MinerUProgress:
    """Render serial MinerU progress on one terminal line."""

    def __init__(self, total: int, stream=None, bar_width: int = 20):
        self.total = max(0, int(total))
        self.stream = stream or sys.stdout
        self.bar_width = max(10, int(bar_width))
        self.completed = 0
        self.succeeded = 0
        self.failed = 0
        self.current = ""
        self.activity_tick = 0
        self.last_width = 0
        self.closed = False
        self.lock = threading.Lock()
        isatty = getattr(self.stream, "isatty", None)
        self.interactive = bool(isatty and isatty()) or os.environ.get("FACTSHEET_PROGRESS_PIPE") == "1"

    @staticmethod
    def _short_name(value: str, limit: int = 30) -> str:
        value = str(value or "").strip()
        if len(value) <= limit:
            return value
        return value[: max(1, limit - 1)] + "…"

    @staticmethod
    def _display_width(value: str) -> int:
        return sum(
            2 if unicodedata.east_asian_width(character) in {"F", "W"} else 1
            for character in value
        )

    def _line(self) -> str:
        ratio = self.completed / self.total if self.total else 1
        filled = min(self.bar_width, int(ratio * self.bar_width))
        bar = "█" * filled + "░" * (self.bar_width - filled)
        pulse = "|/-\\"[self.activity_tick % 4]
        current_note = (
            f"  正在：{self._short_name(self.current)}" if self.current else ""
        )
        return (
            f"MinerU 进度 {pulse} [{bar}] {self.completed}/{self.total}"
            f"  成功/复用 {self.succeeded}  失败 {self.failed}{current_note}"
        )

    def _render(self, *, final: bool = False) -> None:
        if not self.interactive and not final:
            return
        line = self._line()
        line_width = self._display_width(line)
        self.stream.write(
            "\r" + line + " " * max(0, self.last_width - line_width)
        )
        if final:
            self.stream.write("\n")
        self.stream.flush()
        self.last_width = line_width

    def start(self, category: str, pdf_name: str) -> None:
        with self.lock:
            if self.closed:
                return
            self.current = f"{category} / {pdf_name}"
            self.activity_tick += 1
            self._render()

    def pulse(self) -> None:
        with self.lock:
            if self.closed:
                return
            self.activity_tick += 1
            self._render()

    def complete(self, *, success: bool) -> None:
        with self.lock:
            if self.closed:
                return
            self.completed += 1
            if success:
                self.succeeded += 1
            else:
                self.failed += 1
            self.current = ""
            self.activity_tick += 1
            self._render()

    def finish(self) -> None:
        with self.lock:
            if self.closed:
                return
            self.closed = True
            self._render(final=True)


def _pulse_progress_until_stopped(
    stop_event: threading.Event,
    progress: MinerUProgress,
) -> None:
    while not stop_event.wait(1):
        progress.pulse()


def latest_run_root() -> Path:
    return latest_batch_root(DATA_DIR)


def build_cache_index(
    run_root: Path,
    *,
    requested_mode: str,
    model: str,
) -> dict[str, Path]:
    """建立跨批次 MinerU 缓存索引；参数 run_root/mode/model 为解析上下文。"""

    # 外部临时批次通常用于单次补充来源或测试，不应为复用缓存扫描整个
    # data 目录；这项扫描在历史批次较多时会掩盖 PDF 级并发收益。
    data_root = DATA_DIR.resolve()
    resolved_run_root = run_root.resolve()
    try:
        resolved_run_root.relative_to(data_root)
        same_data_tree = True
    except ValueError:
        # 测试/临时数据目录允许把 DATA_DIR 指向同一临时父目录；除此之外
        # 的外部路径不做全量历史缓存扫描。
        same_data_tree = resolved_run_root.parent == data_root.parent
    if not same_data_tree:
        return {}

    index: dict[str, tuple[int, Path]] = {}
    for manifest_path in DATA_DIR.glob("**/markdown/**/manifest.json"):
        if manifest_path.is_relative_to(run_root):
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        mode = str(manifest.get("mode") or "")
        if manifest.get("status") != "done":
            continue
        if requested_mode in {"precise", "agent"} and mode != requested_mode:
            continue
        if mode == "precise" and manifest.get("model_version") != model:
            continue
        source_hash = str(manifest.get("source_sha256") or "")
        markdown = Path(str(manifest.get("markdown_path") or ""))
        if not source_hash or not markdown.is_file():
            continue
        rank = 0 if mode == "precise" else 1
        current = index.get(source_hash)
        if current is None or rank < current[0]:
            index[source_hash] = (rank, manifest_path)
    return {source_hash: item[1] for source_hash, item in index.items()}


def _is_precise_auth_failure(reason: str) -> bool:
    """判断 Precise 降级是否属于认证失败；参数 reason 为降级原因。"""

    normalized = str(reason or "").casefold()
    return any(
        marker in normalized
        for marker in (
            "http 401",
            "unauthorized",
            "token expired",
            "invalid token",
            "缺少 mineru token",
        )
    )


def _workers_for_mode(mode: str, requested: int, pdf_count: int) -> int:
    """按解析模式返回安全并发；参数 mode/requested/pdf_count 为调度上下文。"""

    agent_limit = max(
        1,
        int(os.environ.get("FACTSHEET_MINERU_AGENT_WORKERS", "18")),
    )
    mode_limit = agent_limit if mode in {"auto", "agent"} else 50
    return max(1, min(int(requested), mode_limit, pdf_count))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="批量调用 MinerU，把批次内已识别分类的 PDF 转为 Markdown/图片缓存。"
    )
    parser.add_argument("run_root", nargs="?", type=Path)
    parser.add_argument("--model", choices=("vlm", "pipeline"), default=os.environ.get("FACTSHEET_MINERU_MODEL", "vlm"))
    parser.add_argument(
        "--mode",
        choices=("auto", "precise", "agent"),
        default=os.environ.get("FACTSHEET_MINERU_PARSE_MODE", "auto"),
        help="MinerU 解析模式；auto 在 Precise 不可用时降级到 Agent API。",
    )
    parser.add_argument(
        "--outdir",
        type=Path,
        help="覆盖 Markdown 根目录；默认写入当前批次的 markdown。",
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--workers",
        type=int,
        default=int(os.environ.get("FACTSHEET_MINERU_WORKERS", "36")),
        help="独立 PDF 的 MinerU 并发数；单份 PDF 内部仍按原解析流程执行。",
    )
    parser.add_argument("--request-timeout", type=int, default=180)
    parser.add_argument("--poll-timeout", type=int, default=1200)
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=float(os.environ.get("FACTSHEET_MINERU_POLL_INTERVAL", "2.5")),
        help="单个解析任务查询结果的间隔秒数。",
    )
    parser.add_argument(
        "--select-batch",
        action="store_true",
        help="运行前列出 data 下的批次文件夹并交互选择。",
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    if args.select_batch and args.run_root:
        parser.error("使用 --select-batch 时不能同时指定 run_root。")
    if args.select_batch:
        selected = select_batch_root(DATA_DIR, action_name=" MinerU 转换")
        if selected is None:
            return 0
        run_root = selected.resolve()
    else:
        run_root = args.run_root.resolve() if args.run_root else latest_run_root()
    categories = pdf_categories_with_files(run_root)
    if not categories:
        raise FileNotFoundError(f"{run_root} 的 factsheet 下没有可解析的 PDF")
    print(f"[*] 识别到分类：{'、'.join(categories)}")
    pdfs: list[tuple[str, Path]] = []
    for category in categories:
        category_dir = pdf_category_dir(run_root, category)
        pdfs.extend((category, path) for path in sorted(category_dir.glob("*.pdf")))

    results: list[dict[str, Any]] = []
    print(f"[*] 批次：{run_root}")
    workers = _workers_for_mode(args.mode, args.workers, len(pdfs))
    cache_index = build_cache_index(
        run_root,
        requested_mode=args.mode,
        model=args.model,
    )
    print(
        f"[*] 共发现 {len(pdfs)} 个 PDF；MinerU PDF 级并发 workers={workers}"
        f"；跨批次缓存 {len(cache_index)} 份"
    )
    progress = MinerUProgress(len(pdfs))
    precise_auth_unavailable = threading.Event()

    def process_pdf(index: int, category: str, pdf_path: Path) -> tuple[int, dict[str, Any]]:
        """解析单份 PDF；参数 index/category/pdf_path 为批次位置和文件。"""

        item: dict[str, Any] = {"category": category, "pdf": str(pdf_path)}
        progress.start(category, pdf_path.name)
        mineru_log = StringIO()

        def log_callback(message: str) -> None:
            """收集单文件 MinerU 轮询日志；参数 message 为日志文本。"""

            mineru_log.write(message + "\n")

        try:
            category_outdir = (
                args.outdir.resolve()
                if args.outdir
                else markdown_root(run_root)
            ) / category
            digest = hashlib.sha256()
            with pdf_path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            task_mode = (
                "agent"
                if args.mode == "auto" and precise_auth_unavailable.is_set()
                else args.mode
            )
            manifest = parse_local_pdf(
                pdf_path,
                outdir=category_outdir,
                mode=task_mode,
                model=args.model,
                force=args.force,
                request_timeout=args.request_timeout,
                poll_timeout=args.poll_timeout,
                interval=args.poll_interval,
                log_callback=log_callback,
                cached_manifest=cache_index.get(digest.hexdigest()),
            )
            item.update(
                {
                    "status": "ok",
                    "mode": manifest.get("mode"),
                    "manifest_path": manifest["manifest_path"],
                    "cache_reused_from": manifest.get("cache_reused_from"),
                }
            )
            fallback_reason = str(manifest.get("fallback_reason") or "")
            if (
                manifest.get("fallback_from") == "precise"
                and _is_precise_auth_failure(fallback_reason)
            ):
                precise_auth_unavailable.set()
            progress.complete(success=True)
        except Exception as exc:  # noqa: BLE001
            item.update(
                {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
            )
            progress.complete(success=False)
        finally:
            item["log_tail"] = mineru_log.getvalue()[-6000:]
        return index, item

    try:
        indexed_results: dict[int, dict[str, Any]] = {}
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="mineru-pdf",
        ) as executor:
            futures = {
                executor.submit(process_pdf, index, category, pdf_path): index
                for index, (category, pdf_path) in enumerate(pdfs)
            }
            for future in concurrent.futures.as_completed(futures):
                index, item = future.result()
                indexed_results[index] = item
        results = [
            indexed_results[index] for index in sorted(indexed_results)
        ]
    finally:
        progress.finish()

    report_dir = reports_root(run_root)
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"mineru_run_report_{datetime.now():%Y%m%d_%H%M%S}.json"
    failed = sum(item["status"] == "failed" for item in results)
    report_path.write_text(
        json.dumps(
            {
                "run_root": str(run_root),
                "model": args.model,
                "mode": args.mode,
                "precise_auth_circuit_open": precise_auth_unavailable.is_set(),
                "workers": workers,
                "poll_interval": args.poll_interval,
                "total": len(results),
                "failed": failed,
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"[*] MinerU 完成：成功/复用 {len(results) - failed}，失败 {failed}")
    if failed:
        print("[!] 失败文件：")
        for item in results:
            if item["status"] == "failed":
                print(
                    f"    - [{item['category']}] {Path(item['pdf']).name}："
                    f"{item['error']}"
                )
    print(f"[*] 报告：{report_path}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
