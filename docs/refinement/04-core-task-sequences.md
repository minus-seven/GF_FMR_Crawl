# 核心任务时序与发布事务

> 视图类型：UML Sequence、状态机、事务边界  
> 范围：预设数据更新任务和 Agent 触发的写任务  
> 读者：后端、Worker、Agent、测试、运维

## 1. 关键决策

1. PostgreSQL 任务表就是 V1/V2 的队列，不引入 Redis 或消息中间件。
2. 全系统只有一个 `global_write` 租约锁；V1 和 V2 都遵守。
3. 任务范围在提交事务中冻结到 `job_fund_scope`。
4. Worker 写 staging 和文件批次，但不直接切换正式发布指针。
5. API 的发布服务负责最终发布事务，保证发布规则只有一处实现。
6. 已发布快照不可修改；运行中任务不会让其他用户看到半成品。
7. 任务失败可以保留诊断产物，但不会切换 `current_batch_id`。

## 2. 任务提交与锁竞争

```mermaid
sequenceDiagram
    autonumber
    actor U as 投研人员
    participant W as Web 看板
    participant A as 业务 API
    participant J as 任务协调服务
    participant DB as PostgreSQL

    U->>W: 选择范围、阶段和输出
    W->>A: POST /api/jobs
    A->>A: 校验身份、阶段、范围和幂等键
    A->>J: create_job(scope, stages)
    J->>DB: BEGIN
    J->>DB: 解析筛选条件并写入 job_fund_scope
    J->>DB: 写入 jobs / job_steps
    J->>DB: INSERT job_locks ON CONFLICT DO NOTHING
    alt 获得全局写锁
        J->>DB: 绑定 lock 到 job，状态 queued
        J->>DB: COMMIT
        A-->>W: 202 Accepted, job_id
    else 已有有效任务锁
        J->>DB: ROLLBACK 本任务锁声明
        J->>DB: 保存状态 waiting_lock
        J->>DB: COMMIT
        A-->>W: 202 Accepted, queue_position
    else 同一幂等键已存在
        J->>DB: 读取原 job_id
        A-->>W: 200 Existing job_id
    end
    W->>A: GET /api/jobs/{id}/events
    A-->>W: SSE 状态和进度
```

### 提交约束

- `scope_definition` 保存原始筛选条件；
- `job_fund_scope` 保存冻结后的基金 ID、代码和名称；
- 本次覆盖率的唯一分母是 `COUNT(job_fund_scope)`；
- 同一 `Idempotency-Key` 返回原任务，不重新执行；
- 任务未获得锁时可排队，但不得开始下载。

## 3. 数据更新 happy path

```mermaid
sequenceDiagram
    autonumber
    participant W as 数据任务 Worker
    participant DB as PostgreSQL
    participant API as 业务 API
    participant SRC as 基金官网与公开来源
    participant M as MinerU
    participant Q as Qwen
    participant FS as 受管文件存储
    participant UI as Web 看板

    W->>DB: SELECT queued job FOR UPDATE SKIP LOCKED
    W->>DB: 状态改为 running，续约 heartbeat
    W->>DB: 读取冻结范围和来源适配器
    loop 按基金或来源分组处理
        W->>DB: 判断月报链接/运行方式是否完整
        opt 来源缺失且管理人已适配
            W->>FS: 复制任务级 crawler 工作簿并清空目标行 I/J
            W->>SRC: 执行公司 probe 和 backfill，仅处理冻结 Excel 行
            SRC-->>W: OK(I/J) 或 SKIP 原因
            W->>DB: 立即提交 fund_report_sources 和补全 metadata
            W-->>UI: SSE job.backfill
        end
        W->>SRC: 使用完整来源下载最新月报或允许的替代财报
        SRC-->>W: PDF 或明确 SKIP 原因
        W->>FS: 写入 staging/batch_id/ 原始文件
        W->>M: 提交 PDF 解析
        M-->>W: Markdown、content_list、图片和页码
        W->>FS: 保存解析产物
        W->>Q: 提交必要页面和结构化 Schema
        Q-->>W: 候选字段、页码和置信度
        W->>W: Schema、权重、日期、币种和来源匹配校验
        W->>DB: 写入带 batch_id 的 staging 数据
        W->>API: POST /api/internal/jobs/{id}/events
        API-->>UI: SSE 基金级进度
    end
    W->>FS: 生成 batch manifest 和 SHA-256 清单
    W->>DB: 状态改为 validating
    W->>W: 计算覆盖率、失败分类和缺口清单
    alt 达到发布条件
        W->>API: POST /api/internal/jobs/{id}/publish
        API->>DB: 校验锁、manifest、批次和覆盖率
        API->>DB: 发布事务切换 current_batch_id
        API-->>W: published
        API-->>UI: completed / completed_with_gaps
    else 未达到发布条件
        W->>DB: 状态 failed 或 review_required
        API-->>UI: 明确缺口和重试建议
    end
```

## 4. 发布与并发读取

```mermaid
sequenceDiagram
    autonumber
    participant W as Worker
    participant API as 发布服务
    participant DB as PostgreSQL
    participant R as 其他投研用户
    participant FS as 文件存储

    W->>API: 提交 publish(batch_id, manifest_sha256)
    API->>DB: BEGIN
    API->>DB: SELECT job_locks / dataset_batches FOR UPDATE
    API->>DB: 校验当前任务持有锁且 heartbeat 未过期
    API->>DB: 校验 batch 清单、范围分母和发布门槛
    FS-->>API: 读取并验证 manifest 哈希
    API->>DB: UPDATE dataset_batches SET status = published
    API->>DB: UPDATE publication_state SET current_batch_id
    API->>DB: UPDATE job status = completed
    API->>DB: DELETE 当前 job 的全局锁
    API->>DB: COMMIT
    API-->>W: 发布成功

    par 发布前后读取
        R->>API: 查询基金和统计
        API->>DB: 读取 publication_state.current_batch_id
        DB-->>API: 发布前为旧批次，提交后为新批次
    and 任务运行期间
        R->>API: 查询、搜索和问答
        API->>DB: 固定读取旧 batch_id
    end
```

### 发布事务伪代码

```sql
BEGIN;

SELECT *
FROM job_locks
WHERE lock_name = 'global_write'
FOR UPDATE;

SELECT *
FROM dataset_batches
WHERE id = :batch_id
FOR UPDATE;

-- 应用层校验 job_id、lock owner、heartbeat、manifest、
-- 冻结范围、最低质量和允许的 SKIP。

UPDATE dataset_batches
SET status = 'published', published_at = now()
WHERE id = :batch_id;

UPDATE publication_state
SET current_batch_id = :batch_id,
    published_at = now(),
    published_by = :user_id
WHERE id = 1;

UPDATE jobs
SET status = :final_status, finished_at = now()
WHERE id = :job_id;

DELETE FROM job_locks
WHERE lock_name = 'global_write' AND job_id = :job_id;

COMMIT;
```

数据库约束或触发器必须保证：

- `current_batch_id` 指向 `published` 批次；
- `global_write` 最多一行；
- 发布指针和批次状态在同一事务内变化；
- 事务提交后新的读请求才看到新批次。

## 5. 状态机

```mermaid
stateDiagram-v2
    [*] --> waiting_lock: 提交任务
    waiting_lock --> queued: 获得全局写锁
    queued --> running: Worker 领取
    running --> validating: 所有基金处理结束
    validating --> review_required: 有阻断性质量缺口
    review_required --> running: 用户确认重试
    validating --> publishing: 达到发布条件
    publishing --> completed: 发布事务成功
    publishing --> publish_failed: 发布事务失败
    publish_failed --> running: 修复后重试
    running --> retrying: 临时错误
    retrying --> running: 退避后重试
    retrying --> failed: 达到重试上限
    waiting_lock --> cancelled: 用户取消
    queued --> cancelled: 用户取消
    running --> cancelled: 取消并终止子进程
    validating --> cancelled: 用户取消
    completed --> [*]
    failed --> [*]
    cancelled --> [*]
```

`completed_with_gaps` 作为 `jobs.outcome` 字段保存，不与主状态混用。主状态仍是 `completed`，但结果标记存在允许的 SKIP。

## 6. 错误与恢复

| 错误 | 任务状态 | 是否释放锁 | 处理 |
|---|---|---|---|
| 无锁 | `waiting_lock` | 无锁可释放 | 排队 |
| 下载超时 | `retrying` | 保持 | 指数退避 |
| 官网结构变化 | `review_required` | 保持直到任务结束 | 停止盲试，标记适配器 |
| 来源补全未命中 | `completed_with_gaps` 或 `failed` | 保持 | 保存 SKIP 原因；无成功下载时失败 |
| 来源补全脚本异常 | `retrying` 或 `failed` | 保持 | 保留原 I/J，记录脚本、退出码和 metadata |
| MinerU 单文件失败 | `running` | 保持 | 本地文本降级并降低质量 |
| Qwen 输出 Schema 错误 | `retrying` | 保持 | 修复一次，再失败进 review |
| Worker 崩溃 | `lease_expired` | 租约到期后恢复 | 新 Worker 恢复 |
| 发布事务失败 | `publish_failed` | 保持 | 不切换指针，允许重试 |
| 低于覆盖率门槛 | `review_required` | 保持或结束 | 用户决定继续、补数或取消 |

## 7. API 与时序对应

| 接口 | 用途 | 幂等性 |
|---|---|---|
| `POST /api/jobs` | 创建并冻结任务范围 | `Idempotency-Key` |
| `GET /api/jobs/{id}` | 获取任务快照 | 自然幂等 |
| `GET /api/jobs/{id}/events` | SSE 推送状态 | 自动重连使用 `Last-Event-ID` |
| `POST /api/jobs/{id}/cancel` | 取消任务 | 对相同请求幂等 |
| `POST /api/internal/jobs/{id}/events` | Worker 上报事件 | `event_id` 唯一 |
| `POST /api/internal/jobs/{id}/publish` | 提交发布申请 | `batch_id + manifest_sha256` |
| `GET /api/snapshots/current` | 获取当前快照元数据 | 自然幂等 |

## 8. 待实现前确认

- Worker 是否通过 API 轮询事件，还是直接写 `job_events`；
- 达到 90% 但存在 SKIP 时是否自动发布，或必须由用户确认；
- 取消任务时是否终止正在进行的 MinerU/Qwen 请求；
- 任务锁丢失后是否允许重新申请，还是整批重跑；
- API 与 Worker 的内部调用认证方式。
