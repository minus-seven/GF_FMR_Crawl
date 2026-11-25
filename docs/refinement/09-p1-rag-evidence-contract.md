# P1 — RAG、引用与证据协议

> 状态：Frozen v1.0（2026-10-26）  
> 目标：让问答、Agent 和原文展示共用一套可验证证据结构  
> 验收目标：问答准确率 ≥80%，原文引用正确率 ≥95%

周一冻结的数据范围、A/B 页码证据等级、报告期排除规则和数据模型见
[数据就绪与知识库方案冻结](../development/04-day1-data-readiness-and-kb-freeze.md)。

## 1. 回答类型

| 类型 | 首选路径 | 证据要求 |
|---|---|---|
| 数值事实 | 参数化 SQL | 指标版本、快照、公式 ID；可选原文页码 |
| 报告解释 | 全文 + 向量 + rerank | 文档版本、页码、原文片段 |
| 跨基金比较 | SQL + RAG | 每只基金的结果和各自来源 |
| 报告期变化 | 两个快照或两版文档的 SQL + RAG | 两期指标和对应页码 |
| 新数据请求 | Agent 计划 + 写任务 | 任务计划和副作用确认 |
| 无答案问题 | 不生成事实结论 | 返回缺失数据和可执行补救 |

## 2. 切片协议

每个 `DOCUMENT_CHUNKS`：

```json
{
  "chunk_id": "018f...",
  "snapshot_id": "batch_20261008_001",
  "fund_id": "fund_...",
  "document_version_id": "docver_...",
  "document_type": "annual",
  "report_period": "2026-03-31",
  "section_title": "投资组合",
  "page_number": 18,
  "chunk_type": "table",
  "content_hash": "sha256:...",
  "metadata": {
    "fund_name_in_document": "HSBC ...",
    "table_id": "asset_allocation",
    "unit": "percent",
    "parent_subject_page_start": 17,
    "parent_subject_page_end": 30
  }
}
```

规则：

- 切片必须带页码；
- 表格切片保留表头、单位和原页位置；
- 文档和基金的关系通过 `DOCUMENT_SUBJECTS` 确认；
- 切片内容不能跨越两个无关基金章节；
- 同一文档版本的同一内容可以有一个或多个切片，但内容哈希可追踪。

## 3. 检索协议

```text
范围过滤
→ 精确名称/代码/ISIN 匹配
→ PostgreSQL 全文检索
→ pgvector 语义召回
→ rerank
→ 证据阈值
→ claim 绑定引用
```

### 3.1 查询请求

```json
{
  "question": "该基金本期久期为什么下降？",
  "snapshot_id": "batch_20261008_001",
  "scope": {
    "fund_ids": ["fund_..."],
    "report_periods": ["2026-06-30", "2026-03-31"],
    "document_types": ["interim", "annual"]
  },
  "answer_mode": "grounded",
  "max_evidence": 8
}
```

### 3.2 检索结果

```json
{
  "evidence": [
    {
      "evidence_id": "ev_001",
      "document_version_id": "docver_...",
      "chunk_id": "chunk_...",
      "page_number": 18,
      "excerpt": "……",
      "score": 0.91
    }
  ],
  "below_threshold": false
}
```

## 4. 回答协议

```json
{
  "schema_version": "1.0",
  "snapshot_id": "batch_20261008_001",
  "status": "answered",
  "answer": "本期久期由 ... 降至 ...，主要变化来自 ...。",
  "claims": [
    {
      "claim_id": "claim_1",
      "text": "本期修正久期下降至 ...",
      "claim_type": "numeric",
      "metric_id": "effective_duration",
      "formula_version": "effective_duration.v1",
      "citations": [
        {
          "document_version_id": "docver_...",
          "page_number": 18,
          "excerpt": "……",
          "validation_status": "validated"
        }
      ]
    }
  ],
  "limitations": [],
  "next_actions": [
    {
      "action": "open_document",
      "document_version_id": "docver_...",
      "page_number": 18
    }
  ]
}
```

## 5. 无答案协议

```json
{
  "schema_version": "1.0",
  "snapshot_id": "batch_20261008_001",
  "status": "insufficient_evidence",
  "answer": "现有资料不足以回答该问题。",
  "reasons": [
    {
      "code": "E_RAG_EVIDENCE_INSUFFICIENT",
      "message": "缺少该基金 2026-08 月报中的组合久期披露",
      "missing_fund_ids": ["fund_..."],
      "missing_document_types": ["monthly"]
    }
  ],
  "next_actions": [
    {
      "action": "create_data_task",
      "label": "更新该基金报告",
      "requires_confirmation": true
    }
  ]
}
```

不能把“找不到证据”转换为“0”或“没有变化”。

## 6. Claim 与引用校验

### 6.1 Claim

一个 claim 是最小可验证事实：

```text
基金 A 在 2026-06-30 的修正久期为 X
基金 A 的地区配置中，中国大陆占比为 Y
基金 A 本期投资策略新增了某项限制
```

### 6.2 引用有效条件

- 文档版本属于当前 `snapshot_id`；
- `document_subjects` 能匹配问题基金；
- `page_number` 真实存在；
- excerpt 与解析页面内容一致；
- excerpt 支持 claim，而不是只属于同一章节；
- 数值型 claim 有可追溯的指标和公式版本。

### 6.3 引用状态

```text
validated
wrong_fund
wrong_version
wrong_page
excerpt_mismatch
claim_not_supported
stale
```

发布前引用为 `validated` 才能进入最终答案。

## 7. 检索与生成边界

- 先走 SQL，再决定是否需要 RAG；
- 数值回答不能由模型自己计算原始表格；
- 知识库解释不得偏离已选基金和报告期；
- 检索只读取当前或指定历史快照；
- 报告全文不写进模型服务日志；
- 引用不足时停止生成；
- 每个事实性 claim 至少绑定一个有效 citation。

## 8. 证据评测

### 8.1 100 道验收题

每类至少 10 道：

1. 单基金数值查询；
2. 跨基金比较；
3. 分类统计；
4. 两个报告期变化；
5. 原文定位；
6. 无答案问题。

### 8.2 指标

| 指标 | 计算 |
|---|---|
| 答案准确率 | 事实正确或足以支持正确结论的题数 / 全部题数 |
| 引用正确率 | 基金、文档版本、页码、内容均正确的引用数 / 全部引用数 |
| 拒答正确率 | 缺证据时正确拒答或说明缺口的题数 / 无答案题数 |
| 可追溯率 | 所有事实性 claim 都有有效引用的回答数 / 回答总数 |

### 8.3 回归样本

- 富达 ILAS 整伞年报章节；
- 汇丰 Index Tracker 英国月报；
- 已终止基金的最后完整报告；
- 同一伞下同名基金；
- 一个文档覆盖多个基金；
- 一个指标同时存在 report 和模型候选值；
- 网页存在但 PDF 缺失；
- 报告 PDF 解析成功但权重校验失败。

## 9. Assumptions

- V1 的本地 Embedding 模型和 reranker 需要完成中英文检索评测；
- 证据阈值由评测集确定，初期可按 reranker 分数和人工样本联合标定；
- 报告外发 MinerU/Qwen 前必须确认供应商数据保留和禁止训练条款。

## 10. 周二实现状态

页级底座已按本契约实现：

- `document_pages`、`document_chunks`、`chunk_embeddings` 已落库；
- 切片身份包含发布快照，旧快照证据不会被新快照覆盖；
- PostgreSQL GIN 全文/trigram 和 pgvector HNSW 已启用；
- 当前与历史证据均返回基金、报告期、文档版本、页码、来源路径和内容哈希；
- 调试接口为 `GET /api/knowledge/search` 和 `GET /api/knowledge/status`；
- 向量不可用时 `hybrid` 明确降级全文，`vector` 返回稳定错误码。

实现与实测见
[周二页级切片、索引与检索底座](../development/05-day2-page-index-and-search.md)。
