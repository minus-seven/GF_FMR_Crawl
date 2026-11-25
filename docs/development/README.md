# 开发文档入口

当前版本的运行环境、依赖安装、状态目录、数据库/API/Worker/Web 启停和 Windows 服务方式，统一以：

[运行环境、依赖与服务器启停手册](59-runtime-startup-and-dependency-runbook-20261126.md)

为准。

## 文档中的命令约定

Unix shell 先执行：

```bash
export FACTSHEET_STATE_ROOT=/path/to/fund-research-state
export FACTSHEET_DATA_ROOT="$FACTSHEET_STATE_ROOT/data"
export FACTSHEET_RUNTIME_ROOT="$FACTSHEET_STATE_ROOT/runtime"
export FACTSHEET_VENV_ROOT="$FACTSHEET_STATE_ROOT/dependencies/venv"
export PLATFORM_RUNTIME_ROOT="$FACTSHEET_STATE_ROOT/platform-runtime"
export FACTSHEET_CONFIG="$FACTSHEET_STATE_ROOT/config.ini"
```

Windows PowerShell 使用同名 `$env:` 变量。文档中的 `$FACTSHEET_VENV_ROOT/bin/...`、`$FACTSHEET_DATA_ROOT/...` 和 `$PLATFORM_RUNTIME_ROOT/...` 都指向外置状态目录；项目内的 `data`、`.venv`、`platform/.runtime` 仅是开发机兼容链接。

历史验收文档保留当时的快照、日期和测试结果；如果历史命令与本入口冲突，以本手册和当前脚本实现为准。
