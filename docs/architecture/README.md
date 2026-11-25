# 基金研究智能看板与知识库 Agent — 架构总览

> 架构方法：C4 Model  
> 版本：V1 / V2 分阶段设计  
> 参考需求：[PRD-Fund-Research-Knowledge-Agent.md](../PRD-Fund-Research-Knowledge-Agent.md)  
> 日期：2026-10-22

## 文档导航

| 层级 | 文档 | 作用 |
|---|---|---|
| Level 1 | [01-context.md](01-context.md) | 系统边界、用户、外部数据源和模型服务 |
| Level 2 | [02-container.md](02-container.md) | Web、API、Worker、Agent、数据库和文件存储 |
| Level 3 | [03-component-web-frontend.md](03-component-web-frontend.md) | 浏览器端功能组件 |
| Level 3 | [03-component-api-service.md](03-component-api-service.md) | API、任务、查询、权限和发布组件 |
| Level 3 | [03-component-ingestion-worker.md](03-component-ingestion-worker.md) | 导入、下载、解析、结构化和 staging 组件 |
| Level 3 | [03-component-agent-service.md](03-component-agent-service.md) | Agent 计划、工具、模型和报告组件 |
| Level 3 | [03-component-knowledge-service.md](03-component-knowledge-service.md) | 切片、索引、检索和引用组件 |
| Deployment | [04-deployment-v1-mac.md](04-deployment-v1-mac.md) | V1 Mac 单机部署 |
| Deployment | [04-deployment-v2-windows.md](04-deployment-v2-windows.md) | V2 Windows 共享部署 |
| Dynamic | [05-dynamic-task-publication.md](05-dynamic-task-publication.md) | 任务锁、staging、发布和快照读取流程 |
| Dynamic | [06-dynamic-agent-query.md](06-dynamic-agent-query.md) | 自然语言问答、工具调用和证据链 |
| Data | [07-data-model-and-metrics.md](07-data-model-and-metrics.md) | 数据实体、导入规则和分析指标口径 |
| Quality | [08-cross-cutting-quality.md](08-cross-cutting-quality.md) | 安全、权限、性能、质量门、测试、备份和回滚 |
| Operations | [../development/59-runtime-startup-and-dependency-runbook-20261126.md](../development/59-runtime-startup-and-dependency-runbook-20261126.md) | 当前版本的状态目录、依赖安装、服务器启停、Worker 预检和故障排查 |

## 开发细化视图

架构主图保持稳定，以下视图随开发推进迭代：

| 视图 | 文档 | 作用 |
|---|---|---|
| 分层功能架构 | [01-functional-architecture.md](../refinement/01-functional-architecture.md) | 按门户、研究应用、任务运营和基础能力展示业务功能 |
| 分层技术架构 | [02-technology-architecture.md](../refinement/02-technology-architecture.md) | 按交互、API、处理、AI、数据和运行平台展示技术栈 |
| 数据库 ER | [03-database-er.md](../refinement/03-database-er.md) | 基金主数据、文档证据、任务发布和 Agent 审计关系 |
| 核心任务时序 | [04-core-task-sequences.md](../refinement/04-core-task-sequences.md) | 提交、锁竞争、处理、发布、并发读取、失败恢复 |
| 前端信息架构 | [05-frontend-information-architecture.md](../refinement/05-frontend-information-architecture.md) | 路由、关键页面、ECharts 数据透视和低保真原型 |
| P0 API 与事件契约 | [06-p0-api-and-event-contracts.md](../refinement/06-p0-api-and-event-contracts.md) | OpenAPI、任务事件、错误码和幂等规则 |
| P1 指标字典 | [07-p1-metric-dictionary.md](../refinement/07-p1-metric-dictionary.md) | 规模、净增长、业绩贡献、净流入和覆盖率口径 |
| P1 来源适配器 | [08-p1-source-adapter-contract.md](../refinement/08-p1-source-adapter-contract.md) | manifest、发现顺序、下载规则和健康检查 |
| P1 RAG 证据 | [09-p1-rag-evidence-contract.md](../refinement/09-p1-rag-evidence-contract.md) | claim、citation、缺证据拒答和评测协议 |
| 细化设计说明 | [README.md](../refinement/README.md) | 参考方法对比、设计优先级与开发顺序 |

## 设计范围

### 输入事实

`HKEX基金综合资料库_完整_20261008.xlsx`：

- 2 个工作表：`主数据`、`改名`；
- `主数据` 有 2,479 条基金记录、67 列；
- 187 个规范化基金管理人；原始文本有 191 个值，其中 4 组仅尾部空格不同；
- 306 个有中央编号的伞；原始“编号+名称”有 308 个组合，差异来自伞名称变更；
- 基金代码唯一；
- 主数据含基金分类、注册地、管理人、资产类别、地域、标签和 2019—2026 年规模快照。

`爬虫list.xlsx` 不作为基金主数据来源。当前 775 行只提供报告页面链接、下载
命令和来源适配信息，统一存入 `fund_report_sources`；基金身份以 HKEX 基金代码为准。

`月报底稿20261008_已刷_v2.xlsx`：

- 19 个分析工作表；
- `01透视` 有 960 个公式单元格，其中 388 个为 `SUMIFS`，按资产类别、主动/被动、红利、备兑期权、API、MPF、宽基、行业主题、ETF互联互通、60/40 等维度汇总规模、净流入、业绩与净增长；
- `02趋势图` 展示月末总规模时间序列和组合图；
- `03瀑布图` 展示各资产类别的单月增量及汇总变化；
- `04大类资产` 展示期初、期末、单月增量、增速、净流入、业绩贡献和份额变化；
- 其余工作表按主动权益、核心被动、杠杆反向、货币、固收、中资、外资、北上和混合等分类展示；
- `净总回报` 有 7,436 个公式单元格，其中 4,957 个 Bloomberg `BDH` 调用拉取历史净总回报。

### 产品边界

- Excel 底稿用于定义业务分析口径和交互方式，不要求 V1 自动生成全部 19 页底稿。
- V1 提供与底稿一致的预设分析工具，包括枢纽统计、规模趋势、类别增量瀑布图和分类对比。
- V1 的基金范围以用户当前选择的名单计算覆盖率；当前基线为 775 只，90% 目标对应至少 698 只。
- 港交所全量 2,479 只基金是系统最终支持的数据范围；当前有 27 个公司适配组，
  其余管理人实体分批接入。
- 当前 775 只是下载和覆盖率验收范围，不是基金主数据的全部范围。
- V1 不接 Bloomberg。投研人员导入净总回报和补净回报数据；V2 改为 Bloomberg API 拉取。

## 推荐架构

V1 是 Mac 本地 Web 应用，V2 迁移到 Windows 服务器。两端使用同一套业务逻辑和 PostgreSQL 数据结构。

```text
浏览器
  ↓ HTTPS/JSON、SSE
FastAPI 业务服务
  ├─ 基金与快照查询
  ├─ 任务与全局写锁
  ├─ 模型配置和费用
  └─ Agent/RAG 编排
  ↓ SQL                       ↓ 文件接口
PostgreSQL + pgvector         本地文件库 / 托管文件库
  ↑
批处理 Worker
  ├─ 港交所 Excel 导入
  ├─ 基金管理人官网适配
  ├─ 月报链接与运行方式补全
  ├─ PDF 下载与校验
  ├─ MinerU 解析
  ├─ Qwen 结构化
  └─ staging 发布
```

## 模块与版本矩阵

| 模块 | V1 目标 | V2 目标 |
|---|---|---|
| 使用方式 | 单台 Mac、单用户完整验收 | Windows 主机、约 3 名投研人员共享 |
| Web 看板 | 本地浏览器访问 | 局域网浏览器访问 |
| 基金主数据 | 导入港交所 Excel；支持 2,479 只基金模型 | 保留导入，并支持港交所增量刷新 |
| 现有下载器 | 接入 27 家公司，覆盖当前 775 只名单的 90% | 扩展到其余管理人实体 |
| 月报发现 | 月报优先，年报/中报兜底 | 保持并增加来源健康监控 |
| 月报入口补全 | 复用公司 `*_fund_link.py` 的探测和 I/J 补全，只处理冻结行并写入 PostgreSQL | 保持同一契约，增加来源健康监控 |
| 解析 | MinerU precise/VLM 与本地文本降级 | 共享解析缓存和有限并发 |
| 结构化 | 权益与固收字段抽取、校验、人工 review | 沿用同一结构，支持共享审核状态 |
| 查询统计 | 无外部生成模型也可用；单基金 P95 ≤2 秒 | 保持 |
| 批量统计 | P95 ≤2n 秒，n 为基金数 | 保持 |
| 知识库 | 页级、章节级和表格级切片；SQL + 全文 + 向量 + rerank | 扩大共享缓存和增量索引 |
| 原文引用 | 文档名、页码和可跳转原文；正确率 ≥95% | 保持 ≥95% |
| 自然语言问答 | 固定模型；准确率 ≥80% | 保持 ≥80% |
| Agent | 规划、工具调用、失败重试、补数、生成报告 | 增加 V2 数据工具，沿用安全边界 |
| Tableau / ECharts | Web 内全部图表由 ECharts 渲染；Tableau Desktop 工作簿用于深入分析和研究交付 | 保持相同分工 |
| 收益数据 | 投研人员导入净总回报与补净回报 | Bloomberg API 拉取历史净总回报 |
| 高级数据源 | 不新增 | 港交所实时月度库、ETF 历史、互认基金持仓披露 |
| 任务并发 | 本地单写；单用户 | 多人只读，数据库全局单写锁 |
| 数据发布 | staging 成功后原子发布 | 保持一致，并暴露多人可见进度 |
| 模型与 Key | 固定模型，平台默认 Key，可覆盖个人同模型 Key | 保持，增加用户级加密与费用审计 |
| 部署与发布 | Mac 本地启动 | 私有 GitHub、Windows 构建、受控发布和回滚 |
| 安全与质量 | Keychain、localhost、质量门和本地备份 | DPAPI/加密、局域网访问、共享审计和恢复演练 |

## 核心架构决策

### 1. PostgreSQL 是正式数据源

Excel 用于导入、导出和业务交换，不是并发任务状态库。PostgreSQL 保存：

- 基金和伞主数据；
- 来源映射和 crawler 补充报告来源（含月报链接、运行方式与匹配状态）；
- 任务与重试；
- 文档版本与哈希；
- staging 记录；
- 解析结果；
- 结构化指标；
- 检索切片和向量；
- 模型与工具调用审计。

### 2. 单写多读

- 三位用户可以同时查看、搜索和问答；
- 全系统同一时间只允许一个下载/解析/结构化写任务；
- 写任务持有数据库租约锁和 heartbeat；
- 写任务只更新 staging；
- 任务成功后一次事务发布并切换 `current_batch_id`；
- 其他用户始终读取最近一份已发布快照。

### 3. 无模型基础模式

以下能力不依赖 Qwen：

- 基金搜索和筛选；
- 覆盖率与数据质量；
- 规模、分类、管理人、地域等 SQL 统计；
- 趋势、分类瀑布和 ECharts 预设图；
- XLSX、DOCX、PNG 的确定性模板导出。

自然语言问答、语义检索解释和 Agent 智能模式才需要外部模型。

### 4. 固定模型，凭证可覆盖

- 模型、Base URL 和能力由平台固定；
- 默认使用平台 Key；
- 用户可以覆盖同一模型的个人 Key；
- 用户不能配置任意模型或任意地址；
- 每次任务记录模型、凭证范围、token 和估算费用；
- 个人 Key 加密保存，不进入日志和前端持久化。

### 5. 数字与叙述分离

- 数字和计算走结构化 SQL；
- 报告解释、风险描述和文字变化走 RAG；
- 回答必须带文档和页码引用；
- 引用不足时禁止编造。

### 6. 不可变批次与单一发布指针

- 主数据和结果按 `batch_id` 追加写入，不在发布时复制整库；
- 文件保存到与该批次绑定的受管目录；
- 发布事务只切换 `current_batch_id`；
- 用户、Agent、ECharts 查询和 Tableau Desktop 工作簿都读取当前已发布批次；
- 运行中的 staging 不可见，失败不会污染正式结果。

### 7. 受限 Agent 与确定性工具

- Agent 只能调用版本化工具注册表中的工具；
- 数值查询走参数化 SQL，文档解释走 RAG；
- 写任务必须经用户确认并申请全局锁；
- Agent 不能执行任意 Shell、任意 URL 或生成生产爬虫；
- 每次回答保存快照、模型、工具和引用。

## 非功能目标

| 指标 | V1 验收 | V2 验收 |
|---|---|---|
| 下载/分析覆盖率 | 当前选择名单的 ≥90% | 继续按每次任务开始名单的 ≥90% |
| 问答准确率 | 100 道内部题 ≥80% | 保持 ≥80% |
| 引用正确率 | ≥95% | 保持 ≥95% |
| 单基金结构化查询 | P95 ≤2 秒 | 保持 |
| 批量确定性统计 | P95 ≤2n 秒，n 为基金数 | 保持 |
| 当前 775 只全量更新 | ≤2 小时 | 保持 |
| 共享查看 | 1 人 | 3 人 |
| 写任务并发 | 1 | 1 |

“全量更新 ≤2 小时”以任务开始时名单、公开服务可用、缓存正常为条件；不包含从零重建全部历史报告的冷启动。

## 明确不做

- V1 不自动生成完整 19 页月报底稿。
- V1 不接入 Bloomberg，不计算依赖 Bloomberg 且没有导入数据的收益字段。
- V1/V2 不需要 Tableau Server/Cloud；Web 图表不依赖 Tableau，Tableau Desktop 仅作为分析与交付出口。
- 不支持任意模型、任意 Base URL。
- 不允许 Agent 执行任意 Shell 或生成线上爬虫。
- 不建设评论、批注、收藏、自定义看板设计器和团队任务模板。
- 不提供交易建议或交易执行。
- 不把 PDF 二进制存入 PostgreSQL。

## 实施顺序

1. 建立 PostgreSQL schema、Excel 导入校验和不可变批次发布；
2. 封装现有 27 家下载器、公司来源补全、MinerU 和 Qwen 流程为 Worker 适配器；
3. 建立 Web 基金浏览、总览、数据质量、任务和预设分析；
4. 建立页级知识库、全文/向量检索、重排和引用；
5. 建立受限 Agent、工具注册、失败恢复和报告导出；
6. 接入 ECharts 右侧数据透视、Tableau 只读视图和两个预设工作簿；
7. 完成 775 只覆盖、100 题问答、引用和 2 小时性能验收；
8. 再进入 Windows 共享、三人权限和 V2 新数据源建设。

## 交付完整性

- 每份 C4 文档都包含图、Legend、Elements、Key Relationships、Assumptions 和跨层链接；
- 所有容器和组件写明职责与技术；
- 所有关系为单向并表达具体意图；
- 关键模块均在总览矩阵中标注 V1/V2；
- 图示使用 Mermaid C4，Markdown 可直接在支持 Mermaid 的查看器中打开；
- 本目录的 [README.md](README.md) 是架构交付入口。

## 未决与已记录假设

1. 除现有 27 个公司适配组外的管理人来源尚未系统摸查，统一来源发现能力需要分批验证。
2. 港交所“实时更新”解释为每次任务可取得当时最新公开资料，不建设分钟级流式同步。
3. 当前 V1 的 Web 数据透视由 ECharts 验收，不依赖 Tableau Server/Cloud；Tableau 工作簿交付需要本机 Desktop 许可证。
4. 个人同模型 API Key 的故障回退策略尚未最终确定，默认不得未经用户确认切换到公司 Key。
