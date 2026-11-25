# 2026-10-27 周二：页级切片、索引与检索底座

> 状态：已完成  
> 当前发布快照：`aa6a0159-9f45-4c25-bfa5-24da0f6649b1`  
> 历史知识批次：`a85ad135-1fc3-5542-ad56-b8ef53c129fc`  
> 迁移版本：`20261027_0007`（知识库基础表为 `20261027_0005`）

## 1. 交付结论

周二底座已经形成可运行的页级证据链路：

```text
PDF + MinerU content_list / Markdown
→ 页面和确定性切片
→ PostgreSQL 全文 + trigram 索引
→ BGE-M3 1024 维向量 + HNSW
→ 按发布快照、基金和报告期检索
→ 返回基金、版本、页码、来源路径和内容哈希
```

当前发布快照：

| 指标 | 实测 |
|---|---:|
| 候选结构化文档 | 104 |
| 可引用文档 | 99 |
| 因报告期冲突排除 | 5 |
| PDF 页面 | 386 |
| A 级 `content_list` 切片 | 1,040 |
| B 级 PDF 页切片 | 383 |
| 总切片 | 1,423 |
| BGE-M3 向量 | 1,423 |
| 向量维度 | 1,024 |

可追溯历史批次：

| 指标 | 实测 |
|---|---:|
| 唯一 MinerU manifest | 322 |
| 纳入历史文档 | 169 |
| 已存在于当前快照而跳过重复写入 | 79 |
| 报告月份无法确定 | 42 |
| 基金名称歧义 | 13 |
| 基金名称无法匹配 | 19 |
| 历史 PDF 页面 | 521 |
| 历史切片 | 4,530 |
| BGE-M3 向量 | 4,530 |

另外扫描到 264 个按“基金名 + 报告月”去重的结构化 JSON：

- 可追溯到已由 MinerU 或当前快照索引的 PDF 时复用文档版本，不重复切片；
- 无法唯一匹配基金、PDF 报告月或同一版本的 JSON 进入迁移报告，不建立猜测关联；
- 本轮结构化 JSON 没有产生新的唯一文档版本，说明现有 manifest 路径已覆盖所有可唯一确认的同基金同报告月资产。

数据库已有 661 条 `fund_name_history` 改名记录。知识迁移继续通过稳定 `fund_id`
关联历史名称，不把改名记录复制成文档或知识切片。

当前与历史合计：

```text
268 documents
232 funds
907 pages
5,953 chunks
5,953 embeddings
0 incomplete chunks
```

来源补全前的发布快照 `b76ef053-f098-4c56-9e17-8e8e61142ec3` 对应的
1,423 个切片和向量仍按快照隔离保留。补全与诊断验证任务先后发布新快照，
因此数据库当前共保留 4 个可检索快照、8,799 个切片和 8,799 个向量；
默认查询只读取最新发布快照 `aa6a0159…`。

重复运行完整索引时，当前和历史页面、切片新增均为 0；向量按稳定切片键更新，不产生重复行。

## 2. 数据模型与迁移

迁移 `20261027_0005` 创建：

- `parse_runs`：解析器、页码映射等级和运行状态；
- `document_pages`：物理 PDF 页文本、尺寸和内容哈希；
- `document_chunks`：页级切片、章节、表格元数据、来源路径和证据等级；
- `chunk_embeddings`：版本化 1024 维向量；
- `knowledge_index_runs`：快照、范围哈希、模型版本和质量计数；
- `agent_runs`、`agent_messages`、`agent_events`：Agent 运行和回放；
- `tool_calls`、`model_calls`：工具、模型、Token、成本和确认状态；
- `citations`：claim 到基金、文档版本、页码和原文的绑定；
- `artifacts`、`export_jobs`：导出产物和异步任务。

迁移 `20261027_0006` 为每个向量记录补充来源切片 `content_hash`，用于跳过未变化向量，
只重新计算内容发生变化的切片。

迁移 `20261027_0007` 将切片唯一键调整为
`(snapshot_id, document_version_id, chunk_index)`，使同一不可变文档在多个发布快照中
拥有独立证据版本。

索引：

- `document_chunks.search_vector` 为持久化 `tsvector`，使用 GIN；
- `content` 和 `section_title` 使用 `pg_trgm` GIN；
- `chunk_embeddings.embedding` 使用 HNSW cosine；
- 快照、基金、报告期和文档页码使用组合 B-tree。

ORM 与迁移通过 `alembic check`，无待生成差异。

## 3. 切片规则

### 3.1 A 级：MinerU `content_list`

- 每项必须有整数 `page_idx`；
- 写库时转换为 1-based `page_number`；
- 正文、标题、表格、图表和图片说明分别保留 `chunk_type`；
- 表格保留 caption、footnote、HTML 表体和 bbox；
- 当前标题向下传递为 `section_title`；
- 任意元素缺页码时不能作为 A 级页面；索引器回退 PDF 页。

### 3.2 B 级：PDF 页

- 用 PyMuPDF 提取每页文本、宽度、高度和内容哈希；
- 单页文本按段落和长度切分，最大约 1,800 字符；
- 每个切片只属于一个物理页面；
- Markdown 不直接赋予推测页码；未被确定性锚定的 Markdown 不进入引用索引。

### 3.3 幂等键

```text
document_pages: (document_version_id, page_number)
document_chunks: (snapshot_id, document_version_id, chunk_index)
chunk_embeddings: (chunk_id, model_name, model_version)
knowledge_index_runs: (snapshot_id, scope_hash, index_version)
```

稳定 ID 由同一业务键生成；内容变化更新原行，数量减少时删除尾部旧切片。

## 4. 全文、向量与检索

### 4.1 全文检索

- 使用 PostgreSQL `tsvector` 与 `plainto_tsquery('simple', q)`；
- 中文精确词和未分词片段以 `ILIKE` 与 trigram similarity 补充；
- 默认不需要任何外部模型即可使用；
- 支持基金代码、报告期、查询文本和结果上限过滤。

### 4.2 向量检索

- 模型：`BAAI/bge-m3`；
- 运行环境：Python 3.12、独立 `$FACTSHEET_STATE_ROOT/dependencies/venv-embed`；
- 设备：Apple M5 MPS，FP16；
- 权重：`pytorch_model.bin`；
- 文件大小：2,271,145,830 字节；
- 权重 SHA-256：`b5e0ce3470abf5ef3831aa1bd5553b486803e83251590ab7ff35a117cf6aad38`；
- 与 Hugging Face 官方 LFS OID 一致；
- dense 向量维度：1,024。

`hybrid` 模式合并全文和向量的归一化分数。无查询向量时明确降级全文，
`vector` 模式则返回 `E_KNOWLEDGE_VECTOR_UNAVAILABLE`，避免静默伪称语义检索成功。

### 4.3 调试 API

```text
GET /api/knowledge/status
GET /api/knowledge/search?q=到期收益率&fund_codes=ABD895
GET /api/knowledge/search?mode=hybrid&q=久期
```

可用参数：

- `q`：查询文本；
- `mode`：`fulltext`、`vector`、`hybrid`；
- `fund_codes`：可重复基金代码；
- `report_periods`：可重复报告期；
- `limit`：1-50；
- `snapshot_id`：允许显式查询其他正式发布快照；
- `query_vector`：1,024 维查询向量；
- `embedding_model` / `embedding_version`。

每条结果固定返回：

```text
fund_code
fund_name
report_period
document_version_id
page_number
section_title
chunk_type
citation_tier
content
source_path
content_hash
score
```

## 5. 历史迁移

历史迁移以“同一 PDF SHA-256 + 唯一基金名称 + 可从 PDF 确定的报告月份”为可追溯条件：

- 已存在于当前快照的文档不重复写入历史批次；
- 所有历史文档使用稳定 `knowledge-history-v1` 批次；
- 不修改 `publication_state.current_batch_id`；
- 历史批次状态为 `published`，但只能通过显式快照 ID查询；
- 重复执行不会创建第二个历史批次。

跳过原因已保存在索引质量报告中，不使用“迁移完成”掩盖历史名称歧义或报告期缺失。

相关产物：

- [索引质量报告](artifacts/20261027-day2/knowledge-index-report.json)
- [索引完整性与引用质量报告](artifacts/20261027-day2/knowledge-index-quality.json)
- [来源缺口队列](artifacts/20261027-day2/source-gap-queue.json)

## 6. 来源补全队列

775 条来源分类结果：

| 项 | 数量 |
|---|---:|
| I/J 完整 | 444 |
| 缺口 | 331 |
| 同时缺 URL 和命令 | 331 |

优先级前十管理人：

1. 摩根大通：55
2. 瑞银：36
3. 易方达：35
4. 汇丰：30
5. 华夏：27
6. 中银香港：20
7. 惠理：16
8. 嘉实：12
9. 柏瑞：11
10. 泰康：11

缺口队列逐只记录来源 ID、基金、管理人和处理动作，供后续批量补全与每日覆盖更新使用。

已执行受控来源补全任务：

```text
job_id: 831c4a12-b0bf-4069-9b0e-820b4387bf51
frozen_scope: 331 funds
stage: download（含公司 probe/backfill）
base_snapshot: b76ef053-f098-4c56-9e17-8e8e61142ec3
published_snapshot: e0b9e36b-4bee-44d2-8d97-75d83410d991
final_snapshot_after_verification: aa6a0159-9f45-4c25-bfa5-24da0f6649b1
```

任务结果为 `completed_with_gaps`：

| 结果 | 数量 |
|---|---:|
| 成功 | 241 |
| 业务跳过 | 75 |
| 失败 | 15 |

这些“失败”随后经补全诊断修复并重跑：原 15 行变为 **1 成功、14 真实 SKIP、0 失败**。
另有 5 个原来的“脚本未返回结果”也已变成明确 SKIP。详见
[补全脚本诊断](06-backfill-script-diagnosis.md)。

当日 I/J 完整来源从 444 提升到 **686/775 = 88.52%**，距离 90% 目标
698/775 还差 12 只；最新剩余缺口为 89 只。任务完成后按受控发布流程切换到
`e0b9e36b-4bee-44d2-8d97-75d83410d991`，知识库随后为诊断验证任务发布的最新快照建立独立切片；
旧快照切片不被搬移或覆盖。

补全后缺口队列：

- [source-gap-queue-after-backfill.json](artifacts/20261027-day2/source-gap-queue-after-backfill.json)

## 7. 恢复与回滚回归

新增自动化测试覆盖：

- 过期全局写租约被新 Worker 删除并重新领取；
- 取消外部命令时，同一进程组的子进程和孙进程均被终止；
- 发布 manifest 哈希失败时返回业务错误，批次保持 `staging`；
- 发布失败前后 `publication_state.current_batch_id` 保持不变；
- 当前索引重复执行不新增页面或切片；
- 历史索引重复执行只保留一个历史知识批次；
- 伪向量写入和 pgvector 检索可重复，且不依赖外部模型服务。

## 8. 运行命令

```bash
PLATFORM_DATABASE_URL="$PLATFORM_DATABASE_URL" \
  $FACTSHEET_VENV_ROOT/bin/python platform/scripts/index_knowledge.py --scope both

PLATFORM_DATABASE_URL="$PLATFORM_DATABASE_URL" \
  $FACTSHEET_VENV_ROOT/bin/python platform/scripts/classify_source_gaps.py

HF_HOME=$FACTSHEET_RUNTIME_ROOT/huggingface \
PLATFORM_DATABASE_URL="$PLATFORM_DATABASE_URL" \
  $FACTSHEET_STATE_ROOT/dependencies/venv-embed/bin/python platform/scripts/index_knowledge.py \
  --scope both \
  --embedding-model BAAI/bge-m3 \
  --embedding-model-path $FACTSHEET_RUNTIME_ROOT/models/bge-m3 \
  --embedding-version v1 \
  --embedding-device mps
```

## 9. 验收结果

| 周二验收项 | 结果 |
|---|---|
| 相同文档重复索引不产生重复记录 | 通过；页面/切片新增 0 |
| 检索结果带基金、报告期、页码和路径 | 通过；缺失字段 0 |
| 关闭向量能力后全文检索仍可用 | 通过；全文与 hybrid 降级测试通过 |
| 历史 JSON/Markdown 迁移不重复批次 | 通过；历史批次唯一 |
| 中断任务不污染发布快照 | 通过发布失败故障注入测试 |
| 页级全文、trigram 和 HNSW 索引 | 通过 |
| BGE-M3 1024 维真实向量 | 通过；当前+历史 5,953/5,953，保留快照合计 8,799/8,799 |

最终回归：后端 **93/93**、前端 **16/16**，生产构建通过；`alembic check`
无待生成变更。正式索引完整性验证检查 8,799 个切片，缺失来源路径、过期向量、
无效内容哈希和越界页码均为 0。

后续周三直接在此底座上实现 SQL 优先路由、混合重排、claim/citation 和拒答。
