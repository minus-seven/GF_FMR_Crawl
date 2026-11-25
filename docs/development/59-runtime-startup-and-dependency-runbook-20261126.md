# 运行环境、依赖与服务器启停手册

本文是当前版本的运行手册。历史验收记录中的旧路径只表示当时的环境，不应作为新的安装或启动依据。

## 1. 代码与状态目录

代码目录和本机状态目录分离：

```text
<project>/                         # GitHub 代码发布包
  code/ platform/ docs/ tests/
  config.example.ini

<state>/                            # 不提交 Git 的本机状态
  config.ini
  platform.env.local
  inputs/                           # HKEX 工作簿、爬虫工作簿
  data/                             # PDF、Markdown、JSON、任务 staging/batches
  runtime/                          # 浏览器、根脚本运行时
  platform-runtime/                 # PostgreSQL、API、Worker 日志和 PID
  dependencies/venv/               # 主 Python 环境
  dependencies/venv-embed/         # 可选 BGE-M3 环境
  dependencies/web/node_modules/   # Web 依赖
```

默认状态目录为项目上两级的 `fund-research-state`。部署到 Windows 时建议显式设置为 `D:\FundResearchAgent\state`。

```bash
export FACTSHEET_STATE_ROOT=/path/to/fund-research-state
export FACTSHEET_DATA_ROOT="$FACTSHEET_STATE_ROOT/data"
export FACTSHEET_RUNTIME_ROOT="$FACTSHEET_STATE_ROOT/runtime"
export FACTSHEET_VENV_ROOT="$FACTSHEET_STATE_ROOT/dependencies/venv"
export PLATFORM_RUNTIME_ROOT="$FACTSHEET_STATE_ROOT/platform-runtime"
export FACTSHEET_CONFIG="$FACTSHEET_STATE_ROOT/config.ini"
```

Windows PowerShell：

```powershell
$env:FACTSHEET_STATE_ROOT = 'D:\FundResearchAgent\state'
$env:FACTSHEET_DATA_ROOT = "$env:FACTSHEET_STATE_ROOT\data"
$env:FACTSHEET_RUNTIME_ROOT = "$env:FACTSHEET_STATE_ROOT\runtime"
$env:FACTSHEET_VENV_ROOT = "$env:FACTSHEET_STATE_ROOT\dependencies\venv"
$env:PLATFORM_RUNTIME_ROOT = "$env:FACTSHEET_STATE_ROOT\platform-runtime"
$env:FACTSHEET_CONFIG = "$env:FACTSHEET_STATE_ROOT\config.ini"
```

首次部署：复制 `config.example.ini` 到状态目录的 `config.ini`，再填写模型、MinerU 和数据库配置。真实密钥不得放在代码目录或 Git。

## 2. 依赖安装

### macOS/Linux 开发机

```bash
source platform/scripts/activate.zsh   # bash 使用 activate.sh
"$FACTSHEET_VENV_ROOT/bin/python" -m pip install -r requirements.txt
PYTHONPATH=platform/api "$FACTSHEET_VENV_ROOT/bin/python" -m pip install -r platform/requirements.txt
"$FACTSHEET_VENV_ROOT/bin/python" -m playwright install chromium
```

可选知识库向量运行时单独安装，不要混入主环境：

```bash
export FACTSHEET_EMBED_VENV_ROOT="$FACTSHEET_STATE_ROOT/dependencies/venv-embed"
python3.12 -m venv "$FACTSHEET_EMBED_VENV_ROOT"
"$FACTSHEET_EMBED_VENV_ROOT/bin/python" -m pip install -r platform/requirements-embeddings.txt
```

### Windows

推荐使用 `tools/build_portable_runtime.ps1 -RuntimeRoot D:\FundResearchAgent\state\runtime` 构建便携 Python、离线 wheels 和 Chromium。运行时不从服务器临时联网安装依赖。

若使用公司统一 Python 环境：

```powershell
& "$env:FACTSHEET_VENV_ROOT\Scripts\python.exe" -m pip install -r requirements.txt
& "$env:FACTSHEET_VENV_ROOT\Scripts\python.exe" -m pip install -r platform\requirements.lock.txt
& "$env:FACTSHEET_VENV_ROOT\Scripts\python.exe" -m playwright install chromium
```

前端依赖使用锁文件：

```powershell
Set-Location platform\web
pnpm install --frozen-lockfile
pnpm test -- --run
pnpm build
```

## 3. 初始化配置和运行时检查

```bash
FACTSHEET_STATE_ROOT=/path/to/fund-research-state \
  "$FACTSHEET_VENV_ROOT/bin/python" code/setup_environment.py
```

Windows：

```powershell
& "$env:FACTSHEET_VENV_ROOT\Scripts\python.exe" -u code\setup_environment.py
```

检查内容包括 Python 版本、核心包、配置文件和 Playwright 浏览器。缺少配置时会在状态目录创建模板，不会写入代码目录。

## 4. 启动全部服务器

启动顺序固定为：PostgreSQL → API → Worker → Web。所有命令必须在真实 Terminal、launchd 或 Windows Service 环境执行；受限 Agent/Codex 会话只做测试和静态检查。

### macOS/Linux

```bash
source platform/scripts/activate.zsh

./platform/scripts/postgres.sh init      # 首次执行；已有数据库只需 start
./platform/scripts/postgres.sh start
./platform/scripts/api.sh start
./platform/scripts/worker.sh browser-check
./platform/scripts/worker.sh start
./platform/scripts/web.sh start
```

验证：

```bash
./platform/scripts/postgres.sh status
./platform/scripts/api.sh status         # /api/health
./platform/scripts/worker.sh status
./platform/scripts/web.sh status         # http://127.0.0.1:5173
```

浏览器访问 `http://127.0.0.1:5173`。局域网部署时只把 Web/API 端口暴露给受控网段，不要暴露 PostgreSQL 端口。

### Windows

PostgreSQL 使用公司批准的 Windows 服务；不要复制 macOS 的 PostgreSQL data directory。数据库迁移使用 `pg_dump`/`pg_restore`。

管理员 PowerShell 注册 Worker：

```powershell
.\platform\scripts\install_worker_service.ps1 `
  -RuntimeRoot 'D:\FundResearchAgent\state\runtime' `
  -DatabaseUrl 'postgresql+psycopg://worker@127.0.0.1:5432/fund_research'
```

API 和 Web 由 Windows Service、NSSM 或公司批准的进程管理器启动。API 的工作目录为代码目录，环境变量必须包含 `FACTSHEET_STATE_ROOT`、`FACTSHEET_DATA_ROOT`、`FACTSHEET_RUNTIME_ROOT`、`PLATFORM_RUNTIME_ROOT` 和 `FACTSHEET_CONFIG`。

## 5. 停止、重启和日志

正常停止顺序与启动相反：

```bash
./platform/scripts/web.sh stop
./platform/scripts/worker.sh stop
./platform/scripts/api.sh stop
./platform/scripts/postgres.sh stop
```

单服务重启：

```bash
./platform/scripts/api.sh restart
./platform/scripts/worker.sh restart
./platform/scripts/web.sh restart
```

日志和 PID 位于 `$PLATFORM_RUNTIME_ROOT`：

```bash
./platform/scripts/api.sh logs 200
./platform/scripts/worker.sh logs 200
./platform/scripts/web.sh logs 200
./platform/scripts/postgres.sh logs 200
```

若 Worker 依赖 Playwright，必须先通过 `worker.sh browser-check`；预检失败时 Worker 不会领取任务。

## 6. 本地 Qwen/Ollama

```bash
ollama serve
ollama pull qwen3:4b
curl http://127.0.0.1:11434/api/tags
```

设置页使用：

- Base URL：`http://127.0.0.1:11434/v1`
- 本地模型：`qwen3:4b`
- 结构化并发：建议 `1`

本地模型只替代结构化调用，不替代 MinerU；图像复核、视觉模型和知识库向量模型仍按各自运行时配置。

## 7. 数据库迁移与备份

```bash
./platform/scripts/postgres.sh init
PYTHONPATH=platform/api "$FACTSHEET_VENV_ROOT/bin/alembic" \
  -c platform/api/alembic.ini upgrade head
./platform/scripts/d5_backup_restore.sh backup
```

生产迁移前必须停止 Worker，确认没有运行中的写任务，备份 PostgreSQL 和 `$FACTSHEET_DATA_ROOT`。跨平台恢复只使用逻辑备份，不复制数据库物理目录。

## 8. 常见故障

| 现象 | 处理 |
|---|---|
| API 返回空响应/连接失败 | `./platform/scripts/api.sh status`，检查 `$PLATFORM_RUNTIME_ROOT/api.log` |
| 任务一直 queued | `worker.sh status`，执行 `worker.sh browser-check`，查看 Worker 日志和租约 |
| Web 依赖缺失 | `cd platform/web && pnpm install --frozen-lockfile` |
| 找不到工作簿 | 检查 `$FACTSHEET_STATE_ROOT/inputs/` 或设置 `HKEX_MASTER_WORKBOOK`、`FACTSHEET_CRAWLER_WORKBOOK` |
| 配置读取错误 | 检查 `$FACTSHEET_CONFIG`，不要在代码目录创建带密钥的 `config.ini` |
| PostgreSQL 无法跨平台启动 | 不复制 data directory，使用 `pg_dump`/`pg_restore` |

## 9. 验收命令

```bash
PYTHONPATH=platform/api:code "$FACTSHEET_VENV_ROOT/bin/pytest" -q \
  platform/api/tests/test_openapi.py \
  platform/api/tests/test_job_api.py \
  platform/api/tests/test_hkex_data.py \
  platform/api/tests/test_report_contract.py \
  tests/test_field_pipeline.py \
  tests/test_money_market_pipeline.py

(cd platform/web && pnpm test -- --run && pnpm build)
```
