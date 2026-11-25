#!/usr/bin/env python
"""Audit extracted fixed-income JSON against PDF text candidates."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[1]))

try:
    from structured_data.extract_fixed_income import (
        _compute_fund_size_hkd_100m,
        _extract_pdf_text,
        _normalize_report_month,
        _parse_fund_size_from_text,
        _parse_metric_from_text,
    )
    from structured_data.rating import normalize_rating
except ImportError:  # pragma: no cover
    from extract_fixed_income import (
        _compute_fund_size_hkd_100m,
        _extract_pdf_text,
        _normalize_report_month,
        _parse_fund_size_from_text,
        _parse_metric_from_text,
    )
    from rating import normalize_rating


MODULE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = MODULE_DIR.parents[1]
DEFAULT_RUN_ID = "20260717143244"
DEFAULT_PDF_DIR = PROJECT_ROOT / "data" / DEFAULT_RUN_ID / "固收"
DEFAULT_JSON_DIR = PROJECT_ROOT / "data" / "_structured" / DEFAULT_RUN_ID / "per_pdf"
DEFAULT_OUT = PROJECT_ROOT / "data" / "_structured" / DEFAULT_RUN_ID / "audit" / "fixed_income_audit.csv"


def _num(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?[0-9][0-9,]*(?:\.[0-9]+)?", str(value))
    return float(match.group(0).replace(",", "")) if match else None


def _close(a: Any, b: Any, tol: float = 0.02) -> bool:
    left = _num(a)
    right = _num(b)
    if left is None or right is None:
        return left is None and right is None
    return abs(left - right) <= tol


def _text_candidate(text: str, field: str, extracted: Any = None) -> Any:
    if field == "report_date":
        return _normalize_report_month(f"{extracted or ''}\n{text}")
    if field == "fund_size_hkd_100m":
        return _compute_fund_size_hkd_100m(_parse_fund_size_from_text(text))
    if field == "portfolio_duration_years":
        return _parse_metric_from_text(text, ["组合久期", "平均久期", "加权平均存续期", "平均存续期", "Duration", "Average duration"])
    if field == "portfolio_ytm_percent":
        return _parse_metric_from_text(
            text,
            ["平均最低孳息率", "最低孳息率", "组合到期收益率", "到期收益率", "最差收益率", "加权平均最差收益率", "Yield to maturity", "Yield to worst", "YTM", "YTW"],
            require_percent=True,
        )
    if field == "credit_rating":
        patterns = [
            r"(?:平均信用评级|平均信贷评级|平均信貸評級|加权平均债券评级|加權平均債券評級|Average Credit Rating|Average rating).{0,30}?([A]{1,3}[+-]?|BBB[+-]?|BB[+-]?|B[+-]?|CCC[+-]?|CC|C|D|NR)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return normalize_rating(match.group(1))
    return None


def _status(extracted: Any, candidate: Any, field: str) -> str:
    if field in {"fund_name", "dividend"}:
        return "not_checked"
    if candidate in (None, ""):
        return "no_text_candidate"
    if extracted in (None, ""):
        return "missing"
    if field == "credit_rating":
        return "match" if normalize_rating(extracted) == normalize_rating(candidate) else "diff"
    return "match" if _close(extracted, candidate) else "diff"


def audit(pdf_dir: Path, json_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    fields = ["report_date", "fund_size_hkd_100m", "portfolio_duration_years", "portfolio_ytm_percent", "credit_rating"]
    for json_path in sorted(json_dir.glob("*.fixed_income.json")):
        data = json.loads(json_path.read_text(encoding="utf-8"))
        fund_name = str(data.get("fund_name") or json_path.stem.replace(".fixed_income", ""))
        meta_path = json_path.with_suffix(".meta.json")
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        source_file = ((meta.get("_meta") or {}).get("source_file") or "").strip()
        candidates = []
        if source_file:
            candidates.append(Path(source_file))
        candidates.append(pdf_dir / f"{json_path.stem.replace('.fixed_income', '')}.pdf")
        candidates.append(pdf_dir / f"{fund_name}.pdf")
        pdf_path = next((path for path in candidates if path.exists()), candidates[0])
        text = _extract_pdf_text(pdf_path) if pdf_path.exists() else ""
        validation_issues = "; ".join((meta.get("_meta") or {}).get("validation_issues") or [])
        for field in fields:
            extracted = data.get(field)
            candidate = _text_candidate(text, field, extracted)
            rows.append({
                "fund_name": fund_name,
                "field": field,
                "extracted": extracted,
                "pdf_text_candidate": candidate,
                "status": _status(extracted, candidate, field),
                "source_json": str(json_path),
                "source_pdf": str(pdf_path),
                "validation_issues": validation_issues,
            })
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = list(rows[0].keys()) if rows else ["empty"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit fixed-income extraction outputs against PDF text.")
    parser.add_argument("--pdf-dir", type=Path, default=DEFAULT_PDF_DIR)
    parser.add_argument("--json-dir", type=Path, default=DEFAULT_JSON_DIR)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    rows = audit(args.pdf_dir, args.json_dir)
    write_csv(args.out, rows)
    summary: dict[str, int] = {}
    for row in rows:
        summary[row["status"]] = summary.get(row["status"], 0) + 1
    print(json.dumps({"rows": len(rows), "summary": summary, "out": str(args.out)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
