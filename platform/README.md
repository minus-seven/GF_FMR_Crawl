# 平台本地开发运行说明

本目录实现开发计划中的 D1、D2、D3、D4 与 D5：原生 PostgreSQL 数据底座、HKEX 权威主数据、
crawler 链接补充、FastAPI 确定性查询、单写任务控制、Worker Adapter、
staging manifest、API 发布快照、React Web 与 ECharts 研究界面，以及
集成验收、迁移和恢复检查。

## 1. 当前交付

- PostgreSQL 17 + pgvector。
- SQLAlchemy 2 数据模型和 Alembic 首次迁移。
- 27 张 D1 核心表。
- HKEX `主数据`全部 2,479 只基金导入。
- 187 个规范化管理人、306 个有中央编号的伞。
- 23 个历史月末规模快照、661 条基金改名记录。
- 775 条 crawler 链接/运行方式记录全部匹配 HKEX；其中 4 条通过改名页关联。
- 地区、业务标签、资产分类、上市代号及补充报告来源结构化存储。
- 最新已有结构化批次导入。
- 持仓、地区/行业/久期配置、标量指标和规模历史导入。
- 指标、持仓和配置按 `batch_id` 隔离，复用的 PDF 不导致新结果覆盖旧快照。
- 结构化 `review` 与校验原因进入 `quality_issues`。
- 可重复导入、当前发布指针、D1 核验和开发库重置脚本。
- FastAPI 健康检查、快照、基金列表/详情/指标、质量问题和 ECharts 数据接口。
- `analytics_readonly` PostgreSQL 只读 schema，供 ECharts/API 与 Tableau 查询。
- FastAPI 自动生成 OpenAPI，并提供实际 HTTP 服务和 P95 冒烟测试。
- D3 任务创建、范围冻结、全局写锁、取消、事件回放和 Worker 进程。
- 下载/解析/结构化白名单 Adapter，完整复用现有业务脚本。
- Worker 只提交 manifest，API 负责验证产物并原子切换当前快照。
- D4 React Web 总览、基金库、基金详情、数据质量和任务中心。
- 两个由 API 数据驱动、随基金范围刷新的 ECharts 视图。
- Tableau Desktop 只读连接信息入口，Web 不加载 Tableau Server/Cloud iframe。
- 任务中心支持全量、管理人、资产类别和指定基金范围。
- 下载最多 5 路并发且同一管理人串行；结构化默认 16 路模型总并发，
  固收与权益流水线同时存在时按 8+8 并行执行。

完整字段说明见
[数据库 ER 与数据字典](../docs/refinement/03-database-er.md)。

## 1.1 代码与本机状态分离

PostgreSQL、任务产物、下载文件、运行日志、密钥和依赖不属于代码发布包。默认外置状态目录为仓库上两级的 `fund-research-state/`；部署到其他机器时可通过以下变量指定独立磁盘目录：

```bash
export FACTSHEET_STATE_ROOT=/path/to/fund-research-state
export FACTSHEET_DATA_ROOT="$FACTSHEET_STATE_ROOT/data"
export FACTSHEET_RUNTIME_ROOT="$FACTSHEET_STATE_ROOT/runtime"
export PLATFORM_RUNTIME_ROOT="$FACTSHEET_STATE_ROOT/platform-runtime"
export FACTSHEET_VENV_ROOT="$FACTSHEET_STATE_ROOT/dependencies/venv"
```

`config.ini` 和 `.env.local` 放在状态目录中；仓库只提供 `config.example.ini`。开发机可保留兼容 symlink，但 Windows 发布时应直接设置环境变量，不依赖 symlink。完整迁移、GitHub 归档和 PostgreSQL 备份/恢复口径见 [GitHub 归档与 Windows 迁移](../docs/development/58-github-windows-storage-layout-20261126.md)。

### 本地 Qwen 4B（开发测试）

安装 Ollama 后执行：

```bash
./platform/scripts/setup_local_qwen.sh
```

随后在“模型设置”填写 `http://127.0.0.1:11434/v1` 与 `qwen3:4b`；创建数据任务时可单独选择“本地 Qwen 4B”。本地路线强制单并发，只处理 MinerU Markdown 文本；视觉复核和联网 Ticker 不会回退至云端，而是按既有质量规则标记 review。

## 2. 身份与代码策略

数据源边界固定为：

- `HKEX基金综合资料库_完整_20261008.xlsx` 是基金主身份、分类、标签、
  管理关系、名称历史和规模快照的权威来源；
- `爬虫list.xlsx` 只提供站点/月报链接、运行方式及来源适配信息；
- 已发布 PDF/Markdown/JSON 只提供报告内容和结构化结果。

`funds.fund_code` 直接使用 HKEX 的 2,479 个唯一“基金代码”。crawler 的
“序号”有跨公司重复，不再参与主身份生成。crawler 补充记录保存在
`fund_report_sources`：

- `monthly_report_url` 保存月报或报告页面链接；
- `run_command` 保存现有下载器命令；
- `match_status` 区分 `matched`、`ambiguous`、`unmatched`；
- `match_method = manager_historical_name` 表示通过 HKEX 改名历史匹配；
- 未匹配记录不会丢失，并写入 `quality_issues` 等待处理。

## 3. 前置环境

需要：

- Python 3.11+；
- PostgreSQL 15+ 与 pgvector；
- Playwright Chromium（正式 Worker 需要原生浏览器权限）。

当前工作区已经把原生 PostgreSQL 17.11 和 pgvector 0.8.1 安装在
外置 `$PLATFORM_RUNTIME_ROOT/postgres/`，并保持数据库运行在
`127.0.0.1:55432`。运行环境由项目内启停脚本管理，不使用 Docker。

新工作区如需重建运行时：

```bash
./platform/scripts/install_postgres_runtime.sh
```

也可使用公司标准 PostgreSQL 15+ 服务，只要环境变量或外置 `.env.local`
中的连接地址指向正确实例。不要把 macOS PostgreSQL data directory 直接复制到 Windows，跨平台迁移请使用 `pg_dump`/`pg_restore`。

安装 Python 依赖：

```bash
source platform/scripts/activate.zsh
$FACTSHEET_VENV_ROOT/bin/python -m pip install -r platform/requirements.lock.txt
```

## 4. 初始化并启动数据库

项目内脚本会初始化持久数据目录并创建：

- 数据库 `fund_research`；
- pgvector 扩展；
- Tableau 只读角色 `fund_research_ro`。

```bash
./platform/scripts/postgres.sh init
```

常用管理命令：

```bash
source platform/scripts/activate.zsh

./platform/scripts/postgres.sh start
./platform/scripts/postgres.sh status
./platform/scripts/postgres.sh stop
./platform/scripts/postgres.sh restart
./platform/scripts/postgres.sh logs 200
./platform/scripts/postgres.sh psql
./platform/scripts/postgres.sh url

./platform/scripts/api.sh start
./platform/scripts/api.sh status
./platform/scripts/api.sh stop
./platform/scripts/api.sh logs 200

./platform/scripts/worker.sh start
./platform/scripts/worker.sh status
./platform/scripts/worker.sh stop
./platform/scripts/worker.sh once

./platform/scripts/web.sh start
./platform/scripts/web.sh status
./platform/scripts/web.sh stop
./platform/scripts/web.sh logs 200
```

### 标准启停流程

启动顺序固定为数据库 → API → Worker → Web，停止顺序相反：

```bash
./platform/scripts/postgres.sh start
./platform/scripts/api.sh start
./platform/scripts/worker.sh start
./platform/scripts/web.sh start

# 停止
./platform/scripts/web.sh stop
./platform/scripts/worker.sh stop
./platform/scripts/api.sh stop
./platform/scripts/postgres.sh stop
```

`start` 可重复执行，已运行时会提示 `already running`。`postgres.sh init`
只用于首次初始化或重建数据库，日常运维不要执行。

服务就绪检查：

```bash
./platform/scripts/postgres.sh status
./platform/scripts/api.sh status
./platform/scripts/worker.sh status
./platform/scripts/web.sh status
curl -sS http://127.0.0.1:8000/api/health
```

暂停队列时，先在任务页取消正在执行的任务，等任务进入 `cancelled`
后再执行 `worker.sh stop`；`queued` 任务会保留，`worker.sh start`
后继续消费。不要直接 `kill -9` 正在执行任务的 Worker：锁租约
10 分钟后才过期，期间新 Worker 不会接管，任务会一直显示 `running`。

只重启单个服务时使用对应脚本的 `restart`：

```bash
./platform/scripts/api.sh restart
./platform/scripts/worker.sh restart
./platform/scripts/web.sh restart
```

`postgres.sh restart` 前应先停 Worker，避免正在执行的任务因断线失败。

脚本内部用 `nohup` 拉起进程，但必须在真实 Terminal 窗口或
launchd/LaunchAgent 中执行；在会随会话回收的宿主（例如自动化工具
会话）里启动后，进程会一并退出。需要开机自启和崩溃拉起时，使用
launchd 托管 api、worker、web 三个服务。

各服务日志位于 `$PLATFORM_RUNTIME_ROOT`：`postgres.log`、`api.log`、
`worker.log`、`web.log`，PID 写入同名 `*.pid` 文件。脚本提示
`running outside this PID file` 表示进程不是由脚本启动的，应在所属
终端停止，或手动结束对应 PID。

macOS 默认 zsh 使用 `activate.zsh`；bash 用户改用
`source platform/scripts/activate.sh`。

执行激活脚本后，当前终端可以直接使用
项目自带的 `psql`，并已导出 `PGHOST`、`PGPORT`、`PGUSER`、`PGDATABASE`
和 `PLATFORM_DATABASE_URL`。

数据库文件位于 `$PLATFORM_RUNTIME_ROOT/postgres-data/`，运行日志位于
`$PLATFORM_RUNTIME_ROOT/postgres.log`。本地连接配置保存在状态目录的
`platform.env.local`（开发机的 `platform/.env.local` 仅为兼容 symlink）。

如 Tableau 需要通过 TCP 和密码连接，可设置
`PLATFORM_READONLY_PASSWORD` 后重新执行 `init`，不要把密码写入仓库。
该角色 D1 只获得数据库连接和 `public` schema 使用权限，不自动获得业务表
读取权限；D2 将创建经过白名单控制的只读视图后再授权。

## 5. 执行迁移

```bash
source platform/scripts/activate.zsh
set -a
source "$FACTSHEET_STATE_ROOT/platform.env.local"
set +a

PLATFORM_DATABASE_URL="$PLATFORM_DATABASE_URL" \
  $FACTSHEET_VENV_ROOT/bin/alembic -c platform/api/alembic.ini upgrade head
```

首次迁移编号为 `20261022_0001`，启用 pgvector 并创建全部 D1 核心表。

## 6. 导入数据

导入 HKEX 主数据、crawler 补充和最新结构化批次：

```bash
source platform/scripts/activate.zsh
set -a
source "$FACTSHEET_STATE_ROOT/platform.env.local"
set +a

PYTHONPATH=platform/api \
PLATFORM_DATABASE_URL="$PLATFORM_DATABASE_URL" \
  $FACTSHEET_VENV_ROOT/bin/python -m app.importers.cli \
  --hkex-workbook "$FACTSHEET_STATE_ROOT/inputs/HKEX基金综合资料库_完整_20261008.xlsx" \
  --crawler-workbook "$FACTSHEET_STATE_ROOT/inputs/爬虫list.xlsx"
```

显式指定批次：

```bash
PYTHONPATH=platform/api \
  $FACTSHEET_VENV_ROOT/bin/python -m app.importers.cli \
  --database-url "$PLATFORM_DATABASE_URL" \
  --structured-root data/20260908162205
```

导入在一个数据库事务内完成。相同 HKEX、crawler 和结构化输入会生成相同批次指纹；
第二次执行不会重复写入或生成新批次。输入发生变化时生成新批次，成功导入后切换
`publication_state.current_batch_id`。

导入报告写入：

```text
data/<batch>/reports/d1_import_report_<timestamp>.json
```

## 7. 核验

```bash
PYTHONPATH=platform/api \
  $FACTSHEET_VENV_ROOT/bin/python platform/scripts/verify_d1.py \
  --database-url "$PLATFORM_DATABASE_URL"
```

当前验收基线：

```text
funds = 2479
report_sources = 775
matched_report_sources = 775
published batches >= 1
current_batch_id is not null
```

指标、持仓和配置数量取决于所选结构化批次，不做固定值断言。

本次开发在临时原生 PostgreSQL 17.11 + pgvector 0.8.1 实例上的实测结果：

```text
Alembic upgrade -> downgrade -> upgrade：通过
managers：187（规范化后）
umbrellas：306
funds：2479
share classes：2479
listings：481
regions：7437
tags：8354
scale history：29563
name history：661
report sources：775
matched report sources：775
structured records：20
skipped records：0
documents：19
document subjects：20
metrics：62
holdings：200
allocations：266
quality issues：13
重复导入：no-op
后端测试：28/28 通过
前端测试：5/5 通过
```

上述数量对应 `data/20260908162205`，仅代表本次验收输入。当前项目内数据库
已经完成迁移和导入并保持运行；机器重启后执行
`./platform/scripts/postgres.sh start` 即可恢复。

## 8. FastAPI 与只读视图

默认 API 地址为 `http://127.0.0.1:8000`，交互文档为
`http://127.0.0.1:8000/docs`。

已实现接口：

```text
GET /api/health
GET /api/snapshots/current
GET /api/funds
GET /api/funds/{fund_code}
GET /api/funds/{fund_code}/metrics
GET /api/quality/issues
GET /api/analytics/perspectives
GET /api/settings/models
PUT /api/settings/models
POST /api/jobs
GET /api/jobs
GET /api/jobs/{job_id}
GET /api/jobs/{job_id}/events
POST /api/jobs/{job_id}/cancel
POST /api/jobs/{job_id}/retry
POST /api/jobs/{job_id}/publish
```

设置页通过右上角用户图标进入。结构化模型默认选择 DeepSeek Flash，使用独立
`deepseek_api_key`，不会把 Qwen Key 发送到 DeepSeek 端点。`GET /api/settings/models` 只用于本机页面回填真实现有
配置，响应设置 `Cache-Control: no-store`；保存时采用原子替换并把 `config.ini`
权限设为 `600`。MinerU `lightweight` 路线直接使用 Agent，`token` 路线优先
Precise/VLM，认证或调用失败后自动降级到 Agent。

启动：

```bash
source platform/scripts/activate.zsh
./platform/scripts/api.sh start
./platform/scripts/api.sh status
```

所有成功响应包含 `data`、`snapshot_id`、`request_id` 和 `meta.server_time`。
时间统一返回 UTC。错误响应包含稳定错误码和同一 `request_id`。

只读视图位于 `analytics_readonly`：

```text
current_snapshot
fund_summary
fund_scale_current
fund_metric_current
fund_holding_current
fund_allocation_current
quality_issue_current
fund_report_source
```

`fund_research_ro` 可读取这些视图，但不能访问 `public.jobs`、任务锁和内部表。

## 9. D4 Web 与 ECharts

Web 默认地址为 `http://127.0.0.1:5173`，开发服务器将 `/api` 代理到
`http://127.0.0.1:8000`。

首次安装前端依赖：

```bash
cd platform/web
pnpm install --frozen-lockfile
```

启动与核验：

```bash
./platform/scripts/web.sh start
./platform/scripts/web.sh status
./platform/scripts/web.sh test
./platform/scripts/web.sh build
```

当前路由：

```text
/                研究总览
/funds           基金列表与筛选
/funds/:code     基金详情
/quality         数据质量
/tasks           任务创建、进度与 SSE 事件
```

浏览器验收已覆盖基金搜索与分页、基金详情、质量问题及空结果、任务创建与
实时进度、错误请求 ID、390px 响应式和两个 ECharts SVG 渲染。Web 图表
只使用 ECharts；Tableau Desktop 通过 `analytics_readonly` 只读视图开展
深入分析，不嵌入页面。

## 10. D3 任务与 Worker

提交指定基金下载任务：

```bash
curl -X POST 'http://127.0.0.1:8000/api/jobs' \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: example-download-all924' \
  -d '{
    "scope": {"mode": "funds", "fund_codes": ["ALL924"]},
    "stages": ["download"],
    "document_policy": {"prefer": "monthly"},
    "model_policy": {}
  }'
```

任务中心范围选项：

```text
GET /api/jobs/scope-options

all       全量基金 2,479
manager   187 个管理人的冻结基金数
category  商品、固收、权益、混合、货币
funds     指定基金代码
```

批量任务示例：

```json
{"scope":{"mode":"all"}}
{"scope":{"mode":"manager","manager_ids":["<manager_uuid>"]}}
{"scope":{"mode":"category","categories":["固收"]}}
```

运行批量任务时：

- 不同管理人最多 5 个下载子进程并行；
- 同一管理人的基金保持串行，避免站点限流；
- MinerU 默认按 PDF 36 路并发，单份 PDF 内部仍串行；GUI 与平台共用同一配置；
- 结构化默认使用 `config.ini` 的 `model_workers=32`；单类别使用 32 路，
  固收与权益同时存在时各使用 16 路并并发运行；
- 每完成 10 只基金提交一次任务范围进度。

全量解析和结构化阶段默认最长运行 24 小时，可分别通过
`PLATFORM_PARSE_TIMEOUT_SECONDS` 和 `PLATFORM_STRUCTURE_TIMEOUT_SECONDS`
覆盖。MinerU Precise PDF 并发由 `FACTSHEET_MINERU_WORKERS` 控制，默认 36；
提交文件按官方 50 个/分钟限额使用滑动窗口限流，结果轮询间隔默认 2.5 秒；
Agent 轻量解析受 500 次/分钟结果查询限制，默认降为 18 路并发；
结果查询另有 450 次/分钟滑动窗口。任一任务收到 429 时，会登记全批次共享冷却，
优先遵循 `Retry-After`，随后从 8 秒开始指数退避并加入随机抖动；冷却后继续轮询
原 MinerU 任务，不会重新提交文件。429 重试默认最多 8 次，可用
`MINERU_RATE_LIMIT_RETRIES` 调整。
不改变单份 PDF 的模型、模式和解析流程。

Worker 每轮只领取一个任务。任务创建时冻结基金列表，并使用数据库
`global_write` 租约锁；第二个写任务进入 `waiting_lock`。Worker 心跳续约，
API 取消按钮通过 `cancellation_requested` 通知 Worker 终止受控子进程。

失败或发布失败的任务可在任务详情中执行“断点续跑”。API 保留已完成阶段，
仅把失败阶段及后续未完成阶段恢复为 pending；Worker 跳过 completed 阶段。
解析和结构化 staging 通过 PDF/JSON 缓存继续复用。跨任务续跑输入在同一文件系统
优先使用硬链接，避免重复复制大型 fact sheet 和 Markdown；跨卷时回退普通复制。

阶段白名单：

```text
download  调用选中的现有公司下载器
parse     调用现有 run_all_mineru.py
structure 调用现有 run_all_structured_data.py
```

下载阶段已移植原 GUI 的三段式爬虫补全：

1. 对任务冻结基金执行公司 `--probe`，公司特有规则也参与判断；
2. 先下载月报链接和运行方式完整的来源；
3. 对缺失来源调用对应 `companies/*/*_fund_link.py --backfill --rows ...`；
4. 解析 `OK` / `SKIP`，把 I/J 写入 `fund_report_sources`；
5. 下载补全成功的基金。

平台为每个任务复制隔离工作簿到
`data/jobs/<job_id>/backfill/爬虫list.xlsx`，只清空数据库中待补全目标
行的 I/J，再由公司脚本自主发现。平台不读取正式 Excel 已有 I/J 作为补全
结果，也不修改正式 `爬虫list.xlsx`。补全状态保存在
`fund_report_sources.metadata_json.backfill`，并通过 `job.backfill` SSE
事件回放。D1 重复导入时，Excel 空值不会覆盖数据库中的有效补全结果。

真实验收任务 `192c9028-4011-49c0-870e-79f3e9b9ffab` 已将 BRE374 的
缺失来源通过汇丰补全脚本写入数据库，并成功下载 PDF。

AGD276 完整验收任务为 `aad7493c-0d85-4dd3-a5f0-5ced6e4342bf`：平台
自主补全摩根 I/J、先提交数据库，再下载 PDF，随后完成 MinerU 和结构化。
下载默认执行 3 次尝试；终态任务仍会在任务中心回放 `job.backfill` 和
`job.retry` 事件。

MinerU 解析阶段使用 `--mode auto`：

- 配置 Token 可用时优先走 Precise/VLM；
- Token 缺失、过期或 Precise 调用失败时，自动降级到无 Token 的
  Agent API，并在 Markdown manifest 中记录 `fallback_from` 和
  `fallback_reason`；
- 结构化阶段同时接受 Precise 与 Agent 缓存。

MinerU 性能：

- PDF 级并发默认 `mineru_workers=36`，由 `FACTSHEET_MINERU_WORKERS` 覆盖；
- 单份 PDF 内部仍使用相同模型、模式和解析流程，不降低质量；
- 每完成一份 Markdown manifest，任务事件推送 `PARSE · RUNNING · n/total`；
- 新任务按 PDF SHA-256 复用历史 MinerU 缓存，优先 Precise、其次 Agent；
- 2 份真实 Agent 解析基准从串行约 95 秒降至 `28.69s`，成功率 2/2；
- 当前摩根批次只有 1/45 个 PDF 命中历史缓存，缓存不是主要提速来源。

结构化成本优化：

- 所有 Chat 请求使用稳定 system 前缀，并在调用明细和批处理汇总中记录
  DeepSeek `cache_hit_tokens` / `cache_miss_tokens`；
- 矩阵文本回退先由 `deepseek-flash` 处理，只有字段仍缺失时才调用
  `deepseek-v4-pro`，避免 Pro 无差别重读全文；
- Ticker 主数据同时建立规范化基金披露名称和交易所 Ticker 别名，已验证代码可跨基金名
  直接复用；ISIN、CUSIP、SEDOL 和债券代码不会进入股票 Ticker 缓存；
- 固收视觉复核优先发送 MinerU 图表/表格裁剪，其次发送摘要区域裁剪，最后才使用整页图，
  单次最多 12 张。三项复杂字段继续共享一次图片请求。

页级知识索引：

- 迁移 `20261027_0005` 新增页面、切片、Embedding、索引运行和 Agent 审计结构；
  `0006` 为向量记录保存来源内容哈希，`0007` 将切片唯一键扩展到发布快照；
- 当前发布快照只索引报告期可信且能定位真实 PDF 页的文档，A 级使用 MinerU
  `content_list.page_idx`，B 级使用 PDF 原文页；
- 当前 99 份文档生成 386 个页面和 1,423 个切片；
- 可追溯历史 MinerU 文档写入独立 `knowledge-history` 批次，不切换当前发布指针；
- 文档、页面和切片使用稳定 UUID，重复索引不产生重复记录；
- PostgreSQL 全文、trigram 和 HNSW 向量索引均由同一 migration 创建；
- BGE-M3 使用 Python 3.12 独立 `$FACTSHEET_STATE_ROOT/dependencies/venv-embed`，dense 向量固定 1024 维；
- 向量服务不可用时，`hybrid` 自动降级全文检索，`vector` 返回明确错误。

索引命令：

```bash
PLATFORM_DATABASE_URL="$PLATFORM_DATABASE_URL" \
  $FACTSHEET_VENV_ROOT/bin/python platform/scripts/index_knowledge.py --scope both

HF_HOME=$FACTSHEET_RUNTIME_ROOT/huggingface \
PLATFORM_DATABASE_URL="$PLATFORM_DATABASE_URL" \
  $FACTSHEET_STATE_ROOT/dependencies/venv-embed/bin/python platform/scripts/index_knowledge.py \
  --scope both \
  --embedding-model BAAI/bge-m3 \
  --embedding-model-path $FACTSHEET_RUNTIME_ROOT/models/bge-m3 \
  --embedding-device mps
```

检索调试：

```text
GET /api/knowledge/status
GET /api/knowledge/search?q=到期收益率&fund_codes=ABD895
GET /api/knowledge/search?mode=hybrid&q=久期
```

检索结果为当前发布快照中的页级片段，包含基金、报告期、文档版本、页码、
来源路径、内容哈希和证据等级。显式历史快照只允许查询 `published` 批次。

Worker 只生成 `staging_manifest.json`，不直接切换发布指针。API 的
`POST /api/jobs/{job_id}/publish` 会验证 manifest 和所有文件哈希，克隆基线
结果到新批次，导入本任务生成的结构化 JSON，再原子更新
`publication_state.current_batch_id`。

D3 真实验收任务产物位于：

```text
data/jobs/<job_id>/worker.log
data/jobs/<job_id>/staging_manifest.json
data/batches/<batch_id>/manifest.json
```

验证最近一次任务：

```bash
PYTHONPATH=platform/api \
  $FACTSHEET_VENV_ROOT/bin/python platform/scripts/verify_d3.py \
  --database-url "$PLATFORM_DATABASE_URL"
```

导出 FastAPI 生成的 OpenAPI：

```bash
PYTHONPATH=platform/api $FACTSHEET_VENV_ROOT/bin/python platform/scripts/export_openapi.py
```

核验只读视图和权限：

```bash
PYTHONPATH=platform/api \
  $FACTSHEET_VENV_ROOT/bin/python platform/scripts/verify_d2.py \
  --database-url "$PLATFORM_DATABASE_URL"
```

## 11. 测试

后端只运行不依赖数据库的测试：

```bash
PYTHONPATH=platform/api \
  $FACTSHEET_VENV_ROOT/bin/pytest -c platform/api/pytest.ini platform/api/tests
```

前端测试与生产构建：

```bash
./platform/scripts/web.sh test
./platform/scripts/web.sh build
```

运行后端完整集成测试时，必须使用名称以 `_test` 结尾的专用数据库：

```bash
PLATFORM_TEST_DATABASE_URL='postgresql+psycopg:///fund_research_d1_test' \
PYTHONPATH=platform/api \
  $FACTSHEET_VENV_ROOT/bin/pytest -c platform/api/pytest.ini platform/api/tests
```

## 12. D5 集成、迁移与恢复

综合验收：

```bash
PYTHONPATH=platform/api $FACTSHEET_VENV_ROOT/bin/python \
  platform/scripts/verify_d5.py \
  --database-url 'postgresql+psycopg://hu@127.0.0.1:55432/fund_research' \
  --api-url 'http://127.0.0.1:8000' \
  --evidence-dir platform/backups/d5
```

迁移检查：

```bash
./platform/scripts/d5_migration_check.sh
```

数据库 dump 与恢复冒烟：

```bash
./platform/scripts/d5_backup_restore.sh
```

本次 D5 证据位于 `platform/backups/d5/`：

```text
d5_acceptance.json
file_manifest.json
migration_report_<timestamp>.json
fund_research_<timestamp>.dump
restore_report_<timestamp>.json
d5-tasks.png
```

正式验收记录和实际覆盖/缺口见
[D5 验收记录](../docs/development/02-d5-acceptance-record.md)。

下一阶段的知识库问答与最小 Agent 周计划见
[2026-10-26 每日开发计划](../docs/development/03-week-20261026-next-stage-plan.md)。

周一数据就绪和知识库方案冻结记录见
[2026-10-26 冻结记录](../docs/development/04-day1-data-readiness-and-kb-freeze.md)。

## 13. 常见错误

| 现象 | 排查 |
|---|---|
| `127.0.0.1` 拒绝连接 | 对应服务未启动或已退出，检查 `*.sh status`、端口监听和 `$PLATFORM_RUNTIME_ROOT/*.log` |
| Web 返回 404 | 确认 `web.sh` 以 `platform/web` 为 Vite 根目录启动 |
| Web 无法连接 API | 执行 `./platform/scripts/api.sh status`，检查 8000 端口 |
| 任务一直 `queued` | 检查 `worker.sh status` 和 `$PLATFORM_RUNTIME_ROOT/worker.log` |
| 数据库连接失败 | 检查 55432 端口、`.env.local` 和 `pg_isready` |
| 来源补全未命中 | 查看任务 `job.backfill`、`job.fund` 和来源 metadata 的 backfill 原因 |
| 前端依赖缺失 | 在 `platform/web` 执行 `pnpm install --frozen-lockfile` |
| 恢复失败 | 确认目标恢复库不存在，并检查 dump SHA-256 |

## 14. 开发库重置

以下命令会清空数据库的 `public` schema，只用于明确的开发测试库：

```bash
PYTHONPATH=platform/api \
  $FACTSHEET_VENV_ROOT/bin/python platform/scripts/reset_dev_database.py \
  --database-url 'postgresql+psycopg:///fund_research_d1_test' \
  --yes
```

脚本拒绝清空 `postgres`、`template0` 和 `template1`，但调用方仍必须确认
连接地址指向可丢弃的开发库。

## 15. 导入语义

- 金额、比例和未知字段保持 `NULL`，不转换为 0。
- 结构化抽取结果默认是 `review_status = pending`，不自动视为人工批准。
- `review`、空字段、比例异常和来源文件缺失写入 `quality_issues`。
- PDF 版本使用 SHA-256；找不到 PDF 时明确记录 `source_pdf_missing`。
- 原始 PDF 和 JSON 保留在受管数据目录，PostgreSQL 只保存元数据和路径。
- 同一结构化批次重复导入为 no-op；不同输入产生新不可变批次。
- 指标、持仓和配置的批次字段随唯一约束一起隔离，发布指针不回写旧批次。
- HKEX 原始行完整保存在 `fund_source_ids.metadata_json`，结构化字段与原始值可交叉核对。

## 16. 已确认并同步的设计差异

以下差异已经过 review，并同步到 PRD、C4、ER 和来源适配器文档：

1. 新增 `fund_report_sources` 承载 crawler 月报链接、备选链接、运行方式和匹配状态。
2. `funds` 增补 HKEX 的财务年度、交易频度、托管人、ESG、跟踪指数和上市分类字段。
3. `fund_metrics` 增加 `text_value`、`metadata_json` 和 `batch_id`，用于信用评级、
   审计元数据和快照隔离；`fund_holdings`、`fund_allocations` 也增加 `batch_id`。
4. `dataset_batches` 增加来源类型和源工作簿 SHA-256。
5. 伞实体按稳定中央编号归并，不再配置管理人外键；HKEX 中两个伞编号存在名称变更，
   当前保留该编号最后一次出现的名称。
6. HKEX 没有独立的份额类别 ID；当前为每只基金建立一个以基金代码标识的默认
   `share_class`，作为上市信息锚点。该 1:1 假设需要业务确认。

## 17. D4 待 review 的实现差异

以下差异在当前实现中为控制一日范围而采用，尚未同步到架构与 PRD：

1. 基金和持仓表使用语义化 HTML `<table>`，未引入 C4 建议的 AG Grid；
   当前 20 行分页足以覆盖本切片，全量横向比较和列配置仍留给后续。
2. Tableau 面板只展示只读数据库连接元数据，尚未提供 `.twbx` 下载接口或
   工作簿打开动作。
3. Web 已支持全量、管理人、资产类别和单基金；标签范围仍只在后端契约中，
   尚未提供 Web 选择器。
4. 右侧数据透视固定为两个预设 ECharts 视图，尚未提供拖拽、自由选图和
   自定义聚合。

## 18. D3 待 review 的实现差异

1. 架构目前写的是 MinerU Precise/VLM 与本地文本降级；实现新增了
   Precise 失败后调用 Agent API 的在线降级路径。
2. Agent API 当前只返回 `full.md`，不包含 Precise ZIP 中的
   `content_list.json` 和图片目录；结构化抽取仍可运行，但证据颗粒度和
   提取质量低于 Precise，长期仍应更新有效 Token。
3. 发布阶段现在会把任务生成的结构化结果导入新批次。原 D3 描述只写
   “克隆基线结果”，没有明确任务新产物的导入责任。

以上差异需 review 后再同步 C4、解析流水线和发布流程文档。

## 19. 本次未包含

- 知识库、Embedding、RAG 和 Agent。
