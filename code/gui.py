# -*- coding: utf-8 -*-
"""基金月报处理工具 - 图形界面。

三步流水线：
  1) 下载：按 爬虫list.xlsx 下载各基金 PDF，自动新建 data/<时间戳> 批次
  2) 解析：MinerU 将批次内 PDF 转为 Markdown 缓存
  3) 结构化：大模型按实际类别抽取并生成 Excel

- 解析/结构化 通过批次下拉框选择，默认最新批次（替代终端序号输入）
- 日志两级：低等级只显示状态日志；高等级显示全部输出并追加 log/json 报告
"""

from __future__ import annotations

import codecs
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
from collections import deque
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

from batch_layout import pdf_category_dir, pdf_categories_with_files
from batch_selector import available_batch_roots


CODE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = Path(
    os.environ.get("FACTSHEET_PROJECT_ROOT") or CODE_DIR.parent
).resolve()
STATE_ROOT = Path(
    os.environ.get("FACTSHEET_STATE_ROOT") or PROJECT_ROOT.parent.parent / "fund-research-state"
).expanduser().resolve()
DATA_DIR = STATE_ROOT / "data"
if os.environ.get("FACTSHEET_DATA_ROOT"):
    DATA_DIR = Path(os.environ["FACTSHEET_DATA_ROOT"]).expanduser().resolve()
CRAWLER_WORKBOOK = STATE_ROOT / "inputs" / "爬虫list.xlsx"
if (PROJECT_ROOT / "爬虫list.xlsx").is_file():
    CRAWLER_WORKBOOK = PROJECT_ROOT / "爬虫list.xlsx"
if os.environ.get("FACTSHEET_CRAWLER_WORKBOOK"):
    CRAWLER_WORKBOOK = Path(os.environ["FACTSHEET_CRAWLER_WORKBOOK"]).expanduser().resolve()
RUN_WITH_CONFIG = CODE_DIR / "run_with_config.py"
DOWNLOAD_SCRIPT = "run_all_downloaders.py"
MINERU_SCRIPT = "run_all_mineru.py"
STRUCTURED_SCRIPT = "run_all_structured_data.py"

STAGE_NAMES = {"download": "下载", "mineru": "解析", "structure": "结构化"}
STAGE_ORDER = ("download", "mineru", "structure")

LEVEL_LOW = "low"
LEVEL_HIGH = "high"
LOG_BUFFER_LIMIT = 20000

# 识别控制台中的"状态行"（低等级日志只显示这些行）
SUMMARY_RE = re.compile(
    r"^(?:\[\*\]|\[\+\]|\[!\]|\[FAILED\]|\[RETRY\]|\[ERROR\])"
    r"|下载进度|MinerU 进度|Excel 解析"
    r"|补全进度"
    r"|失败基金|失败文件|失败项目"
    r"|下载完成|MinerU 完成|Excel 解析完成"
    r"|模型调用|Token|原因：|^  \d+\.|^    -|重试"
)
RE_RUN_ID = re.compile(r"\[\*\] Run ID:\s*(\S+)")
RE_DOWNLOAD_PROGRESS = re.compile(
    r"下载进度.*?(\d+)/(\d+)\s+成功\s+(\d+)\s+失败\s+(\d+)"
)
# 整轮汇总进度行：下载进度 ... 11/11 回填 4 成功 9 跳过 2 失败 0
RE_DOWNLOAD_PROGRESS_SUMMARY = re.compile(
    r"下载进度.*?(\d+)/(\d+)(?:\s+回填\s+(\d+))?"
    r"\s+成功\s+(\d+)\s+跳过\s+(\d+)\s+失败\s+(\d+)"
)
RE_BACKFILL_PROGRESS = re.compile(
    r"补全进度.*?(\d+)/(\d+)\s+成功\s+(\d+)\s+失败\s+(\d+)"
)
RE_MINERU_PROGRESS = re.compile(
    r"MinerU 进度.*?(\d+)/(\d+)\s+成功/复用\s+(\d+)\s+失败\s+(\d+)"
)
RE_STRUCT_PROGRESS = re.compile(
    r"Excel 解析.*?(\d+)/(\d+)\s+成功\s+(\d+)\s+失败\s+(\d+)"
)
RE_STAGE_NOTE = re.compile(r"(?:阶段|处理中\d+项)：(.+)")


def friendly_failure_message(item):
    """把下载报告中的失败类别转成友好中文提示。

    参数 item：下载报告中的单个结果项（dict）。
    """
    categories = {
        "timeout": "网站响应超时",
        "network": "网络连接失败",
        "forbidden": "网站暂时拒绝访问",
        "not_found": "下载地址不存在",
        "missing_document": "网站上没有找到对应月报",
        "invalid_download": "下载内容不是有效 PDF",
        "source_website_changed": "基金公司网站结构可能已改变",
        "runner_exception": "下载程序运行异常",
        "config_error": "下载配置不完整，需要人工处理",
        "cancelled": "已取消",
    }
    category = str(item.get("failure_category") or "")
    if category in categories:
        return categories[category]
    return str(item.get("exception_summary") or "未知错误")


def summarize_download_items(items):
    """按下载结果项统计汇总。

    参数 items：下载报告中的 results 列表（list[dict]）。
    """
    return {
        "total": len(items),
        # 只有真正下载/已存在的行算成功，跳过与失败单独统计
        "ok": sum(
            1
            for it in items
            if it.get("status") not in ("failed", "skipped", "cancelled")
        ),
        "failed": sum(1 for it in items if it.get("status") == "failed"),
        "downloaded": sum(1 for it in items if it.get("status") == "downloaded"),
        "existing": sum(1 for it in items if it.get("status") == "existing"),
        "skipped": sum(1 for it in items if it.get("status") == "skipped"),
        "retried": sum(1 for it in items if it.get("retried")),
    }


def download_rows_from_items(items):
    """把下载报告结果项转为结果明细表行。

    参数 items：下载报告中的 results 列表（list[dict]）。
    """
    rows = []
    for item in items:
        status = item.get("status")
        if status == "failed":
            status_text = "失败"
            reason = friendly_failure_message(item)
        elif status == "skipped":
            status_text = "跳过"
            reason = item.get("exception_summary") or "官网未收录/未匹配，跳过"
        elif status == "cancelled":
            status_text = "已取消"
            reason = "手动停止"
        else:
            status_text = "成功"
            reason = {
                "existing": "已存在，跳过下载",
                "skipped": "跳过",
            }.get(status, "")
        rows.append(
            {
                "row_number": item.get("row_number"),
                "name": item.get("fund_name") or "",
                "category": item.get("category") or "",
                "status": status_text,
                "reason": reason,
            }
        )
    return rows


class RetryDialog(tk.Toplevel):
    """失败重试询问对话框：重试失败项 / 继续下一步 / 停止。"""

    def __init__(
        self,
        parent,
        stage_name,
        failure_lines,
        next_label="继续下一步",
        total_failures=None,
    ):
        """初始化重试询问对话框。

        参数 parent：父窗口；stage_name：阶段名称；failure_lines：失败项文本列表；
        参数 next_label：中间按钮文字（单步运行时显示"忽略并继续"）；
        参数 total_failures：失败项总数（列表可能只展示前 50 条）。
        """
        super().__init__(parent)
        self.title(f"{stage_name}存在失败项")
        self.result = None  # 用户选择：retry / next / stop
        self.transient(parent)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._close)

        body = ttk.Frame(self, padding=12)
        body.pack(fill=tk.BOTH, expand=True)

        total = total_failures if total_failures is not None else len(failure_lines)
        ttk.Label(
            body,
            text=f"{stage_name} 完成后仍有 {total} 个失败项，请选择处理方式：",
        ).pack(anchor=tk.W, pady=(0, 6))

        list_frame = ttk.Frame(body)
        list_frame.pack(fill=tk.BOTH, expand=True)
        listbox = tk.Listbox(
            list_frame,
            height=min(12, max(4, len(failure_lines))),
            width=88,
        )
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=listbox.yview)
        listbox.configure(yscrollcommand=scrollbar.set)
        for line in failure_lines:
            listbox.insert(tk.END, line)
        listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        buttons = ttk.Frame(body)
        buttons.pack(fill=tk.X, pady=(10, 0))
        ttk.Button(
            buttons, text="重试失败项", command=lambda: self._choose("retry")
        ).pack(side=tk.LEFT, padx=4)
        ttk.Button(
            buttons, text=next_label, command=lambda: self._choose("next")
        ).pack(side=tk.LEFT, padx=4)
        ttk.Button(
            buttons, text="停止", command=lambda: self._choose("stop")
        ).pack(side=tk.RIGHT, padx=4)

        self.update_idletasks()
        x = parent.winfo_rootx() + max(0, (parent.winfo_width() - self.winfo_reqwidth()) // 2)
        y = parent.winfo_rooty() + max(0, (parent.winfo_height() - self.winfo_reqheight()) // 3)
        self.geometry(f"+{x}+{y}")

    def _choose(self, choice):
        """记录用户选择并关闭对话框。

        参数 choice：retry / next / stop。
        """
        self.result = choice
        self._close()

    def _close(self):
        """释放模态并销毁对话框。"""
        self.grab_release()
        self.destroy()


class PipelineGUI:
    """基金月报处理工具主界面。"""

    def __init__(self, root):
        """初始化主界面。

        参数 root：tkinter 根窗口。
        """
        self.root = root
        self.root.title("基金月报处理工具")
        self.root.geometry("1000x750")
        self.root.minsize(750, 0)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # 运行状态
        self._proc = None  # 当前阶段子进程
        self._stop_event = threading.Event()  # 停止信号
        self._running_stage = None  # 当前运行阶段 key
        self._active_batch = None  # 当前运行阶段锁定的批次路径
        self._pending_on_done = None  # 阶段结束后的 GUI 回调
        self._all_queue = []  # 一键全部的执行队列
        self._all_mode = False  # 是否处于一键全部流程（用于进度标题 1/3、2/3、3/3）

        # 批次与日志状态
        self._batches = {}  # 批次名 -> 批次路径
        self._log_buffer = deque(maxlen=LOG_BUFFER_LIMIT)  # (kind, text)

        # 下载重试上下文
        self._retry_run_id = None  # 重试时固定使用的 run_id
        self._retry_only = []  # 重试时只重跑的基金名
        self._retry_previous_items = []  # 重试前的完整结果项（用于合并）
        self._download_report_path = None  # 最近一次下载报告路径
        self._last_download_run_id = None  # 最近一次真实下载批次 id

        self._build_vars()
        self._configure_styles()
        self._build_widgets()
        self._configure_log_tags()
        self._refresh_batches()
        self._load_sheet_names()
        self._fit_window_height()

    # ---------- 界面构建 ----------

    def _build_vars(self):
        """创建界面绑定的变量。"""
        self._batch_var = tk.StringVar()  # 当前选中的批次名
        self._batch_info_var = tk.StringVar(value="")  # 批次 PDF 数量说明
        self._log_level = tk.StringVar(value=LEVEL_LOW)  # 日志等级
        self._progress_var = tk.DoubleVar(value=0)  # 进度条数值
        self._crawler_list_var = tk.StringVar(
            value=str(CRAWLER_WORKBOOK)
        )  # 爬虫list.xlsx 路径
        self._sheet_var = tk.StringVar(value="所有")  # 工作表选择（默认所有）
        self._backfill_var = tk.BooleanVar(value=False)  # 是否开启爬虫补全
        # 爬虫补全下载的进度段：1=下载已有链接基金、2=补全、3=下载补全的基金
        self._backfill_segment = 1

    def _configure_styles(self):
        """调整按钮与页签样式：核心按钮与步骤页签字号固定为 11。"""
        style = ttk.Style(self.root)
        default = tkfont.nametofont("TkDefaultFont")
        family = str(default.cget("family"))
        size = 11
        # 一键全部/停止：字号 11 不加粗，内边距让按钮本体略大于默认
        style.configure("Primary.TButton", font=(family, size), padding=(4, 3))
        # 步骤页签：1.下载/2.解析/3.结构化 字号 11
        style.configure("TNotebook.Tab", font=(family, size))

    def _build_widgets(self):
        """创建主界面全部组件。"""
        main = ttk.Frame(self.root, padding=8)
        main.pack(fill=tk.BOTH, expand=True)
        # 左右分栏：左侧功能区与右侧日志区等宽
        main.columnconfigure(0, weight=1, uniform="col")
        main.columnconfigure(1, weight=1, uniform="col")
        main.rowconfigure(0, weight=1)

        left = ttk.Frame(main)
        left.grid(row=0, column=0, sticky=tk.NSEW, padx=(0, 1))
        left.columnconfigure(0, weight=1)

        # 右侧日志区使用与左侧一致的左右内边距，保证两侧内容严格等宽
        right = ttk.Frame(main, padding=(2, 0))
        right.grid(row=0, column=1, sticky=tk.NSEW, padx=(1, 0))

        # 置顶居中：一键全部 / 停止（放大 20%）
        top_bar = ttk.Frame(left)
        top_bar.grid(row=0, column=0, sticky=tk.EW, padx=(12, 12), pady=(0, 6))
        top_bar.columnconfigure(0, weight=1)
        top_bar.columnconfigure(3, weight=1)
        self._btn_run_all = ttk.Button(
            top_bar,
            text="一键全部",
            style="Primary.TButton",
            command=self._run_all_clicked,
        )
        self._btn_run_all.grid(row=0, column=1, padx=5)
        self._btn_stop = ttk.Button(
            top_bar,
            text="停止",
            style="Primary.TButton",
            command=self._stop_clicked,
            state=tk.DISABLED,
        )
        self._btn_stop.grid(row=0, column=2, padx=5)

        # 进度区：位于核心按钮与步骤页之间
        self._progress_frame = ttk.LabelFrame(left, text="进度", padding=2)
        self._progress_frame.grid(
            row=1, column=0, sticky=tk.EW, padx=(12, 12), pady=(0, 6)
        )
        self._progress_frame.columnconfigure(0, weight=1)
        self._progress_bar = ttk.Progressbar(
            self._progress_frame,
            variable=self._progress_var,
            maximum=100,
            mode="determinate",
        )
        self._progress_bar.grid(row=0, column=0, sticky=tk.EW, pady=(0, 4))
        self._status_label = ttk.Label(self._progress_frame, text="就绪")
        self._status_label.grid(row=1, column=0, sticky=tk.W)

        # 三个步骤的 Notebook
        notebook = ttk.Notebook(left)
        # Notebook 区块比进度/明细约宽 5%（左右边距收窄为 4）
        notebook.grid(row=2, column=0, sticky=tk.NSEW, padx=(6, 6))
        self._notebook = notebook
        self._stage_labels = {}
        self._batch_combos = {}
        self._refresh_buttons = []
        self._build_download_tab(notebook)
        self._build_batch_tab(notebook, "mineru")
        self._build_batch_tab(notebook, "structure")
        self._mineru_combo = self._batch_combos["mineru"]
        self._struct_combo = self._batch_combos["structure"]

        # 打开输出目录：分步执行区块下方
        open_frame = ttk.Frame(left, padding=(8, 0))
        open_frame.grid(row=3, column=0, sticky=tk.W, padx=(12, 12), pady=(6, 6))
        self._btn_open = ttk.Button(
            open_frame, text="打开输出目录", command=self._open_output_dir
        )
        self._btn_open.pack(side=tk.LEFT)

        # 结果明细表
        table_frame = ttk.LabelFrame(left, text="结果明细", padding=6)
        table_frame.grid(row=4, column=0, sticky=tk.NSEW, padx=(12, 12))
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        columns = ("name", "category", "status", "reason")
        self._tree = ttk.Treeview(
            table_frame, columns=columns, show="headings", height=5
        )
        for col, title, width in (
            ("name", "名称", 200),
            ("category", "类别", 70),
            ("status", "状态", 60),
            ("reason", "失败原因", 200),
        ):
            self._tree.heading(col, text=title)
            self._tree.column(col, width=width, anchor=tk.W)
        self._tree.tag_configure("ok", foreground="#1e8e3e")
        self._tree.tag_configure("err", foreground="#d93025")
        self._tree.tag_configure("skip", foreground="#b06000")
        table_scroll_y = ttk.Scrollbar(
            table_frame, orient=tk.VERTICAL, command=self._tree.yview
        )
        table_scroll_x = ttk.Scrollbar(
            table_frame, orient=tk.HORIZONTAL, command=self._tree.xview
        )
        self._tree.configure(
            yscrollcommand=table_scroll_y.set, xscrollcommand=table_scroll_x.set
        )
        self._tree.grid(row=0, column=0, sticky=tk.NSEW)
        table_scroll_y.grid(row=0, column=1, sticky=tk.NS)
        table_scroll_x.grid(row=1, column=0, sticky=tk.EW)

        # 日志区（右侧，高度、宽度与左侧整体内容一致）
        log_frame = ttk.LabelFrame(right, text="日志", padding=6)
        log_frame.pack(fill=tk.BOTH, expand=True)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(1, weight=1)
        log_toolbar = ttk.Frame(log_frame)
        log_toolbar.grid(row=0, column=0, sticky=tk.W, pady=(0, 4))
        ttk.Label(log_toolbar, text="日志等级：").pack(side=tk.LEFT)
        ttk.Radiobutton(
            log_toolbar,
            text="Info",
            variable=self._log_level,
            value=LEVEL_LOW,
            command=self._render_log,
        ).pack(side=tk.LEFT, padx=4)
        ttk.Radiobutton(
            log_toolbar,
            text="Debug",
            variable=self._log_level,
            value=LEVEL_HIGH,
            command=self._render_log,
        ).pack(side=tk.LEFT, padx=4)
        self._log_text = scrolledtext.ScrolledText(
            log_frame, height=12, state=tk.DISABLED, wrap="none"
        )
        self._log_text.grid(row=1, column=0, sticky=tk.NSEW)

    def _make_wrapping_label(self, parent, text, **pack_options):
        """创建随容器宽度自动换行的标签。

        参数 parent：父容器；text：标签文本；pack_options：pack 布局参数。
        """
        label = ttk.Label(parent, text=text, justify=tk.LEFT)
        label.pack(fill=tk.X, **pack_options)

        def _update_wrap(_event=None):
            width = label.winfo_width()
            if width > 0:
                label.configure(wraplength=width)

        label.bind("<Configure>", _update_wrap)
        return label

    def _load_sheet_names(self):
        """从项目根目录的爬虫list.xlsx 读取爬虫列表工作表名并刷新下拉框（自动排除说明页）。"""
        names = []
        try:
            import openpyxl

            workbook = openpyxl.load_workbook(
                CRAWLER_WORKBOOK, read_only=True
            )
            required_columns = {"基金", "大类", "运行方式"}
            for sheet_name in workbook.sheetnames:
                try:
                    rows = workbook[sheet_name].iter_rows(values_only=True)
                    header = next(rows, None) or ()
                    header_values = {str(value).strip() for value in header}
                except Exception:
                    continue
                if required_columns.issubset(header_values):
                    names.append(sheet_name)
            workbook.close()
        except Exception:
            names = []
        values = ["所有"] + names
        self._sheet_combo["values"] = values
        if self._sheet_var.get() not in values:
            self._sheet_var.set("所有")

    def _refresh_sheet_names(self):
        """手动刷新“工作表”下拉框（重新读取项目根目录的爬虫list.xlsx）。"""
        self._load_sheet_names()
        self._append_log(
            f"已刷新工作表下拉：{'、'.join(self._sheet_combo['values'])}",
            kind="status",
        )

    def _build_download_tab(self, notebook):
        """构建"下载"步骤页。

        参数 notebook：父级 Notebook 组件。
        """
        tab = ttk.Frame(notebook, padding=0)
        notebook.add(tab, text="1. 下载")

        state_bar = ttk.Frame(tab)
        state_bar.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(state_bar, text="状态：").pack(side=tk.LEFT)
        self._stage_labels["download"] = ttk.Label(
            state_bar, text="未开始", foreground="#5f6368"
        )
        self._stage_labels["download"].pack(side=tk.LEFT)

        self._make_wrapping_label(
            tab,
            text="按 爬虫list.xlsx 下载各基金 PDF，自动新建 data/<时间戳> 批次文件夹。",
            anchor=tk.W,
            pady=(0, 4),
        )
        self._make_wrapping_label(
            tab,
            text="批次下拉框在“解析 / 结构化”步骤中用于选择要处理的批次，默认选最新文件夹。",
            anchor=tk.W,
            pady=(0, 12),
        )

        # 工作表选择
        sheet_row = ttk.Frame(tab)
        sheet_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(sheet_row, text="工作表：").pack(side=tk.LEFT)
        self._sheet_combo = ttk.Combobox(
            sheet_row, textvariable=self._sheet_var, state="readonly", width=20
        )
        self._sheet_combo.pack(side=tk.LEFT, padx=4)
        self._chk_backfill = ttk.Checkbutton(
            sheet_row, text="爬虫补全", variable=self._backfill_var
        )
        self._chk_backfill.pack(side=tk.LEFT, padx=(12, 0))
        sheet_refresh = ttk.Button(
            sheet_row, text="刷新", command=self._refresh_sheet_names
        )
        sheet_refresh.pack(side=tk.LEFT, padx=(4, 0))
        self._refresh_buttons.append(sheet_refresh)
        self._make_wrapping_label(
            tab,
            text=(
                "勾选“爬虫补全”后：先下载已有月报链接/运行方式的基金，"
                "再摸查并调用各公司补全脚本回填空缺行，最后把补全行并入同一批次补跑。"
            ),
            anchor=tk.W,
            pady=(2, 0),
        )
        self._make_wrapping_label(
            tab,
            text="“所有”=处理全部爬虫列表工作表（自动跳过说明页）。",
            anchor=tk.W,
            pady=(0, 8),
        )

        self._btn_download = ttk.Button(
            tab,
            text="开始下载",
            command=lambda: self._start_stage("download"),
        )
        self._btn_download.pack(anchor=tk.W)
        return tab

    def _build_batch_tab(self, notebook, stage_key):
        """构建"解析 / 结构化"步骤页（带批次下拉框）。

        参数 notebook：父级 Notebook 组件；stage_key：mineru 或 structure。
        """
        tab = ttk.Frame(notebook, padding=0)
        index = 2 if stage_key == "mineru" else 3
        notebook.add(tab, text=f"{index}. {STAGE_NAMES[stage_key]}")

        state_bar = ttk.Frame(tab)
        state_bar.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(state_bar, text="状态：").pack(side=tk.LEFT)
        self._stage_labels[stage_key] = ttk.Label(
            state_bar, text="未开始", foreground="#5f6368"
        )
        self._stage_labels[stage_key].pack(side=tk.LEFT)

        row = ttk.Frame(tab)
        row.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(row, text="批次：").pack(side=tk.LEFT)
        combo = ttk.Combobox(
            row, textvariable=self._batch_var, state="readonly", width=16
        )
        combo.pack(side=tk.LEFT, padx=4)
        combo.bind("<<ComboboxSelected>>", lambda _event: self._update_batch_info())
        self._batch_combos[stage_key] = combo
        refresh_button = ttk.Button(row, text="刷新", command=self._refresh_batches)
        refresh_button.pack(side=tk.LEFT)
        self._refresh_buttons.append(refresh_button)

        ttk.Label(tab, textvariable=self._batch_info_var, foreground="#5f6368").pack(
            anchor=tk.W, pady=(0, 8)
        )
        hint = (
            "把批次内 PDF 转为 Markdown 缓存（MinerU），供下一步使用。"
            if stage_key == "mineru"
            else "读取已有 Markdown 缓存，按实际类别生成 Excel"
            "（固收/权益/中国精选/混合；货币等暂不支持类别会跳过并提示）。"
        )
        ttk.Label(tab, text=hint).pack(anchor=tk.W, pady=(0, 12))

        button = ttk.Button(
            tab,
            text=f"开始{STAGE_NAMES[stage_key]}",
            command=lambda: self._start_stage(stage_key),
        )
        button.pack(anchor=tk.W)
        if stage_key == "mineru":
            self._btn_mineru = button
        else:
            self._btn_structure = button
        return tab

    def _configure_log_tags(self):
        """配置日志文本的配色标签。"""
        for tag, color in (
            ("status", "#1a73e8"),
            ("ok", "#1e8e3e"),
            ("warn", "#b06000"),
            ("error", "#d93025"),
            ("retry", "#b06000"),
            ("info", "#5f6368"),
            ("raw", "#80868b"),
            ("report", "#37474f"),
        ):
            self._log_text.tag_configure(tag, foreground=color)

    def _fit_window_height(self):
        """按左侧功能区内容自然高度设置默认窗口高度（取各步骤页最大高度，避免大片空白）。"""
        notebook = self._notebook
        current = notebook.select()
        heights = []
        try:
            for tab_id in notebook.tabs():
                notebook.select(tab_id)
                self.root.update_idletasks()
                heights.append(self.root.winfo_reqheight())
        finally:
            notebook.select(current)
            self.root.update_idletasks()
        natural = max(heights) if heights else self.root.winfo_reqheight()
        # 整体宽度在自然宽度基础上减少 40%（保持左右等宽）
        width = int(round(self.root.winfo_reqwidth() * 0.75))
        self.root.geometry(f"{width}x{natural}")

    # ---------- 批次选择 ----------

    def _selected_batch(self):
        """返回当前选中的批次路径；未选择时返回 None。"""
        name = self._batch_var.get().strip()
        return self._batches.get(name)

    def _refresh_batches(self, prefer=None):
        """刷新批次下拉列表，默认选择最新批次。

        参数 prefer：可选，优先选中的批次名（下载完成后传新 run_id）。
        """
        self._batches = {batch.name: batch for batch in available_batch_roots(DATA_DIR)}
        names = list(self._batches)
        for combo in getattr(self, "_batch_combos", {}).values():
            combo["values"] = names
        if prefer and prefer in self._batches:
            self._batch_var.set(prefer)
        elif names:
            self._batch_var.set(names[0])
        else:
            self._batch_var.set("")
        self._update_batch_info()

    def _update_batch_info(self):
        """更新当前选中批次的 PDF 数量说明。"""
        batch = self._selected_batch()
        if batch is None:
            self._batch_info_var.set(
                "暂无可用批次（data 下需包含含 PDF 的类别文件夹）"
            )
            return
        categories = pdf_categories_with_files(batch)
        if not categories:
            self._batch_info_var.set("该批次暂无可识别的 PDF 类别")
            return
        counts = [
            len(list(pdf_category_dir(batch, category).glob("*.pdf")))
            for category in categories
        ]
        parts = "｜".join(f"{c} {n}" for c, n in zip(categories, counts))
        self._batch_info_var.set(f"{parts}｜合计 {sum(counts)}")

    # ---------- 日志 ----------

    def _tag_for(self, text):
        """根据文本前缀返回日志配色标签。

        参数 text：日志文本。
        """
        if text.startswith("[+]"):
            return "ok"
        if text.startswith("[!]"):
            return "warn"
        if (
            text.startswith("[FAILED]")
            or text.startswith("[ERROR]")
            or "Traceback" in text
        ):
            return "error"
        if text.startswith("[RETRY]") or "重试" in text:
            return "retry"
        if text.startswith("[*]"):
            return "info"
        return "raw"

    def _insert_log_item(self, kind, text):
        """把一条日志写入日志文本框。

        参数 kind：status/summary/detail/report；text：日志文本。
        """
        if kind == "status":
            tag = "status"
        elif kind == "report":
            tag = "report"
        else:
            tag = self._tag_for(text)
        self._log_text.config(state=tk.NORMAL)
        self._log_text.insert(tk.END, text + "\n", tag)
        self._log_text.config(state=tk.DISABLED)
        self._log_text.see(tk.END)

    def _append_log(self, text, kind="detail"):
        """追加一条日志到缓冲；低等级只渲染状态/汇总行，高等级全部渲染。

        参数 text：日志文本；kind：status/summary/detail/report。
        """
        text = str(text).rstrip("\n")
        if not text:
            return
        if kind == "status":
            text = f"[{datetime.now():%H:%M:%S}] {text}"
        self._log_buffer.append((kind, text))
        if self._log_level.get() == LEVEL_HIGH or kind in ("status", "summary"):
            self._insert_log_item(kind, text)

    def _render_log(self):
        """按当前日志等级重绘整个日志区。"""
        self._log_text.config(state=tk.NORMAL)
        self._log_text.delete("1.0", tk.END)
        high = self._log_level.get() == LEVEL_HIGH
        for kind, text in self._log_buffer:
            if high or kind in ("status", "summary"):
                self._insert_log_item(kind, text)
        self._log_text.config(state=tk.DISABLED)

    # ---------- 进度与状态 ----------

    def _set_stage_state(self, stage_key, state):
        """更新某一步骤的状态标签。

        参数 stage_key：download/mineru/structure；state：idle/running/success/failed/stopped。
        """
        label = self._stage_labels[stage_key]
        mapping = {
            "idle": ("未开始", "#5f6368"),
            "running": ("进行中", "#1a73e8"),
            "success": ("成功", "#1e8e3e"),
            "failed": ("失败", "#d93025"),
            "stopped": ("已停止", "#5f6368"),
        }
        text, color = mapping.get(state, ("未开始", "#5f6368"))
        label.config(text=text, foreground=color)

    def _set_progress(
        self,
        stage_key,
        completed,
        total,
        succeeded,
        failed,
        note="",
        segment_range=None,
        skipped=None,
        backfilled=None,
    ):
        """更新进度条与状态文字。

        参数 stage_key：阶段名；completed/total/succeeded/failed：进度计数；
        参数 note：附加说明（正在处理的对象或子阶段）；
        参数 segment_range：进度百分比区间 (start, end)，用于爬虫补全三段进度；
        参数 skipped：跳过数量（仅下载/补全流程使用）；
        参数 backfilled：补全成功行数（仅下载/补全流程使用）。
        """
        if total > 0:
            if segment_range:
                start, end = segment_range
                percent = start + (completed / total) * (end - start)
            else:
                percent = completed / total * 100
            self._progress_var.set(min(100.0, percent))
        text = f"{STAGE_NAMES[stage_key]}：{completed}/{total}"
        if backfilled:
            text += f"｜回填 {backfilled}"
        text += f"｜成功 {succeeded}"
        if skipped:
            text += f"｜跳过 {skipped}"
        text += f"｜失败 {failed}"
        if note:
            text += f"｜{note}"
        self._status_label.config(text=text)

    def _set_progress_title(self, step, total):
        """设置进度区块标题，形如“进度（2/3）”。

        参数 step：当前步骤序号；total：总步骤数。
        """
        self._progress_frame.config(text=f"进度（{step}/{total}）")

    def _reset_progress_title(self):
        """运行结束后把进度区块标题恢复为“进度”。"""
        self._progress_frame.config(text="进度")

    def _set_ui_busy(self, busy):
        """根据运行状态启用/禁用操作按钮。

        参数 busy：True 表示有阶段在运行。
        """
        if not busy:
            self._reset_progress_title()
        state = tk.DISABLED if busy else tk.NORMAL
        for button in (
            self._btn_download,
            self._btn_mineru,
            self._btn_structure,
            self._btn_run_all,
        ):
            button.config(state=state)
        for button in getattr(self, "_refresh_buttons", []):
            button.config(state=state)
        for combo in getattr(self, "_batch_combos", {}).values():
            combo.config(state="disabled" if busy else "readonly")
        if hasattr(self, "_sheet_combo"):
            self._sheet_combo.config(state="disabled" if busy else "readonly")
        if hasattr(self, "_chk_backfill"):
            self._chk_backfill.config(state=tk.DISABLED if busy else tk.NORMAL)
        self._btn_stop.config(state=tk.NORMAL if busy else tk.DISABLED)

    def _populate_table(self, rows):
        """刷新结果明细表。

        参数 rows：表格行列表，每行含 name/category/status/reason。
        """
        for item in self._tree.get_children():
            self._tree.delete(item)
        for row in rows:
            if row.get("status") == "成功":
                tag = "ok"
            elif row.get("status") == "跳过":
                tag = "skip"
            else:
                tag = "err"
            self._tree.insert(
                "",
                tk.END,
                values=(
                    row.get("name", ""),
                    row.get("category", ""),
                    row.get("status", ""),
                    row.get("reason", ""),
                ),
                tags=(tag,),
            )

    # ---------- 阶段执行 ----------

    def _build_command(self, stage_key, retry=False):
        """构造阶段子进程命令。

        参数 stage_key：download/mineru/structure；retry：是否为失败重试。
        """
        if stage_key == "download":
            crawler_list = self._crawler_list_var.get().strip()
            sheet_value = self._sheet_var.get().strip() or "所有"
            command = [
                sys.executable,
                str(RUN_WITH_CONFIG),
                DOWNLOAD_SCRIPT,
                "--no-interactive-retry",
                "--crawler-list",
                crawler_list,
                "--sheet",
                sheet_value,
            ]
            if self._backfill_var.get():
                command.append("--backfill")
            if self._retry_run_id:
                command += ["--run-id", self._retry_run_id]
            for name in self._retry_only:
                command += ["--only", name]
            return command
        batch = self._active_batch or self._selected_batch()
        if batch is None:
            raise ValueError("未选择批次文件夹")
        script = MINERU_SCRIPT if stage_key == "mineru" else STRUCTURED_SCRIPT
        command = [
            sys.executable,
            str(RUN_WITH_CONFIG),
            script,
            str(batch),
        ]
        if stage_key == "structure" and retry:
            command.append("--skip-existing")
        return command

    def _start_stage(self, stage_key, on_done=None, retry=False):
        """启动指定阶段（校验输入后开后台线程）。

        参数 stage_key：download/mineru/structure；on_done：阶段结束回调（GUI 线程）；
        参数 retry：是否为失败重试。
        """
        if self._running_stage is not None:
            messagebox.showwarning("提示", "已有任务在运行，请先停止。")
            return
        if stage_key == "download":
            crawler_list = self._crawler_list_var.get().strip()
            if not crawler_list or not Path(crawler_list).is_file():
                messagebox.showerror(
                    "错误", f"爬虫list.xlsx 不存在：{crawler_list or '（未选择）'}"
                )
                return
        if stage_key in ("mineru", "structure"):
            batch = self._selected_batch()
            if batch is None or not batch.is_dir():
                messagebox.showerror("错误", "请先在下拉框中选择有效的批次文件夹。")
                return
            if stage_key == "mineru":
                pdf_categories = pdf_categories_with_files(batch)
                pdf_count = sum(
                    len(list(pdf_category_dir(batch, category).glob("*.pdf")))
                    for category in pdf_categories
                )
                if pdf_count == 0:
                    messagebox.showerror("错误", f"该批次没有可解析的 PDF：{batch}")
                    return
            else:
                if not (batch / "markdown").is_dir():
                    messagebox.showerror(
                        "错误",
                        f"该批次缺少 markdown 缓存，请先执行“解析”：\n{batch}",
                    )
                    return
        if not retry and stage_key == "download":
            self._retry_run_id = None
            self._retry_only = []
            self._retry_previous_items = []
            self._backfill_segment = 1

        self._active_batch = (
            self._selected_batch() if stage_key in ("mineru", "structure") else None
        )
        # 新阶段开始时清空“结果明细”，避免上一批次（或上次停止前）的行残留
        self._populate_table([])
        self._pending_on_done = on_done
        self._running_stage = stage_key
        self._set_stage_state(stage_key, "running")
        self._set_ui_busy(True)
        if self._all_mode:
            step = STAGE_ORDER.index(stage_key) + 1
            total = len(STAGE_ORDER)
        else:
            step = total = 1
        self._set_progress_title(step, total)
        self._stop_event.clear()
        self._progress_var.set(0)
        self._status_label.config(text=f"{STAGE_NAMES[stage_key]} 启动中...")
        thread = threading.Thread(
            target=self._run_stage_worker, args=(stage_key, retry), daemon=True
        )
        thread.start()

    def _run_stage_worker(self, stage_key, retry):
        """后台线程：运行阶段子进程并读取控制台输出。

        参数 stage_key：download/mineru/structure；retry：是否为失败重试。
        """
        try:
            command = self._build_command(stage_key, retry)
        except Exception as exc:
            self.root.after(0, lambda: self._on_stage_launch_error(stage_key, str(exc)))
            return

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        env["FACTSHEET_PROGRESS_PIPE"] = "1"
        # 项目便携包 .runtime/ms-playwright 只内置 Windows 版 Chromium（build 1228）；
        # macOS 开发环境应使用本机 playwright install 安装的浏览器缓存（build 1234），
        # 因此只在 Windows 上把浏览器路径指到便携包。
        if os.name == "nt":
            runtime_browsers = Path(
                os.environ.get("FACTSHEET_RUNTIME_ROOT") or STATE_ROOT / "runtime"
            ) / "ms-playwright"
            if runtime_browsers.is_dir():
                env.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(runtime_browsers))

        popen_kwargs = {}
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True

        self.root.after(
            0,
            lambda: self._append_log(
                "开始执行：" + " ".join(str(part) for part in command), kind="status"
            ),
        )
        try:
            proc = subprocess.Popen(
                command,
                cwd=str(PROJECT_ROOT),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
                **popen_kwargs,
            )
        except Exception as exc:
            self.root.after(
                0,
                lambda: self._on_stage_launch_error(
                    stage_key, f"无法启动子进程：{exc}"
                ),
            )
            return
        self._proc = proc
        if self._stop_event.is_set():
            self._terminate_proc(proc)

        run_id = None
        pending_text = ""
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        try:
            while True:
                chunk = proc.stdout.read(4096)
                if not chunk:
                    break
                pending_text += decoder.decode(chunk)
                segments = re.split(r"[\r\n]+", pending_text)
                pending_text = segments.pop()
                for segment in segments:
                    segment = segment.strip()
                    if not segment:
                        continue
                    match = RE_RUN_ID.search(segment)
                    if match:
                        run_id = match.group(1)
                    self.root.after(
                        0,
                        lambda text=segment: self._on_console_line(stage_key, text),
                    )
        finally:
            pending_text += decoder.decode(b"", final=True)
            if pending_text.strip():
                self.root.after(
                    0,
                    lambda text=pending_text.strip(): self._on_console_line(stage_key, text),
                )
            proc.wait()
            cancelled = self._stop_event.is_set()
            self._proc = None
            result = self._collect_stage_result(
                stage_key, run_id, cancelled, proc.returncode
            )
            self.root.after(0, lambda: self._on_stage_process_done(stage_key, result))

    def _terminate_proc(self, proc):
        """结束子进程及其进程树。

        参数 proc：subprocess.Popen 实例。
        """
        if proc is None or proc.poll() is not None:
            return
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                    check=False,
                )
            except Exception:
                pass
            return
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass

    def _on_console_line(self, stage_key, text):
        """处理阶段子进程输出的一行（GUI 线程）。

        参数 stage_key：阶段名；text：控制台文本。
        """
        kind = "summary" if SUMMARY_RE.search(text) else "detail"
        self._append_log(text, kind)

        if stage_key == "download":
            backfill_enabled = bool(self._backfill_var.get())
            if backfill_enabled:
                if "阶段1：" in text:
                    self._backfill_segment = 1
                elif "摸查：" in text or "补全：" in text or "补全进度" in text:
                    self._backfill_segment = 2
                elif "进入阶段2" in text or "阶段2" in text:
                    self._backfill_segment = 3
                if "跳过阶段2补跑" in text:
                    # 没有可补跑的补全行：进度条直接补满到 100%
                    self._backfill_segment = 3
                    self._progress_var.set(100.0)
                    self._status_label.config(
                        text="下载：无补全结果，跳过阶段2，进度已完成"
                    )
            segment_range = None
            if backfill_enabled:
                if self._backfill_segment == 1:
                    segment_range = (0.0, 100.0 / 3)
                elif self._backfill_segment == 2:
                    segment_range = (100.0 / 3, 200.0 / 3)
                else:
                    segment_range = (200.0 / 3, 100.0)
            backfill_match = RE_BACKFILL_PROGRESS.search(text)
            if backfill_match:
                self._set_progress(
                    stage_key,
                    *map(int, backfill_match.groups()),
                    note="爬虫补全",
                    segment_range=segment_range,
                )
                return
            summary_match = RE_DOWNLOAD_PROGRESS_SUMMARY.search(text)
            if summary_match:
                (
                    completed,
                    total,
                    backfilled,
                    succeeded,
                    skipped,
                    failed,
                ) = summary_match.groups()
                self._set_progress(
                    stage_key,
                    int(completed),
                    int(total),
                    int(succeeded),
                    int(failed),
                    skipped=int(skipped),
                    backfilled=int(backfilled) if backfilled else 0,
                    segment_range=(200.0 / 3, 100.0) if backfill_enabled else None,
                )
                self._progress_var.set(100.0)
                return
            match = RE_DOWNLOAD_PROGRESS.search(text)
            if match:
                self._set_progress(
                    stage_key,
                    *map(int, match.groups()),
                    segment_range=segment_range,
                )
        elif stage_key == "mineru":
            match = RE_MINERU_PROGRESS.search(text)
            if match:
                note = ""
                note_match = re.search(r"正在：(.+)$", text)
                if note_match:
                    note = note_match.group(1).strip()
                self._set_progress(stage_key, *map(int, match.groups()), note=note)
        else:
            match = RE_STRUCT_PROGRESS.search(text)
            if match:
                note = ""
                note_match = RE_STAGE_NOTE.search(text)
                if note_match:
                    note = note_match.group(1).strip()
                self._set_progress(stage_key, *map(int, match.groups()), note=note)

    # ---------- 结果收集 ----------

    def _collect_stage_result(self, stage_key, run_id, cancelled, exit_code):
        """读取阶段报告并整理结果字典（后台线程）。

        参数 stage_key：阶段名；run_id：下载阶段的批次 id；cancelled：是否被停止；
        参数 exit_code：子进程退出码。
        """
        base = {
            "stage": stage_key,
            "run_id": run_id,
            "cancelled": cancelled,
            "exit_code": exit_code,
            "ok": False,
            "rows": [],
            "failures": [],
            "summary": {},
            "report_blocks": [],
            "items": [],
        }
        if stage_key == "download":
            collected = self._collect_download_result(base)
            if cancelled:
                # 停止时也要读已落盘的报告，把已补全/下载/未处理行显示到结果明细
                return collected
            return collected
        if cancelled:
            return base
        batch = self._active_batch or self._selected_batch()
        if stage_key == "mineru":
            return self._collect_mineru_result(base, batch)
        return self._collect_structure_result(base, batch)

    def _find_download_report(self, run_id, allow_fallback=True):
        """定位并读取下载报告。

        参数 run_id：下载批次 id；参数 allow_fallback：run_id 为空时是否回退到最新报告。
        返回 (报告路径, 数据 dict 或 None)。指定 run_id 时只读该批次报告，
        避免被停止的批次（尚未写出报告）显示成上一次批次的结果。
        """
        if run_id:
            candidate = DATA_DIR / run_id / "reports" / f"download_run_report_{run_id}.json"
            if candidate.is_file():
                try:
                    return candidate, json.loads(candidate.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    return candidate, None
            return None, None
        if not allow_fallback:
            return None, None
        candidates = sorted(
            DATA_DIR.glob("*/reports/download_run_report_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        path = candidates[0] if candidates else None
        if path is None or not path.is_file():
            return None, None
        try:
            return path, json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return path, None

    def _collect_download_result(self, base):
        """整理下载阶段结果。

        参数 base：结果字典骨架。
        """
        # 被停止的批次可能还没写出报告：不要回退到上一次批次的报告，
        # 否则结果明细/日志会“回吐”上一轮（例如东亚联丰）的内容。
        report_path, report = self._find_download_report(
            base.get("run_id"), allow_fallback=not base.get("cancelled")
        )
        self._download_report_path = report_path
        if not base.get("run_id") and report_path is not None:
            base["run_id"] = report_path.parent.parent.name
        items = list((report or {}).get("results") or [])

        # 重试后把旧报告与新报告按行号合并，并写回合并后的报告
        if self._retry_previous_items and self._retry_run_id == base.get("run_id"):
            merged = {item.get("row_number"): item for item in self._retry_previous_items}
            for item in items:
                merged[item.get("row_number")] = item
            items = [merged[key] for key in sorted(merged)]
            if report_path is not None:
                try:
                    payload = dict(report or {})
                    merged_summary = dict((report or {}).get("summary") or {})
                    merged_summary.update(summarize_download_items(items))
                    payload["summary"] = merged_summary
                    payload["results"] = items
                    report_path.write_text(
                        json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                except OSError:
                    pass

        summary = summarize_download_items(items)
        rows = download_rows_from_items(items)
        failures = [row for row in rows if row.get("status") == "失败"]
        if report is None and not items and not base.get("cancelled"):
            failures = [
                {
                    "name": "阶段",
                    "category": "",
                    "status": "失败",
                    "reason": "未生成下载报告，请切换高等级日志查看原因",
                }
            ]
        exit_code = base.get("exit_code")
        hard_failures = [
            row for row in failures if row.get("name") != "下载/补全流程"
        ]
        has_download = (
            summary.get("downloaded", 0) + summary.get("existing", 0)
        ) >= 1
        if (
            not base.get("cancelled")
            and exit_code not in (None, 0)
            and not hard_failures
            and not has_download
        ):
            failure_row = {
                "name": "下载/补全流程",
                "category": "",
                "status": "失败",
                "reason": (
                    f"子进程退出码 {exit_code}，且没有基金下载成功，"
                    "请查看上方日志"
                ),
            }
            failures.append(failure_row)
            rows.append(failure_row)
        elif (
            not base.get("cancelled")
            and not has_download
            and not hard_failures
            and not failures
        ):
            failure_row = {
                "name": "下载/补全流程",
                "category": "",
                "status": "失败",
                "reason": "没有任何基金下载成功（可能全部未收录或补全失败）",
            }
            failures.append(failure_row)
            rows.append(failure_row)
        base.update(
            {
                # 只要至少一支基金下载成功且没有真实失败项，阶段即算成功；
                # 补全中途失败等流程级错误在日志中展示，不覆盖“有成功”的结果。
                "ok": has_download and len(hard_failures) == 0,
                "rows": rows,
                "failures": failures,
                "summary": summary,
                "items": items,
            }
        )
        if report_path is not None and report is not None:
            base["report_blocks"].append(
                (
                    f"下载报告 JSON：{report_path}",
                    json.dumps(report, ensure_ascii=False, indent=2),
                )
            )
        return base

    def _collect_mineru_result(self, base, batch):
        """整理解析（MinerU）阶段结果。

        参数 base：结果字典骨架；batch：批次路径。
        """
        candidates = sorted(
            (batch / "reports").glob("mineru_run_report_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        report_path = candidates[0] if candidates else None
        report = None
        if report_path is not None:
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                report = None
        rows = []
        failures = []
        for item in (report or {}).get("results") or []:
            ok = item.get("status") == "ok"
            row = {
                "name": Path(item.get("pdf", "")).name,
                "category": item.get("category", ""),
                "status": "成功" if ok else "失败",
                "reason": item.get("error", "") if not ok else "",
            }
            rows.append(row)
            if not ok:
                failures.append(row)
        if report is None:
            failures = [
                {
                    "name": "阶段",
                    "category": "",
                    "status": "失败",
                    "reason": "未生成 MinerU 报告，请切换高等级日志查看原因",
                }
            ]
        summary = {
            "total": (report or {}).get("total", len(rows)),
            "failed": (report or {}).get("failed", len(failures)),
            "batch": str(batch),
        }
        base.update(
            {
                "ok": base.get("exit_code") == 0 and len(failures) == 0,
                "rows": rows,
                "failures": failures,
                "summary": summary,
            }
        )
        if report is not None:
            base["report_blocks"].append(
                (
                    f"MinerU 报告 JSON：{report_path}",
                    json.dumps(report, ensure_ascii=False, indent=2),
                )
            )
        return base

    def _collect_structure_result(self, base, batch):
        """整理结构化阶段结果。

        参数 base：结果字典骨架；batch：批次路径。
        """
        work_dir = batch / "structure_data" / "_work"
        candidates = sorted(
            work_dir.glob("structured_run_summary_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        summary_path = candidates[0] if candidates else None
        report = None
        if summary_path is not None:
            try:
                report = json.loads(summary_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                report = None
        rows = []
        failures = []
        for item in (report or {}).get("failures") or []:
            row = {
                "name": Path(item.get("pdf", "")).name,
                "category": "",
                "status": "失败",
                "reason": item.get("error", ""),
            }
            rows.append(row)
            failures.append(row)
        if report is None:
            failures = [
                {
                    "name": "阶段",
                    "category": "",
                    "status": "失败",
                    "reason": "未生成结构化统计文件，请切换高等级日志查看原因",
                }
            ]
        elif report.get("failed", 0) == 0:
            rows = [
                {
                    "name": f"全部成功（共 {report.get('total_funds', 0)} 只基金）",
                    "category": "",
                    "status": "成功",
                    "reason": "",
                }
            ]
        summary = {
            "total_funds": (report or {}).get("total_funds", 0),
            "succeeded": (report or {}).get("succeeded", 0),
            "failed": (report or {}).get("failed", 0),
            "batch": str(batch),
        }
        stage_failures = (report or {}).get("stage_failures") or []
        base.update(
            {
                "ok": (
                    base.get("exit_code") == 0
                    and len(failures) == 0
                    and not stage_failures
                ),
                "rows": rows,
                "failures": failures,
                "summary": summary,
            }
        )
        if report is not None:
            base["report_blocks"].append(
                (
                    f"结构化统计 JSON：{summary_path}",
                    json.dumps(report, ensure_ascii=False, indent=2),
                )
            )
            log_path = report.get("log_path")
            if log_path and Path(log_path).is_file():
                try:
                    log_text = Path(log_path).read_text(encoding="utf-8", errors="replace")
                    base["report_blocks"].append((f"结构化详细日志：{log_path}", log_text))
                except OSError:
                    pass
        return base

    # ---------- 阶段结束处理 ----------

    def _on_stage_process_done(self, stage_key, result):
        """阶段子进程结束后的统一回调（GUI 线程）。

        参数 stage_key：阶段名；result：结果字典。
        """
        self._proc = None
        self._running_stage = None
        if stage_key == "download" and result.get("run_id"):
            self._last_download_run_id = result.get("run_id")
        if result.get("cancelled"):
            self._populate_table(result.get("rows") or [])
            for title, text in result.get("report_blocks") or []:
                self._append_log(f"===== {title} =====\n{text}", kind="report")
            self._set_stage_state(stage_key, "stopped")
            self._all_queue = []
            self._all_mode = False
            self._retry_run_id = None
            self._retry_only = []
            self._retry_previous_items = []
            self._set_ui_busy(False)
            self._append_log(f"{STAGE_NAMES[stage_key]} 已停止", kind="status")
            return

        self._set_stage_state(stage_key, "success" if result.get("ok") else "failed")
        self._populate_table(result.get("rows") or [])
        for title, text in result.get("report_blocks") or []:
            self._append_log(f"===== {title} =====\n{text}", kind="report")
        if result.get("ok"):
            self._on_stage_ok(stage_key, result)
        else:
            failures = result.get("failures") or []
            self._append_log(
                f"{STAGE_NAMES[stage_key]}：{len(failures)} 个失败项", kind="status"
            )
            self._prompt_retry_flow(stage_key, result)

    def _on_stage_launch_error(self, stage_key, error):
        """阶段启动失败回调（GUI 线程）。

        参数 stage_key：阶段名；error：错误信息。
        """
        self._running_stage = None
        self._all_queue = []
        self._all_mode = False
        self._set_ui_busy(False)
        self._set_stage_state(stage_key, "failed")
        self._append_log(f"启动失败：{error}", kind="status")
        messagebox.showerror("错误", f"无法启动{STAGE_NAMES[stage_key]}：\n{error}")

    def _on_close(self):
        """关闭主窗口前停止正在运行的阶段子进程。"""
        if self._running_stage is not None:
            self._stop_event.set()
            if self._proc is not None:
                self._terminate_proc(self._proc)
        self.root.destroy()

    def _stage_summary_text(self, stage_key, result):
        """生成阶段完成摘要文本。

        参数 stage_key：阶段名；result：结果字典。
        """
        summary = result.get("summary") or {}
        if stage_key == "download":
            run_id = result.get("run_id") or ""
            skipped = summary.get("skipped", 0)
            backfilled = summary.get("backfilled", 0)
            parts = ""
            if backfilled:
                parts += f"回填 {backfilled}｜"
            parts += f"成功 {summary.get('ok', 0)}"
            if skipped:
                parts += f"｜跳过 {skipped}"
            parts += (
                f"｜失败 {summary.get('failed', 0)}｜共 {summary.get('total', 0)}"
            )
            return f"{parts}\n输出目录：{DATA_DIR / run_id}"
        if stage_key == "mineru":
            total = summary.get("total", 0)
            failed = summary.get("failed", 0)
            return (
                f"成功/复用 {total - failed}｜失败 {failed}｜共 {total}\n"
                f"批次：{summary.get('batch', '')}"
            )
        return (
            f"成功 {summary.get('succeeded', 0)}｜失败 {summary.get('failed', 0)}｜"
            f"共 {summary.get('total_funds', 0)}\n批次：{summary.get('batch', '')}"
        )

    def _on_stage_ok(self, stage_key, result):
        """阶段全部成功后的推进逻辑（GUI 线程）。

        参数 stage_key：阶段名；result：结果字典。
        """
        self._append_log(f"{STAGE_NAMES[stage_key]}：全部成功", kind="status")
        if stage_key == "download" and result.get("run_id"):
            self._refresh_batches(prefer=result.get("run_id"))
        self._set_ui_busy(False)
        callback = self._pending_on_done
        self._pending_on_done = None
        if callback:
            callback(stage_key, result)
        else:
            messagebox.showinfo(
                "完成",
                f"{STAGE_NAMES[stage_key]}完成！\n{self._stage_summary_text(stage_key, result)}",
            )

    def _prompt_retry_flow(self, stage_key, result):
        """阶段失败后询问重试/继续/停止，并推进流程（GUI 线程）。

        参数 stage_key：阶段名；result：结果字典。
        """
        failures = result.get("failures") or []
        while True:
            next_label = "继续下一步" if self._pending_on_done else "忽略并继续"
            lines = [
                f"[{item.get('category') or ''}] {item.get('name') or ''}："
                f"{item.get('reason') or '未知原因'}"
                for item in failures[:50]
            ]
            dialog = RetryDialog(
                self.root,
                STAGE_NAMES[stage_key],
                lines,
                next_label=next_label,
                total_failures=len(failures),
            )
            self.root.wait_window(dialog)
            decision = dialog.result or "stop"

            if decision == "retry":
                if stage_key == "download":
                    report_path = self._download_report_path
                    if report_path is not None and report_path.is_file():
                        backup = report_path.with_name(f"{report_path.stem}_pre_retry.json")
                        if not backup.exists():
                            try:
                                shutil.copy2(report_path, backup)
                            except OSError:
                                pass
                    self._retry_run_id = result.get("run_id") or self._retry_run_id
                    self._retry_only = [
                        item.get("name") for item in failures if item.get("name")
                    ]
                    self._retry_previous_items = result.get("items") or []
                self._append_log(
                    f"用户选择重试失败项（{len(failures)} 个）", kind="status"
                )
                self._start_stage(stage_key, on_done=self._pending_on_done, retry=True)
                return
            if decision == "next":
                self._append_log("用户选择继续下一步", kind="status")
                self._advance_or_finish(stage_key, result)
                return
            self._append_log("用户选择停止", kind="status")
            self._all_queue = []
            self._all_mode = False
            self._retry_run_id = None
            self._retry_only = []
            self._retry_previous_items = []
            self._set_ui_busy(False)
            return

    def _advance_or_finish(self, stage_key, result):
        """阶段结束（成功或用户忽略失败）后的统一推进。

        参数 stage_key：阶段名；result：结果字典。
        """
        self._retry_run_id = None
        self._retry_only = []
        self._retry_previous_items = []
        callback = self._pending_on_done
        self._pending_on_done = None
        self._set_ui_busy(False)
        if callback:
            callback(stage_key, result)

    def _on_all_stage_done(self, stage_key, result):
        """一键全部：当前阶段结束后启动下一阶段（GUI 线程）。

        参数 stage_key：阶段名；result：结果字典。
        """
        if stage_key == "download" and result.get("run_id"):
            if self._batch_var.get() != result.get("run_id"):
                self._append_log(
                    "警告：新批次未出现在批次列表中，一键流程中止，"
                    "请在“解析 / 结构化”页手动选择批次。",
                    kind="status",
                )
                self._all_queue = []
                self._set_ui_busy(False)
                messagebox.showwarning(
                    "批次未就绪",
                    f"下载完成，但新批次 {result.get('run_id')} 未出现在批次下拉框中"
                    "（批次内可能没有 PDF 或目录未就绪）。\n请在“解析 / 结构化”页手动选择批次。",
                )
                return
        if self._all_queue:
            self._all_queue.pop(0)
        if not self._all_queue:
            self._all_mode = False
            self._append_log("一键流程全部结束", kind="status")
            messagebox.showinfo(
                "全部完成", "下载 / 解析 / 结构化 流程已结束，请查看日志与输出目录。"
            )
            return
        next_stage = self._all_queue[0]
        self._append_log(f"开始下一步：{STAGE_NAMES[next_stage]}", kind="status")
        self._start_stage(next_stage, on_done=self._on_all_stage_done)

    # ---------- 顶部按钮 ----------

    def _run_all_clicked(self):
        """点击"一键全部"：按 下载→解析→结构化 顺序执行。"""
        if self._running_stage is not None:
            messagebox.showwarning("提示", "已有任务在运行，请先停止。")
            return
        self._all_queue = list(STAGE_ORDER)
        self._all_mode = True
        self._append_log("开始一键流程：下载 → 解析 → 结构化", kind="status")
        self._start_stage("download", on_done=self._on_all_stage_done)

    def _stop_clicked(self):
        """点击"停止"：停止当前阶段并中止一键流程。"""
        self._stop_event.set()
        if self._proc is not None:
            self._terminate_proc(self._proc)
        self._all_queue = []
        self._append_log("用户请求停止...", kind="status")

    def _open_output_dir(self):
        """打开当前批次（或 data 目录）所在位置。"""
        target = DATA_DIR
        run_id = getattr(self, "_last_download_run_id", "") or ""
        if run_id and (DATA_DIR / run_id).is_dir():
            target = DATA_DIR / run_id
        else:
            batch = self._selected_batch()
            if batch is not None and batch.is_dir():
                target = batch
        if not target.is_dir():
            target = DATA_DIR
        try:
            if os.name == "nt":
                os.startfile(str(target))  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(target)])
            else:
                subprocess.Popen(["xdg-open", str(target)])
        except OSError as exc:
            messagebox.showerror("错误", f"无法打开目录：{exc}")


def run_gui():
    """创建主窗口并进入事件循环。"""
    root = tk.Tk()
    PipelineGUI(root)
    root.mainloop()


if __name__ == "__main__":
    run_gui()
