# Agent 意图识别与受控路由契约

> 版本：V0.3 Implemented Core + 150-case Golden Set  
> 适用：知识问答、受控 Agent、基金详情入口、数据质量入口  
> 原则：模型负责分类和槽位补全，业务代码负责权限、工具路由和副作用

## 1. 技术决策

1. 规则预解析优先：基金代码、日期、报告类型、Top N、URL 和明确动作不调用模型。
2. Qwen 只返回 Pydantic Schema 对应的 JSON，不直接回答事实，不直接调用工具。
3. 当前使用项目内 Python 状态机；LangGraph 仅作为未来长流程 checkpoint/interrupt 的可选运行时。
4. PostgreSQL 是 Agent 运行、消息、工具调用、模型调用和确认状态的事实源。
5. 所有读工具绑定当前发布快照；所有写工具必须先返回计划并等待用户确认。

## 2. `IntentEnvelope`

| 字段 | 类型 | 说明 |
|---|---|---|
| `schema_version` | string | 例如 `intent-v1` |
| `intent` | enum | `query_metric`、`query_holdings`、`compare_funds`、`explain_report`、`trace_quality_issue`、`locate_source`、`find_missing_data`、`plan_monthly_job`、`plan_semiannual_job`、`plan_supplement_source`、`unknown` |
| `operation` | enum | `read`、`plan`、`write` |
| `domain` | enum | `fund_metric`、`holding`、`report`、`quality`、`task` |
| `scope` | object | 基金代码、集合、类别、管理人 |
| `time` | object | 报告类型、截止日、latest |
| `constraints` | object | 指标、Top N、币种、来源要求 |
| `output` | enum | `answer_with_evidence`、`table`、`evidence_only`、`task_plan`、`clarification` |
| `confidence` | number | 0–1；业务代码会复核，不直接相信模型自报值 |
| `needs_clarification` | boolean | 是否必须先补充信息 |
| `clarification_question` | string/null | 最小澄清问题 |

## 3. 识别流水线

```text
PreParser → CandidateIntents → Qwen JSON Classifier（仅歧义）
         → Pydantic 校验 → Conflict/Confidence Gate
         → RouteDecision → Tool Registry → Evidence/Confirmation Gate
```

### 置信度规则

- `>=0.90`：允许只读路由；写操作仍需确认；
- `0.70–0.90`：只生成澄清问题；
- `<0.70`：返回 `unknown_intent`；
- 报告期、基金范围、来源可见性或操作权限冲突时，无条件澄清。

## 4. Qwen 调用边界

- Ollama 使用 `format` 传入 JSON Schema；禁止自由文本解析作为正式结果。
- `temperature=0`，固定上下文、输出上限和超时；本地单并发。
- Prompt 只包括候选意图、槽位说明、规则解析结果和用户问题。
- 输出失败或 Schema 不合法时最多修复一次，随后进入 `unknown`/待复核。
- 记录模型、Prompt 版本、token、延迟、失败阶段和原始响应摘要。

## 5. 工具注册约束

工具必须声明：

- 输入/输出 Schema；
- 允许的 intent；
- 只读或写入；
- 是否需要用户确认；
- 所需快照和证据；
- 可重试边界；
- 错误码和副作用。

模型不得直接生成 SQL、Shell、URL、基金 ID 或任务状态变更语句。

## 6. 验收指标

- 意图准确率 ≥95%；
- 基金代码、日期、报告类型关键槽位准确率 ≥98%；
- claim-evidence 绑定率 100%；
- 越权工具调用 0；
- 低置信度问题自动执行 0；
- 写操作确认率 100%。

## 7. 正式评测集

正式 150 题金标准集覆盖查询、比较、解释、质量诊断、来源定位、缺失数据、三类任务计划和模糊输入。题集及可重复报告见：[150 道正式意图评测集](../development/11-intent-golden-evaluation.md)。当前版本意图、关键槽位、澄清判断和路由安全均为 100%，失败题为 0。
