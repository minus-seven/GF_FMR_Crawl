# SQL 优先问答后端与证据契约

> 周二 2026-11-10 交付；状态：V0.1 已实现，供基金知识库 Agent、前端问答页和验收脚本共用。

## 1. 目标与边界

问答入口先读取当前已发布快照中的结构化数据库，再把无法由字段契约确定的问题交给页级知识检索。这样可以保证规模、收益率、流动性、久期、到期收益率、成立日期和持仓等确定性问题不依赖模型自由发挥。

本阶段实现的是后端确定性闭环，不包含 Qwen 生成式归纳。对尚未定义为结构化字段的自然语言问题，接口返回可引用的原文片段并明确 `evidence_only`，而不是伪造答案。Qwen/Agent 的受控归纳放在后续阶段，必须复用本契约的 claim 和 evidence 绑定。

## 2. 接口

`POST /api/chat`

请求最小示例：

```json
{
  "question": "比较 BJK348 与 BMV635 2026年6月30日的日流动资产",
  "fund_codes": ["BJK348", "BMV635"],
  "mode": "hybrid",
  "limit": 8
}
```

请求字段：

| 字段 | 约束 | 说明 |
|---|---|---|
| `question` | 1–2000 字符 | 原始问题，基金代码和月份可直接写在问题中 |
| `fund_codes` | 最多 100 个 | 可选的显式范围；与问题中代码合并去重 |
| `report_period` | ISO 日期，可选 | 只按年月匹配；例如 `2026-09-01` 匹配 2026 年 9 月报告 |
| `snapshot_id` | 已发布快照 UUID | 不传则读取当前快照；传入 staging/未发布快照会拒绝 |
| `mode` | `fulltext`/`vector`/`hybrid` | 仅用于非结构化证据检索 |
| `query_vector` | 最多 1024 维 | vector/hybrid 的可选查询向量 |
| `limit` | 1–20 | 返回的原文片段上限 |

响应由 `ApiResponse[ChatResponseData]` 包裹，核心字段为：

| 字段 | 取值 | 语义 |
|---|---|---|
| `status` | `answered` | 全部请求范围有结构化证据支持 |
| `status` | `insufficient_evidence` | 缺基金、缺字段、缺报告期或证据被隐藏；禁止用模型补齐 |
| `status` | `evidence_only` | 已找到原文片段，但当前后端不对未定义字段生成归纳 |
| `route` | `sql` | 指标或持仓由 SQL 直接回答 |
| `route` | `knowledge_search` | 由页级知识检索提供证据片段 |
| `structured_values` | 数组 | SQL 命中的原始值、单位、报告期和质量状态 |
| `claims` | 数组 | 回答中的事实声明；每个声明必须列出 `evidence_ids` |
| `evidence` | 数组 | 可审计的结构化记录或文档切片 |
| `limitations` | 数组 | 缺失、质量告警、隐藏来源和未启用模型归纳等原因 |

## 3. SQL 优先路由

```text
问题
 ├─ 识别基金代码/唯一基金名称、报告年月
 ├─ 识别结构化字段或持仓意图
 │   ├─ 指标/文本字段 → FundMetric SQL
 │   ├─ 前五/前十大/主要持仓 → FundHolding SQL
 │   └─ 未定义字段 → knowledge_search
 └─ 生成 claim → 绑定 evidence_ids → 返回
```

当前已覆盖的结构化字段包括：

- 年化收益率、年化波动率；
- 日/周流动资产；
- 加权平均存续期（WAL）、加权平均到期日（WAM）；
- 到期收益率（YTM）、组合久期；
- 股债比、基金规模、成立日期；
- 投资目标、投资策略；
- 前五大或前十大持仓（按权重降序）。

报告期只按 `year/month` 比较，不把“9 月月报”和 9 月 30 日误判为不同月份。未指定报告期时，每只基金取该字段在当前快照中最新的可见记录。

## 4. Evidence 契约

### 4.1 结构化记录

`evidence_type=structured_record` 时：

- `evidence_id` 使用 `metric:<uuid>` 或 `holding:<uuid>`，对应正式数据库行；
- `document_version_id` 必须来自该记录的来源版本；
- `page_number` 只在数据库确实保存页码时填写，不为结构化值臆造 PDF 页码；
- `content` 是可读的字段和值，例如 `annualized_return_percent=3.95%`；
- `source_label` 由来源版本和来源绑定生成，区分月报、半年报、年报及补充来源；
- `review_status`/`quality_status` 原样进入 `structured_values`，质量告警只能披露，不能静默修正。

### 4.2 文档切片

`evidence_type=document_chunk` 时：

- 必须有 `document_version_id`、`page_number`、`content_hash` 和原文 `content`；
- 如索引已有章节标题和 citation tier，必须原样保留；
- 文档切片是证据，不等于模型已经回答。当前实现使用 `evidence_only` 明确这一点。

### 4.3 Claim 绑定

- 每个事实性 `claim` 至少绑定一个 `evidence_ids`；
- `evidence_ids` 必须出现在同一响应的 `evidence` 数组中；
- SQL 直接命中的比较结果仍绑定每个基金各自的结构化证据；
- 无证据时返回 `insufficient_evidence`，不得生成无引用的数字、排序或结论；
- 隐藏来源及其派生记录不参与默认问答，但不会从数据库和知识库删除。

## 5. 快照和可见性

问答只读取 `DatasetBatch.status=published` 的快照。指定快照必须再次校验已发布；当前快照由 `get_current_snapshot()` 确定。

来源隐藏通过 `FundReportSource.is_visible=false` 和其 `metadata_json.document_version_id` 关联过滤。删除来源后的派生记录若已不在当前发布快照中，也不会被问答读到。多基金共用的原始文档不会因为单基金来源操作而被物理删除。

## 6. 缺证据与错误语义

| 场景 | 返回 |
|---|---|
| 基金代码不存在/无法确定唯一范围 | `insufficient_evidence` + `fund_not_found` 或 `fund_scope_required` |
| 指标没有记录 | `insufficient_evidence` + `field_not_disclosed:<field>` |
| 比较范围只有部分基金有记录 | `insufficient_evidence`，同时返回已有值和 `missing_field:<field>:...` |
| 持仓不存在 | `insufficient_evidence` + `missing_holdings:<fund>` |
| generic 文本问题 | `evidence_only` + `model_summarization_not_enabled` |
| vector 检索不可用 | HTTP 503 `E_KNOWLEDGE_VECTOR_UNAVAILABLE` |
| 参数/向量不合法 | HTTP 422 `E_KNOWLEDGE_QUERY_INVALID` |
| 指定快照未发布 | HTTP 403 `E_KNOWLEDGE_SNAPSHOT_NOT_PUBLISHED` |

## 7. 实现位置与验收

| 位置 | 内容 |
|---|---|
| `platform/api/app/schemas/chat.py` | 请求、响应、claim、evidence Pydantic 契约 |
| `platform/api/app/services/chat.py` | 意图识别、SQL 查询、来源过滤、证据组装 |
| `platform/api/app/api/routes/chat.py` | `POST /api/chat` 及稳定错误码映射 |
| `platform/api/tests/test_chat_contract.py` | 基金范围、报告期、字段意图和请求约束测试 |

周二验收门：

1. SQL 指标问题返回 `route=sql`，并且每条 claim 可回指结构化证据；
2. 只有部分基金有值时不返回“全部回答成功”；
3. 隐藏来源不出现在默认响应；
4. 未定义文本问题返回 `evidence_only`，不冒充模型摘要；
5. OpenAPI、Python 编译、聚焦测试和 API 冒烟均通过。

