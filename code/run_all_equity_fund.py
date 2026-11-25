# -*- coding: utf-8 -*-
"""One-click entrypoint for extracting all equity fund factsheets."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from batch_layout import (
    batch_root_from_pdf_input,
    latest_batch_root,
    markdown_root,
    pdf_category_dir,
    structure_root,
    work_root,
)
from app_config import apply_environment

apply_environment()

CODE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = Path(os.environ.get("FACTSHEET_PROJECT_ROOT") or CODE_DIR.parent).resolve()
DATA_DIR = Path(os.environ.get("FACTSHEET_DATA_ROOT") or PROJECT_ROOT / "data").resolve()
RUN_BATCH = CODE_DIR / "structured_data" / "run_batch_equity_fund.py"
DEFAULT_CATEGORIES = ("权益", "中国精选")


def latest_category_dir(category: str) -> Path:
    return pdf_category_dir(latest_batch_root(DATA_DIR, (category,)), category)


def latest_equity_dir() -> Path:
    return latest_category_dir("权益")


def latest_equity_category_paths(categories: list[str]) -> dict[str, Path]:
    latest_root = latest_batch_root(DATA_DIR, tuple(categories))
    return {category: pdf_category_dir(latest_root, category) for category in categories}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run structured extraction for all equity fund PDFs.")
    parser.add_argument("input", nargs="?", type=Path, help="PDF file or folder. Defaults to latest data/<run_id>/factsheet/权益.")
    parser.add_argument(
        "--category",
        action="append",
        dest="categories",
        help="Category folder to include; repeatable. Defaults to 权益 and 中国精选.",
    )
    parser.add_argument("--workers", type=int, default=int(os.environ.get("FACTSHEET_MODEL_WORKERS", "32")), help="LLM extraction workers; MinerU remains a separate cached vlm stage.")
    parser.add_argument("--attempts", type=int, default=int(os.environ.get("FACTSHEET_API_ATTEMPTS", "3")), help="Retries for upload/chat calls.")
    parser.add_argument("--timeout", type=int, default=int(os.environ.get("FACTSHEET_API_TIMEOUT", "300")), help="Seconds per API request.")
    parser.add_argument("--primary-model", default=os.environ.get("FACTSHEET_PRIMARY_MODEL", "deepseek-flash"))
    parser.add_argument("--fallback-model", default=os.environ.get("FACTSHEET_FALLBACK_MODEL", "deepseek-v4-pro"))
    parser.add_argument("--vision-model", default=os.environ.get("FACTSHEET_VISION_MODEL", "deepseek-flash"))
    parser.add_argument("--source", choices=("mineru", "auto", "file-id"), default="mineru")
    parser.add_argument("--mineru-model", choices=("vlm", "pipeline"), default=os.environ.get("FACTSHEET_MINERU_MODEL", "vlm"))
    parser.add_argument("--mineru-force", action="store_true", help="Ignore completed MinerU parse cache.")
    parser.add_argument("--mineru-cache-only", action="store_true", help="只读 MinerU 缓存，不调用 MinerU API。")
    parser.add_argument("--mineru-poll-timeout", type=int, default=900)
    parser.add_argument(
        "--no-thinking",
        action="store_true",
        default=os.environ.get("FACTSHEET_STRUCTURED_THINKING", "false").lower()
        not in {"1", "true", "yes"},
        help="Disable model thinking mode for faster extraction",
    )
    parser.add_argument("--thinking-budget", type=int, default=None, help="Optional Qwen thinking token budget")
    parser.add_argument("--skip-existing", action="store_true", help="Reuse existing per-PDF JSON outputs.")
    parser.add_argument("--per-pdf-outdir", type=Path, help="Override per-PDF output directory.")
    parser.add_argument("--combined-outdir", type=Path, help="Override combined output directory.")
    parser.add_argument("--excel-outdir", type=Path, help="Override final Excel directory.")
    parser.add_argument("--mineru-root", type=Path, help="Root containing Markdown category folders.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    categories = args.categories or list(DEFAULT_CATEGORIES)
    explicit_input = args.input.resolve() if args.input else None
    if explicit_input and explicit_input.is_dir() and any((explicit_input / category).is_dir() for category in categories):
        category_paths = {category: explicit_input / category for category in categories}
        input_paths = list(category_paths.values())
    elif explicit_input:
        category_paths = {}
        input_paths = [explicit_input]
    else:
        category_paths = latest_equity_category_paths(categories)
        input_paths = list(category_paths.values())
    run_root = batch_root_from_pdf_input(input_paths[0])
    if run_root:
        default_work = work_root(run_root) / "equity"
        default_excel = structure_root(run_root)
        default_mineru_root = markdown_root(run_root)
    else:
        default_work = DATA_DIR / "_structured_equity" / "manual"
        default_excel = default_work / "combined"
        default_mineru_root = DATA_DIR / "_mineru"
    per_pdf_outdir = (args.per_pdf_outdir or default_work / "per_pdf").resolve()
    combined_outdir = (args.combined_outdir or default_work / "combined").resolve()
    excel_outdir = (args.excel_outdir or default_excel).resolve()
    mineru_root = (args.mineru_root or default_mineru_root).resolve()

    worker_exe = str(os.environ.get("FACTSHEET_WORKER_EXE") or "").strip()
    command = (
        [worker_exe, "--worker-script", f"code/{RUN_BATCH.relative_to(CODE_DIR).as_posix()}"]
        if worker_exe
        else [sys.executable, str(RUN_BATCH)]
    ) + [
        "--workers",
        str(args.workers),
        "--attempts",
        str(args.attempts),
        "--timeout",
        str(args.timeout),
        "--primary-model",
        args.primary_model,
        "--fallback-model",
        args.fallback_model,
        "--vision-model",
        args.vision_model,
        "--source",
        args.source,
        "--mineru-model",
        args.mineru_model,
        "--mineru-poll-timeout",
        str(args.mineru_poll_timeout),
        "--mineru-root",
        str(mineru_root),
        "--per-pdf-outdir",
        str(per_pdf_outdir),
        "--combined-outdir",
        str(combined_outdir),
        "--excel-outdir",
        str(excel_outdir),
    ]
    if not category_paths:
        command.insert(2, str(input_paths[0]))
    else:
        for category, path in category_paths.items():
            command.extend(["--category-input", f"{category}={path}"])
    if args.no_thinking:
        command.append("--no-thinking")
    if args.thinking_budget is not None:
        command.extend(["--thinking-budget", str(args.thinking_budget)])
    if args.skip_existing:
        command.append("--skip-existing")
    if args.mineru_force:
        command.append("--mineru-force")
    if args.mineru_cache_only:
        command.append("--mineru-cache-only")

    print("[*] Inputs:")
    if category_paths:
        for category, input_path in category_paths.items():
            state = "" if input_path.is_dir() else "（目录不存在，将生成空结果）"
            print(f"    - {category}: {input_path}{state}")
    else:
        print(f"    - {input_paths[0]}")
    print(f"[*] Per-PDF output: {per_pdf_outdir}")
    print(f"[*] Combined output: {combined_outdir}")
    print(f"[*] Excel output: {excel_outdir}")
    print("[*] Running:", " ".join(f'"{part}"' if " " in part else part for part in command))
    proc = subprocess.run(command, cwd=PROJECT_ROOT)
    return int(proc.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
