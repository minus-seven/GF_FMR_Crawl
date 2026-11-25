# 数据模型与分析口径

> 文档类型：数据架构补充  
> 适用范围：V1 Mac 单机版、V2 Windows 共享版  
> 数据依据：`HKEX基金综合资料库_完整_20261008.xlsx` 与 `月报底稿20261008_已刷_v2.xlsx`

数据源职责固定为：HKEX 综合资料库提供基金身份、关系、分类、标签、名称历史和
规模快照；`爬虫list.xlsx` 只提供月报页面链接、下载命令和来源适配信息。

## 1. 数据建模原则

1. 港交所 Excel 是导入文件，PostgreSQL 是正式数据源。
2. 主数据、任务、文档、结构化结果、检索索引和模型调用分别建模。
3. 数据库保存业务标识和元数据，PDF 与 Markdown 保存为文件。
4. 所有可回溯数据必须带 `batch_id`、来源 URL、文件哈希和版本。
5. 空值不等于零。规模、收益或状态缺失时必须保持缺失状态。
6. 解析、模型和业务规则版本必须随结果保存。
7. 多值标签拆成关联表，不把逗号分隔字符串直接作为分析主键。
8. crawler 补充记录必须保留来源行和匹配状态，不能因匹配失败而丢弃。

## 2. 港交所主数据映射

输入表 `主数据` 有 2,479 条基金记录、67 列。

### 2.1 关键规模

| 项目 | 当前值 |
|---|---:|
| 基金记录 | 2,479 |
| 唯一基金代码 | 2,479 |
| 基金管理人 | 187（Tail 空格规范化后） |
| 有中央编号的伞 | 306 |
| 伞编号为空 | 149 |
| `运作状态` 非空 | 171 |
| 2026-08-31 规模非空 | 926 |

伞编号为空或运作状态为空都不能自行推断。数据库应分别使用：

```text
umbrella_id = NULL
operation_status = UNKNOWN
fund_size = NULL
```

不得写成：

```text
umbrella_id = ""
operation_status = "active"
fund_size = 0
```

### 2.2 主数据到规范表

| 输入列 | 规范字段/表 | 说明 |
|---|---|---|
| 基金代码 | `funds.fund_code` | 唯一标识，字符串 |
| ID | `fund_source_ids.external_id` | 外部复合 ID |
| 伞型基金中央编号 | `umbrellas.umbrella_code` | 可为空 |
| 伞型基金名称 | `umbrellas.name` | 与编号组合使用 |
| 基金名称 | `funds.name_cn` | 当前中文名 |
| Fund name | `funds.name_en` | 当前英文名 |
| 基金管理人 | `managers.name_cn` | 管理人中文名 |
| Fund manager | `managers.name_en` | 管理人英文名 |
| 基金分类 | `funds.regulation_category` | HKEX 监管分类原文 |
| 基金注册地 | `funds.domicile` | 地区代码/名称 |
| 证监会认可日期 | `funds.sfc_authorized_at` | 标准日期 |
| 财政年度终结日 | `funds.financial_year_end` | 仅表示财年口径 |
| 交易频密程度 | `funds.trading_frequency` | 枚举或空 |
| 基金保管人/受托人 | `funds.custodian_name` | 原文保留 |
| 基金保管人简称 | `funds.custodian_short_name` | 分析显示 |
| ESG投资主题 | `funds.esg_theme` | 可选 |
| ESG投资策略 | `funds.esg_strategy` | 可选 |
| 股份代号 | `fund_listings.stock_code` | 一只基金可能有多个代码 |
| 基础货币 | `funds.base_currency` | 标准化为 ISO 代码 |
| 管理人简称 | `managers.short_name` | 分析显示 |
| 管理人中外资 | `managers.ownership_type` | 中资、外资、合资等 |
| 产品主级别代码 | `fund_source_ids.primary_code` | 来源系统主代码 |
| 主被动 | `funds.management_style` | 主动、被动 |
| 上市分类 | `funds.listing_*`、`fund_listings.listing_type` | 同时保留基金级分类和具体股份代号 |
| 资产类别一级至四级 | `fund_classifications` | 层级结构 |
| 跟踪指数 | `funds.tracking_index` | 可为空 |
| 投资地域一级/二级 | `fund_regions` | 多值或层级关系 |
| 所属地域 | `fund_regions`（`region` 级） | 分析维度 |
| 竞品库分类 | `fund_tags` | 标签 |
| 北上、ETF互联互通、ETF互挂、MPF | `fund_tags` | 布尔式标签 |
| 运作状态 | `funds.status` | 缺失为 UNKNOWN |
| `60-40` | `fund_tags` | 特殊分析标签 |
| `AS:BO` 规模列 | `fund_scale_history` | 宽表转为月份长表 |

### 2.3 规模历史

输入表用多列表示月份，数据库应转为一行一个月：

```text
fund_scale_history
  fund_id
  as_of_date
  fund_size
  currency
  source_batch_id
```

当前 Excel 单位是“亿港币”。数据库建议同时保存：

- 原值；
- 原单位；
- 标准金额；
- 币种；
- 数据日期；
- 来源批次。

### 2.5 Crawler 补充来源

`爬虫list.xlsx` 当前有 775 行、774 个唯一“序号”。序号只在公司页内参考，
不是 HKEX 主键。775 行全部保存在 `fund_report_sources`，其中通过 HKEX
基金代码、管理人、伞、当前名称或 `改名` 历史匹配到唯一基金。重点字段：

当前 2026-10-08 数据中 775 条补充记录均已唯一匹配，其中 4 条通过 HKEX
`改名` 页的历史名称匹配。

```text
fund_report_sources
  id, fund_id, source_system, source_key, source_sheet, source_row,
  manager_name_source, fund_name_source, umbrella_name_source,
  monthly_report_url, fallback_report_url, run_command,
  match_status, match_method, match_confidence, is_active, metadata_json
```

`source_key` 使用“来源系统 + 工作表 + Excel 行号”，保证重复导入只更新同一
补充记录。`fund_id` 允许为空，未匹配或歧义记录保留并写入 `quality_issues`；
不得根据相似名称或随机 URL 推断基金身份。

`metadata_json.backfill` 保存平台补全审计：

```text
backfill.status         succeeded / skipped / failed / unsupported
backfill.script         companies/<manager>/*_fund_link.py
backfill.reason         补全跳过或失败原因
backfill.attempted_at   最近一次尝试时间
```

平台任务直接更新 `monthly_report_url` 和 `run_command`，并设置
`is_active=true`。正式 `爬虫list.xlsx` 的 I/J 不由平台写入；重复导入时，
Excel 空值不得覆盖数据库已有的有效来源值。

转换为标准金额时使用明确的 `unit_multiplier = 100000000`；前端和报表仍可按“亿港币”显示。

### 2.4 改名历史

`改名` 表有 661 条变更记录：

```text
fund_name_history
  fund_id
  old_name
  new_name
  detected_at
  source_batch_id
```

名称匹配必须按基金代码优先，名称只作为发现和校验条件。历史报告可以继续使用旧名称，但最终关联到同一 `fund_id`。

## 3. 核心数据实体

### 3.1 主数据

```text
managers
  id, name_cn, name_en, short_name, ownership_type

umbrellas
  id, umbrella_code, name_cn, name_en

funds
  id, fund_code, umbrella_id, manager_id, name_cn, name_en,
  domicile, base_currency, sfc_authorized_at, regulation_category,
  financial_year_end, trading_frequency, custodian_name, custodian_short_name,
  esg_theme, esg_strategy, tracking_index, listing_status,
  leverage_inverse_status, listed_share_class_status, api_listing_status,
  sixty_forty_classification, management_style, status, source_batch_id

share_classes
  id, fund_id, share_class_code, name, currency, isin,
  distribution_status, source_batch_id

fund_source_ids
  fund_id, source_system, external_id, primary_code, isin, metadata_json

fund_report_sources
  id, fund_id, source_system, source_key, source_sheet, source_row,
  manager_name_source, fund_name_source, umbrella_name_source,
  monthly_report_url, fallback_report_url, run_command, match_status,
  match_method, match_confidence, is_active, metadata_json

fund_listings
  fund_id, share_class_id, exchange, stock_code,
  listing_type, trading_currency

fund_classifications
  fund_id, level_1, level_2, level_3, level_4

fund_regions
  fund_id, region_level, region_name

fund_tags
  fund_id, tag_type, tag_value, valid_from, valid_to

fund_scale_history
  fund_id, as_of_date, fund_size, currency, source_batch_id

fund_name_history
  fund_id, old_name, new_name, detected_at, source_batch_id
```

### 3.2 任务与文件

```text
jobs
  id, scope_json, requested_stages, status, requested_by,
  idempotency_key, created_at, started_at, finished_at

job_steps
  id, job_id, step_type, status, total, succeeded, skipped, failed

job_attempts
  id, job_step_id, attempt_no, error_type, error_message,
  started_at, finished_at

job_events
  id, job_id, event_type, payload_json, created_at

job_locks
  lock_name, job_id, owner, acquired_at, heartbeat_at, expires_at

dataset_batches
  id, status, scope_hash, source_kind, source_sha256, coverage, requested_by,
  created_at, published_at, error_summary

documents
  id, fund_id, document_type, source_id, first_seen_at

document_versions
  id, document_id, source_url, report_date, retrieved_at,
  sha256, mime_type, size_bytes, file_path, batch_id
```

### 3.3 解析与结构化

```text
parse_runs
  id, document_version_id, parser_name, parser_version,
  artifact_path, status, error_code

fund_metrics
  id, batch_id, fund_id, report_period, document_version_id, metric_name,
  numeric_value, text_value, unit, confidence, review_status, metadata_json

fund_holdings
  id, batch_id, fund_id, report_period, security_name, ticker, weight,
  market_value, source_page, document_version_id

fund_allocations
  id, batch_id, fund_id, report_period, dimension, category, weight, unit,
  source_page, document_version_id, metadata_json

return_series
  fund_id, as_of_date, return_type, value, source, source_batch_id
```

### 3.4 知识库

```text
document_pages
  document_version_id, page_number, text, width, height

document_chunks
  id, document_version_id, page_number, section_title,
  chunk_type, content, metadata_json, content_hash

chunk_embeddings
  chunk_id, model_name, model_version, embedding
```

### 3.5 模型与 Agent

```text
model_profiles
  id, name, provider, model_id, base_url, capabilities, active

model_credentials
  id, user_id, model_profile_id, encrypted_secret,
  label, status, last_verified_at

model_calls
  id, job_id, agent_run_id, model_profile_id, credential_scope,
  input_tokens, output_tokens, estimated_cost, latency_ms, status

agent_runs
  id, user_id, snapshot_id, question, plan_json, status

tool_calls
  id, agent_run_id, tool_name, tool_version, input_json,
  output_json, status, error_message

citations
  id, agent_run_id, document_version_id, page_number,
  excerpt, claim_text, validation_status

artifacts
  id, batch_id, artifact_type, file_path, sha256,
  template_version, created_at
```

### 3.6 快照发布不变量

主数据和分析结果采用“不可变批次 + 单一发布指针”，不在发布时批量复制数据：

1. API 创建 `dataset_batches.status = staging`，任务范围写入 `scope_hash`；
2. Worker 为所有候选记录写入同一个 `batch_id`，文件写入 `data/batches/<batch_id>/`；
3. 质量门校验基金数、文件数、文档版本、解析结果和结构化结果的完整性；
4. 发布事务再次确认 `dataset_batches` 为 staging 且全局锁仍由当前任务持有；
5. API 的发布服务在同一事务中将批次状态改为 `published`，并更新唯一 `publication_state.current_batch_id`；
6. 所有用户查询、ECharts 透视、Tableau Desktop 只读视图和知识库检索都从 `current_batch_id` 解析版本；
7. 发布事务失败时指针不变，staging 记录保留但不可见；
8. 回滚只切换指针到此前已验证的 `published` 批次，不修改历史批次内容。

建议增加单行表：

```text
publication_state
  id
  current_batch_id
  published_at
  published_by
  release_version
```

该模式将数据库事务和文件目录绑定到同一 `batch_id`。如果需要对历史批次做差异分析，按两个 `batch_id` 执行只读比较，不覆盖旧记录。

## 4. 数据分析口径

以下口径来自 `月报底稿 v2` 的工作表公式，应在数据库服务中实现为可测试函数，不能在 Agent 提示词中临时解释。

### 4.1 月度规模

```text
month_size = fund_scale_history[as_of_date]
```

### 4.2 净增长

用于 `规模筛选` 的月度净增长：

```text
net_growth = end_size - begin_size
```

两个月份的规模都必须是数值，否则结果为空。

### 4.3 总回报率

使用导入或 V2 Bloomberg 提供的前后月份净总回报指数：

```text
total_return_rate = end_return_index / begin_return_index - 1
```

底稿只在比例位于 0.1 至 10 之间时计算，避免错误指数值被当成有效收益。数据库应记录相同合理性校验。

### 4.4 业绩贡献

```text
performance_contribution = total_return_rate * begin_size
```

### 4.5 净流入和净流出

```text
estimated_net_flow = net_growth - performance_contribution

net_inflow = max(estimated_net_flow, 0)
net_outflow = min(estimated_net_flow, 0)

net_inflow_ratio = net_inflow / net_growth
net_outflow_ratio = net_outflow / net_growth
```

当 `net_growth = 0` 或输入缺失时，不计算比率。

### 4.6 分类统计

底稿通过 `SUM`/`SUMIFS` 对规模、净增长、净流入和业绩贡献做筛选。数据库使用参数化 SQL：

```text
group_by:
  asset_category
  management_style
  fund_tag
  manager
  listing_status
  region
  report_period
```

输出至少包括：

- 基金数；
- 期初规模；
- 期末规模；
- 单月净增长；
- 单月增速；
- 净流入；
- 净流出；
- 业绩贡献；
- 期末占比；
- 期初占比。

## 5. 预设分析工具映射

| 底稿工作表 | 系统预设工具 | V1 | V2 |
|---|---|---|---|
| `01透视` | 多维规模与净增长透视 | 支持 | 支持 |
| `02趋势图` | 全市场或选定范围的月末规模趋势 | 支持 | 支持 |
| `03瀑布图` | 资产类别单月增量瀑布 | 支持 | 支持 |
| `04大类资产` | 大类资产规模、增速、流入和业绩对比 | 支持 | 支持 |
| `05主动权益` | 主动权益基金筛选表 | 支持 | 支持 |
| `06核心被动` | 核心被动基金筛选表 | 支持 | 支持 |
| `07杠反` | 杠杆和反向产品分析 | 支持 | 支持 |
| `08货基` | 货币基金分析 | 支持 | 支持 |
| `09固收` | 固收基金分析 | 支持 | 支持 |
| `10中资` | 中资管理人规模和变动 | 支持 | 支持 |
| `11中+外资` | 中资、外资、合资管理人对比 | 支持 | 支持 |
| `12北上` | 北上相关基金分析 | 支持 | 支持 |
| `13混合` | 混合基金分析 | 支持 | 支持 |
| `14北上内地资金` | 北上内地资金汇总 | 支持 | 支持 |

“支持”表示可重新计算、筛选和导出相同分析，不表示自动复制每个 Excel 单元格。

## 6. 输入校验

### 港交所主数据

- 基金代码不可重复；
- 基金名称不可为空；
- 伞编号和名称同时存在或同时为空；
- 日期格式可解析；
- 管理人映射可追溯；
- 分类和标签拆分后不得出现空项；
- 规模必须是数值或空值；
- 新基金、终止基金和改名基金必须产生差异报告。

### 净总回报

- 基金代码或稳定映射键必须存在；
- 日期必须与报告月末匹配；
- 回报指数必须为正数；
- 空值不得按 0；
- 异常跳变进入 `review`；
- 数据来源和导入批次必须记录。

## 7. 数据质量状态

```text
complete
missing_file
missing_metric
parse_failed
extraction_failed
validation_warning
review_required
stale
```

看板必须区分：

- 真正为零；
- 未披露；
- 未解析；
- 解析失败；
- 报告过期；
- 基金已终止。

## 8. 备份和保留

- PostgreSQL 每个批次发布后生成可恢复备份；
- 原始 PDF 和 Markdown 按哈希保存；
- 正式快照长期保留；
- failed staging 保留用于排障，并按策略定期清理；
- Agent 会话和模型调用记录按业务要求保留；
- 导出文件可重新生成，不属于唯一业务数据。
