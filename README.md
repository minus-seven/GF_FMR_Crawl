# factsheet

香港基金月报、factsheet 和相关基金文件下载脚本集合。

## 给普通 Windows 用户的首次配置

交付人员先在一台可联网的 Windows 电脑上双击 `制作便携离线运行包.bat`。依赖、Playwright
浏览器和运行时状态默认放在项目目录旁的 `fund-research-state/`（可用
`FACTSHEET_STATE_ROOT`、`FACTSHEET_RUNTIME_ROOT` 覆盖），不会进入代码发布包。制作完成后，
复制代码目录和外置状态目录（或在目标机重新生成运行时）到目标电脑。

目标电脑不需要安装 Python，也不需要管理员权限或联网安装依赖，只需：

1. 双击 `首次配置环境.bat`，从本地离线依赖包检查并修复环境。
2. 双击 `配置API.bat`，填写或更新 Qwen Key、MinerU Token、API URL 和模型名称。
3. 按顺序使用下面三个正式批处理入口。

实际配置保存在外置状态目录的 `config.ini`（也可用 `FACTSHEET_CONFIG` 指定），该文件包含密钥并被 Git 忽略。首次升级时，程序会把
旧的 `code/structured_data/local_config.py` 中已有的 Key 自动迁移进去。以后 Key 过期只需
双击 `配置API.bat` 修改并保存，不需要改 Python 代码。可提交的无密钥模板是
`config.example.ini`。

## 三个批处理入口（正式交付方式）

按顺序双击项目根目录下的三个文件：

1. `run_all_downloaders.bat`：默认处理 61 个可自动下载项目，同时处理 5 家公司，同一公司内部串行。下载时只用一条进度条原地更新，不逐项刷屏。太平大中华新动力股票基金属于手动项目，不计入下载数或失败数。运行结束会列出失败基金并询问是否立即重试；选择 `Y` 时只重跑失败项目。
2. `run_all_mineru.bat`：双击后可按序号选择需要处理的批次文件夹；使用单行进度条显示处理情况，并以 MinerU `vlm` 精准模式按 PDF 默认 36 路并发转换，单份 PDF 内部保持串行，生成 Markdown、内容列表和图表图片缓存。
3. `run_all_structured_data.bat`：双击后可按序号选择需要解析的批次文件夹；严格读取已有 MinerU 缓存，默认使用 DeepSeek Flash 非思考模式并按 32 路总并发抽取；使用单行总体进度条显示基金完成数、运行时长和活跃任务，并分别生成固收、权益、中国精选三个 Excel。结束后按模型显示调用次数和输入/输出 Token；详细模型日志与统计分别保存在 `structure_data/_work/structured_run_*.log` 和 `structured_run_summary_*.json`。缺少缓存时会提示先运行 MinerU，不会暗中补调 MinerU。

每个批次独立保存：PDF 位于 `data/<批次>/factsheet/<类别>/`，MinerU 结果位于
`data/<批次>/markdown/<类别>/`，三个最终 Excel 位于 `data/<批次>/structure_data/`。
下载和 MinerU 报告位于 `data/<批次>/reports/`，结构化 JSON、CSV 位于 `structure_data/_work/`。需要手动下载的 PDF 请在执行 MinerU 前放入
`factsheet/固收`、`factsheet/权益` 或 `factsheet/中国精选`。

MinerU 的精度由解析后端决定：固定使用精度更高的 `vlm`，不使用精度较低的 `pipeline`。并发只用于后续大模型解析，不改变 MinerU 结果。Excel 解析以大模型判断为主，默认使用 `deepseek-flash` 做非思考文本抽取、`deepseek-v4-pro` 做复杂文本备用、`deepseek-flash` 做视觉复核；Qwen 的模型与密钥继续保留，可在设置页切回。权益和中国精选的 Ticker 与公司中文名优先复用跨批次证券缓存，未命中项由当前结构化模型核对，并由模型按不确定性自行决定是否联网。对于固收的持仓、久期分布和地区分布，视觉模型分别独立复核“是否披露、口径、单位、逐项数值和证据”；固收不生成 Ticker，完整保留报告中的债券持仓名称和比例。代码只做格式统一、合计检查和异常标记，不会因为合计不接近 100 就删除模型结果。久期分布同时支持百分比配置和以“年”为单位的久期贡献。

## 目录结构

```text
code/
  run_all_downloaders.py
  companies/
    <company>/
      download_*.py
data/
  <批次日期时间>/
    factsheet/
      固收/
      权益/
      中国精选/
    markdown/
      固收/
      权益/
      中国精选/
    reports/
    structure_data/
      固收_基金提取结果.xlsx
      权益_基金提取结果.xlsx
      中国精选_基金提取结果.xlsx
      _work/
```

## Web 平台正式 Worker

完整的依赖安装、状态目录配置、PostgreSQL/API/Worker/Web 启停、Windows Service、Ollama 和备份恢复步骤见 [运行环境、依赖与服务器启停手册](docs/development/59-runtime-startup-and-dependency-runbook-20261126.md)。

平台 Worker 需要执行部分依赖 Playwright 的管理人适配器，必须由具备原生浏览器进程权限的 Terminal、macOS `launchd` 或 Windows Service 启动。不要从受限 Agent/Codex 子进程领取真实任务。

```bash
./platform/scripts/worker.sh browser-check
./platform/scripts/worker.sh start
```

macOS 常驻服务：`./platform/scripts/install_worker_launchd.sh`；Windows 服务：以管理员 PowerShell 运行 `platform/scripts/install_worker_service.ps1`（需要公司批准的 NSSM）。启动前会执行 Playwright 浏览器预检，失败时 Worker 不会领取任务。详细说明见 [原生 Worker 浏览器运行方案](docs/development/14-native-worker-browser-runtime.md)。

`code/run_all_downloaders.py` 是总入口，比每家公司脚本高一级。不同批次和类别的文件不会互相覆盖，下载结果不会提交到 Git。

## 开发环境安装

```powershell
pip install -r requirements.txt
python -m playwright install chromium
```

## 一键运行

```powershell
run_all_downloaders.bat
```

或：

```powershell
python code\run_all_downloaders.py
```

批量入口最多同时运行 5 家公司的下载脚本；同一公司的基金严格串行。某个基金公司失败时不会阻断其他公司，最终会输出汇总，并写入 `data/_reports/download_run_report_<timestamp>.json`。

常用参数：

```powershell
python code\run_all_downloaders.py --only hsbc
python code\run_all_downloaders.py --exclude ubs
python code\run_all_downloaders.py --timeout 240
python code\run_all_downloaders.py --allow-failures
```

## 固收月报结构化

固收 PDF 结构化工具在 `code/structured_data/`。主流程先用 MinerU 精准 VLM 生成 Markdown、内容列表和图表图片，并合并 PDF 原生文本层，再调用 Qwen 抽取字段；代码层负责缓存、汇率换算、字段归一、合并 JSON/Excel，以及审计对比。MinerU 默认按 PDF 36 路并发，Qwen 结构化默认 16 路总并发；固收与权益同时存在时按 8+8 并行执行。

Key、API URL、模型和并发统一设置在外置状态目录 `config.ini`。普通用户双击
`配置API.bat` 即可打开。环境变量仍可作为临时覆盖：

```powershell
$env:QWEN_API_KEY = "..."
$env:MINERU_API_TOKEN = "..."
```

单个 PDF：

```powershell
python code\structured_data\extract_fixed_income.py data\<run>\<file>.pdf
```

批量处理并合并：

```powershell
python code\run_all_fixed_income.py
```

合并输出在：

```text
data/<run>/structure_data/固收_基金提取结果.xlsx
data/<run>/structure_data/_work/fixed_income/
```

最终字段包括基金名称、报告月份、规模（亿港币）、组合久期、组合到期收益率、信用评级、前十大/主要持仓、久期分布、地区分布和派息信息。固收 Excel 包含 `基金汇总`、`前十大持仓`、`久期分布矩阵`、`地区矩阵`；两个矩阵中的浅黄色空白表示该基金未披露或无法可靠配对该项目。可用审计入口复核当前结果：

```powershell
python code\structured_data\audit_fixed_income_outputs.py --pdf-dir data\<run>\factsheet\固收 --json-dir data\<run>\structure_data\_work\fixed_income\per_pdf
```

## 权益月报结构化

权益 PDF 结构化同样默认使用 MinerU 文本抽取，并用 MinerU 裁出的图表图片补全地区和行业配置，入口为：

```powershell
python code\run_all_equity_fund.py
```

默认会在最新下载批次中同时处理 `权益` 和 `中国精选`；两类使用相同字段定义，但分别生成
`权益_基金提取结果.xlsx` 和 `中国精选_基金提取结果.xlsx`。相同基金名或内容完全相同的 PDF
会在调用 API 前去重，并按原始目录归入对应结果。

要一次运行 `固收`、`权益`、`中国精选` 三个实际目录并获得三个 Excel：

```powershell
python code\run_all_structured_data.py
```

三个 Excel 会集中写入 `data/<run_id>/structure_data/`。

也可以指定某个批次：

```powershell
python code\run_all_equity_fund.py data\<run>\权益
```

输出在：

```text
data/<run>/structure_data/_work/equity/per_pdf
data/<run>/structure_data/_work/equity/combined
```

最终字段包括基金名称、报告日期、规模（亿港币）、前十大或主要持仓及比例、地区分布及比例、行业分布及比例、派息信息。Excel 会拆成 `基金汇总`、`前十大持仓`、`地区矩阵`、`行业矩阵` 四个 sheet，方便做透视表和图表。矩阵 sheet 会在代码层合并常见同义项，例如 `USA` 和 `美国` 统一到 `美国`，`资讯科技` 和 `信息技术` 统一到 `信息技术`。地区和行业明细仍会保留在 CSV 与 combined JSON 中，方便审计。

地区和行业分布只从明确标题图表或表格抽取；若名称和比例无法精确配对，或出现明显重复比例/合计异常，会进入 review。可用审计入口复核：

```powershell
python code\structured_data\audit_equity_fund_outputs.py --pdf-dir data\<run>\factsheet\权益 --json-dir data\<run>\structure_data\_work\equity\per_pdf
```

## 提交范围

仓库提交脚本、运行入口、项目说明和 `爬虫list.xlsx`。下载出来的 PDF、运行报告、调试 HTML、Claude/Codex 本地迁移原始记录不会提交。
