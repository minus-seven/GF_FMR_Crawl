# P1 — 指标字典与查询语义

> 状态：Draft v0.1  
> 目标：统一 ECharts 网页图表、Tableau Desktop 工作簿、Agent 和导出报告的指标含义  
> 基础数据：港交所综合资料库、结构化基金报告、V1 导入净总回报

## 1. 通用口径

| 规则 | 定义 |
|---|---|
| 数据粒度 | `fund_id × as_of_date × metric_name`；持仓和分布再增加证券或分类维度 |
| 时间 | 月末日期表示该月截止日；报告期以文档披露日期为准 |
| 金额 | 数据库保存原值和标准金额；默认展示币种为 HKD |
| 比例 | Storage store as decimal, `0.012` 表示 `1.2%` |
| 空值 | 未披露、未解析、未导入都以 `NULL` 或质量状态表示，不能转为 0 |
| 快照 | 所有查询必须绑定 `snapshot_id` |
| 版本 | 每个派生指标带 `formula_id` 和 `formula_version` |

## 2. 核心规模与流量指标

| 指标代码 | 中文名称 | 定义 | 单位 | 缺失规则 | V1 来源 | V2 来源 |
|---|---|---|---|---|---|---|
| `fund_size` | 基金规模 | 指定截止日的基金资产净值 | 币种金额 | 缺失保持空 | 港交所 Excel | 港交所增量 |
| `begin_size` | 期初规模 | 比较期起点的基金规模 | 币种金额 | 任一端缺失则净流量为空 | Excel | 港交所增量 |
| `end_size` | 期末规模 | 比较期终点的基金规模 | 币种金额 | 任一端缺失则净流量为空 | Excel | 港交所增量 |
| `net_growth` | 净增长 | `end_size - begin_size` | 币种金额 | 任一端缺失则为空 | 确定性 SQL | 保持 |
| `net_growth_rate` | 单月增速 | `net_growth / begin_size` | 小数 | 期初为 0 或缺失时为空 | 确定性 SQL | 保持 |
| `total_return_rate` | 净总回报率 | `end_return_index / begin_return_index - 1` | 小数 | 指数非正、缺失或比例不在 0.1～10 时为空 | 投研导入 | Bloomberg API |
| `performance_contribution` | 业绩贡献 | `total_return_rate × begin_size` | 币种金额 | 回报率或期初规模缺失时为空 | 确定性 SQL | 保持 |
| `estimated_net_flow` | 估算净流入 | `net_growth - performance_contribution` | 币种金额 | 任一输入缺失时为空 | 确定性 SQL | 保持 |
| `net_inflow` | 净流入 | `max(estimated_net_flow, 0)` | 币种金额 | 估算值为空时为空 | 确定性 SQL | 保持 |
| `net_outflow` | 净流出 | `min(estimated_net_flow, 0)` | 币种金额 | 估算值为空时为空 | 确定性 SQL | 保持 |
| `net_inflow_ratio` | 净流入占净增长 | `net_inflow / net_growth` | 小数 | `net_growth = 0` 或缺失时为空 | 确定性 SQL | 保持 |
| `net_outflow_ratio` | 净流出占净增长 | `net_outflow / net_growth` | 小数 | `net_growth = 0` 或缺失时为空 | 确定性 SQL | 保持 |
| `end_share` | 期末占比 | `fund end_size / selected scope end_size` | 小数 | 分母缺失或为 0 时为空 | 确定性 SQL | 保持 |
| `begin_share` | 期初占比 | `fund begin_size / selected scope begin_size` | 小数 | 分母缺失或为 0 时为空 | 确定性 SQL | 保持 |

### 2.1 示例

```text
begin_size = 100 亿港币
end_size = 112 亿港币
total_return_rate = 0.03

net_growth = 112 - 100 = 12
performance_contribution = 0.03 × 100 = 3
estimated_net_flow = 12 - 3 = 9
net_inflow = 9
```

如果 `total_return_rate` 没有导入，则：

- `performance_contribution`、`estimated_net_flow`、`net_inflow`、`net_outflow` 都为空；
- 仍然可以显示 `net_growth = 12`；
- 页面必须提示“净流入待收益数据”。

## 3. 覆盖率与数据质量指标

| 指标代码 | 中文名称 | 公式 | 分母 | 备注 |
|---|---|---|---|---|
| `scope_count` | 任务范围基金数 | `COUNT(job_fund_scope)` | — | 任务开始时冻结 |
| `download_coverage` | 下载覆盖率 | `successful_downloads / scope_count` | 冻结范围 | 替代财经文件是否计入由 `document_policy` 决定 |
| `parse_coverage` | 解析覆盖率 | `successful_parse / scope_count` | 冻结范围 | 本地降级解析是否计入需标记质量 |
| `structure_coverage` | 结构化覆盖率 | `valid_structured_funds / scope_count` | 冻结范围 | 仅有 PDF 不算结构化成功 |
| `end_to_end_coverage` | 端到端覆盖率 | `query_ready_funds / scope_count` | 冻结范围 | 90% 验收使用此指标 |
| `freshness_days` | 数据新鲜度 | `snapshot_date - report_date` | — | 报告期和发布日分别展示 |
| `missing_rate` | 缺失率 | `missing_count / scope_count` | 冻结范围 | 不把缺失当零 |
| `review_rate` | 待复核率 | `review_required_count / scope_count` | 冻结范围 | 区分阻断性和提示性 |

### 3.1 覆盖率发布门槛

V1 默认规则：

```text
publishable =
  end_to_end_coverage ≥ 0.90
  AND no blocker-level quality issues
  AND manifest_valid = true
```

当任务存在已登记的 SKIP 且覆盖率达标时，可发布为：

```text
status = completed
outcome = completed_with_gaps
```

是否允许低于 90% 的人工强制发布不在本版默认规则内；若后续需要，必须单独增加权限和审计。

## 4. 基金分层口径

### 4.1 被动与主动

| 值 | 规则 |
|---|---|
| `passive` | `management_style` 为被动，或来源标签明确为指数、ETF、指数追踪 |
| `active` | `management_style` 为主动 |
| `unknown` | 来源缺失，不用默认值猜测 |

### 4.2 资产类别与标签

- 资产类别使用 `fund_classifications.level_1..level_4`；
- 标签使用 `fund_tags`，同一基金可拥有多个标签；
- 统计按 `fund_id` 去重，不按标签行数重复计数；
- 多标签基金进入多个筛选视图时，各视图可重复出现，但全市场汇总必须按基金去重。

### 4.3 伞与份额

- 基金级统计按 `fund_id`；
- 上市代码和交易币种按 `share_class_id`；
- 同一基金多个份额不能被重复加总为多个基金；
- 伞级文档可关联多个基金，但报告章节和基金身份必须通过 `DOCUMENT_SUBJECTS` 确认。

## 5. 报告结构化指标

### 5.1 权益基金

| 指标代码 | 中文名称 | 数据类型 | 来源 |
|---|---|---|---|
| `top10_weight` | 前十大持仓合计权重 | decimal | 持仓表汇总 |
| `region_weight` | 地区配置 | decimal | 地区分布 |
| `industry_weight` | 行业配置 | decimal | 行业分布 |
| `cash_weight` | 现金权重 | decimal | 报告或结构化字段 |
| `holdings_count` | 披露持仓数量 | integer | 持仓表 |

### 5.2 固收基金

| 指标代码 | 中文名称 | 数据类型 | 来源 |
|---|---|---|---|
| `effective_duration` | 修正久期 | decimal | 报告结构化字段 |
| `average_yield` | 到期收益率 | decimal | 报告结构化字段 |
| `credit_rating_weight` | 信用评级分布 | decimal | 评级分布 |
| `maturity_weight` | 期限分布 | decimal | 期限分布 |

### 5.3 结构与校验

- 比例字段统一返回 decimal；
- 权重合计允许 0.98～1.02 的展示误差，超出进入 review；
- 原始披露值和标准化值都保留；
- `fund_metrics`、`fund_holdings` 和 `fund_allocations` 都要求 `source_page`；
- 同一指标可以保留多个候选值，发布查询只使用 `approved` 或 `preferred`。

## 6. 查询语义

### 6.1 查询请求

Agent 只能调用注册的指标工具，不允许自由拼接指标公式。示例：

```json
{
  "metric": "net_inflow",
  "fund_scope": {
    "tag": "权益"
  },
  "period": {
    "start": "2026-07-31",
    "end": "2026-08-31"
  },
  "group_by": [
    "asset_category_1"
  ],
  "snapshot_id": "batch_20261008_001"
}
```

### 6.2 返回结构

```json
{
  "metric": "net_inflow",
  "formula_id": "net_inflow.v1",
  "unit": "HKD_100M",
  "scope_count": 775,
  "eligible_count": 683,
  "missing_count": 92,
  "rows": [
    {
      "asset_category_1": "权益",
      "value": 12.4,
      "fund_count": 320
    }
  ],
  "quality": "partial"
}
```

### 6.3 空值与分母

- `fund_count` 是范围内基金数；
- `eligible_count` 是参与该指标计算的基金数；
- `missing_count = scope_count - eligible_count`；
- 不输出“未知=0”的行；
- 如果所有基金都缺少数值，返回空结果和质量状态，不返回 0；
- 分项汇总不等于全市场时，优先检查缺失或币种换算，不自动补齐。

## 7. 示例 SQL

### 7.1 选定范围的净增长

```sql
SELECT
  e.fund_id,
  e.fund_size AS end_size,
  b.fund_size AS begin_size,
  e.fund_size - b.fund_size AS net_growth
FROM fund_scale_history e
JOIN fund_scale_history b
  ON b.fund_id = e.fund_id
 AND b.as_of_date = :begin_date
WHERE e.as_of_date = :end_date
  AND e.source_batch_id = :snapshot_id
  AND b.source_batch_id = :snapshot_id
  AND e.fund_size IS NOT NULL
  AND b.fund_size IS NOT NULL;
```

### 7.2 分类汇总

```sql
SELECT
  c.level_1 AS asset_category,
  COUNT(DISTINCT f.id) AS fund_count,
  SUM(s.end_size) AS end_size,
  SUM(s.end_size - s.begin_size) AS net_growth
FROM funds f
JOIN fund_classifications c
  ON c.fund_id = f.id
LEFT JOIN fund_size_snapshot s
  ON s.fund_id = f.id
 AND s.snapshot_id = :snapshot_id
WHERE f.id = ANY(:fund_ids)
GROUP BY c.level_1;
```

`fund_size_snapshot` 是已发布只读视图，不在 API 中直接执行任意 SQL。

## 8. 指标版本与测试

每个派生指标需要：

```text
metric_code
formula_id
formula_version
description
inputs
unit
null_policy
implementation_test
effective_from
effective_to
```

单元测试至少覆盖：

- 期初和期末都为正常值；
- 期初为 0；
- 任一端缺失；
- 总回报指数异常；
- 同一基金多标签；
- 多币种；
- 分类汇总与底层基金求和能对账；
- 空集；
- 历史快照与当前快照分开查询。
