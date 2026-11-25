#!/usr/bin/env python
"""A/B benchmark structured extraction token cost and field completeness.

Baseline uses the legacy prompt-first message order and always appends the
native PDF text layer. Optimized puts the report context first (cacheable
prefix) and lets ``auto`` drop a redundant native text layer.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from structured_data.validate import source_stem


MODULE_DIR = Path(__file__).resolve().parent
CODE_DIR = MODULE_DIR.parent
PROJECT_ROOT = MODULE_DIR.parents[1]
SUPPORTED_CATEGORIES = {
    "固收": "fixed",
    "权益": "equity",
    "中国精选": "equity",
    "混合": "equity",
}
CORE_FIELDS = {
    "fixed": (
        "fund_name",
        "report_date",
        "fund_size_hkd_100m",
        "portfolio_duration_years",
        "portfolio_ytm_percent",
        "credit_rating",
        "top_holdings",
        "duration_distribution",
        "geographic_allocation",
        "dividend",
    ),
    "equity": (
        "fund_name",
        "report_date",
        "fund_size_hkd_100m",
        "top_holdings",
        "geographic_allocation",
        "sector_allocation",
        "dividend",
    ),
}
VARIANTS = {
    "baseline": {
        "FACTSHEET_CACHE_FRIENDLY_CONTEXT": "0",
        "FACTSHEET_PDF_TEXT_LAYER": "always",
        "FACTSHEET_CONTEXT_MODE": "full",
    },
    "cache": {
        "FACTSHEET_CACHE_FRIENDLY_CONTEXT": "1",
        "FACTSHEET_PDF_TEXT_LAYER": "always",
        "FACTSHEET_CONTEXT_MODE": "full",
    },
    "text_layer": {
        "FACTSHEET_CACHE_FRIENDLY_CONTEXT": "0",
        "FACTSHEET_PDF_TEXT_LAYER": "auto",
        "FACTSHEET_CONTEXT_MODE": "full",
    },
    "optimized": {
        "FACTSHEET_CACHE_FRIENDLY_CONTEXT": "1",
        "FACTSHEET_PDF_TEXT_LAYER": "auto",
        "FACTSHEET_CONTEXT_MODE": "full",
    },
    "local_first": {
        "FACTSHEET_CACHE_FRIENDLY_CONTEXT": "1",
        "FACTSHEET_PDF_TEXT_LAYER": "auto",
        "FACTSHEET_LOCAL_FIRST": "enforce",
        "FACTSHEET_CONTEXT_MODE": "full",
    },
    "compact": {
        "FACTSHEET_CACHE_FRIENDLY_CONTEXT": "1",
        "FACTSHEET_PDF_TEXT_LAYER": "auto",
        "FACTSHEET_CONTEXT_MODE": "compact",
    },
    "local_compact": {
        "FACTSHEET_CACHE_FRIENDLY_CONTEXT": "1",
        "FACTSHEET_PDF_TEXT_LAYER": "auto",
        "FACTSHEET_LOCAL_FIRST": "enforce",
        "FACTSHEET_CONTEXT_MODE": "compact",
    },
}


def _kind_for_category(category: str) -> str:
    """返回类别对应的抽取器类型；参数 category 为类别名。"""

    return SUPPORTED_CATEGORIES[category]


def _is_filled(value: Any) -> bool:
    """判断字段是否有值；参数 value 为字段值。"""

    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        ignored = {
            "note",
            "field_evidence",
            "_source_json",
            "currency",
            "frequency",
            "unit",
        }
        return any(
            _is_filled(item)
            for key, item in value.items()
            if key not in ignored
        )
    if isinstance(value, (list, tuple)):
        return any(_is_filled(item) for item in value)
    return True


def _normalized(value: Any) -> str:
    """把字段值转成可比较的稳定字符串；参数 value 为字段值。"""

    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _field_filled(field: str, value: Any) -> bool:
    """按字段语义判断是否真正有值；参数 field/value 为字段名和值。"""

    if field == "dividend":
        if not isinstance(value, dict):
            return _is_filled(value)
        return any(
            _is_filled(value.get(key))
            for key in (
                "dividend_per_unit",
                "annualized_dividend_yield_percent",
                "yield_percent",
                "rate_percent",
            )
        )
    return _is_filled(value)


def _cached_manifest_available(
    pdf_path: Path,
    mineru_root: Path,
    category: str,
) -> bool:
    """检查 MinerU 缓存是否存在，避免 A/B 期间触发重新解析。"""

    from structured_data.parse_mineru_pdf import parse_local_pdf

    try:
        with contextlib.redirect_stdout(io.StringIO()):
            parse_local_pdf(
                pdf_path,
                outdir=mineru_root / category,
                mode="auto",
                cache_only=True,
            )
        return True
    except Exception:  # noqa: BLE001 - a missing cache simply excludes the PDF
        return False


def select_pdfs(
    pdf_root: Path,
    mineru_root: Path,
    categories: list[str],
    limit: int,
    *,
    require_cache: bool = True,
) -> list[dict[str, str]]:
    """按类别轮询挑选 PDF；参数 root/categories/limit 为候选范围。"""

    pools: dict[str, list[Path]] = {}
    for category in categories:
        directory = pdf_root / category
        if not directory.is_dir():
            continue
        pools[category] = sorted(directory.glob("*.pdf"))
    selected: list[dict[str, str]] = []
    indexes = {category: 0 for category in pools}
    categories_left = [category for category, paths in pools.items() if paths]
    while categories_left and len(selected) < limit:
        for category in list(categories_left):
            paths = pools[category]
            while indexes[category] < len(paths):
                pdf_path = paths[indexes[category]]
                indexes[category] += 1
                if require_cache and not _cached_manifest_available(
                    pdf_path,
                    mineru_root,
                    category,
                ):
                    continue
                selected.append(
                    {
                        "pdf": str(pdf_path.resolve()),
                        "category": category,
                        "kind": _kind_for_category(category),
                    }
                )
                break
            if indexes[category] >= len(paths):
                categories_left.remove(category)
            if len(selected) >= limit:
                break
    return selected


def _sum_usage(result: dict[str, Any]) -> dict[str, int]:
    """汇总单份结果的 token 用量；参数 result 为抽取结果。"""

    meta = result.get("_meta") or {}
    usage = meta.get("token_usage") or {}
    totals = {
        "calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cache_hit_tokens": 0,
        "cache_miss_tokens": 0,
    }
    for call in usage.get("calls") or []:
        if not isinstance(call, dict):
            continue
        totals["calls"] += 1
        for key in totals:
            if key == "calls":
                continue
            totals[key] += int(call.get(key) or 0)
    return totals


def _result_path(root: Path, entry: dict[str, str]) -> Path:
    """返回某份 PDF 在某变体下的结果文件路径；参数 root/entry 为上下文。"""

    return root / "results" / entry["category"] / f"{source_stem(entry['pdf'])}.json"


def _extract_one(entry: dict[str, str], args: argparse.Namespace) -> dict[str, Any]:
    """执行单份 PDF 抽取；参数 entry/args 为样本和运行配置。"""

    from structured_data import extract_equity_fund, extract_fixed_income

    pdf_path = Path(entry["pdf"])
    category = entry["category"]
    kind = entry["kind"]
    mineru_outdir = Path(args.mineru_root) / category
    common = {
        "pdf_path": pdf_path,
        "primary_model": args.primary_model,
        "fallback_model": args.fallback_model,
        "attempts": args.attempts,
        "prompt_path": (
            extract_fixed_income.DEFAULT_PROMPT
            if kind == "fixed"
            else extract_equity_fund.DEFAULT_PROMPT
        ),
        "timeout": args.timeout,
        "enable_thinking": args.enable_thinking,
        "thinking_budget": None,
        "vision_model": args.vision_model,
        "source": "mineru",
        "mineru_model": args.mineru_model,
        "mineru_outdir": mineru_outdir,
        "mineru_cache_only": True,
        "mineru_poll_timeout": 900,
    }
    if kind == "fixed":
        result = extract_fixed_income.extract_pdf(
            **common,
            max_pages=12,
            zoom=2.0,
            split_pages=True,
        )
    else:
        result = extract_equity_fund.extract_pdf(**common)
    return {
        "pdf": str(pdf_path),
        "category": category,
        "kind": kind,
        "result": result,
    }


def run_variant(
    variant: str,
    manifest: list[dict[str, str]],
    out_dir: Path,
    args: argparse.Namespace,
) -> int:
    """子进程模式：运行一个变体并写出逐份结果；参数 variant/manifest 为上下文。"""

    for key, value in VARIANTS[variant].items():
        os.environ[key] = value
    out_dir.mkdir(parents=True, exist_ok=True)
    total = len(manifest)
    for index, entry in enumerate(manifest, start=1):
        result_path = _result_path(out_dir, entry)
        result_path.parent.mkdir(parents=True, exist_ok=True)
        if args.resume and result_path.is_file():
            print(f"[{variant}] {index}/{total} cached {result_path.name}")
            continue
        print(
            f"[{variant}] {index}/{total} {entry['category']}/{Path(entry['pdf']).name}",
            flush=True,
        )
        try:
            payload = _extract_one(entry, args)
        except Exception as exc:  # noqa: BLE001 - persist per-PDF failure
            payload = {
                **entry,
                "result": {
                    "status": "failed",
                    "data": {},
                    "_meta": {"failures": [f"{type(exc).__name__}: {exc}"]},
                },
            }
        result_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return 0


def summarize_variant(root: Path) -> dict[str, Any]:
    """汇总一个变体的 token、完整率和校验问题；参数 root 为变体目录。"""

    rows = []
    totals = {
        "pdfs": 0,
        "calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cache_hit_tokens": 0,
        "cache_miss_tokens": 0,
        "validation_issues": 0,
    }
    status_counts: Counter[str] = Counter()
    filled_counts: Counter[str] = Counter()
    by_category: dict[str, dict[str, int]] = defaultdict(
        lambda: {"pdfs": 0, "total_tokens": 0, "input_tokens": 0}
    )
    for path in sorted((root / "results").rglob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        result = payload.get("result") or {}
        status = str(result.get("status") or "unknown")
        status_counts[status] += 1
        usage = _sum_usage(result)
        totals["pdfs"] += 1
        for key in (
            "calls",
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "cache_hit_tokens",
            "cache_miss_tokens",
        ):
            totals[key] += usage[key]
        meta = result.get("_meta") or {}
        issues = meta.get("validation_issues") or []
        totals["validation_issues"] += len(issues)
        data = result.get("data") or {}
        fields = CORE_FIELDS.get(payload.get("kind") or "fixed", ())
        filled = [
            field for field in fields
            if _field_filled(field, data.get(field))
        ]
        filled_counts["filled"] += len(filled)
        filled_counts["possible"] += len(fields)
        category = payload.get("category") or "?"
        by_category[category]["pdfs"] += 1
        by_category[category]["total_tokens"] += usage["total_tokens"]
        by_category[category]["input_tokens"] += usage["input_tokens"]
        rows.append(
            {
                "pdf": payload.get("pdf"),
                "category": category,
                "kind": payload.get("kind"),
                "status": status,
                "usage": usage,
                "validation_issues": len(issues),
                "filled_fields": filled,
                "data": data,
            }
        )
    totals["avg_tokens_per_pdf"] = (
        round(totals["total_tokens"] / totals["pdfs"], 1)
        if totals["pdfs"]
        else 0
    )
    totals["completeness"] = (
        round(filled_counts["filled"] / filled_counts["possible"], 4)
        if filled_counts["possible"]
        else 0
    )
    totals["statuses"] = dict(status_counts)
    totals["by_category"] = dict(by_category)
    totals["rows"] = rows
    return totals


def compare_variants(
    baseline: dict[str, Any],
    optimized: dict[str, Any],
) -> dict[str, Any]:
    """比较两个变体的 token、完整率与字段差异；参数为汇总结果。"""

    def delta(key: str) -> float:
        """返回相对基线变化百分比；参数 key 为指标名。"""

        base = float(baseline.get(key) or 0)
        new = float(optimized.get(key) or 0)
        if not base:
            return 0.0
        return round((new - base) / base * 100, 2)

    baseline_rows = {row["pdf"]: row for row in baseline.get("rows") or []}
    optimized_rows = {row["pdf"]: row for row in optimized.get("rows") or []}
    changed: dict[str, list[dict[str, Any]]] = defaultdict(list)
    changed_pdfs: set[str] = set()
    for pdf in sorted(set(baseline_rows) & set(optimized_rows)):
        before = baseline_rows[pdf]["data"]
        after = optimized_rows[pdf]["data"]
        fields = CORE_FIELDS.get(
            baseline_rows[pdf].get("kind") or "fixed",
            (),
        )
        for field in fields:
            if not _field_filled(field, before.get(field)) and not _field_filled(
                field,
                after.get(field),
            ):
                continue
            if _normalized(before.get(field)) != _normalized(after.get(field)):
                changed_pdfs.add(pdf)
                if len(changed[field]) < 5:
                    changed[field].append(
                        {
                            "pdf": pdf,
                            "baseline": before.get(field),
                            "optimized": after.get(field),
                        }
                    )
    return {
        "delta_percent": {
            key: delta(key)
            for key in (
                "total_tokens",
                "input_tokens",
                "output_tokens",
                "cache_hit_tokens",
                "validation_issues",
                "completeness",
            )
        },
        "changed_pdf_count": len(changed_pdfs),
        "changed_fields": {field: items for field, items in changed.items()},
        "comparable_pdfs": len(set(baseline_rows) & set(optimized_rows)),
    }


def _write_report(
    out_dir: Path,
    manifest: list[dict[str, str]],
    summaries: dict[str, dict[str, Any]],
    comparisons: dict[str, dict[str, Any]],
) -> None:
    """写出 JSON 和 Markdown 报告；参数 out_dir/manifest/summaries 为结果。"""

    report = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "pdf_count": len(manifest),
        "variants": {name: VARIANTS[name] for name in summaries},
        "summaries": summaries,
        "comparisons": comparisons,
    }
    (out_dir / "ab_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = [
        "# 结构化抽取成本 A/B 报告",
        "",
        f"- 样本数：{len(manifest)}",
        f"- 生成时间：{report['generated_at']}",
        "",
        "| 变体 | 消息顺序 | 文本层 | 上下文 | PDF | Token 合计 | 输入 | 输出 | 缓存命中 | 缓存未命中 | 平均 Token/PDF | 校验问题 | 字段完整率 |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, summary in summaries.items():
        env = VARIANTS[name]
        lines.append(
            "| {name} | {order} | {layer} | {context} | {pdfs} | {total} | {input} | {output} | "
            "{cache} | {cache_miss} | {avg} | {issues} | {completeness:.1%} |".format(
                name=name,
                order=(
                    "context-first"
                    if env["FACTSHEET_CACHE_FRIENDLY_CONTEXT"] == "1"
                    else "prompt-first"
                ),
                layer=env["FACTSHEET_PDF_TEXT_LAYER"],
                context=env.get("FACTSHEET_CONTEXT_MODE", "full"),
                pdfs=summary["pdfs"],
                total=summary["total_tokens"],
                input=summary["input_tokens"],
                output=summary["output_tokens"],
                cache=summary["cache_hit_tokens"],
                cache_miss=summary["cache_miss_tokens"],
                avg=summary["avg_tokens_per_pdf"],
                issues=summary["validation_issues"],
                completeness=summary["completeness"],
            )
        )
    for name, comparison in comparisons.items():
        lines.extend(
            [
                "",
                f"## optimized vs {name}",
                "",
                "- Token 变化：{total_tokens:+.2f}%".format(
                    **comparison["delta_percent"]
                ),
                "- 输入变化：{input_tokens:+.2f}%".format(
                    **comparison["delta_percent"]
                ),
                "- 输出变化：{output_tokens:+.2f}%".format(
                    **comparison["delta_percent"]
                ),
                "- 缓存命中变化：{cache_hit_tokens:+.2f}%".format(
                    **comparison["delta_percent"]
                ),
                "- 校验问题变化：{validation_issues:+.2f}%".format(
                    **comparison["delta_percent"]
                ),
                "- 字段完整率变化：{completeness:+.2f}%".format(
                    **comparison["delta_percent"]
                ),
                f"- 有字段差异的 PDF：{comparison['changed_pdf_count']}"
                f" / {comparison['comparable_pdfs']}",
            ]
        )
        if comparison["changed_fields"]:
            lines.extend(["", "### 差异最大的字段", ""])
            for field, items in comparison["changed_fields"].items():
                lines.append(f"- `{field}`：{len(items)} 个示例")
                for item in items[:3]:
                    lines.append(
                        f"  - {Path(item['pdf']).name}: "
                        f"{_normalized(item['baseline'])[:120]} → "
                        f"{_normalized(item['optimized'])[:120]}"
                    )
    lines.extend(
        [
            "",
            "> 说明：若样本在近期生产任务中已运行过，baseline 可能命中历史缓存；"
            "缓存命中差异应以冷样本或首轮调用的 cache_miss 为准。",
            "",
        ]
    )
    (out_dir / "ab_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _variant_command(
    variant: str,
    manifest_path: Path,
    out_dir: Path,
    args: argparse.Namespace,
) -> tuple[list[str], dict[str, str]]:
    """构造子进程命令和环境；参数 variant/manifest 为上下文。"""

    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--run-variant",
        variant,
        "--manifest",
        str(manifest_path),
        "--out",
        str(out_dir),
        "--mineru-root",
        str(args.mineru_root),
        "--primary-model",
        args.primary_model,
        "--fallback-model",
        args.fallback_model,
        "--vision-model",
        args.vision_model,
        "--mineru-model",
        args.mineru_model,
        "--attempts",
        str(args.attempts),
        "--timeout",
        str(args.timeout),
    ]
    if args.enable_thinking:
        command.append("--enable-thinking")
    if not args.resume:
        command.append("--no-resume")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(CODE_DIR), env.get("PYTHONPATH", "")]
    ).strip(os.pathsep)
    env.update(VARIANTS[variant])
    return command, env


def build_parser() -> argparse.ArgumentParser:
    """构造命令行参数解析器；参数为空。"""

    from app_config import value as config_value

    parser = argparse.ArgumentParser(
        description="A/B structured extraction token cost on cached MinerU PDFs.",
    )
    parser.add_argument("--pdf-root", type=Path, help="factsheet 根目录（含类别子目录）")
    parser.add_argument("--mineru-root", type=Path, help="markdown 根目录（含类别子目录）")
    parser.add_argument(
        "--categories",
        default="固收,权益,中国精选,混合",
        help="逗号分隔的类别；只选择存在的类别",
    )
    parser.add_argument("--limit", type=int, default=10, help="总样本数")
    parser.add_argument("--out", type=Path, help="输出目录")
    parser.add_argument(
        "--variants",
        default="baseline,optimized",
        help="逗号分隔的变体：baseline,cache,text_layer,optimized,local_first,compact,local_compact",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="实际调用模型；不加时只列出样本",
    )
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--primary-model", default=config_value("models", "primary_model", "deepseek-flash"))
    parser.add_argument("--fallback-model", default=config_value("models", "fallback_model", "deepseek-v4-pro"))
    parser.add_argument("--vision-model", default=config_value("models", "vision_model", "deepseek-flash"))
    parser.add_argument("--mineru-model", default=config_value("models", "mineru_model", "vlm"))
    parser.add_argument(
        "--attempts",
        type=int,
        default=int(config_value("processing", "api_retry_attempts", "3") or 3),
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(config_value("processing", "api_timeout_seconds", "300") or 300),
    )
    parser.add_argument("--enable-thinking", action="store_true")
    parser.add_argument("--run-variant", help=argparse.SUPPRESS)
    parser.add_argument("--manifest", type=Path, help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    """命令入口；参数 argv 为可选参数列表。"""

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.run_variant:
        if not args.manifest or not args.out:
            parser.error("--run-variant requires --manifest and --out")
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        return run_variant(args.run_variant, manifest, args.out, args)

    if not args.pdf_root or not args.mineru_root:
        parser.error("--pdf-root and --mineru-root are required")
    categories = [
        item.strip()
        for item in args.categories.split(",")
        if item.strip() in SUPPORTED_CATEGORIES
    ]
    out_dir = args.out or (
        PROJECT_ROOT
        / "data"
        / "_ab"
        / f"structured_cost_{datetime.now():%Y%m%d_%H%M%S}"
    )
    manifest = select_pdfs(
        args.pdf_root,
        args.mineru_root,
        categories,
        max(1, args.limit),
    )
    if not manifest:
        print("没有找到带 MinerU 缓存的 PDF 样本", file=sys.stderr)
        return 2
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "ab_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"样本 {len(manifest)} 份，输出目录：{out_dir}")
    for entry in manifest:
        print(f"  - {entry['category']}/{Path(entry['pdf']).name}")
    if not args.execute:
        print("未加 --execute，仅生成样本清单；不会调用模型。")
        return 0

    variants = [
        item.strip()
        for item in args.variants.split(",")
        if item.strip() in VARIANTS
    ]
    summaries: dict[str, dict[str, Any]] = {}
    for variant in variants:
        variant_dir = out_dir / variant
        command, env = _variant_command(variant, manifest_path, variant_dir, args)
        print(f"[*] 运行变体 {variant}: {' '.join(command)}", flush=True)
        completed = subprocess.run(command, env=env, check=False)
        if completed.returncode != 0:
            print(f"变体 {variant} 退出码 {completed.returncode}", file=sys.stderr)
        summaries[variant] = summarize_variant(variant_dir)

    comparisons: dict[str, dict[str, Any]] = {}
    optimized = summaries.get("optimized")
    if optimized is not None:
        for name, summary in summaries.items():
            if name == "optimized":
                continue
            comparisons[name] = compare_variants(summary, optimized)
    _write_report(out_dir, manifest, summaries, comparisons)
    for name, summary in summaries.items():
        print(
            f"[{name}] tokens={summary['total_tokens']} "
            f"input={summary['input_tokens']} output={summary['output_tokens']} "
            f"cache_hit={summary['cache_hit_tokens']} "
            f"avg={summary['avg_tokens_per_pdf']} "
            f"completeness={summary['completeness']:.1%} "
            f"issues={summary['validation_issues']}"
        )
    print(f"报告：{out_dir / 'ab_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
