# P0 — API、任务事件与错误码契约

> 状态：D2、D3 已实现，v0.3  
> 目标：让前端、API、Worker、Agent 和后端测试可以并行开发  
> 机器可读契约：[OpenAPI v0.3 YAML](contracts/openapi-v0.3.yaml) · [FastAPI 生成 JSON](contracts/openapi-generated-v0.3.json) · [Job Event JSON Schema](contracts/job-event.schema.json)

## 1. 契约边界

本契约覆盖 V1 目标接口。D2 已实现：

- 当前快照和基金查询；
- 基金详情、指标、质量问题和 ECharts 数据；

D3 已实现：

- 创建、查看、取消任务；
- SSE 任务事件；
- Worker 单写锁、staging manifest 和批次发布；

D3 以后实现：

- Tableau Desktop 工作簿出口；
- 问答、Agent 计划和执行；
- XLSX、DOCX、PNG、PDF 导出。

Bloomberg、ETF 历史、互认基金持仓和港交所实时数据只增加 V2 工具接口，不改变本契约的基础语义。

## 2. 通用规则

### 2.1 身份与请求头

| Header | 必填 | 说明 |
|---|---|---|
| `Authorization` | V2 必填 | `Bearer <session-token>` |
| `X-Request-Id` | 建议 | 调用方生成的请求追踪 ID |
| `Idempotency-Key` | 创建任务和写操作必填 | 1～128 字符，按用户和接口唯一 |
| `Accept-Language` | 可选 | `zh-HK`、`zh-CN` 或 `en` |

### 2.2 响应信封

普通成功响应：

```json
{
  "data": {},
  "snapshot_id": "018f6f0a-7d66-7c21-b25b-5aeb0bd3b30c",
  "request_id": "req_01JABCDEF",
  "meta": {
    "server_time": "2026-10-22T08:30:00Z"
  }
}
```

失败响应：

```json
{
  "error": {
    "code": "E_LOCK_BUSY",
    "message": "已有写任务占用全局锁",
    "retryable": true,
    "details": {
      "lock_owner_job_id": "018f6f0a-7d66-7c21-b25b-5aeb0bd3b30c",
      "expires_at": "2026-10-22T08:35:00Z"
    }
  },
  "request_id": "req_01JABCDEF"
}
```

所有时间使用 UTC ISO 8601。金额、比例和规模字段返回数值类型，同时返回单位或币种，不使用带符号的字符串。

## 3. 核心接口

### 3.1 健康检查

```http
GET /api/health
```

D2 返回 API、数据库、当前快照、基金数和补充来源数。数据库可用但没有已发布
快照时，`status` 返回 `degraded`。

### 3.2 当前快照

```http
GET /api/snapshots/current
```

响应：

```json
{
  "data": {
    "snapshot_id": "d6d80682-ad9b-5451-8f6b-53cc4297e3f5",
    "status": "published",
    "source_kind": "hkex-master+crawler-supplement+structured",
    "source_sha256": "0753...",
    "coverage": null,
    "fund_count": 2479,
    "published_at": "2026-10-22T09:51:32Z",
    "release_version": "d1-hkex-v2"
  },
  "snapshot_id": "d6d80682-ad9b-5451-8f6b-53cc4297e3f5",
  "request_id": "req_01JABCDEF",
  "meta": {
    "server_time": "2026-10-23T01:00:00Z"
  }
}
```

### 3.3 创建任务

先查询可选范围：

```http
GET /api/jobs/scope-options
```

返回全量基金数、管理人和资产类别及其冻结后基金数。`mode` 支持：

- `all`：全量基金；
- `manager`：`manager_ids`；
- `category`：`categories`；
- `funds`：`fund_codes`；
- `tag`：`tag_filters`（保留给后续 Web 范围选择）。

```http
POST /api/jobs
Idempotency-Key: scope-20261022-hsbc-full-001
Content-Type: application/json
```

请求：

```json
{
  "scope": {
    "mode": "category",
    "manager_ids": [],
    "categories": ["固收"],
    "fund_codes": [],
    "tag_filters": []
  },
  "stages": ["download", "parse", "structure"],
  "document_policy": {
    "prefer": "monthly",
    "allow_fallback": ["annual", "interim", "hkex"],
    "allow_partial_publish": true
  },
  "model_policy": {
    "profile": "analysis",
    "credential_scope": "platform"
  }
}
```

响应 `202 Accepted`：

```json
{
  "data": {
    "job_id": "31d41d6c-2337-4c35-a522-bf68ed245753",
    "batch_id": "134d4762-3647-4c34-84a4-02188ddda754",
    "base_batch_id": "d6d80682-ad9b-5451-8f6b-53cc4297e3f5",
    "status": "queued",
    "outcome": null,
    "scope_count": 94,
    "scope_hash": "sha256:4f7c...",
    "current_stage": null,
    "cancellation_requested": false,
    "progress": {
      "total": 94, "processed": 0, "succeeded": 0,
      "skipped": 0, "failed": 0
    },
    "steps": [],
    "lock": {
      "acquired": true, "owner": "api",
      "expires_at": "2026-10-23T01:40:10Z"
    },
    "created_at": "2026-10-23T01:30:10Z",
    "started_at": null,
    "finished_at": null,
    "error_code": null,
    "error_message": null,
    "manifest_path": null
  },
  "snapshot_id": "d6d80682-ad9b-5451-8f6b-53cc4297e3f5",
  "request_id": "req_01JABCDEF",
  "meta": {"server_time": "2026-10-23T01:30:10Z"}
}
```

规则：

- 首次创建返回 `202`；相同幂等键和相同请求返回原任务及 `200`；
- `scope_count` 在创建事务中计算并冻结；
- 相同 `Idempotency-Key` 必须返回同一 `job_id`；
- 任务没有被授予锁时返回 `waiting_lock`，仍可订阅事件；
- 不接受任意 Shell、命令字符串或未注册 URL。

### 3.4 查询任务列表与详情

```http
GET /api/jobs?status=completed&page=1&page_size=50
```

列表响应使用统一信封和 `data.items` 分页结构。

```http
GET /api/jobs/{job_id}
```

响应：

```json
{
  "data": {
    "job_id": "018f6f0a-7d66-7c21-b25b-5aeb0bd3b30c",
    "batch_id": "134d4762-3647-4c34-84a4-02188ddda754",
    "base_batch_id": "d6d80682-ad9b-5451-8f6b-53cc4297e3f5",
    "status": "running",
    "outcome": null,
    "scope": {
      "count": 94,
      "frozen_at": "2026-10-22T08:20:00Z"
    },
    "current_stage": "parse",
    "cancellation_requested": false,
    "progress": {
      "total": 94,
      "processed": 72,
      "succeeded": 67,
      "skipped": 3,
      "failed": 2
    },
    "lock": {
      "acquired": true,
      "owner": "worker-mac-01",
      "expires_at": "2026-10-22T08:35:00Z"
    },
    "created_at": "2026-10-22T08:20:00Z",
    "started_at": "2026-10-22T08:20:02Z",
    "finished_at": null
  }
}
```

### 3.5 任务事件

```http
GET /api/jobs/{job_id}/events
Accept: text/event-stream
Last-Event-ID: 018f6f0e-0adf-75e8-9d50-15a89e4f3a27
```

SSE 帧：

```text
id: 018f6f0e-0adf-75e8-9d50-15a89e4f3a27
event: job.stage
data: {"schema_version":"1.0","event_id":"018f6f0e-0adf-75e8-9d50-15a89e4f3a27","job_id":"018f6f0a-7d66-7c21-b25b-5aeb0bd3b30c","sequence_no":27,"type":"job.stage","occurred_at":"2026-10-22T08:26:04Z","payload":{"stage":"parse","status":"running","total":94,"processed":72}}

```

重连时服务端从 `Last-Event-ID` 之后继续发送；事件保留至少 24 小时。客户端断线不应中止任务。

爬虫补全使用 `job.backfill`：

```text
event: job.backfill
data: {"schema_version":"1.0", ..., "payload":{
  "status":"completed",
  "sheet":"汇丰",
  "rows":[6],
  "succeeded":[6],
  "skipped":[],
  "failed":[]
}}
```

补全开始时 `status=running` 并返回 `scripts`；结束状态为 `completed` 或
`cancelled`。`rows`、`succeeded`、`skipped` 和 `failed` 使用原 Excel
行号，便于与公司补全脚本审计。

### 3.6 取消任务

```http
POST /api/jobs/{job_id}/cancel
Idempotency-Key: cancel-job-018f6f0a
```

取消请求返回新的目标状态 `cancelled`，但 Worker 可能需要完成当前不可中断的外部请求后才能停止。取消不会改变当前发布快照。

### 3.7 Worker 发布申请

```http
POST /api/jobs/{job_id}/publish
Content-Type: application/json
```

请求：

```json
{
  "manifest_path": "data/jobs/<job_id>/staging_manifest.json",
  "manifest_sha256": "4f7c..."
}
```

API 校验任务持有 `global_write` 锁、manifest 路径位于该任务目录、所有产物
存在且 SHA-256 一致，然后克隆基线结果到新批次并在同一发布服务中切换
`publication_state.current_batch_id`。Worker 不直接修改发布指针。

### 3.8 基金查询

```http
GET /api/funds?manager_id=...&tag=...&report_period=2026-08-31&page=1&page_size=50
```

过滤参数只接受白名单字段。排序字段、方向、状态枚举和分页上限均由 OpenAPI 约束。

分页统一放在响应信封的 `data.items` 中：

```json
{
  "data": {
    "items": [
      {
        "fund_id": "ef83646e-500d-5168-8c63-169ac5173bea",
        "fund_code": "ALL924",
        "name_cn": "柏瑞环球债券基金",
        "name_en": "PineBridge Global Bond Fund",
        "manager_id": "e7e240e8-4823-5273-8fcf-64b040a88eb8",
        "manager_name": "PineBridge Investments Ireland Limited",
        "umbrella_name": "柏瑞环球基金",
        "asset_category": "固收",
        "asset_subcategory": null,
        "status": "UNKNOWN",
        "report_period": "2026-07-01",
        "quality_status": "complete"
      }
    ],
    "page": 1,
    "page_size": 50,
    "total": 2479
  },
  "snapshot_id": "d6d80682-ad9b-5451-8f6b-53cc4297e3f5",
  "request_id": "req_01JABCDEF",
  "meta": {"server_time": "2026-10-23T01:00:00Z"}
}
```

基金详情路径使用稳定 HKEX 代码：

```http
GET /api/funds/{fund_code}
GET /api/funds/{fund_code}/metrics
```

基金详情响应同时返回补充报告来源：

```json
{
  "data": {
    "fund_code": "AAQ913",
    "fund_name": "摩根国际债券基金",
    "report_sources": [
      {
        "source_id": "crawler-list-v1:摩根:2",
        "monthly_report_url": "https://example.com/fund-documents",
        "run_command": "python download_jpmorgan_documents.py ...",
        "match_status": "matched",
        "match_method": "manager_name_umbrella",
        "is_active": true
      }
    ]
  }
}
```

规则：

- `fund_code` 来自 HKEX，crawler 的“序号”不作为基金代码返回；
- API 可以展示 `run_command` 供维护人员审计，但创建任务时只接受已注册
  Adapter 和参数，不接受任意命令文本；
- 未匹配 crawler 记录不出现在普通基金详情中，只在数据质量问题页展示；
- 同一来源行使用稳定 `source_id` 幂等更新。

### 3.9 质量问题

```http
GET /api/quality/issues?severity=warning&status=open&fund_code=ALL924
```

仅返回当前发布批次的问题，分页结构同样使用 `data.items`。

### 3.10 ECharts 数据透视

```http
GET /api/analytics/perspectives?scope_fund_codes=...&period=latest
```

响应：

```json
{
  "data": {
    "views": [
      {
        "view_id": "fund_scale_histogram",
        "title": "当前基金规模直方图",
        "chart_type": "histogram",
        "dimension": "fund_size",
        "metric": "fund_count",
        "unit": "HKD_100M",
        "dataset": [
          {"bin_start": 0, "bin_end": 5, "value": 32},
          {"bin_start": 5, "bin_end": 10, "value": 48}
        ],
        "snapshot_id": "batch_20261008_001",
        "quality": "complete"
      },
      {
        "view_id": "fund_scale_trend",
        "title": "当前基金规模趋势",
        "chart_type": "line",
        "dimension": "month_end",
        "metric": "fund_size",
        "unit": "HKD_100M",
        "dataset": [
          {"month_end": "2026-06-30", "value": 420.5},
          {"month_end": "2026-07-31", "value": 438.1},
          {"month_end": "2026-08-31", "value": 452.7}
        ],
        "snapshot_id": "batch_20261008_001",
        "quality": "complete"
      }
    ],
    "scope_count": 3
  },
  "snapshot_id": "d6d80682-ad9b-5451-8f6b-53cc4297e3f5",
  "request_id": "req_01JABCDEF",
  "meta": {"server_time": "2026-10-23T01:00:00Z"}
}
```

限制：

- 最多返回两个已批准图表定义；
- API 返回维度和指标数据，前端使用 ECharts 构造 option 和交互；
- 图表必须绑定当前 `snapshot_id`，并返回数据质量状态；
- API 不接受任意前端计算的 SQL 或任意图表脚本；
- 数据不足时返回结构化缺口，不用空数组伪装为零值。

### 3.11 Tableau Desktop 工作簿出口（后续）

```http
GET /api/analytics/tableau/workbooks?view_id=fund_scale_histogram
```

响应：

```json
{
  "data": {
    "workbook_id": "fund_scale_histogram_v1",
    "title": "基金规模直方图",
    "file_name": "fund_scale_histogram.twbx",
    "download_url": "/api/analytics/tableau/workbooks/fund_scale_histogram_v1/file",
    "snapshot_id": "batch_20261008_001",
    "view_sql_id": "vw_fund_scale_snapshot"
  }
}
```

该接口只提供工作簿模板和只读视图标识，不返回或启动 Tableau Server 嵌入会话。

## 4. 任务状态枚举

`jobs.status`：

| 值 | 含义 |
|---|---|
| `waiting_lock` | 等待全局写锁 |
| `queued` | 已持锁，等待 Worker 领取 |
| `running` | Worker 正在执行 |
| `retrying` | 自动重试中 |
| `validating` | 正在做覆盖率和质量校验 |
| `review_required` | 存在阻断性问题，等待用户确认 |
| `publishing` | 正在执行发布事务 |
| `completed` | 发布成功或任务完成 |
| `failed` | 达到失败上限 |
| `cancelled` | 用户取消 |
| `publish_failed` | 发布事务失败 |

`jobs.outcome`：

| 值 | 含义 |
|---|---|
| `complete` | 无业务跳过 |
| `completed_with_gaps` | 达到发布门槛，但存在已记录 SKIP |
| `partial` | 只完成了部分阶段，未正式发布 |

## 5. 错误码

| 错误码 | HTTP | 可重试 | 含义 |
|---|---:|---|---|
| `E_AUTH_REQUIRED` | 401 | 否 | 未登录或会话失效 |
| `E_AUTH_FORBIDDEN` | 403 | 否 | 无操作权限 |
| `E_SCOPE_EMPTY` | 422 | 否 | 筛选后没有基金 |
| `E_SCOPE_INVALID` | 422 | 否 | 范围字段或枚举非法 |
| `E_SCOPE_TOO_LARGE` | 422 | 否 | 超过当前版本允许的批量规模 |
| `E_LOCK_BUSY` | 409 | 是 | 已有写任务持锁 |
| `E_LOCK_LOST` | 409 | 是 | Worker 租约失效 |
| `E_JOB_NOT_FOUND` | 404 | 否 | 任务不存在 |
| `E_JOB_STATE_CONFLICT` | 409 | 否 | 当前状态不允许该操作 |
| `E_IDEMPOTENCY_KEY` | 422 | 否 | Idempotency-Key 缺失或长度不合法 |
| `E_IDEMPOTENCY_CONFLICT` | 409 | 否 | 同一幂等键用于不同请求体 |
| `E_TASK_EXECUTION` | 500 | 是 | 任务 Adapter 或子进程执行失败 |
| `E_TASK_CANCELLED` | 200 | 否 | 任务已请求取消，由 Worker 收尾 |
| `E_LAST_EVENT_INVALID` | 422 | 否 | SSE Last-Event-ID 格式无效 |
| `E_SOURCE_NOT_REGISTERED` | 422 | 否 | 没有可用的来源适配器 |
| `E_SOURCE_TIMEOUT` | 504 | 是 | 来源访问超时 |
| `E_SOURCE_BLOCKED` | 403 | 否 | 来源拒绝访问或需要人工处理 |
| `E_FILE_INVALID` | 422 | 否 | 下载文件不是允许格式 |
| `E_FILE_HASH_MISMATCH` | 409 | 是 | 文件内容与 manifest 不一致 |
| `E_PARSE_FAILED` | 502 | 是 | MinerU 或本地解析失败 |
| `E_STRUCTURE_SCHEMA` | 422 | 是 | 模型输出不符合 Schema |
| `E_QUALITY_BLOCKER` | 422 | 否 | 存在阻断性质量问题 |
| `E_PUBLISH_CONFLICT` | 409 | 是 | 发布指针或批次状态冲突 |
| `E_PUBLISH_MANIFEST` | 422 | 否 | manifest 不完整或不可验证 |
| `E_MODEL_UNAVAILABLE` | 503 | 是 | 模型服务不可用 |
| `E_MODEL_CREDENTIAL` | 401 | 否 | 个人或平台 Key 无效 |
| `E_RAG_EVIDENCE_INSUFFICIENT` | 200 | 否 | 回答依据不足，返回结构化缺口 |
| `E_RAG_CITATION_INVALID` | 422 | 否 | 引用不匹配基金、版本或页码 |
| `E_PERSPECTIVE_DATA_UNAVAILABLE` | 422 | 是 | 透视数据缺少必要指标或快照 |
| `E_WORKBOOK_TEMPLATE_UNAVAILABLE` | 404 | 否 | Tableau Desktop 工作簿模板不存在或版本不兼容 |
| `E_EXPORT_FAILED` | 500 | 是 | 导出或模板渲染失败 |

`E_RAG_EVIDENCE_INSUFFICIENT` 是业务结果而非 HTTP 错误，前端根据响应显示“现有资料不足”。

## 6. 幂等和并发

### 6.1 创建任务

- 幂等键作用域为 `(user_id, endpoint, idempotency_key)`；
- 相同键和相同请求体返回原任务；
- 相同键但不同请求体返回 `E_IDEMPOTENCY_CONFLICT`；
- 范围冻结后，后续新增基金不得进入本任务分母。

### 6.2 Worker 上报

- `event_id` 全局唯一；
- `(job_id, sequence_no)` 唯一并且单调递增；
- 重复上报同一 `event_id` 返回 `200`，不重复写事件；
- Worker 不能伪造发布结果，必须经过 API 发布服务。

### 6.3 查询

- `GET` 请求默认从 `publication_state.current_batch_id` 读取；
- 对需要重放的 Agent 回答，显式带上原 `snapshot_id`；
- 已发布快照不因当前任务运行而改变。

## 7. 响应中的数据质量语义

每个数值响应可以附带：

```json
{
  "value": 12.4,
  "unit": "HKD_100M",
  "quality": "complete",
  "source_page": 18,
  "document_version_id": "018f...",
  "formula_id": "net_growth.v1"
}
```

`quality` 只能取：

```text
complete
missing_file
missing_metric
parse_failed
extraction_failed
validation_warning
review_required
stale
```

前端和 Agent 必须保留这些状态，不得把 `missing_metric` 简化为 0。

## 8. OpenAPI 与实现顺序

D2 已完成：

1. `/health`、`/snapshots/current`、`/funds`；
2. 基金详情、指标和质量问题；
3. ECharts 规模直方图和规模趋势；
4. 只读分析视图和实际 HTTP P95 冒烟。

D3 已完成：

1. 创建、查询、取消任务和 SSE 事件；
2. Worker 内部事件和发布接口；
3. 单写租约锁、staging manifest 与 API-owned 批次发布；
4. 现有下载、MinerU 和结构化脚本的白名单 Adapter。

后续继续：

1. Tableau Desktop 工作簿出口；
2. 问答、Agent 和导出。

FastAPI 生成的 `openapi-generated-v0.3.json` 是当前实际接口的机器契约；
`openapi-v0.3.yaml` 由同一应用生成，不能再手工维护平行的 API 字段。

契约版本升级规则：

- 新增可选字段为次版本升级；
- 删除字段、修改枚举或改变语义为主版本升级；
- API 响应、SSE payload、导出清单和 Agent 工具结果都要带 `schema_version`。
