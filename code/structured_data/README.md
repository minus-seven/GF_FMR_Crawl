# 固收月报结构化抽取

报告类型、语言/版式识别和安全纠错由 `report_contract.py` 统一提供。它覆盖月报、半年报、年报
以及简体/繁体/英文、HTML/Markdown/原生文字/OCR 混合输入；最终结果的
`disclosure_info.report_contract` 和 `disclosure_info.correction_audit` 保存识别及修正依据。
完整字段关键词、历史失败根因和纠错流程见
[`docs/refinement/13-report-structured-robustness.md`](../../docs/refinement/13-report-structured-robustness.md)。

这套工具默认先用 MinerU 精准 `vlm` 把 PDF 解析为 Markdown、`content_list.json` 和图表图片，再把这些产物与 PDF 原生文本层合并后交给 Qwen 抽取结构化字段。批处理结果按批次和类别保存在 `data/<run_id>/markdown/<类别>/`，同一目录内按 PDF SHA-256 复用。

旧的 DashScope `file_id` 路径仍可通过 `--source file-id` 使用；`--source auto` 会在 MinerU 失败时自动回退。

模型只负责抽取原始字段；基金规模换算由代码层调用汇率 API 完成。最终 JSON 只保留：

```text
fund_name
report_date
fund_size_hkd_100m
portfolio_duration_years
portfolio_ytm_percent
credit_rating
dividend
```

固收结果还会保存 `top_holdings`、`top_holdings_total_percent`、`duration_distribution` 和
`geographic_allocation`，并在 Excel 中生成 `前十大持仓`、`久期分布矩阵`、`地区矩阵`。
矩阵中的浅黄色空白表示未披露或无法可靠配对；久期分布合计明显不完整、地区合计超过合理上限，
或地区分布明显由持仓所在地推导时，代码会留空而不是保留可疑数值。

字段级来源、模型中间产物、兜底顺序和回溯口径见
[`docs/refinement/10-structured-field-lineage.md`](../../docs/refinement/10-structured-field-lineage.md)；
按字段固定链路、压低思考与重复调用 Token 的设计见
[`docs/refinement/11-field-pipeline-hardening.md`](../../docs/refinement/11-field-pipeline-hardening.md)。

准确率加固：

- 默认开启本地优先标量预抽取（`local_first.py`）：规模、日期、久期、YTM、信用评级先用
  `content_list`/Markdown/原生文字层的页块证据解析。`FACTSHEET_LOCAL_FIRST=shadow` 只记录证据，
  `enforce` 才把高置信值合并进结果；`off` 完全关闭。
- 本地结果写入 `_meta.local_first`，合并后的字段证据写入 `field_evidence.local_first`，包含
  `rule/status/confidence/page/block/quote`。`absent` 表示原文无锚点，`unparsed` 表示有锚点但
  无法可靠解析，二者都不会被猜值。
- 默认使用紧凑 MinerU 上下文：用 `[PAGE n]` + 正文/表格投影替代重复的 `content_list` JSON，
  再用 `[MARKDOWN_SUPPLEMENT]` 补回落掉的段落。`FACTSHEET_CONTEXT_MODE=full` 可回退旧上下文。
- 规模、久期、收益率、信用评级等关键字段会保留在 meta 的 `raw_model_output` 里用于排查。
- 默认关闭模型 thinking，以减少延迟和输出 Token；如需开启，设置
  `FACTSHEET_STRUCTURED_THINKING=true`。如需限制思考预算，可传
  `--thinking-budget <tokens>`。
- 如果规模为空、信用评级为空、久期/收益率缺失或明显冲突，会用同一份 MinerU 文本做一次核心字段二次核对。
- 地区、行业或久期矩阵为空或明显异常时，先用当前主模型 `deepseek-flash` 定向重读 MinerU Markdown；仍有缺失时才交给 `deepseek-v4-pro` 核对残余字段。
- 固收的三个复杂矩阵视觉复核共享一次图片请求；只有合并请求未返回对应字段时才逐字段回退，
  避免同一组图片被重复发送三次。
- 所有结构化 Chat 请求共享固定 system 前缀，并默认把完整报告上下文放在抽取指令之前，
  让同一 PDF 的多次调用共享可缓存前缀；调用明细记录 `cache_hit_tokens` 和
  `cache_miss_tokens`。如需回退旧的 prompt-first 顺序，设置
  `FACTSHEET_CACHE_FRIENDLY_CONTEXT=0`。
- 视觉复核优先使用 MinerU 已裁出的图表/表格图片，其次使用基金摘要区域裁剪，最后才
  回退整页图；单次最多发送 12 张，避免 artifacts 较多时放大图片 Token。
- 如果组合久期、到期收益率或信用评级仍缺失，会使用 `deepseek-flash` 检查 MinerU 裁出的相关图表图片；评级分布也会由代码按评级顺序计算中位评级。
- 权益和中国精选工作簿的主要持仓输出 `Ticker` 列。程序先读取 `data/_cache/equity_security_master.json` 的跨批次高置信度缓存，按规范化基金披露名称和 Ticker 别名联合命中；中文名称加有效报告 Ticker 直接采用，其余未命中证券先做无 thinking、无联网的轻量核对，只有仍不确定的少数持仓才启用 thinking 和联网工具。写入缓存时同时保存中文名称和标准化 Ticker 别名，并拒绝 ISIN、CUSIP、SEDOL 和债券代码。固收不输出 Ticker，完整保留报告中的债券持仓名称和比例。
- 地区和行业矩阵在写入 Excel 前统一处理中英文、脚注和常见同义项；固收久期列按期限自然递增排列，`10年以上` 位于 `7年以上` 之后。
- 固收/货币基金报告日期优先保留 PDF 原文完整截至日 `YYYY-MM-DD`；原文只有月份时才输出 `YYYY-MM`。数据库对两种格式均可索引。
- 每次模型调用的 token 会记录在 `.meta.json` 的 `_meta.token_usage.calls`，批处理结束会打印整批 `input_tokens`、`output_tokens` 和 `total_tokens`。

成功时会同时写：

```text
<pdf>.fixed_income.json       # 最终 7 字段结果
<pdf>.fixed_income.meta.json  # 调用状态、file_id、校验信息
```

货币基金沿用同一证据契约，但由 `money_market_prompt.md` 抽取收益率、WAM/WAL、流动资产、资产类型、银行敞口和持仓：

```text
<pdf>.money_market.json       # 货币基金结构化结果
<pdf>.money_market.meta.json   # 调用状态、来源和校验信息
```

## 配置

运行时、依赖和服务启停统一见 [`docs/development/59-runtime-startup-and-dependency-runbook-20261126.md`](../../docs/development/59-runtime-startup-and-dependency-runbook-20261126.md)。配置文件位于外置状态目录；代码包只提供 `config.example.ini`。

推荐在平台右上角“模型设置”中配置。也可在 `$FACTSHEET_CONFIG` 指向的外置 `config.ini` 中配置：

```ini
[api]
deepseek_base_url = https://api.deepseek.com
deepseek_api_key = 你的 DeepSeek Key

[models]
primary_model = deepseek-flash
fallback_model = deepseek-v4-pro
vision_model = deepseek-flash

[processing]
structured_provider = deepseek
structured_thinking = false
```

也可以用环境变量：

```powershell
$env:DEEPSEEK_BASE_URL = "https://api.deepseek.com"
$env:DEEPSEEK_API_KEY = "你的 DeepSeek Key"
$env:FACTSHEET_STRUCTURED_PROVIDER = "deepseek"
$env:FACTSHEET_STRUCTURED_THINKING = "false"
$env:FACTSHEET_LOCAL_FIRST = "shadow"   # off | shadow | enforce
$env:FACTSHEET_CONTEXT_MODE = "compact" # compact | full
```

### 上下文缓存与原生文本层

同一份 PDF 通常要经过主抽取、字段核对和矩阵补提多次模型调用。默认策略：

- **上下文优先**：把完整 MinerU 报告上下文放在抽取指令之前，使同一 PDF 的多次请求
  共享稳定的可缓存前缀。设置 `FACTSHEET_CACHE_FRIENDLY_CONTEXT=0` 可回退旧顺序。
- **原生文本层按需追加**：MinerU Markdown 足够完整时，不再重复追加 PDF 原生文本层。
  `FACTSHEET_PDF_TEXT_LAYER=auto`（默认）仅在 Markdown 为空、少于 500 字符，或明显短于
  原生文本时追加；`always` 保留旧行为，`never` 完全关闭原生文本层。

这两个开关只影响发送给模型的上下文，不改变字段定义、校验规则或输出结构。

### 管理人 profile

Web 任务进入结构化阶段时，Worker 会按任务冻结范围生成
`structure_data/_work/manager_map.json`（基金名/PDF 路径 → 管理人），并通过
`FACTSHEET_MANAGER_MAP` 传给抽取子进程。手动运行时可自行设置该变量。
管理人策略默认从 `data/_cache/manager_profiles.json` 读取，也可用
`FACTSHEET_MANAGER_PROFILES` 指向其他文件。可参考
`code/structured_data/manager_profiles.example.json`：

```json
{
  "version": 1,
  "profiles": {
    "摩根基金": {
      "aliases": {
        "portfolio_duration_years": ["組合久期", "存續期", "Average duration"],
        "portfolio_ytm_percent": ["到期收益率", "YTM"]
      },
      "absent_fields": ["duration_distribution"],
      "skip_refinements": ["matrix_flash"],
      "vision_review": false,
      "layout_id": "jpm-hk-factsheet-2026",
      "field_pipelines": "pipelines/jpm-hk-factsheet-2026.json",
      "drift_threshold": 0.8
    }
  }
}
```

- `aliases` 用于二次核对提示；当前报告出现别名时，不会因历史缺失而跳过该字段。
- `absent_fields` 跳过该管理人稳定不披露字段对应的矩阵/视觉复核；字段本身仍会保留在输出中。
- `skip_refinements` 支持 `matrix_flash`、`matrix_fallback`、`matrix`、`field`、`vision`。
  跳过 flash 时会直接把剩余字段交给 fallback 模型。
- `vision_review: false` 关闭该管理人的图片复核。
- 每次抽取的 `_meta.manager_profile` 会记录实际命中的管理人、缺失字段和跳过的复核。

### 字段流水线契约

确定性标量规则位于 `code/structured_data/pipelines/`：`_generic.json` 是无画像时的安全回退，
管理人 profile 可通过 `layout_id`、`field_pipelines`、`drift_threshold` 选择专用契约。
`field_pipeline.py` 会先计算锚点覆盖率；低于阈值即回退 `_generic`，并在
`_meta.pipeline` 记录 `layout_drift`、缺失锚点和 `fallback_reason`。`_meta.field_provenance`
记录规模、日期、久期、收益率、评级的确定性尝试；`_meta.call_decisions` 记录合并文本补提
和视觉复核是否调用及其 `skip_reason`。

### 输出与证据契约

紧凑 MinerU 上下文会标记 `[PAGE n]` 和 `[BLOCK m]`。模型只返回
`{"page_idx": n, "block_index": m}`，代码在最终 `data.field_evidence` 回填来源、块类型和可读
引文，并在 `_meta.evidence_contract` 记录有效/无效引用。Qwen 调用启用严格 JSON Schema；其余
兼容供应商使用同一字段子 Schema 与 JSON object 回退。输出预算与数组上限集中在
`evidence_contract.py`，证据引用错误最多进行一次 300 token 的定向重试。

### 成本 A/B

`ab_extraction_cost.py` 用同一批已缓存 MinerU 产物的 PDF 对比不同上下文策略，逐份结果
可断点续跑，并输出 `ab_report.json` / `ab_report.md`：

```bash
python code/structured_data/ab_extraction_cost.py \
  --pdf-root data/<run_id>/factsheet \
  --mineru-root data/<run_id>/markdown \
  --limit 20 --out data/_ab/run_20261028
```

上面的命令只生成 `ab_manifest.json` 样本清单。确认样本后加 `--execute` 实际调用模型：

```bash
python code/structured_data/ab_extraction_cost.py \
  --pdf-root data/<run_id>/factsheet \
  --mineru-root data/<run_id>/markdown \
  --limit 20 --variants baseline,optimized \
  --execute --out data/_ab/run_20261028
```

变体含义：

- `baseline`：prompt-first + 始终追加原生文本层（旧行为）。
- `cache`：context-first + 始终追加原生文本层。
- `text_layer`：prompt-first + 自动省略冗余文本层。
- `optimized`：context-first + 自动省略冗余文本层（默认新行为）。
- `local_first`：在 optimized 基础上启用本地优先合并（`enforce`），但仍使用完整上下文。
- `compact`：在 optimized 基础上启用紧凑上下文，本地优先保持 `shadow`。
- `local_compact`：紧凑上下文 + 本地优先 `enforce`。

报告会统计 token、输入/输出、缓存命中/未命中、校验问题数、字段完整率和字段差异。
优先使用**未跑过结构化**的冷样本，否则 baseline 可能命中历史缓存，缓存收益会被低估。
`--resume`（默认开启）会复用 `<out>/<variant>/results/` 下的逐份结果；需要重跑时加
`--no-resume`。

### 模型接口

代码会调用 MinerU 解析 API，以及 OpenAI-compatible 模型接口：

```text
POST <base>/chat/completions
```

## 单个 PDF

```powershell
python code\structured_data\extract_fixed_income.py data\20260708105353\固收\东亚联丰亚洲策略债券基金.pdf
```

默认主模型为 `deepseek-flash`，文本备用为 `deepseek-v4-pro`，视觉模型为 `deepseek-flash`。

常用切换：

```powershell
# MinerU 失败时回退旧 PDF file_id 流程
python code\structured_data\extract_fixed_income.py <pdf> --source auto

# 忽略 MinerU 缓存并重新解析
python code\structured_data\extract_fixed_income.py <pdf> --mineru-force
```

## 批量处理

推荐使用项目级入口，默认自动处理最新 `data/<run_id>/factsheet/固收`：

```powershell
python code\run_all_fixed_income.py
```

如果要指定某个批次：

```powershell
python code\run_all_fixed_income.py data\20260717143244\固收
```

输出默认放在：

```text
data/<run_id>/structure_data/_work/fixed_income/per_pdf
data/<run_id>/structure_data/_work/fixed_income/combined
```

底层批处理脚本也可以直接运行：

```powershell
python code\structured_data\run_batch.py data\20260708105353\固收 --skip-existing
```

MinerU 默认按独立 PDF 36 路并发，结构化模型默认 32 路总并发；可通过
`FACTSHEET_MINERU_WORKERS` 和 `FACTSHEET_MODEL_WORKERS` 覆盖。单份 PDF
内部的 MinerU、文本抽取和视觉补提仍按阶段串行。失败会写入 meta 文件并
继续处理其他 PDF。

MinerU Agent 模式额外受 500 次/分钟结果查询限制，因此默认并发上限为 18，
并保留 450 次/分钟结果查询窗口。触发 429 后整批共享冷却，优先采用
`Retry-After`，再以带抖动的指数退避重试原任务；默认最多 8 次。

`run_all_mineru.py` 会按 PDF SHA-256 扫描 `data/**/markdown/**/manifest.json`
复用历史解析缓存，优先 Precise，其次 Agent；复制缓存后自动改写 manifest
中的 Markdown、artifact 和 ZIP 路径。

权益入口 `python code\run_all_equity_fund.py` 默认同时纳入最新批次的 `权益`、`中国精选` 目录，并在模型调用前按 PDF
内容哈希和基金名去重。两个目录使用相同提取字段，但分别输出 `权益_基金提取结果.xlsx` 和
`中国精选_基金提取结果.xlsx`。

统一入口 `python code\run_all_structured_data.py` 会一次运行 `固收`、`权益`、`中国精选`，并将三个 Excel
集中写入 `data/<run_id>/structure_data/`。

## 合并到 Excel

```powershell
python code\structured_data\combine_fixed_income.py data\<run>\structure_data\_work\fixed_income\per_pdf --outdir data\<run>\structure_data\_work\fixed_income\combined --excel-outdir data\<run>\structure_data
```

输出：

```text
data/<run>/structure_data/fixed_income_excel.xlsx
data/<run>/structure_data/_work/fixed_income/combined/fixed_income_combined.json
data/<run>/structure_data/_work/fixed_income/combined/fund_summary.csv
```

Excel 只有一个 `基金汇总` sheet，一只基金一行，适合后续做可视化。
