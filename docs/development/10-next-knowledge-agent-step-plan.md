# 下一阶段知识问答 Agent 计划：意图识别与受控编排优先

> 版本：V0.2 重写版  
> 起始日期：2026-11-10，Asia/Hong_Kong  
> 目标：先把“用户想做什么”识别准确，再扩大工具和任务能力  
> 意图契约：[Agent 意图与路由契约](../refinement/17-agent-intent-and-routing-contract.md)

> 当前落地状态：Step 0–2 的核心代码已实现；Step 3 的基础澄清状态和 Step 4 的意图识别 API 已实现；150 题正式评测集已生成并通过四项发布门；知识问答、基金详情和数据质量页面已完成统一入口，canary 发布和灰度验收仍需继续执行。

## 1. 为什么下一步先做意图识别

当前系统的 SQL 问答、证据绑定、拒答和 Agent 计划确认已经成立，但 Agent 仍主要依赖关键词：

- `chat.py` 能识别指标、持仓和检索问题，但复杂组合问题会退化为 `search`；
- `agent.py` 的意图分类只有查询、比较、补充来源、月报任务、半年报任务；
- 基金代码、报告期、报告类型、任务目标和操作权限尚未形成统一的槽位模型；
- Qwen 目前没有参与问答意图识别，也没有结构化输出、置信度和澄清闭环；
- Agent 还不能可靠地区分“我要查数据”“我要解释原因”“我要创建任务”“我要修改数据”。

所以当前优先级不是继续增加页面或直接引入更大的模型，而是建立稳定的：

```text
自然语言
→ 意图分类
→ 槽位抽取
→ 置信度/澄清
→ 策略门
→ 工具计划
→ 只读回答或用户确认
→ 受控执行
```

## 2. 推荐技术路线

### 2.1 核心选择

| 层 | 推荐技术 | 用途 | 是否现在引入 |
|---|---|---|---|
| 规则预解析 | Python 正则、词典、日期/基金代码解析 | 代码、报告期、报告类型、Top N、币种、任务动作 | 立即 |
| 意图契约 | Pydantic v2 `BaseModel` + JSON Schema | 固定字段、枚举、必填项、拒答和版本化 | 立即 |
| 本地分类模型 | Ollama `qwen3:4b` 结构化输出 | 只处理规则无法确定的语义分类和槽位补全 | 立即，小范围 |
| 路由与权限 | 当前 Python 状态机 + 工具白名单 | 将意图映射到 SQL、检索、质量、任务工具 | 立即 |
| 状态持久化 | PostgreSQL `agent_runs`/`agent_messages`/`tool_calls` | 多轮上下文、确认、重试、审计 | 立即 |
| 长流程运行时 | LangGraph `StateGraph` | 未来需要 checkpoint、interrupt、resume 时使用 | 暂不作为硬依赖 |
| 观测与评测 | JSONL golden set + 结构化日志 | 评估 intent、slot、路由和拒答准确率 | 立即 |

Ollama 支持把 JSON Schema 传给 `format`，将模型输出约束为指定结构；本项目应使用 Pydantic 模型生成该 Schema，而不是让 Qwen 自由输出文本。[Ollama Structured Outputs](https://ollama.com/blog/structured-outputs) 和 [Ollama API](https://github.com/ollama/ollama/blob/main/docs/api.md) 均明确支持这种方式。

LangGraph 适合把 state、nodes、edges 和人工中断组合成可恢复流程，但当前项目已有 PostgreSQL 状态机；因此先保留自有状态机，等出现多步任务恢复、人工中断或并行工具编排需求后，再评估引入 `StateGraph`。[LangGraph Graph API](https://langchain-ai.github.io/langgraph/how-tos/multi-agent-network/)

### 2.2 不推荐的路线

- 不直接使用开放式 ReAct Agent 让模型自由选择 SQL、Shell 或 URL 工具；
- 不让 Qwen 直接生成答案并绕过 `claim/evidence` 契约；
- 不把向量检索当成意图分类器；向量只用于证据召回；
- 不现在引入 LangChain/Deep Agents 作为强依赖，避免在意图契约尚未稳定时增加运行时复杂度；
- 不自动在本地 Qwen 失败时切换 DeepSeek 或其他云端模型。

## 3. 意图模型

所有问答和 Agent 计划先统一为 `IntentEnvelope`，至少包含：

```json
{
  "schema_version": "intent-v1",
  "intent": "query_metric",
  "operation": "read",
  "domain": "fund_metric",
  "scope": {
    "fund_codes": ["BPJ877"],
    "collection_id": null,
    "category": null
  },
  "time": {
    "report_type": "monthly",
    "report_date": "2026-09-30",
    "latest": false
  },
  "constraints": {
    "metric": "portfolio_ytm_percent",
    "top_n": null,
    "currency": "original"
  },
  "output": "answer_with_evidence",
  "confidence": 0.98,
  "needs_clarification": false,
  "clarification_question": null
}
```

### 3.1 首批意图枚举

| 意图 | 示例 | 默认操作 |
|---|---|---|
| `query_metric` | BPJ877 9 月到期收益率是多少 | 读数据库 |
| `query_holdings` | BSG677 半年报前十大持仓 | 读数据库+证据 |
| `compare_funds` | 比较前 15 只货币基金的流动性 | SQL 聚合 |
| `explain_report` | 这只基金的投资策略是什么 | 文本证据 |
| `trace_quality_issue` | 为什么 BTE502 没有持仓 | 质量+原文 |
| `locate_source` | 半年报原文在哪一页 | 证据检索 |
| `find_missing_data` | 哪些基金缺少半年报 | 来源/质量查询 |
| `plan_monthly_job` | 给权益基金创建 9 月月报任务 | 只生成计划 |
| `plan_semiannual_job` | 给这批基金补半年报 | 只生成计划 |
| `plan_supplement_source` | 给 BPJ877 加这个 PDF 来源 | 只生成计划 |
| `unknown` | 无法安全归类的问题 | 澄清或拒答 |

### 3.2 槽位与优先级

槽位按以下优先级处理：

1. 基金代码、集合 ID、资产类别、管理人；
2. 报告类型和报告截止日；
3. 指标、持仓 Top N、币种和单位；
4. 输出形式（数值、表格、原文、图表、任务计划）；
5. 操作权限（只读、需要确认、禁止执行）。

规则已能确定的槽位不交给模型；模型只补齐规则无法确定的语义槽位。

## 4. 识别与路由架构

```text
输入问题
  ↓
DeterministicPreParser
  ├─ 基金代码/日期/报告类型/Top N/URL
  └─ 明确任务动词/指标别名
  ↓
RuleCandidateRouter
  ├─ 高置信度 → 直接 IntentEnvelope
  └─ 有歧义 → Qwen Structured Classifier
                     ↓
              Pydantic 校验
                     ↓
              Confidence/Policy Gate
        ├─ 高置信度 → 工具计划
        ├─ 中置信度 → 询问澄清
        └─ 低置信度/越权 → 拒绝
```

### 4.1 置信度门

- `confidence >= 0.90`：允许进入只读工具路由；写操作仍需要用户确认；
- `0.70 <= confidence < 0.90`：只允许生成澄清问题，不执行工具；
- `< 0.70`：返回 `unknown_intent`，解释需要补充的信息；
- 任何报告期、基金范围或操作权限冲突，直接进入澄清，不用平均置信度掩盖冲突。

置信度不是完全相信模型自报值；应综合规则命中、枚举合法性、槽位完整性、证据可用性和模型分数。

## 5. 分步实施计划

### Step 0：冻结意图契约与评测集

任务：

1. 新增 `IntentEnvelope`、`IntentCandidate`、`ClarificationRequest`、`RouteDecision` Pydantic Schema。
2. 将当前 `ChatRequest` 和 `AgentPlanRequest` 的基金范围、报告期、任务目标统一映射到意图槽位。
3. 建立 150 道固定 JSONL 意图评测集，覆盖查询指标/持仓、比较、解释、质量、来源定位、缺失数据、三类任务计划和歧义输入；每题记录 gold intent、槽位、路由和澄清要求。
4. 每题记录 gold intent、gold slots、允许的路由和是否需要确认。

验收门：Schema 可生成 OpenAPI；150/150 题意图、关键槽位、澄清安全门和受控路由全部通过；正式题集与报告位于 `docs/development/artifacts/20261109-week/`，并由后端契约测试锁定。

### Step 1：规则预解析与候选意图

任务：

1. 抽取基金代码、日期、报告类型、Top N、资产类别、币种、任务动词和 URL。
2. 把 `chat.py` 的指标别名和 `agent.py` 的关键词表合并为版本化词典。
3. 输出多个候选意图及命中原因，不直接覆盖原始问题。
4. 对“比较/排名/规模超过/缺少/为什么/创建/补充”等组合动词建立优先级。

验收门：150 道评测集的基金代码和日期槽位准确率 ≥98%；明确查询不调用 Qwen。

### Step 2：Qwen 结构化意图分类器

任务：

1. 新增本地 `IntentClassifier`，通过 Ollama `format=<IntentEnvelope JSON Schema>` 调用 `qwen3:4b`。
2. Prompt 只包含意图枚举、槽位定义、候选规则结果和用户问题，不把数据库原文直接塞入分类上下文。
3. 设置 `temperature=0`、固定 `num_ctx`、`num_predict`、超时和单并发。
4. 对 JSON 解析失败、枚举越界、槽位冲突执行最多一次修复；仍失败则 `unknown`。
5. 将模型版本、Prompt 版本、输入/输出 token、延迟和错误写入 `model_calls` 或等价审计记录。

验收门：意图准确率 ≥95%、关键槽位准确率 ≥95%、JSON 合法率 100%；不允许模型直接调用工具。

### Step 3：澄清策略与多轮状态

任务：

1. 对缺少基金范围、报告期、报告类型或操作目标的问题生成最小澄清问题。
2. 将澄清前后的 `IntentEnvelope` 保存到 `agent_runs` 和 `agent_messages`。
3. 用户补充后只重跑缺失槽位，不重复解析全部对话。
4. 对“最新”“上次”“这批基金”等指代绑定当前页面范围、集合 ID 和当前快照。
5. 过期集合、隐藏来源和快照变更时，要求重新确认范围。

验收门：澄清轮次不超过 2 次；范围冲突时不执行；页面上下文不会静默扩大为全量基金。

### Step 4：意图到工具的受控路由

任务：

1. 建立工具注册表：`query_metric`、`query_holdings`、`search_evidence`、`trace_quality`、`compare_funds`、`create_plan`、`create_job`、`get_job`。
2. 每个工具声明输入 Schema、输出 Schema、只读/写入、副作用、证据要求和允许的意图。
3. 先生成 `RouteDecision`，再调用工具；禁止模型直接拼接 SQL、Shell、URL 或数据库 ID。
4. 所有读工具绑定发布快照；所有写工具必须返回计划并进入确认状态。
5. 对工具失败返回阶段、错误码、重试边界和已产生的副作用。

验收门：意图到工具映射可追踪；越权工具调用 100% 被拒绝；每个回答继续满足 claim/evidence 契约。

### Step 5：受控 Agent 任务编排

任务：

1. 在现有 Python 状态机上增加 `clarify → plan → confirm → execute → verify → publish` 状态。
2. 先支持 1–3 只 canary 基金的月报/半年报任务，不开放全量自动执行。
3. 加入 dry-run、任务取消、失败重试和旧快照回滚点。
4. 发布后自动用同一 `IntentEnvelope` 重问一次，比较新旧快照答案和证据。
5. 只有当状态恢复、人工中断和并行工具编排复杂度超过当前状态机承载能力时，才引入 LangGraph `StateGraph`；引入后仍保留 PostgreSQL 作为业务状态源。

验收门：未确认不写任务；取消释放 lease；失败不污染旧快照；发布后原意图可复问。

### Step 6：评测、灰度与发布

任务：

1. 用 150 道意图集做规则-only、Qwen-only、hybrid 三组对比。
2. 分别统计 intent accuracy、slot accuracy、clarification precision、route safety、证据绑定率和延迟。
3. 对低置信度问题做人工复核，收集为下一轮词典和 Prompt 样本。
4. 知识问答、基金详情和数据质量页共用上下文问答入口后，再进入 canary 灰度。
5. 更新 OpenAPI、Agent 契约、运行手册、模型审计和回滚说明。

建议发布门：

- 意图准确率 ≥95%；
- 基金代码/日期/报告类型关键槽位准确率 ≥98%；
- claim-evidence 绑定率 100%；
- 越权工具调用 0；
- 需要确认的写操作确认率 100%；
- 低置信度问题不自动执行；
- 现有后端、前端和集成测试无回归。

## 6. 数据质量是并行保障线，不再作为 Agent 主线

来源缺口、报告期冲突、结构化详情覆盖仍需继续处理，但改为并行保障线：

- Step 0–2 只补齐评测集命中的基金和证据；
- Step 4 的 canary 任务只选择来源和报告期已确认的基金；
- 全量来源补齐不阻塞意图识别开发，但任何缺证据问题必须拒答；
- 当前 136 条来源缺口和 80/906 结构化详情覆盖继续作为发布报告指标。

## 7. 暂不做

- 开放式自主 Agent；
- 模型自由生成 SQL、Shell 或 URL；
- 多 Agent 协作和自动子 Agent；
- 直接把 Qwen 生成文本当作事实答案；
- 在意图、槽位和证据契约未稳定前引入复杂 Agent 框架。
