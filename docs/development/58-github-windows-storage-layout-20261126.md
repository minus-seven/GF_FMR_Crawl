# GitHub 归档与 Windows 迁移：代码、状态和依赖分离

## 目标

本次整理不改变现有数据库内容、任务产物或业务处理逻辑，只把可发布代码与本机状态分离：密钥、PostgreSQL 运行目录、下载/解析/结构化产物、缓存和 Python/Node 依赖均不进入 Git 发布包。

## 目录约定

代码仓库为 `202607291309/GF_FMR_Crawl/`。默认外置状态目录为仓库上两级的 `fund-research-state/`，也可以在部署时指定独立磁盘目录：

```text
fund-research-state/
  config.ini                 # 密钥和模型配置，不提交
  platform.env.local         # Web/API 本地环境，不提交
  inputs/                    # HKEX 主工作簿、爬虫工作簿
  data/                      # 下载、Markdown、结构化、任务 staging/batches
  runtime/                   # 根脚本运行时、浏览器、缓存
  platform-runtime/          # PostgreSQL、API/Worker 日志和平台运行时
  dependencies/              # .venv、Playwright、node_modules 等
  archive/                   # 历史备份和缓存归档
```

开发机上保留少量兼容 symlink（例如 `data`、`.venv`、`platform/.runtime`），发布到 Git 的是忽略后的代码和模板；symlink 指向的真实内容仍在外置状态目录中。

## 配置入口

优先使用环境变量，未设置时自动落到上述默认目录：

| 变量 | 用途 |
| --- | --- |
| `FACTSHEET_STATE_ROOT` | 全部本机状态的根目录 |
| `FACTSHEET_DATA_ROOT` | 下载、解析、任务和发布批次目录 |
| `FACTSHEET_RUNTIME_ROOT` | 根脚本的 Python/浏览器运行时 |
| `FACTSHEET_VENV_ROOT` | Python 虚拟环境目录 |
| `FACTSHEET_CONFIG` | 指定 `config.ini` |
| `PLATFORM_RUNTIME_ROOT` | API/Worker/PostgreSQL 运行时目录 |
| `HKEX_MASTER_WORKBOOK` | HKEX 综合资料库工作簿 |
| `FACTSHEET_CRAWLER_WORKBOOK` | 月报爬虫工作簿 |
| `HKEX_CRAWLER_ROOT` | HKEX 爬虫代码目录 |

复制 `config.example.ini` 为外置状态目录中的 `config.ini` 后再填写密钥；不要把真实 `config.ini`、`.env.local` 或 API Key 放进仓库。

## GitHub 归档建议

1. 在本地先确认 `git status --ignored` 中没有把 `data/`、`*.xlsx`、PDF、日志、`.venv`、`node_modules` 或密钥加入暂存区。
2. 提交代码、迁移脚本、配置模板、测试和本目录文档；不提交业务数据和数据库目录。
3. GitHub 仓库只保存源码版本。若需要交付样例，只放脱敏小 fixture，并明确它不是生产数据。
4. 建议启用 Secret Scanning/Push Protection，并在首次归档前轮换历史上曾经出现在工作树中的 API Key。

## Windows 部署

推荐把代码放在 `D:\\FundResearchAgent\\current`，把状态放在 `D:\\FundResearchAgent\\state`，服务环境设置：

```powershell
$env:FACTSHEET_STATE_ROOT = 'D:\\FundResearchAgent\\state'
$env:FACTSHEET_DATA_ROOT = 'D:\\FundResearchAgent\\state\\data'
$env:FACTSHEET_RUNTIME_ROOT = 'D:\\FundResearchAgent\\state\\runtime'
$env:PLATFORM_RUNTIME_ROOT = 'D:\\FundResearchAgent\\state\\platform-runtime'
$env:FACTSHEET_CONFIG = 'D:\\FundResearchAgent\\state\\config.ini'
```

先安装/准备 Python、依赖和 Playwright Chromium，再用 `platform/scripts/install_worker_service.ps1` 注册原生 Worker；API 与 Web 也应以服务方式启动，并授予状态目录读写权限。不要复制 macOS 的 PostgreSQL data directory 到 Windows；在 Windows 安装 PostgreSQL/pgvector 后，用 `pg_dump`/`pg_restore` 迁移数据库。

## 不变性与回滚

- 本次路径改造只改变“从哪里读取/写入”，数据库表结构和已有业务数据不删除、不重写。
- 原始文件移动到状态目录，仓库内兼容链接仍可让旧脚本按原路径访问；如果发布包不带链接，使用上面的环境变量即可运行。
- 回滚时只需将 `FACTSHEET_STATE_ROOT` 指回备份状态目录；不需要复制或修改数据库内容。

## 验证记录

- Python 代码编译通过，API/Web 关键回归测试通过，前端构建通过。
- 历史测试 `test_crawler_workbook_is_only_a_supplement` 仍按旧快照写死 853 行，而当前正式工作簿实际为 2110 行；该失败是既有测试口径与当前数据快照不一致，不是本次路径分离引入。后续应把它改成动态基线或显式 fixture。
