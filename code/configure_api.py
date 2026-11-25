"""Create, validate, or open the local unified configuration file."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

from app_config import ConfigError, apply_environment, config_path, ensure_config, safe_summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--open", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        path, created = ensure_config()
        apply_environment()
        summary = safe_summary()
    except ConfigError as exc:
        print(f"[!] 配置错误：{exc}")
        return 2

    if created:
        print(f"[+] 已创建统一配置：{path}")
    if args.check:
        print(f"配置文件：{summary['config']}")
        print(f"Qwen URL：{summary['qwen_url']}")
        print(f"Qwen Key：{summary['qwen_key']}")
        print(f"DeepSeek URL：{summary['deepseek_url']}")
        print(f"DeepSeek Key：{summary['deepseek_key']}")
        print(f"MinerU URL：{summary['mineru_url']}")
        print(f"MinerU Token：{summary['mineru_token']}")
        print(f"MinerU 路线：{summary['mineru_mode']}")
        print(
            "模型："
            f"{summary['primary_model']} / {summary['fallback_model']} / "
            f"{summary['vision_model']}"
        )
        print(f"结构化供应商：{summary['structured_provider']}")
    if args.open:
        if os.name != "nt":
            print(path)
            return 0
        subprocess.Popen(["notepad.exe", str(path)])
        print("[+] 配置文件已打开。保存后，下次 API 调用立即使用新配置。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
