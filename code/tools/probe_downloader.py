# -*- coding: utf-8 -*-
"""通用下载探测工具：不改任何公司下载器，直接复用它们完成“能不能下载”验证。

原理：所有公司下载器都统一调用 common_paths 的输出函数。本工具用子进程执行
下载器，并设置 FACTSHEET_OUTPUT_ROOT / FACTSHEET_RUN_ID 两个环境变量，
把“真实下载”临时重定向到 data/_probe/<run_id>/，跑完展示日志与产出文件。
data/_probe 以下划线开头，GUI 批次选择器会忽略，不影响正式批次。

两种用法：
  1) 探测还没写入爬虫list 的新基金（临时给命令和参数）：
     python code/tools/probe_downloader.py --cmd "python download_blackrock_factsheet.py \"PID\" \"fact-sheet\""
  2) 探测爬虫list 里已写好的行：
     python code/tools/probe_downloader.py --only "基金关键字" --sheet Sheet1
"""

from __future__ import annotations

import argparse
import datetime
import os
import subprocess
import sys


CODE_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
PROJECT_ROOT = os.path.abspath(os.path.join(CODE_DIR, ".."))
PROBE_ROOT = os.path.join(PROJECT_ROOT, "data", "_probe")
OUTPUT_LIMIT = 12000

sys.path.insert(0, CODE_DIR)
import run_all_downloaders as runner  # noqa: E402  复用爬虫list 的行解析与脚本发现逻辑


def ensure_utf8_stdout():
    """让 Windows 控制台也能正常打印中文。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def clip_text(value, limit=OUTPUT_LIMIT):
    """截断过长输出：参数 value：原始文本；limit：最大保留字符数。"""
    if value is None:
        return ""
    if len(value) <= limit:
        return value
    return f"……（输出过长，已省略前 {len(value) - limit} 个字符）……\n{value[-limit:]}"


def failure_hint(output):
    """根据报错文本给一句人工排查提示：参数 output：子进程 stdout+stderr。"""
    text = (output or "").lower()
    if any(word in text for word in ("access denied", "forbidden", "403")):
        return "提示：网站拒绝脚本访问（反爬/403）。可稍后重试，或先在浏览器确认链接仍可用。"
    if any(word in text for word in ("no blackrock document matched", "missing document",
                                     "没有找到", "找不到", "not found", "404")):
        return "提示：网站在该参数下找不到目标文件（基金可能已停发月报，或参数/链接不对）。"
    if any(word in text for word in ("timeout", "timed out", "readtimeout", "connecttimeout",
                                     "connectionerror", "connection reset")):
        return "提示：网络超时/连接失败，稍后重试，或检查代理配置 FACTSHEET_PROXY。"
    if any(word in text for word in ("not a pdf", "not pdf", "invalid download")):
        return "提示：下载到的内容不是 PDF，可能是反爬页面，需要人工检查网址。"
    return "提示：见上方日志；若日志不足，可先不带 --probe 直接运行该下载器复现。"


def build_probe_env(run_id, category, fund_name):
    """构造重定向输出目录的子进程环境：参数 run_id/category/fund_name。"""
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("FACTSHEET_TRUST_ENV", "1")
    env["FACTSHEET_OUTPUT_ROOT"] = PROBE_ROOT
    env["FACTSHEET_RUN_ID"] = run_id
    env["FACTSHEET_OUTPUT_CATEGORY"] = category or "probe"
    env["FACTSHEET_OUTPUT_FUND"] = fund_name or "probe_fund"
    return env


def collect_output_files(run_dir):
    """扫描探测批次目录里的产出文件：参数 run_dir：本次探测的输出根目录。"""
    if not os.path.isdir(run_dir):
        return []
    files = []
    for root, _, names in os.walk(run_dir):
        for name in sorted(names):
            full = os.path.join(root, name)
            files.append(os.path.relpath(full, PROJECT_ROOT))
    return files


def run_one_probe(label, script_rel, args, category, fund_name, timeout):
    """执行一次下载器探测。

    参数 label：任务说明；script_rel：脚本相对 code 的路径；
    args：下载器参数列表；category/fund_name：输出目录标识；
    timeout：单次超时秒数。返回是否成功（退出码 0 且至少产出一个文件）。
    """
    stamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    run_id = f"probe_{stamp}_{abs(hash(label)) % 10000:04d}"
    script_path = os.path.join(CODE_DIR, *script_rel.split("/"))
    command = [sys.executable, script_path] + list(args)
    env = build_probe_env(run_id, category, fund_name)
    run_dir = os.path.join(PROBE_ROOT, run_id)

    print("\n" + "=" * 70)
    print(f"[任务] {label}")
    print(f"[命令] python {' '.join([os.path.relpath(script_path, PROJECT_ROOT)] + list(args))}")
    print(f"[探测输出] {os.path.relpath(run_dir, PROJECT_ROOT)}")
    try:
        proc = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        print(f"[失败] 超过 {timeout} 秒未结束，已中断。")
        return False
    except Exception as exc:
        print(f"[失败] 启动下载器异常：{type(exc).__name__}: {exc}")
        return False

    combined = f"{proc.stdout or ''}\n{proc.stderr or ''}".strip()
    print("---- 下载器日志 ----")
    print(clip_text(combined) or "（无输出）")
    if proc.returncode != 0:
        print(f"---- 失败（退出码 {proc.returncode}）----")
        print(failure_hint(combined))
        return False

    files = collect_output_files(run_dir)
    pdfs = [f for f in files if f.lower().endswith(".pdf")]
    print("---- 产出文件 ----")
    if files:
        for item in files:
            print(f"  {item}")
    else:
        print("  （没有文件产出）")
    if not files:
        print("[结论] 命令退出码为 0，但没有产出文件，仍需人工确认。")
        print(failure_hint(combined))
        return False
    print(f"[结论] 探测成功：找到 {len(pdfs)} 个 PDF / 共 {len(files)} 个文件。")
    return True


def resolve_cmd_task(cmd_text, category, fund_name):
    """把 --cmd 的命令文本解析成任务：参数 cmd_text：形如 python 脚本 参数。"""
    lookup = runner.script_lookup()
    try:
        script_rel, args = runner.parse_run_command(cmd_text, lookup, 0)
    except ValueError as exc:
        print(f"[FAILED] 命令解析失败：{exc}")
        return None
    script_name = os.path.basename(script_rel)
    label = f"{script_name} {' '.join(args)}".strip()
    return {
        "label": label,
        "script_rel": script_rel,
        "args": args,
        "category": category or "probe",
        "fund_name": fund_name or script_name[:-3],
    }


def resolve_row_tasks(only_patterns, sheet_name, crawler_list_path):
    """从爬虫list 解析选中的行作为任务：参数 only_patterns/sheet_name/crawler_list_path。"""
    if crawler_list_path and not os.path.isabs(crawler_list_path):
        crawler_list_path = os.path.join(PROJECT_ROOT, crawler_list_path)
    sheet_names = [sheet_name] if sheet_name else None
    try:
        targets = runner.selected_targets(only_patterns, [], crawler_list_path, sheet_names=sheet_names)
    except Exception as exc:
        print(f"[FAILED] 读取爬虫list 失败：{type(exc).__name__}: {exc}")
        return []
    tasks = []
    for target in targets:
        label = f"row {target.get('row_number')} [{target.get('category')}] {target.get('fund_name')}"
        tasks.append({
            "label": label,
            "script_rel": target.get("script") or "",
            "args": target.get("args") or [],
            "category": target.get("category") or "probe",
            "fund_name": target.get("fund_name") or "probe_fund",
        })
    return tasks


def parse_args(argv):
    """解析命令行参数：参数 argv：sys.argv[1:]。"""
    parser = argparse.ArgumentParser(
        description="复用各公司现有下载器做探测（试下载到 data/_probe，不改任何下载器）。",
    )
    parser.add_argument("--cmd", default="", help="临时命令，例如：python download_blackrock_factsheet.py \"PID\" \"fact-sheet\"")
    parser.add_argument("--only", action="append", default=[], help="按基金名/脚本关键字筛选爬虫list 行，可多次传入")
    parser.add_argument("--sheet", default="", help="爬虫list 工作表名，默认第一张表")
    parser.add_argument("--crawler-list", default=runner.CRAWLER_LIST_PATH, help="爬虫list.xlsx 路径")
    parser.add_argument("--fund", default="", help="--cmd 模式下的基金名（只影响探测目录内文件名）")
    parser.add_argument("--category", default="", help="--cmd 模式下的输出类别，默认 probe")
    parser.add_argument("--timeout", type=int, default=600, help="单个下载器超时秒数，默认 600")
    parser.add_argument("--list", action="store_true", help="只列出将要执行的命令，不真正运行")
    return parser.parse_args(argv)


def main(argv=None):
    """探测入口：参数 argv：可选参数列表，缺省读 sys.argv。"""
    ensure_utf8_stdout()
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.cmd and args.only:
        print("[FAILED] --cmd 与 --only 二选一，不能同时使用。")
        return 2
    if not args.cmd and not args.only:
        print("[FAILED] 必须提供 --cmd（临时命令）或 --only（爬虫list 行关键字）。")
        return 2

    if args.cmd:
        task = resolve_cmd_task(args.cmd, args.category, args.fund)
        tasks = [task] if task else []
    else:
        tasks = resolve_row_tasks(args.only, args.sheet, args.crawler_list)
        if not tasks:
            print("[FAILED] 没有匹配到任何爬虫list 行。")
            return 2

    if args.list:
        for task in tasks:
            script = task["script_rel"].replace("/", os.sep)
            arg_text = " ".join(task["args"])
            print(f"python code/{script} {arg_text}".rstrip())
        return 0

    results = []
    for index, task in enumerate(tasks):
        ok = run_one_probe(
            task["label"],
            task["script_rel"],
            task["args"],
            task["category"],
            task["fund_name"],
            args.timeout,
        )
        results.append(ok)
    print("\n" + "=" * 70)
    succeeded = sum(1 for ok in results if ok)
    print(f"[汇总] 成功 {succeeded} / {len(results)}")
    return 0 if succeeded == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
