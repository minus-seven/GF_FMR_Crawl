# 受控 Agent 计划与任务创建契约

> 周四 2026-11-12 交付；状态：V0.1 已实现。

## 1. 目标

把自然语言意图转换为可审阅的计划。查询、比较和补充来源意图只生成只读计划；月报/半年报任务必须先进入 `awaiting_confirmation`，用户确认后才调用现有任务创建服务。

```text
自然语言问题
  → 确定性意图分类
  → 基金范围冻结预览
  → 目标/模型/耗时/影响范围
  → 用户确认
  → JobCreateRequest
  → 现有 global_write / Worker 状态机
```

## 2. API

### `POST /api/agent/plan`

请求：

```json
{
  "question": "为 BJK348 创建半年报任务",
  "fund_codes": ["BJK348"],
  "task_target": "fund_semiannual",
  "structured_provider": "local"
}
```

响应核心字段：

- `intent`：`query`、`compare`、`supplementary_source`、`create_monthly_job`、`create_semiannual_job`；
- `status`：只读计划为 `read_only`，写计划为 `awaiting_confirmation`；
- `scope_definition` / `scope_count`：冻结前的确定性范围预览；
- `task_target`：`fund_monthly` 或 `fund_semiannual`；
- `model_policy`：模型提供方、本地模型和并发数；
- `estimated_minutes`：`minimum`/`maximum` 区间；
- `impact_summary`：副作用、锁和产物说明；
- `requires_confirmation`：写操作固定为 `true`。

### `POST /api/agent/runs/{run_id}/confirm`

请求 `{ "confirmed": true }` 时：

1. 校验运行仍处于 `awaiting_confirmation`；
2. 使用计划中保存的 scope、stages、task target 和 model policy 构造 `JobCreateRequest`；
3. 调用现有 `create_job`，沿用幂等键、前序任务子集校验、全局写锁和 Worker fencing；
4. 返回 `job_id`，并把 Agent 运行置为 `confirmed`。

请求 `{ "confirmed": false }` 时只将计划置为 `rejected`，不创建任务。

### `GET /api/agent/runs/{run_id}`

读取计划和确认结果，用于页面刷新和重连。

## 3. 意图与边界

| 意图 | 当前行为 |
|---|---|
| 查询/比较 | 只读计划，不创建任务 |
| 补充来源 | 返回需要基金详情入口和 URL 的限制，不自动开放任意 URL 写入 |
| 创建月报任务 | 下载、解析、结构化，目标 `fund_monthly`，待确认 |
| 创建半年报任务 | 下载、解析、结构化，目标 `fund_semiannual`，待确认 |

基金范围优先级为：临时基金集合 → 显式基金代码（含问题中的代码）→ 问题中的单一资产类别 → 全量基金默认范围。无法识别的基金代码直接返回稳定错误；“管理人/公司”自然语言范围暂提示需要显式选择，避免误选管理人。

## 4. 审计与安全

每次计划写入：

- `agent_runs`：问题、快照、计划 JSON、状态和 schema 版本；
- `agent_messages`：用户问题、计划说明和确认结果；
- `tool_calls`：`agent.plan` 的输入、输出、确认要求、确认时间和错误码。

当前计划解析为确定性规则，不调用云端或本地模型，因此没有虚假的模型调用记录。后续启用 Qwen 时必须新增 `model_calls`，记录模型名、延迟、token 和失败状态，并禁止自动回退到云端模型。

## 5. 验收

- 未确认的月报/半年报计划不会创建 `jobs`；
- 确认后返回真实 `job_id`，并进入现有任务状态机；
- 月报和半年报目标分别写入 `task_target`；
- 计划显示基金数、模型、预计耗时和影响范围；
- 拒绝计划只写审计记录，不产生下载/解析/结构化副作用；
- 单基金真实冒烟已验证“计划 → 拒绝”和“计划 → 确认 → 取消测试任务”。

