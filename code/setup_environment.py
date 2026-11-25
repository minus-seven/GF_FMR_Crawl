"""Validate the portable Windows runtime and prepare local configuration."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import platform
import struct
import sys
from datetime import datetime
from pathlib import Path

from app_config import ConfigError, ensure_config, safe_summary


CODE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CODE_DIR.parent
_state_root = os.environ.get("FACTSHEET_STATE_ROOT")
RUNTIME_ROOT = Path(
    os.environ.get("FACTSHEET_RUNTIME_ROOT")
    or (Path(_state_root) / "runtime" if _state_root else PROJECT_ROOT / ".runtime")
).resolve()

REQUIRED_IMPORTS = {
    "requests": "requests",
    "playwright": "playwright",
    "openpyxl": "openpyxl",
    "pypdf": "pypdf",
    "opencc": "opencc-python-reimplemented",
    "fitz": "PyMuPDF",
}


def _find_browser() -> Path | None:
    browser_root = Path(
        os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
        or RUNTIME_ROOT / "ms-playwright"
    )
    if not browser_root.is_dir():
        return None
    candidates = (
        "*chromium*/chrome-win*/chrome.exe",
        "*chromium*/chrome-win*/headless_shell.exe",
        "*chromium*/chrome-headless-shell-win*/chrome-headless-shell.exe",
    )
    for pattern in candidates:
        match = next(browser_root.glob(pattern), None)
        if match:
            return match
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-portable", action="store_true")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    failures: list[str] = []
    executable = Path(sys.executable).resolve()
    portable_python = (RUNTIME_ROOT / "python" / "python.exe").resolve()
    if sys.version_info < (3, 11):
        failures.append(f"Python 版本过低：{platform.python_version()}，至少需要 3.11")
    if struct.calcsize("P") * 8 != 64:
        failures.append("必须使用 64 位 Python")
    if args.require_portable and executable != portable_python:
        failures.append(f"没有使用项目便携 Python：{portable_python}")

    versions: dict[str, str] = {}
    for module_name, package_name in REQUIRED_IMPORTS.items():
        try:
            module = importlib.import_module(module_name)
            versions[package_name] = str(getattr(module, "__version__", "已安装"))
        except Exception as exc:
            failures.append(f"缺少或无法加载 {package_name}：{type(exc).__name__}: {exc}")

    browser = _find_browser()
    if args.require_portable and browser is None:
        failures.append("便携包中缺少 Playwright Chromium 浏览器")

    try:
        config_file, created = ensure_config()
        summary = safe_summary()
    except ConfigError as exc:
        failures.append(str(exc))
        config_file = PROJECT_ROOT / "config.ini"
        created = False
        summary = {}

    if failures:
        print("[!] 环境检查未通过：")
        for failure in failures:
            print(f"    - {failure}")
        print("请重新取得完整的离线运行包，或联系交付人员重新制作。")
        return 2

    ready = {
        "checked_at": datetime.now().astimezone().isoformat(),
        "python": platform.python_version(),
        "python_executable": str(executable),
        "portable": executable == portable_python,
        "packages": versions,
        "browser": str(browser) if browser else "",
        "config": str(config_file),
    }
    RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    ready_path = RUNTIME_ROOT / "READY.json"
    temporary = ready_path.with_suffix(".json.writing")
    temporary.write_text(json.dumps(ready, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, ready_path)

    print("[+] Windows 运行环境检查通过。")
    print(f"    Python：{platform.python_version()} ({'便携版' if ready['portable'] else '本机开发环境'})")
    print(f"    配置：{config_file}{'（已自动创建）' if created else ''}")
    print(f"    Qwen Key：{summary.get('qwen_key', '未填写')}")
    print(f"    DeepSeek Key：{summary.get('deepseek_key', '未填写')}")
    print(f"    MinerU Token：{summary.get('mineru_token', '未填写')}")
    structured_key = (
        summary.get("deepseek_key")
        if summary.get("structured_provider") == "deepseek"
        else summary.get("qwen_key")
    )
    if structured_key == "未填写" or summary.get("mineru_token") == "未填写":
        print("[!] Key 尚未填完整，请双击“配置API.bat”。")
    else:
        print("[+] 可以直接使用三个 run_all_*.bat。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
