"""Apply config.ini and execute an existing project entrypoint."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

from app_config import ConfigError, apply_environment


CODE_DIR = Path(__file__).resolve().parent


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        print("[!] 缺少要运行的脚本。")
        return 2
    target = (CODE_DIR / arguments.pop(0)).resolve()
    if target.parent != CODE_DIR and CODE_DIR not in target.parents:
        print("[!] 拒绝运行项目 code 目录以外的脚本。")
        return 2
    if not target.is_file() or target.suffix.lower() != ".py":
        print(f"[!] 找不到脚本：{target}")
        return 2
    try:
        apply_environment()
    except ConfigError as exc:
        print(f"[!] 配置文件错误：{exc}")
        print("    请双击“配置API.bat”检查配置。")
        return 2
    sys.argv = [str(target), *arguments]
    try:
        runpy.run_path(str(target), run_name="__main__")
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
