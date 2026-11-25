#!/usr/bin/env python
"""Run fixed-income extraction for many PDFs, then combine Excel-friendly outputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[1]))

try:
    from structured_data.combine_fixed_income import write_outputs
    from structured_data.extract_fixed_income import DEFAULT_OUTDIR as DEFAULT_PER_PDF_DIR
    from structured_data.extract_fixed_income import DEFAULT_PROMPT, extract_pdf, write_result
    from structured_data.progress_events import emit_progress_event
    from structured_data.qwen_client import provider_unavailable_reason
    from structured_data.token_usage import summarize_by_model
    from structured_data.validate import source_stem
except ImportError:  # pragma: no cover
    from combine_fixed_income import write_outputs
    from extract_fixed_income import DEFAULT_OUTDIR as DEFAULT_PER_PDF_DIR
    from extract_fixed_income import DEFAULT_PROMPT, extract_pdf, write_result
    from progress_events import emit_progress_event
    from qwen_client import provider_unavailable_reason
    from token_usage import summarize_by_model
    from validate import source_stem


MODULE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = Path(os.environ.get("FACTSHEET_PROJECT_ROOT") or MODULE_DIR.parents[1]).resolve()
DEFAULT_DATA_ROOT = PROJECT_ROOT / "data"
DEFAULT_COMBINED_DIR = PROJECT_ROOT / "data" / "_structured" / "combined"


def _sha256(path: Path) -> str:
    """计算输入 PDF 的内容指纹，用于缓存命中校验。"""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def discover_pdfs(paths: list[Path]) -> list[Path]:
    pdfs: list[Path] = []
    for path in paths:
        if path.is_file() and path.suffix.lower() == ".pdf":
            pdfs.append(path)
        elif path.is_dir():
            pdfs.extend(sorted(path.rglob("*.pdf")))
    return sorted(dict.fromkeys(pdf.resolve() for pdf in pdfs))


def process_one(
    pdf_path: Path,
    args: argparse.Namespace,
    stop_event: threading.Event | None = None,
) -> dict:
    if stop_event is not None and stop_event.is_set():
        return {
            "status": "skipped",
            "_meta": {
                "source_file": str(pdf_path),
                "skipped_reason": "provider_unavailable",
            },
        }
    emit_progress_event({"event": "started", "pdf": str(pdf_path)})
    output = args.per_pdf_outdir / f"{source_stem(str(pdf_path))}.{args.output_suffix}.json"
    # 正常流水线只会传入存在的 PDF；保留空值回退，便于上层的失败/熔断
    # 测试替身以及诊断流程仍能得到原有的错误结果。
    source_sha256 = _sha256(pdf_path) if pdf_path.is_file() else ""
    if output.exists() and args.skip_existing:
        meta_path = output.with_suffix(".meta.json")
        if meta_path.is_file():
            cached = json.loads(meta_path.read_text(encoding="utf-8"))
            cached_meta = cached.get("_meta") or {}
            if (
                source_sha256
                and cached.get("status") != "failed"
                and cached_meta.get("source_sha256") == source_sha256
            ):
                return cached
    result = extract_pdf(
        pdf_path=pdf_path,
        primary_model=args.primary_model,
        fallback_model=args.fallback_model,
        attempts=args.attempts,
        prompt_path=args.prompt,
        timeout=args.timeout,
        max_pages=args.max_pages,
        zoom=args.zoom,
        split_pages=not args.no_split_pages,
        vision_model=args.vision_model,
        enable_thinking=not args.no_thinking,
        thinking_budget=args.thinking_budget,
        source=args.source,
        mineru_model=args.mineru_model,
        mineru_outdir=args.mineru_outdir,
        mineru_force=args.mineru_force,
        mineru_cache_only=args.mineru_cache_only,
        mineru_poll_timeout=args.mineru_poll_timeout,
        category=args.category,
    )
    result.setdefault("_meta", {})["source_sha256"] = source_sha256
    write_result(output, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract fixed-income data from multiple PDFs and combine outputs.")
    parser.add_argument("paths", nargs="*", type=Path, default=[DEFAULT_DATA_ROOT])
    parser.add_argument("--per-pdf-outdir", type=Path, default=DEFAULT_PER_PDF_DIR)
    parser.add_argument("--combined-outdir", type=Path, default=DEFAULT_COMBINED_DIR)
    parser.add_argument("--excel-outdir", type=Path)
    parser.add_argument("--output-stem", default="fixed_income")
    parser.add_argument("--output-suffix", default="fixed_income")
    parser.add_argument("--category", default="fixed")
    parser.add_argument("--primary-model", default="deepseek-flash")
    parser.add_argument("--fallback-model", default="deepseek-v4-pro")
    parser.add_argument("--vision-model", default="deepseek-flash")
    parser.add_argument("--source", choices=("mineru", "auto", "file-id"), default="mineru")
    parser.add_argument("--mineru-model", choices=("vlm", "pipeline"), default="vlm")
    parser.add_argument("--mineru-outdir", type=Path, default=PROJECT_ROOT / "data" / "_mineru")
    parser.add_argument("--mineru-force", action="store_true")
    parser.add_argument("--mineru-cache-only", action="store_true")
    parser.add_argument("--mineru-poll-timeout", type=int, default=900)
    parser.add_argument("--no-thinking", action="store_true", help="Disable model thinking mode")
    parser.add_argument("--thinking-budget", type=int, default=None, help="Optional Qwen thinking token budget")
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--prompt", type=Path, default=DEFAULT_PROMPT)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--max-pages", type=int, default=12)
    parser.add_argument("--zoom", type=float, default=2.0)
    parser.add_argument("--no-split-pages", action="store_true", help="Only send full page images, without left/right crops")
    parser.add_argument("--skip-existing", action="store_true")
    args = parser.parse_args()

    pdfs = discover_pdfs(args.paths)
    args.per_pdf_outdir.mkdir(parents=True, exist_ok=True)
    records = []
    failures = []
    skipped: list[str] = []
    provider_stop_reason: str | None = None
    stop_event = threading.Event()
    token_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "calls": []}
    with ThreadPoolExecutor(max_workers=max(args.workers, 1)) as executor:
        future_map = {
            executor.submit(process_one, pdf, args, stop_event): pdf for pdf in pdfs
        }
        for future in as_completed(future_map):
            pdf = future_map[future]
            try:
                record = future.result()
                meta = record.get("_meta") or {}
                if record.get("status") == "skipped":
                    skipped.append(str(pdf))
                    emit_progress_event({
                        "event": "finished",
                        "pdf": str(pdf),
                        "status": "failed",
                        "error": "模型账户不可用，批次已熔断，本基金未处理",
                    })
                    continue
                if record.get("status") == "failed":
                    failures.append({
                        "pdf": str(pdf),
                        "error": (meta.get("failures") or ["抽取失败"])[0],
                    })
                    if provider_stop_reason is None:
                        matched = provider_unavailable_reason(
                            " ".join(str(item) for item in meta.get("failures") or [])
                        )
                        if matched:
                            provider_stop_reason = matched
                            stop_event.set()
                else:
                    data = dict(record.get("data", record))
                    data["_source_json"] = str(
                        args.per_pdf_outdir / f"{source_stem(str(pdf))}.{args.output_suffix}.json"
                    )
                    records.append(data)
                usage = meta.get("token_usage") if isinstance(meta, dict) else None
                if isinstance(usage, dict):
                    token_usage["input_tokens"] += int(usage.get("input_tokens") or 0)
                    token_usage["output_tokens"] += int(usage.get("output_tokens") or 0)
                    token_usage["total_tokens"] += int(usage.get("total_tokens") or 0)
                    for call in usage.get("calls") or []:
                        if isinstance(call, dict):
                            token_usage["calls"].append({"pdf": str(pdf), **call})
                emit_progress_event({
                    "event": "finished",
                    "pdf": str(pdf),
                    "status": record.get("status"),
                    "fund_count": 1 if record.get("status") != "failed" else 0,
                    "first_failure": (meta.get("failures") or [""])[0],
                    "input_tokens": (usage or {}).get("input_tokens") if isinstance(usage, dict) else 0,
                    "output_tokens": (usage or {}).get("output_tokens") if isinstance(usage, dict) else 0,
                    "model_usage": summarize_by_model(usage),
                })
            except Exception as exc:  # noqa: BLE001
                failures.append({"pdf": str(pdf), "error": f"{type(exc).__name__}: {exc}"})
                if provider_stop_reason is None:
                    matched = provider_unavailable_reason(f"{type(exc).__name__}: {exc}")
                    if matched:
                        provider_stop_reason = matched
                        stop_event.set()
                emit_progress_event({
                    "event": "finished",
                    "pdf": str(pdf),
                    "status": "failed",
                    "error": str(exc),
                })

    outputs = write_outputs(
        records,
        args.combined_outdir,
        output_stem=args.output_stem,
        excel_outdir=args.excel_outdir,
    )
    summary = {
        "pdf_count": len(pdfs),
        "success_count": len(records),
        "failure_count": len(failures),
        "skipped_count": len(skipped),
        "provider_unavailable": provider_stop_reason,
        "failures": failures,
        "token_usage": token_usage,
        "outputs": outputs,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not failures and provider_stop_reason is None else 2


if __name__ == "__main__":
    raise SystemExit(main())
