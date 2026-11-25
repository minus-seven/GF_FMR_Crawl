"""Export the FastAPI-generated OpenAPI document."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from app.core.config import get_settings
from app.main import app


def parse_args() -> argparse.Namespace:
    """解析 OpenAPI 导出参数；参数为空。"""

    parser = argparse.ArgumentParser(description="导出 FastAPI OpenAPI JSON。")
    parser.add_argument(
        "--json-output",
        type=Path,
        default=(
            get_settings().project_root
            / "docs"
            / "refinement"
            / "contracts"
            / "openapi-generated-v0.3.json"
        ),
    )
    parser.add_argument(
        "--yaml-output",
        type=Path,
        default=(
            get_settings().project_root
            / "docs"
            / "refinement"
            / "contracts"
            / "openapi-v0.3.yaml"
        ),
    )
    return parser.parse_args()


def main() -> int:
    """生成并写入 OpenAPI JSON；参数为空。"""

    args = parse_args()
    schema = app.openapi()
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    args.yaml_output.parent.mkdir(parents=True, exist_ok=True)
    args.yaml_output.write_text(
        yaml.safe_dump(
            schema,
            allow_unicode=True,
            sort_keys=False,
            width=1000,
        ),
        encoding="utf-8",
    )
    print(args.json_output.resolve())
    print(args.yaml_output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
