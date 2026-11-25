# 架构细化设计 — 开发视图

> 当前环境安装、外置状态目录、依赖和服务器启停请以 [运行环境、依赖与服务器启停手册](../development/59-runtime-startup-and-dependency-runbook-20261126.md) 为准。

> 状态：V0.1，开发过程中持续更新  
> 参考方法：[软件架构设计分层模型和构图思考](https://blog.csdn.net/thebulesky/article/details/129159283)  
> 上位架构：[C4 架构总览](../architecture/README.md)  
> 目标：让开发人员能直接确认数据关系、任务边界、接口契约和页面结构

## 1. 本次细化了什么

现有 C4 文档回答“系统由哪些可运行容器和组件构成，它们为什么这样连接”。本次增加五类更接近代码和数据的设计视图：

| 文档 | 视图 | 主要读者 | 何时更新 |
|---|---|---|---|
| [01-functional-architecture.md](01-functional-architecture.md) | 分层功能架构 | 产品、投研、开发 | 业务能力或页面范围变化时 |
| [02-technology-architecture.md](02-technology-architecture.md) | 分层技术架构 | 开发、测试、运维 | 技术栈或部署方式变化时 |
| [03-database-er.md](03-database-er.md) | 概念/逻辑 ER 图 | 后端、数据库、数据工程 | 表、约束、批次或血缘变化时 |
| [04-core-task-sequences.md](04-core-task-sequences.md) | UML 时序、状态机和事务边界 | 后端、Worker、测试 | 任务状态或发布协议变化时 |
| [05-frontend-information-architecture.md](05-frontend-information-architecture.md) | 路由、页面、ECharts 数据透视和低保真原型 | 前端、产品、投研 | 用户流程或页面组件变化时 |
| [06-p0-api-and-event-contracts.md](06-p0-api-and-event-contracts.md) | API、任务事件和错误码 | 前端、后端、Agent、测试 | 接口或状态语义变化时 |
| [07-p1-metric-dictionary.md](07-p1-metric-dictionary.md) | 指标定义与查询口径 | 投研、后端、Agent、测试 | 指标公式或数据源变化时 |
| [08-p1-source-adapter-contract.md](08-p1-source-adapter-contract.md) | 来源适配器协议 | 爬虫、数据工程、测试 | 新管理人接入或来源策略变化时 |
| [09-p1-rag-evidence-contract.md](09-p1-rag-evidence-contract.md) | RAG、claim 和引用协议 | AI 工程、后端、投研验收 | 检索、引用或验收规则变化时 |
| [10-structured-field-lineage.md](10-structured-field-lineage.md) | 结构化字段数据流、证据与回溯口径（v0.2 已含本地优先与紧凑上下文） | 数据工程、后端、投研验收 | 字段、兜底或落库链路变化时 |
| [11-field-pipeline-hardening.md](11-field-pipeline-hardening.md) | 字段流水线固定化与 token 优化设计（v0.2 已落地 shadow/enforce 与 A/B 变体） | 数据工程、AI 工程、后端 | 抽取策略、版式画像或成本目标变化时 |
| [12-local-qwen4b-structured-model.md](12-local-qwen4b-structured-model.md) | 本地 Qwen 4B 结构化模型设计与开发计划（Draft） | AI 工程、后端、前端、测试 | 本地模型路线、任务级路由或运行时能力变化时 |
| [13-report-structured-robustness.md](13-report-structured-robustness.md) | 月报/半年报/年报关键词解析、报告契约与纠错补正 | 数据工程、AI 工程、后端、投研验收 | 新报告版式、字段锚点或纠错规则变化时 |
| [15-chat-sql-first-and-evidence-contract.md](15-chat-sql-first-and-evidence-contract.md) | SQL 优先问答、发布快照隔离与 claim/evidence 证据契约 | 后端、知识库 Agent、前端、投研验收 | 问答路由、引用或拒答语义变化时 |
| [16-controlled-agent-plan-contract.md](16-controlled-agent-plan-contract.md) | 自然语言意图、任务计划预览、确认和审计契约 | Agent、后端、前端、任务验收 | 计划意图、确认副作用或 Agent 审计变化时 |
| [17-agent-intent-and-routing-contract.md](17-agent-intent-and-routing-contract.md) | 意图枚举、槽位、置信度、Qwen 结构化分类和工具路由 | 知识问答、Agent、后端、测试 | 意图或工具路由变化时 |
| [contracts/](contracts/README.md) | OpenAPI、JobEvent、Adapter 和 Answer 机器契约 | 开发、测试、Agent | 接口或 Schema 变化时 |

## 2. 参考文章与 C4 的区别

| 维度 | 参考文章的分层图 | 当前 C4 图 |
|---|---|---|
| 主要目的 | 说明业务功能、技术栈、平台资源分别有哪些内容 | 说明系统边界、可运行容器、内部组件及依赖方向 |
| 组织方式 | 按“表现/应用/服务/数据/基础设施”等层纵向堆叠 | 按 Context、Container、Component 逐层放大 |
| 是否强调部署边界 | 通常较弱，容易把逻辑层和技术产品画在同一张图 | 强，容器代表独立运行或部署单元 |
| 是否强调关系 | 常用于表达“属于哪一层”，线关系可能较抽象 | 每条关系要求具体意图、方向和协议 |
| 适合的讨论 | 业务能力全景、技术选型、架构汇报 | 系统拆分、接口边界、开发责任、部署和演进 |
| 主要风险 | 业务功能与技术产品混画，层级失真 | 对数据库字段、消息顺序和页面细节不够直观 |

两类图不是替代关系：

- **C4 是主架构基线**，用于防止系统边界和运行依赖走偏；
- **分层图是沟通视图**，用于向业务和开发同时展示能力与技术栈；
- **ER 图、时序图和原型图是实现视图**，直接对应数据库、事务和 UI。

开发时如果三套视图冲突，以 C4 中的容器边界和本目录的事务/数据约束为准，再同步修正分层图。

## 3. 建议进一步设计的优先级

| 优先级 | 设计项 | 为什么现在做 | 最小交付 |
|---|---|---|---|
| P0 | 数据库逻辑模型与约束 | 先固定主键、批次、文档多基金复用和引用血缘，避免下游返工 | 已完成 ER、字段字典和关键约束 v0.1 |
| P0 | 核心任务时序 | 单写锁、staging、失败恢复和快照发布是系统正确性的核心风险 | 已完成 happy path、锁冲突、发布失败时序 v0.1 |
| P0 | API 与事件契约 | 前端、Worker、Agent 可并行开发的前提 | D2 查询和 D3 任务 OpenAPI v0.3 已实现 |
| P1 | 前端信息架构与原型 | 先确认投研人员实际工作流，再决定组件粒度 | 已完成导航、关键页面和 ECharts 数据透视侧栏 v0.1 |
| P1 | 指标字典与查询语义 | 规模和净流入口径必须可测试，不能由 Agent 临时解释 | 已完成指标版本、空值规则、公式和示例 SQL v0.1 |
| P1 | 来源适配器协议 | 27 个公司适配组向其余管理人扩展依赖稳定插件契约 | 已完成 manifest、匹配顺序、失败分类和健康检查 v0.1 |
| P1 | RAG 证据协议 | 引用正确率 ≥95% 需要可验证的数据结构 | 已完成 claim、citation、无答案和评测协议 v0.1 |
| P2 | 视觉高保真与组件库 | 低风险，但应在页面流程确认后推进 | Figma/图片、设计 token、响应式规范 |
| P2 | Windows 运维细化 | V1 末段才需要落地 | 服务包装、备份恢复、发布回滚手册 |

## 4. 当前可用于开发的基线

已经具备：

- 数据库字段字典和 4 组 ER 关系；
- 任务提交、锁竞争、发布和失败恢复时序；
- 已实现的 D2/D3 OpenAPI v0.3、SSE 事件和错误码；
- 净增长、业绩贡献、净流入和覆盖率口径；
- 来源适配器 manifest 与健康检查；
- RAG claim、citation 和无证据拒答协议；
- 前端路由、顶部任务条和右侧 ECharts 数据透视布局。

下一轮实现前仍需确认：

1. Tableau Desktop 许可证和两个预设工作簿的最终版式；
2. 90% 达标但存在 SKIP 时是否自动发布；
3. 取消任务时哪些外部请求允许强制中止；
4. 低质量本地文本解析是否计入端到端覆盖率；
5. 个人 Key 失败时是否允许切换公司 Key。

## 5. 开发顺序建议

```text
数据骨架
→ Worker 任务状态机
→ 快照发布事务
→ 基金查询与任务中心页面
→ PDF/MinerU/Qwen 接入
→ 知识库与引用
→ Agent 与报告
→ ECharts 网页图表与 Tableau 工作簿出口
→ Windows 共享部署
```

这里刻意不按页面数量推进。数据库关系、任务事务和 API 协议如果不先固定，前端和 Agent 会反复返工。

补充实现设计：[14-source-collection-task-targets.md](14-source-collection-task-targets.md)

## 6. V1 / V2 边界

| 设计项 | V1 | V2 |
|---|---|---|
| 分层功能架构 | 单用户完整闭环 | 增加共享用户、队列和权限管理 |
| 技术架构 | Mac 本地进程和 PostgreSQL | Windows 服务、多人访问和备份作业 |
| ER | 完整主数据、任务、文档、知识库和审计表 | 增加更多外部数据源和用户权限字段 |
| 任务时序 | 单机单写，仍使用全局租约锁 | 三人排队，仍全局单写 |
| 前端原型 | 右侧预设透视由 ECharts 渲染，Tableau Desktop 作为分析和交付出口 | 复用 ECharts 视图和角色差异 |

## 7. 本轮没有替代 C4 的内容

- 分层图不定义新的运行时容器；
- ER 图不改变正式数据库选型；
- UML 时序图不引入消息队列；
- 原型图不改变 V1 只做预设分析工具的范围；
- Bloomberg、ETF 历史和互认基金持仓仍只属于 V2。
