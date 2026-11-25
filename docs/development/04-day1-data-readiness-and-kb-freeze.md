# 2026-10-26 周一：数据就绪与知识库方案冻结

> 状态：已完成并冻结 v1.0  
> 发布快照：`b76ef053-f098-4c56-9e17-8e8e61142ec3`  
> 冻结产物：[readiness-audit.json](artifacts/20261026-day1/readiness-audit.json)、
> [knowledge-index-scope.json](artifacts/20261026-day1/knowledge-index-scope.json)、
> [historical-assets.json](artifacts/20261026-day1/historical-assets.json)、
> [qa-20.jsonl](artifacts/20261026-day1/qa-20.jsonl)

## 1. 冻结结论

周一形成的结论如下，后续实现不得用历史基线或局部样本替代这些事实：

1. 当前 775 条 crawler 来源中，444 条有月报链接和运行方式，331 条两者均缺失；
   当前来源完整率为 **444/775 = 57.29%**，不是 PRD 历史基线 683/775。
2. 当前发布快照有 **104 只基金**具备结构化详情，且 104 份都有可读取 PDF；
   当前结构化详情率为 **104/775 = 13.42%**。
3. 104 份文档中，99 份通过报告期和页码门槛进入第一版可引用范围；
   5 份因结构化报告期与 PDF 首页不一致被排除，修正后重新索引。
4. 99 份可索引文档全部能落到 PDF 真实页。19 份为 A 级
   `content_list + page_idx`；80 份为 B 级 PDF 页级证据。
5. Agent 生成的 Markdown 不等于页级证据。B 级文档的 Markdown 段落只有在与
   PDF 页文本完成确定性锚定后，才能作为引用摘录；否则只能引用 PDF 页文本。
6. 当前 359 条结构化指标的 `source_page` 全部为空。指标查询仍以指标版本和快照
   为主证据，需要原文页码时由证据定位器在使用前补充，不得伪造页码。
7. 本地 BGE-M3/reranker 运行时尚未安装；周二先完成全文索引和页级切片，
   向量能力在模型依赖通过验收后启用，不能阻塞基础检索。

## 2. 三层覆盖率

统一分母为当前 775 条 crawler 来源：

| 层级 | 口径 | 当前值 | 比例 | 结论 |
|---|---|---:|---:|---|
| 来源完整 | `monthly_report_url` 与 `run_command` 均非空 | 444/775 | 57.29% | 当前实测 |
| 已有 PDF | 当前快照结构化记录引用且 PDF 可读取 | 104/775 | 13.42% | 当前实测 |
| 结构化详情 | 当前快照存在可查询指标 | 104/775 | 13.42% | 当前实测 |
| 结构化记录有 PDF | 结构化记录内部完整性 | 104/104 | 100% | 当前样本 |
| PRD 历史基线 | 历史已验证覆盖 | 683/775 | 88.13% | **不得作为当前值** |

90% 目标仍需 **698/775**。以当前来源完整数计算还缺 254 只；以当前结构化详情数
计算还缺 594 只。周五只能冻结新实测结果，不能用 88.13% 代替。

当前 775 条来源中：

- 444 条 I/J 完整；
- 0 条只有其中一项；
- 331 条两项均缺；
- 775 条来源均匹配到 HKEX 基金。

## 3. 第一版知识库范围

### 3.1 纳入条件

文档必须同时满足：

- 属于 `publication_state.current_batch_id` 指向的发布快照；
- 通过 `document_subjects` 唯一定位基金；
- 有不可变 `document_version_id` 和 SHA-256；
- 有可读取 PDF；
- 能确定真实 PDF 页码；
- 结构化 `report_date` 不与该 PDF 的报告月份冲突。

仅有结构化 JSON、没有 PDF 的文档不进入“可引用”索引；可以进入结构化 SQL 查询，
但回答中的原文引用必须明确为不可用。

### 3.2 已冻结数量

| 等级 | 数量 | 允许用法 |
|---|---:|---|
| A：Precise `content_list.page_idx` | 19 | 正文、标题、表格、图注均可按元素页码引用 |
| B：PDF 原文页 | 80 | PDF 页文本可直接引用；Markdown 需锚定后才能引用 |
| 排除：报告期冲突 | 5 | 不进入第一版可引用索引 |
| 合计当前结构化文档 | 104 | 99 份可索引 |

报告期核验结果：83 份自动确认、16 份只能以现有版本元数据为准、5 份冲突。
16 份 `metadata_only` 仍可索引，因为文档版本和 PDF 页存在；其报告期必须保留
“来源元数据”证据类型，不能伪称为 PDF 自动识别。

### 3.3 排除清单

| 基金代码 | 基金 | 当前结构化报告期 | PDF 报告期 | 处理 |
|---|---|---|---|---|
| ABD893 | 日本中小型企业特别时机基金 | 2026-08 | 2026-09 | 修正后重建文档版本和指标版本 |
| BWB363 | 贝莱德系统分析亚太股票绝对回报基金 | 2026-10 | 2026-09 | 修正后重建 |
| BXR351 | 贝莱德系统分析环球股票绝对回报基金 | 2026-10 | 2026-09 | 修正后重建 |
| BXR352 | 贝莱德系统分析美国股票绝对回报基金 | 2026-10 | 2026-09 | 修正后重建 |
| BYA949 | 贝莱德系统分析多元绝对回报基金 | 2026-10 | 2026-09 | 修正后重建 |

不得直接修改不可变文档版本的日期后原地覆盖。周二应生成新的解析/结构化结果，
以新业务键发布，并保留旧记录用于审计。

### 3.4 页码证据方案

内部统一使用 1-based `page_number`；MinerU `page_idx` 为 0-based，写入前统一
`page_number = page_idx + 1`。

A 级切片：

```text
content_list item
→ 校验 page_idx 为整数
→ page_number
→ chunk_type/text/table/caption
→ content_hash
```

B 级切片：

```text
PDF page text
→ page_number
→ 确定性清洗和分段
→ content_hash
```

B 级 Markdown 锚定：

1. 对 PDF 页文本和 Markdown 块执行 NFKC、空白压缩和标点归一化；
2. 使用连续字符指纹或 token 重叠做确定性匹配；
3. 只有唯一命中且相似度超过冻结阈值时写入页码；
4. 未命中、多页命中或低置信度 Markdown 进入不可引用区，不发送给回答模型；
5. 表格必须回退到 A 级表格元素或 PDF 页，不从无页码 Markdown 重建。

每条 citation 保存：

- `snapshot_id`；
- `fund_id`；
- `document_version_id`；
- `page_number`；
- `excerpt` 与 `excerpt_sha256`；
- `citation_tier`；
- `validation_status`。

## 4. 历史资产迁移清单

全量扫描 `data/` 后的事实：

| 资产 | 文件数 | 去重数 | 当前结论 |
|---|---:|---:|---|
| PDF | 4,150 | 1,160 个 SHA-256 | 2,990 个重复副本；总量约 2.53 GB |
| MinerU manifest | 1,110 | 322 个 PDF SHA-256 | 唯一 PDF 解析覆盖率 27.76% |
| Precise manifest | 341 | 164 个唯一 PDF | 均有可重建页码的 `content_list` |
| Agent manifest | 755 | 158 个唯一 PDF | Markdown 可用，但默认没有页级元素 |
| 逐 PDF 结构化 JSON | 637 | 428 个唯一内容哈希 | 需继续做基金和报告期匹配后迁移 |
| 基金改名记录 | 661 | 661 | 已在数据库，保留旧名到稳定基金 ID 的映射 |

- 1160 个唯一 PDF 中，322 个有 MinerU 产物，838 个尚无解析产物；
- JSON 去重只能证明内容重复，不能证明其已匹配基金；迁移前必须同时匹配基金、
  报告期和来源 PDF；
- 文件副本优先硬链接，跨卷回退复制，不重复发布相同 SHA-256 文档；
- 当前数据库中的 104 份文档是首个迁移验收子集，不代表 428 个唯一 JSON 已全部迁移；
- 完整迁移清单见 `historical-assets.json`，其中每个 PDF 包含 SHA-256、全部副本、
  manifest 状态、MinerU 模式和页码等级。

## 5. 公司下载器与来源缺口

775 条来源分布在 28 个管理人名称下。当前缺口最大的管理人：

| 管理人 | 来源 | I/J 完整 | 缺口 |
|---|---:|---:|---:|
| 摩根大通 | 100 | 45 | 55 |
| 瑞银 | 36 | 0 | 36 |
| 易方达 | 35 | 0 | 35 |
| 汇丰 | 94 | 64 | 30 |
| 华夏 | 27 | 0 | 27 |
| 中银香港 | 20 | 0 | 20 |
| 惠理 | 18 | 2 | 16 |
| 嘉实 | 12 | 0 | 12 |
| 柏瑞 | 32 | 21 | 11 |
| 泰康 | 11 | 0 | 11 |
| 南方 | 10 | 0 | 10 |
| 博时 | 15 | 6 | 9 |
| 汇添富 | 10 | 1 | 9 |
| 海通 | 8 | 0 | 8 |
| 东亚联丰 | 24 | 17 | 7 |
| 贝莱德 | 101 | 95 | 6 |
| 东方汇理 | 43 | 38 | 5 |
| 招银国际 | 5 | 0 | 5 |
| 兴证国际 | 4 | 0 | 4 |
| 建银国际 | 4 | 0 | 4 |
| 富国 | 8 | 5 | 3 |
| 大成 | 11 | 9 | 2 |
| 太平资管 | 2 | 0 | 2 |
| 富达 | 137 | 135 | 2 |
| 太平 | 1 | 0 | 1 |
| 睿远 | 1 | 0 | 1 |
| 首域盈信 | 1 | 1 | 0 |
| 高腾 | 5 | 5 | 0 |

适配状态：

- 28 个来源管理人名称均已映射到补全脚本，现有 444 条活跃命令没有额外参数，
  可由平台按冻结行执行；
- 脚本支持的扩展参数尚未全部进入统一行动模型：
  `amundi --lang`、`chinaamc --lang`、`efunds_cn --all`、
  `efunds_hk --type/--all/--all-types`、`jpmorgan --type/--all`、
  `valuepartners --doc`、`fidelity_ilas --refresh-links`；
- `efunds_cn`、`fsmone`、`gffunds` 的下载脚本不属于当前 28 个来源管理人，
  尚未经过 775 名单端到端验收；
- 周二优先选择缺口最大的一个高价值管理人完成参数迁移和端到端验证，再扩展到其他公司；
- 补全和下载仍只处理任务冻结行，正式 `爬虫list.xlsx` 的 I/J 不写回。

## 6. 本地模型与索引依赖

冻结技术选择：

| 项 | 冻结值 |
|---|---|
| PostgreSQL | 17.11 |
| pgvector | 0.8.1 |
| 全文检索 | PostgreSQL `tsvector`，中文先用确定性分词词典/短语召回 |
| Embedding | `BAAI/bge-m3` |
| Dense 维度 | 1024 |
| 向量列 | `vector(1024)` |
| Reranker | `BAAI/bge-reranker-v2-m3` |
| Python | 3.12，独立 `$FACTSHEET_STATE_ROOT/dependencies/venv-embed` |
| 硬件 | Apple M5，10 核，16 GB 统一内存 |

本机当前状态：

- `pgvector` 已安装；
- `pg_trgm` 尚未安装，周二与迁移一起启用；
- Python `pgvector` 客户端已安装；
- `torch`、`transformers`、`sentence-transformers`、`FlagEmbedding` 均未安装；
- 本地 Hugging Face 模型缓存不存在；
- 平台主环境为 Python 3.14；本机已有 Python 3.12.15，知识模型使用独立
  `$FACTSHEET_STATE_ROOT/dependencies/venv-embed`，避免 PyTorch 生态版本风险污染 API/Worker 环境；
- 因而周二必须保证全文检索和页级切片可独立完成，向量与 rerank 失败不得影响基础查询。

BGE-M3 的 dense 向量固定 1024 维；模型名、模型版本和向量维度进入索引唯一键。
模型升级生成新向量记录，不原地覆盖旧向量。rerank 是可降级能力，不是引用正确性的前提。

## 7. 任务恢复与回滚测试清单

| 场景 | 当前实现 | 周一状态 | 周二/周三必须验证 |
|---|---|---|---|
| 全局写租约过期 | `JobLock.expires_at`、创建时清理过期锁 | 代码已实现，自动化不足 | 模拟租约过期，新 Worker 可接管且旧锁不能写 |
| Worker 心跳中断 | 监控线程续约 | 代码已实现，自动化不足 | 停止心跳后任务可恢复，正式指针不变 |
| Worker 进程中断 | 任务和批次保持 staging | 代码已实现，自动化不足 | 中断后批次数、指标数和发布指针与中断前一致 |
| 外部子进程取消 | 进程组 SIGTERM，超时再 SIGKILL | 代码已实现，无进程树测试 | 验证 MinerU/结构化子进程和孙进程均终止 |
| 取消后终止态 | `cancelled`、staging 标 failed | API/SSE 有测试 | 断点续跑不执行未完成阶段以外的已完成阶段 |
| 发布验证失败 | 发布事务不切指针 | 业务保护已实现，无故障注入测试 | 坏 manifest/hash 失败后仍读取旧快照 |
| 发布成功幂等 | 不可变批次和发布指针 | 有重复导入 no-op 测试 | 同一批次重复发布不产生第二份当前结果 |
| 孤儿 staging | 非 current 指针不可见 | 代码已实现 | 查询、知识库和导出均拒绝读取 staging |
| 断点续跑 | 已完成阶段跳过，输入硬链接 | 有 API 测试 | 下载完成、解析失败后只跑解析及后续阶段 |

周二补齐租约、进程树和发布故障注入；周三执行真实中断任务回归。周五再次验证
“中断、取消、发布失败均不污染正式快照”。

## 8. 第一版问答测试集

已冻结 `qa-20.jsonl`，共 20 题：

- 12 道单基金事实/持仓/报告期题；
- 3 道跨基金比较题；
- 1 道报告期冲突数据质量题；
- 4 道无答案题。

每题均包含：

- 发布快照 ID；
- 基金代码；
- 报告期；
- 标准答案；
- 已核验的页级引用，或明确的全文证据搜索范围与缺失原因。

周三运行首轮 20 题，指标为答案准确率、引用正确率、拒答正确率和可追溯率。
`qa16` 故意验证报告期冲突文档不会进入知识库；在 BWB363 修正并重新发布前，
系统应拒答，而不是引用错误版本。

## 9. 数据模型冻结

### 9.1 已存在并复用

- `documents`：同一逻辑文档；
- `document_versions`：不可变文档版本和 SHA-256；
- `document_subjects`：文档到基金及页码范围；
- `fund_metrics` / `fund_holdings` / `fund_allocations`：结构化事实；
- `dataset_batches` / `publication_state`：不可变批次和单一发布指针；
- `jobs` / `job_steps` / `job_fund_scope` / `job_events`：任务、范围、事件和恢复。

### 9.2 周二迁移新增

```text
parse_runs
  id, document_version_id, parser_name, parser_version,
  artifact_path, page_mapping_tier, status, error_code, started_at, finished_at

document_pages
  id, document_version_id, page_number, text, width, height, content_hash
  UNIQUE(document_version_id, page_number)

document_chunks
  id, document_version_id, fund_id, snapshot_id, page_number,
  section_title, chunk_type, content, content_hash, metadata_json

chunk_embeddings
  id, chunk_id, model_name, model_version, dimensions, embedding vector(1024)
  UNIQUE(chunk_id, model_name, model_version)

knowledge_index_runs
  id, snapshot_id, scope_hash, parser_version, embedding_model,
  embedding_version, status, counts_json, error_message, started_at, finished_at
```

### 9.3 Agent、引用、导出与审计

```text
agent_runs
  id, user_id, snapshot_id, question, route, status, plan_json,
  response_schema_version, started_at, finished_at

agent_messages
  id, agent_run_id, role, content, sequence_no, created_at

tool_calls
  id, agent_run_id, tool_name, tool_version, input_json, output_json,
  status, error_code, latency_ms, requires_confirmation, confirmed_at

model_calls
  id, agent_run_id, job_id, provider, model_name, model_version,
  input_tokens, output_tokens, cache_hit_tokens, cache_miss_tokens,
  estimated_cost, latency_ms, status

citations
  id, agent_run_id, claim_id, snapshot_id, fund_id,
  document_version_id, page_number, excerpt, excerpt_sha256,
  citation_tier, validation_status, metadata_json

artifacts
  id, agent_run_id, batch_id, artifact_type, file_path, sha256,
  template_version, status, created_at

export_jobs
  id, agent_run_id, artifact_type, status, request_json,
  artifact_id, error_message, created_at, finished_at

agent_events
  id, agent_run_id, sequence_no, event_type, payload_json, created_at
```

冻结约束：

1. 任何 citation 只能绑定一个基金、一个文档版本和一个页码；
2. citation 引用的文档版本必须属于回答的 snapshot；
3. `excerpt_sha256` 必须与引用原文一致；
4. 数值 claim 必须同时绑定指标名、公式版本和结构化快照；
5. 写任务工具调用必须有 `requires_confirmation=true` 和确认事件；
6. API Key、完整密钥和敏感凭证不得进入 `model_calls`、prompt 或 Agent 审计；
7. 导出必须记录快照、引用、口径和模板版本；
8. 知识库、Agent、导出和只读查询都不能读取 staging 批次。

## 10. 设计评审结论

周一方案通过以下评审并冻结：

- SQL 事实与原文证据分层，模型不得自行计算数据库指标；
- 第一版只承诺当前 99 份文档可引用，不把 104 份结构化详情全部包装成页级知识库；
- A/B 证据等级公开写入 citation，前端和审计可区分；
- 报告期冲突文档不进入索引，不能通过修改元数据绕过；
- 全文检索是基础能力，向量和 rerank 是增强能力；
- 无答案必须说明缺失基金、报告期和字段，不能转换为 0；
- Agent 写操作必须二次确认，所有工具调用、模型调用和引用可审计；
- 历史 88.13% 仅作为对照，不作为本周覆盖率；
- 周二迁移完成后必须用同一审计脚本重新生成基线并比较变化。

## 11. 复现命令

```bash
PLATFORM_DATABASE_URL='postgresql+psycopg://hu@127.0.0.1:55432/fund_research' \
  $FACTSHEET_VENV_ROOT/bin/python platform/scripts/audit_knowledge_readiness.py

PYTHONPATH=platform/api \
  $FACTSHEET_VENV_ROOT/bin/pytest -q -c platform/api/pytest.ini \
  platform/api/tests/test_knowledge_readiness.py
```

审计脚本只读数据库和本地资产，输出三个 JSON；不会修改发布指针、数据库或原始文件。

## 12. 独立验证记录

> 验证日期：2026-10-27  
> 验证对象：周一数据就绪与知识库方案冻结  
> 结果：通过

验证时将审计脚本重新运行到临时目录，并与已冻结产物逐字段交叉核对：

| 检查项 | 期望 | 实测 | 结果 |
|---|---|---|---|
| 发布快照 | `b76ef053-f098-4c56-9e17-8e8e61142ec3` | 同左，API 健康检查一致 | 通过 |
| 重跑摘要 | 与冻结摘要一致 | `summary` 完全一致 | 通过 |
| 来源账目 | 775 = 444 完整 + 331 缺口 | 一致 | 通过 |
| 当前文档 | 104 份，99 纳入、5 排除 | 一致 | 通过 |
| 排除基金 | ABD893、BWB363、BXR351、BXR352、BYA949 | 完全一致 | 通过 |
| A/B 页码 | 19 A + 80 B | 一致 | 通过 |
| 可索引文档 | PDF 存在、页数大于 0、无报告期冲突 | 99/99 满足 | 通过 |
| 问答题 | 20 道且 ID 唯一 | 20/20 | 通过 |
| 答题证据 | 所有已答题引用有页码 | 18 条引用全部命中 PDF 原页 | 通过 |
| 无答案题 | 有明确证据搜索范围和缺失原因 | 5/5 满足 | 通过 |
| 历史资产 | 4,150 PDF / 1,160 唯一 / 322 有 MinerU | 一致 | 通过 |
| 结构化资产 | 637 JSON / 428 唯一 | 一致 | 通过 |
| 数据模型冻结 | 知识、Agent、引用、导出和审计表齐全 | 文档齐全 | 通过 |
| 恢复测试清单 | 租约、心跳、中断、进程取消、发布失败 | 场景齐全 | 通过 |
| 模型方案 | BGE-M3 1024 维、Python 3.12、全文降级 | 决策与依赖文件一致 | 通过 |
| 计划联动 | 周计划标完成、RAG 契约为 Frozen v1.0 | 一致 | 通过 |

已知且已冻结的缺口不属于本次验收失败：

- 5 份报告期冲突文档尚未重新生成；
- 359 条结构化指标缺少 `source_page`；
- BGE-M3/reranker 运行环境和模型权重尚未安装；
- `pg_trgm` 尚未启用；
- 租约、进程树和发布故障注入仍需在周二/周三补齐自动化测试。

验证命令：

```bash
PLATFORM_DATABASE_URL='postgresql+psycopg://hu@127.0.0.1:55432/fund_research' \
  $FACTSHEET_VENV_ROOT/bin/python platform/scripts/audit_knowledge_readiness.py \
  --output-dir "${TMPDIR:-/tmp}/day1-kb-verify"

PLATFORM_TEST_DATABASE_URL='postgresql+psycopg://hu@127.0.0.1:55432/fund_research_d5_ui_test' \
PYTHONPATH=platform/api $FACTSHEET_VENV_ROOT/bin/pytest -q \
  -c platform/api/pytest.ini platform/api/tests
```

完整后端测试 **76/76** 通过。
