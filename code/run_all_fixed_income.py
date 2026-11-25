# -*- coding: utf-8 -*-
"""One-click entrypoint for extracting all fixed-income factsheets.

Default behavior:
  - find the latest data/<run_id>/factsheet/固收 directory
  - run structured_data/run_batch.py for every PDF inside it
  - write process files under structure_data/_work and Excel under structure_data/
"""

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
RUN_BATCH = CODE_DIR / "structured_data" / "run_batch.py"
def latest_category_dir(category: str) -> Path:
    return pdf_category_dir(latest_batch_root(DATA_DIR, (category,)), category)


def latest_fixed_income_dir() -> Path:
    return latest_category_dir("固收")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run structured extraction for all fixed-income PDFs.")
    parser.add_argument(
        "input",
        nargs="?",
        type=Path,
        help="PDF file or folder to process. Defaults to latest data/<run_id>/factsheet/固收.",
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
    parser.add_argument("--mineru-outdir", type=Path, help="MinerU cache directory for this category.")
    parser.add_argument("--output-stem", default="fixed_income", help="Base name for combined output files.")
    parser.add_argument("--output-suffix", default="fixed_income", help="Per-PDF JSON suffix; money channel uses money_market.")
    parser.add_argument("--category", default="fixed", help="Local-first category key passed to the extractor.")
    parser.add_argument("--prompt", type=Path, default=None, help="Override extraction prompt.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    work_name = "money_market" if args.category in {"money", "货币"} else "fixed_income"
    category_name = "货币" if args.category in {"money", "货币"} else "固收"
    input_path = args.input.resolve() if args.input else latest_category_dir(category_name)
    run_root = batch_root_from_pdf_input(input_path)
    if run_root:
        default_work = work_root(run_root) / work_name
        default_excel = structure_root(run_root)
        default_mineru = markdown_root(run_root) / category_name
    else:
        default_work = DATA_DIR / "_structured" / "manual"
        default_excel = default_work / "combined"
        default_mineru = DATA_DIR / "_mineru"
    per_pdf_outdir = (args.per_pdf_outdir or default_work / "per_pdf").resolve()
    combined_outdir = (args.combined_outdir or default_work / "combined").resolve()
    excel_outdir = (args.excel_outdir or default_excel).resolve()
    mineru_outdir = (args.mineru_outdir or default_mineru).resolve()

    worker_exe = str(os.environ.get("FACTSHEET_WORKER_EXE") or "").strip()
    command = (
        [worker_exe, "--worker-script", f"code/{RUN_BATCH.relative_to(CODE_DIR).as_posix()}"]
        if worker_exe
        else [sys.executable, str(RUN_BATCH)]
    ) + [
        str(input_path),
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
        "--mineru-outdir",
        str(mineru_outdir),
        "--per-pdf-outdir",
        str(per_pdf_outdir),
        "--combined-outdir",
        str(combined_outdir),
        "--excel-outdir",
        str(excel_outdir),
        "--output-stem",
        args.output_stem,
        "--output-suffix",
        args.output_suffix,
        "--category",
        args.category,
    ]
    if args.prompt:
        command.extend(["--prompt", str(args.prompt)])
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

    print(f"[*] Input: {input_path}")
    print(f"[*] Per-PDF output: {per_pdf_outdir}")
    print(f"[*] Combined output: {combined_outdir}")
    print(f"[*] Excel output: {excel_outdir}")
    print("[*] Running:", " ".join(f'"{part}"' if " " in part else part for part in command))

    proc = subprocess.run(command, cwd=PROJECT_ROOT)
    return int(proc.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
