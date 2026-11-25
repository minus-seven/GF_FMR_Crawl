# -*- coding: utf-8 -*-
"""基金月报处理工具 - GUI 入口。

直接启动图形界面，不做环境检测/安装。
"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
CODE_DIR = PROJECT_ROOT / "code"


def main():
    """把 code 目录加入模块搜索路径后启动 GUI。"""
    if str(CODE_DIR) not in sys.path:
        sys.path.insert(0, str(CODE_DIR))
    from gui import run_gui

    run_gui()


if __name__ == "__main__":
    main()
